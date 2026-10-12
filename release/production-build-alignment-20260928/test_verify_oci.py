import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from verify_oci import expected_identity, verify_archive


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def fixture(path, corrupt_layer=False):
    layer = b"production-image-layer"
    config = encoded({"architecture": "arm64", "os": "linux"})
    manifest = encoded(
        {"config": {"digest": digest(config)}, "layers": [{"digest": digest(layer)}]}
    )
    index = encoded({"manifests": [{"digest": digest(manifest)}]})
    files = {
        "index.json": index,
        "blobs/sha256/" + digest(manifest)[7:]: manifest,
        "blobs/sha256/" + digest(config)[7:]: config,
        "blobs/sha256/" + digest(layer)[7:]: b"corrupted" if corrupt_layer else layer,
    }
    with tarfile.open(path, "w") as archive:
        for name, content in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    return digest(manifest), digest(config)


class VerifyOciTest(unittest.TestCase):
    def test_full_archive_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.tar"
            manifest, config = fixture(path)
            self.assertEqual(
                verify_archive(path, manifest, config)["layers_verified"], 1
            )

    def test_corrupted_layer_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.tar"
            manifest, config = fixture(path, corrupt_layer=True)
            with self.assertRaisesRegex(ValueError, "corrupt_oci_blob"):
                verify_archive(path, manifest, config)

    def test_image_without_manifest_lock_fails_closed(self):
        lock = {
            "workloads": [
                {"containers": [{"image": "example:latest"}], "init_containers": []}
            ],
            "runtime_images": [],
        }
        with self.assertRaisesRegex(ValueError, "not_collected"):
            expected_identity(lock, "example:latest")


if __name__ == "__main__":
    unittest.main()
