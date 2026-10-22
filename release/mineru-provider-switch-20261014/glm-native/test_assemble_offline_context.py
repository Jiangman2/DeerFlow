import hashlib
import io
import json
from pathlib import Path
import tempfile
import tarfile
import unittest

import assemble_offline_context as a


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def crate(self, members):
        path = self.root / "sample.crate"
        with tarfile.open(path, "w:gz") as t:
            for name, kind, body in members:
                entry = tarfile.TarInfo(name)
                entry.type = kind
                entry.size = len(body) if kind == tarfile.REGTYPE else 0
                entry.linkname = "/outside" if kind == tarfile.SYMTYPE else ""
                t.addfile(entry, io.BytesIO(body) if kind == tarfile.REGTYPE else None)
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def test_exact_one_line_anydoc_patch(self):
        before = b"// keep this\nuse render::markdown::document_to_markdown;\n// unchanged\n"
        path, digest = self.crate([("anydoc-0.1.9/src/lib.rs", tarfile.REGTYPE, before)])
        files, proof = a.anydoc_members(path, digest)
        self.assertEqual(files["src/lib.rs"], before.replace(b"\nuse ", b"\npub use "))
        self.assertEqual(files[".weknora-patched"], b"0.1.9\n")
        self.assertEqual(proof["changed_upstream_files"], ["src/lib.rs"])

    def test_crate_traversal_rejected(self):
        path, digest = self.crate([("anydoc-0.1.9/../../escape", tarfile.REGTYPE, b"x")])
        with self.assertRaisesRegex(ValueError, "unsafe_crate_path"):
            a.anydoc_members(path, digest)

    def test_symlink_rejected(self):
        path, digest = self.crate([("anydoc-0.1.9/link", tarfile.SYMTYPE, b"")])
        with self.assertRaisesRegex(ValueError, "crate_link"):
            a.anydoc_members(path, digest)

    def test_patch_drift_rejected(self):
        path, digest = self.crate([("anydoc-0.1.9/src/lib.rs", tarfile.REGTYPE, b"different source\n")])
        with self.assertRaisesRegex(ValueError, "patch_shape"):
            a.anydoc_members(path, digest)

    def test_crate_digest_checked_first(self):
        path, _ = self.crate([])
        with self.assertRaisesRegex(ValueError, "crate_digest"):
            a.anydoc_members(path, "0"*64)

    def test_manifest_is_deterministic(self):
        (self.root / "x").write_bytes(b"abc")
        n, digest = a.manifest(self.root, "SOURCE.SHA256")
        self.assertEqual(n, 1)
        self.assertEqual((self.root / "SOURCE.SHA256").read_text(), hashlib.sha256(b"abc").hexdigest()+"  x\n")
        self.assertEqual(digest, a.sha(self.root / "SOURCE.SHA256"))

    def test_new_output_required(self):
        with self.assertRaisesRegex(ValueError, "new_context_required"):
            a.assemble(self.root, self.root, self.root)

    def test_no_server_build_or_provider_command_in_collector(self):
        source = (a.HERE / "prepare_public_inputs.py").read_text()
        for argv in ("['kubectl'", '["kubectl"', "['ssh'", '["ssh"', "['docker'", '["docker"'):
            self.assertNotIn(argv, source)
        dockerfile = (a.HERE / "Dockerfile.offline").read_text()
        self.assertNotIn("--probe-once", dockerfile)
        for line in dockerfile.splitlines():
            if line.startswith("RUN "):
                self.assertIn("--network=none", line)


if __name__ == "__main__":
    unittest.main()
