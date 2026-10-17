import gzip
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

import prepare_public_inputs as p


class PublicInputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_crate_paths(self):
        for name, expected in {"a": "1/a", "ab": "2/ab", "abc": "3/a/abc", "serde": "se/rd/serde"}.items():
            self.assertEqual(p.crate_index_path(name), expected)
        with self.assertRaises(ValueError):
            p.crate_index_path("../foo")

    def test_download_exact_and_verified_resume(self):
        data = b"verified-public-artifact"
        digest = hashlib.sha256(data).hexdigest()
        target = self.root / "blob"
        with patch.object(p, "response", return_value=io.BytesIO(data)) as request:
            self.assertEqual(p.download("https://example.org/a", target, digest, len(data))["bytes"], len(data))
            p.download("https://example.org/a", target, digest, len(data))
            self.assertEqual(request.call_count, 1)

    def test_mismatch_never_published(self):
        target = self.root / "blob"
        with patch.object(p, "response", return_value=io.BytesIO(b"wrong")):
            with self.assertRaisesRegex(ValueError, "digest_mismatch"):
                p.download("https://example.org/a", target, "0"*64)
        self.assertFalse(target.exists())
        self.assertEqual(len(list(self.root.glob("*.partial-*"))), 1)

    def test_oversize_never_published(self):
        with patch.object(p, "response", return_value=io.BytesIO(b"too big")):
            with self.assertRaisesRegex(ValueError, "size_limit"):
                p.download("https://example.org/a", self.root / "blob", "0"*64, 3)
        self.assertFalse((self.root / "blob").exists())

    def test_corrupt_cache_not_overwritten(self):
        target = self.root / "blob"
        target.write_bytes(b"old")
        with patch.object(p, "response") as request:
            with self.assertRaisesRegex(ValueError, "cached_digest_mismatch"):
                p.download("https://example.org/a", target, "0"*64)
            request.assert_not_called()
        self.assertEqual(target.read_bytes(), b"old")

    def test_write_once_no_replacement(self):
        target = self.root / "receipt"
        p.write_once(target, b"original")
        p.write_once(target, b"original")
        with self.assertRaises(ValueError):
            p.write_once(target, b"replacement")
        self.assertEqual(target.read_bytes(), b"original")

    def test_redirect_strips_auth_and_forbids_http(self):
        req = urllib.request.Request("https://registry.example.org/blob", headers={"Authorization": "Bearer public-pull"})
        h = p.PublicRedirect()
        result = h.redirect_request(req, None, 302, "", {}, "https://cdn.example.org/blob")
        self.assertIsNone(result.get_header("Authorization"))
        with self.assertRaises(ValueError):
            h.redirect_request(req, None, 302, "", {}, "http://cdn.example.org/blob")

    def test_layer_diffid_and_platform(self):
        raw = b"test layer"
        compressed = gzip.compress(raw)
        digest = hashlib.sha256(compressed).hexdigest()
        p.write_once(self.root / "blobs/sha256" / digest, compressed)
        manifest = {"layers": [{"digest": "sha256:"+digest, "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip"}]}
        config = {"os": "linux", "architecture": "arm64", "rootfs": {"diff_ids": ["sha256:"+hashlib.sha256(raw).hexdigest()]}}
        self.assertEqual(p.verify_layers(self.root, manifest, config), len(raw))
        config["rootfs"]["diff_ids"][0] = "sha256:"+"0"*64
        with self.assertRaisesRegex(ValueError, "diffid_mismatch"):
            p.verify_layers(self.root, manifest, config)
        config["architecture"] = "amd64"
        with self.assertRaisesRegex(ValueError, "platform"):
            p.verify_layers(self.root, manifest, config)

    def test_manifest_remains_not_release_approval(self):
        lock = json.loads((p.HERE / "BUILD-INPUT-LOCK.json").read_bytes())
        self.assertEqual(lock["runtime_base"]["operator_observed_glibc"], "2.36")
        self.assertFalse(lock["ready_for_build"])
        self.assertFalse(lock["ready_for_rollout"])


if __name__ == "__main__":
    unittest.main()
