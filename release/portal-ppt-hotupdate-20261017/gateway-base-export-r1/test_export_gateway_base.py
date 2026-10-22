import ast
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import time
import types
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("export_gateway_base", Path(__file__).with_name("export_gateway_base.py"))
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def fixture(platform="arm64", duplicate=False):
    layers = [b"synthetic-layer-%02d" % n for n in range(29)]
    if duplicate:
        layers[-1] = layers[0]
    config = encoded({"os": "linux", "architecture": platform,
                      "rootfs": {"type": "layers", "diff_ids": ["sha256:" + tool.sha(b) for b in layers]},
                      "config": {"Env": ["PASSWORD=SYNTHETIC_PRIVATE_VALUE"]}})
    cd = "sha256:" + tool.sha(config)
    manifest = encoded({"schemaVersion": 2, "mediaType": tool.MEDIA,
                        "config": {"mediaType": "application/vnd.oci.image.config.v1+json", "digest": cd, "size": len(config)},
                        "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip", "digest": "sha256:" + tool.sha(b), "size": len(b)} for b in layers]})
    md = "sha256:" + tool.sha(manifest)
    files = [("oci-layout", encoded({"imageLayoutVersion": "1.0.0"})),
             ("index.json", encoded({"schemaVersion": 2, "manifests": [{"mediaType": tool.MEDIA, "digest": md, "size": len(manifest), "annotations": {"io.containerd.image.name": tool.TAG}}]})),
             (tool.blob_path(md), manifest), (tool.blob_path(cd), config)]
    files.extend((tool.blob_path("sha256:" + tool.sha(b)), b) for b in dict.fromkeys(layers))
    docker = {"Config": tool.blob_path(cd), "RepoTags": [tool.TAG], "Layers": [tool.blob_path("sha256:" + tool.sha(b)) for b in layers]}
    return manifest, config, md, cd, files, docker


def archive(path, rows, extra=None):
    with tarfile.open(str(path), "w", format=tarfile.USTAR_FORMAT) as out:
        for name, data in rows:
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            out.addfile(entry, io.BytesIO(data))
        if extra is not None:
            out.addfile(extra)
    os.chmod(str(path), 0o600)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest, self.config, self.md, self.cd, self.files, self.docker = fixture()
        self.blobs, self.paths = tool.expected_blobs(self.manifest, self.config, self.md, self.cd, 29)

    def verify(self, path):
        return tool.validate_archive(path, self.blobs, self.paths, time.monotonic() + 10, self.md, self.cd)

    def test_exact_29_layers_and_optional_docker_metadata(self):
        for docker in (False, True):
            path = self.root / ("docker.tar" if docker else "oci.tar")
            archive(path, self.files + ([("manifest.json", encoded([self.docker]))] if docker else []))
            receipt = self.verify(path)
            self.assertEqual(receipt["verified_unique_blobs"], 31)
            self.assertEqual(receipt["layer_descriptors"], 29)
            self.assertEqual(receipt["optional_docker_manifest"], docker)
            self.assertEqual(receipt["sha256"], tool.sha(path.read_bytes()))
            self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", json.dumps(receipt))

    def test_missing_extra_duplicate_and_corrupt_blobs_rejected(self):
        cases = {
            "missing": self.files[:-1],
            "extra": self.files + [("secret.txt", b"PRIVATE")],
            "duplicate": self.files + [self.files[-1]],
            "corrupt": self.files[:-1] + [(self.files[-1][0], b"X" * len(self.files[-1][1]))],
            "wrong_size": self.files[:-1] + [(self.files[-1][0], b"X")],
        }
        for name, rows in cases.items():
            with self.subTest(name=name):
                path = self.root / (name + ".tar")
                archive(path, rows)
                with self.assertRaises(tool.Failure):
                    self.verify(path)

    def test_repeated_layer_descriptors_need_one_blob_and_preserve_order(self):
        m, c, md, cd, files, docker = fixture(duplicate=True)
        blobs, layers = tool.expected_blobs(m, c, md, cd)
        self.assertEqual(len(layers), 29)
        self.assertEqual(len(blobs), 30)
        path = self.root / "repeated-layer.tar"
        archive(path, files + [("manifest.json", encoded([docker]))])
        result = tool.validate_archive(path, blobs, layers, time.monotonic() + 10, md, cd)
        self.assertEqual(result["verified_unique_blobs"], 30)
        self.assertEqual(result["layer_descriptors"], 29)

    def test_unsafe_paths_links_extensions_and_nonzero_tail_rejected(self):
        for name in ("../escape", "/absolute", "blobs/../escape", "a\\b"):
            path = self.root / ("unsafe-%d.tar" % len(list(self.root.iterdir())))
            archive(path, self.files + [(name, b"x")])
            with self.assertRaises(tool.Failure):
                self.verify(path)
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.XHDTYPE, tarfile.GNUTYPE_LONGNAME):
            entry = tarfile.TarInfo("extra")
            entry.type, entry.linkname = kind, "secret" if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE) else ""
            path = self.root / ("kind-%r.tar" % kind)
            archive(path, self.files, entry)
            with self.assertRaises(tool.Failure):
                self.verify(path)
        path = self.root / "tail.tar"
        archive(path, self.files)
        with path.open("ab") as out:
            out.write(b"HIDDEN")
        with self.assertRaisesRegex(tool.Failure, "NONZERO_TAR_TRAILER"):
            self.verify(path)

    def test_index_and_docker_references_are_bound(self):
        wrong_index = encoded({"schemaVersion": 2, "manifests": [{"mediaType": tool.MEDIA, "digest": "sha256:" + "9" * 64, "size": 12}]})
        rows = [(n, wrong_index if n == "index.json" else b) for n, b in self.files]
        path = self.root / "index.tar"
        archive(path, rows)
        with self.assertRaisesRegex(tool.Failure, "INDEX_MANIFEST_MISMATCH"):
            self.verify(path)
        bad = dict(self.docker, RepoTags=["unrelated:tag"])
        path = self.root / "docker.tar"
        archive(path, self.files + [("manifest.json", encoded([bad]))])
        with self.assertRaisesRegex(tool.Failure, "DOCKER_MANIFEST_MISMATCH"):
            self.verify(path)

    def test_pinned_content_platform_count_and_deadline(self):
        with self.assertRaisesRegex(tool.Failure, "PINNED_CONTENT_HASH_MISMATCH"):
            tool.expected_blobs(self.manifest + b" ", self.config, self.md, self.cd)
        with self.assertRaisesRegex(tool.Failure, "LAYER_COUNT_MISMATCH"):
            tool.expected_blobs(self.manifest, self.config, self.md, self.cd, 28)
        m, c, md, cd, _, _ = fixture("amd64")
        with self.assertRaisesRegex(tool.Failure, "CONFIG_PLATFORM_MISMATCH"):
            tool.expected_blobs(m, c, md, cd)
        config = json.loads(self.config)
        config["rootfs"] = []
        c = encoded(config)
        manifest = json.loads(self.manifest)
        manifest["config"].update(digest="sha256:" + tool.sha(c), size=len(c))
        m = encoded(manifest)
        with self.assertRaisesRegex(tool.Failure, "CONFIG_ROOTFS_SCHEMA"):
            tool.expected_blobs(m, c, "sha256:" + tool.sha(m), "sha256:" + tool.sha(c))
        path = self.root / "late.tar"
        archive(path, self.files)
        with self.assertRaisesRegex(tool.Failure, "TOTAL_TIMEOUT"):
            tool.validate_archive(path, self.blobs, self.paths, 0, self.md, self.cd)

    def test_commands_tag_identity_and_no_dangerous_actions(self):
        runtime = tool.Runtime("/usr/bin/ctr", time.monotonic() + 10)
        with mock.patch.object(tool, "bounded", return_value=(tool.TAG + " " + tool.MEDIA + " " + tool.MANIFEST + " size platform\n").encode()) as run:
            self.assertEqual(runtime.tag(), tool.MANIFEST)
            self.assertEqual(run.call_args[0][0], ["/usr/bin/ctr", "--address", tool.SOCKET, "--namespace", "k8s.io", "images", "ls"])
        with mock.patch.object(tool, "bounded", return_value=(tool.TAG + " " + tool.MEDIA + " sha256:" + "0" * 64).encode()):
            with self.assertRaisesRegex(tool.Failure, "TAG_TARGET_CHANGED"):
                runtime.tag()
        with self.assertRaisesRegex(tool.Failure, "CONTENT_NOT_ALLOWLISTED"):
            runtime.content("sha256:" + "0" * 64)
        with mock.patch.object(tool, "bounded", return_value=b"") as run:
            runtime.export(self.root / "new.partial", 1024)
            self.assertEqual(run.call_args[0][0][-6:], ["images", "export", "--platform", "linux/arm64", str(self.root / "new.partial"), tool.TAG])
        source = Path(tool.__file__).read_text()
        ast.parse(source, feature_version=(3, 7))
        for forbidden in ('["images", "pull"', '["images", "import"', '["images", "tag"', 'extractall(', 'shell=True'):
            self.assertNotIn(forbidden, source)

    def test_actual_bounded_pipe_timeout_cap_and_failed_output(self):
        cases = [("import sys;sys.stderr.write('SYNTHETIC_PRIVATE_VALUE');sys.exit(4)", 3, 1024, "COMMAND_NONZERO_EXIT"),
                 ("print('x'*100000)", 3, 1024, "COMMAND_OUTPUT_LIMIT"),
                 ("import time;time.sleep(5)", 0.1, 1024, "COMMAND_TIMEOUT")]
        for code, timeout, limit, expected in cases:
            with self.subTest(expected=expected), self.assertRaises(tool.Failure) as failure:
                tool.bounded([sys.executable, "-I", "-B", "-c", code], timeout, limit)
            self.assertEqual(failure.exception.code, expected)
            self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", str(failure.exception))

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0, "root POSIX file semantics")
    def test_workflow_low_disk_and_drift_preserve_partial(self):
        class Fake:
            def __init__(owner, drift=False):
                owner.exports, owner.tags, owner.drift = 0, 0, drift
            def tag(owner):
                owner.tags += 1
                if owner.drift and owner.tags > 1:
                    raise tool.Failure("TAG_TARGET_CHANGED")
                return tool.MANIFEST
            def content(owner, pin):
                return self.manifest if pin == tool.MANIFEST else self.config
            def export(owner, path, maximum):
                owner.exports += 1
                archive(path, self.files)
        expected = lambda *args: (self.blobs, self.paths)
        verify = lambda path, blobs, paths, deadline: tool.validate_archive_original(path, blobs, paths, deadline, self.md, self.cd)
        tool.validate_archive_original = tool.validate_archive
        report = {"steps": [], "collection": "INCOMPLETE"}
        fake = Fake()
        with mock.patch.object(tool, "expected_blobs", side_effect=expected), mock.patch.object(tool.shutil, "disk_usage", return_value=types.SimpleNamespace(free=1)):
            with self.assertRaisesRegex(tool.Failure, "INSUFFICIENT_DISK_SPACE"):
                tool.export_workflow(self.root, fake, report, time.monotonic() + 10)
        self.assertEqual(fake.exports, 0)
        report = {"steps": [], "collection": "INCOMPLETE"}
        fake = Fake(True)
        with mock.patch.object(tool, "expected_blobs", side_effect=expected), mock.patch.object(tool, "validate_archive", side_effect=verify):
            with self.assertRaisesRegex(tool.Failure, "TAG_TARGET_CHANGED"):
                tool.export_workflow(self.root, fake, report, time.monotonic() + 10)
        self.assertEqual(fake.exports, 1)
        self.assertTrue((self.root / "gateway.base.oci.tar.partial").is_file())
        self.assertFalse((self.root / "gateway.base.oci.tar").exists())

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0, "root POSIX handoff semantics")
    def test_handoff_fixed_fds_modes_and_link_refusal(self):
        import pwd
        os.chmod(str(self.root), 0o700)
        for name in ("gateway.base.oci.tar", "REPORT.json"):
            p = self.root / name
            p.write_bytes(b"synthetic")
            os.chmod(str(p), 0o600)
        with mock.patch.object(pwd, "getpwnam", return_value=types.SimpleNamespace(pw_uid=12345, pw_gid=12345)):
            tool.handoff(self.root)
        self.assertEqual(self.root.stat().st_uid, 12345)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        for name in ("gateway.base.oci.tar", "REPORT.json"):
            self.assertEqual((self.root / name).stat().st_uid, 12345)
            self.assertEqual(stat.S_IMODE((self.root / name).stat().st_mode), 0o600)
        other = self.root / "unsafe"
        other.mkdir(mode=0o700)
        (other / "gateway.base.oci.tar").symlink_to(self.root / "gateway.base.oci.tar")
        (other / "REPORT.json").write_bytes(b"safe")
        with mock.patch.object(pwd, "getpwnam", return_value=types.SimpleNamespace(pw_uid=12345, pw_gid=12345)):
            with self.assertRaises((tool.Failure, OSError)):
                tool.handoff(other)

    @unittest.skipUnless(os.name == "posix", "directory fsync POSIX")
    def test_report_digest_is_exact_and_report_never_overwritten(self):
        result = tool.write_report(self.root, {"collection": "INCOMPLETE", "handoff": "RESULT_ONLY_ON_STDOUT_AFTER_ALL_ROOT_WRITES"})
        raw = (self.root / "REPORT.json").read_bytes()
        self.assertEqual(result["report_sha256"], tool.sha(raw))
        self.assertEqual(result["report_bytes"], len(raw))
        with self.assertRaises(FileExistsError):
            tool.write_report(self.root, {"overwrite": True})
        self.assertEqual((self.root / "REPORT.json").read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
