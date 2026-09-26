"""Exercise the actual build-script heredoc, not a copy of its selector."""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class OciNameSelectionTests(unittest.TestCase):
    image = 'deerflow.local/deer-flow-gateway:20260923-enterprise-arm64-rc15-ppt-master-v660-r15'

    @classmethod
    def setUpClass(cls):
        text = Path(__file__).resolve().parents[1].joinpath('build-candidate-datax-01.sh').read_text(encoding='utf-8')
        marker = '  python3 - "${layout}" "${source_image}" <<\'PY\'\n'
        cls.code = text.split(marker, 1)[1].split('\nPY\n', 1)[0]

    def run_selector(self, annotations, copies=1, missing=False, corrupt=False, digest=None, media_type=None):
        with tempfile.TemporaryDirectory(prefix='oci-selector-') as directory:
            root = Path(directory)
            payload = json.dumps({'schemaVersion': 2, 'mediaType': media_type or 'application/vnd.oci.image.manifest.v1+json'}).encode()
            expected = hashlib.sha256(payload).hexdigest()
            blob = root / 'blobs' / 'sha256' / expected
            blob.parent.mkdir(parents=True)
            if not missing:
                blob.write_bytes(b'corrupt' if corrupt else payload)
            descriptor = {'mediaType': media_type or 'application/vnd.oci.image.manifest.v1+json',
                          'digest': digest or 'sha256:' + expected,
                          'size': len(payload), 'annotations': annotations}
            (root / 'index.json').write_text(json.dumps({'schemaVersion': 2, 'manifests': [descriptor] * copies}), encoding='utf-8')
            result = subprocess.run([sys.executable, '-c', self.code, str(root), self.image], capture_output=True, text=True)
            receipt = root / 'manifest.digest'
            return result, receipt.read_text().strip() if receipt.exists() else None, 'sha256:' + expected

    def test_containerd_full_name_and_tag_only_ref(self):
        result, digest, expected = self.run_selector({'io.containerd.image.name': self.image,
                                                      'org.opencontainers.image.ref.name': self.image.split(':')[-1]})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(digest, expected)

    def test_legacy_full_ref(self):
        result, digest, expected = self.run_selector({'org.opencontainers.image.ref.name': self.image})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(digest, expected)

    def test_exact_containerd_name_without_ref(self):
        result, _, _ = self.run_selector({'io.containerd.image.name': self.image})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_index_descriptor_keeps_original_digest(self):
        result, digest, expected = self.run_selector({'io.containerd.image.name': self.image}, media_type='application/vnd.oci.image.index.v1+json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(digest, expected)

    def test_never_selects_unidentified_or_conflicting_image(self):
        cases = [
            {},
            {'org.opencontainers.image.ref.name': self.image.split(':')[-1]},
            {'io.containerd.image.name': 'different/image:same-tag'},
            {'io.containerd.image.name': 'different/image:tag', 'org.opencontainers.image.ref.name': self.image},
        ]
        for annotations in cases:
            with self.subTest(annotations=annotations):
                result, digest, _ = self.run_selector(annotations)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('base_manifest_match_count=0', result.stderr)
                self.assertIsNone(digest)

    def test_multiple_exact_matches_fail_closed(self):
        result, digest, _ = self.run_selector({'io.containerd.image.name': self.image}, copies=2)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('base_manifest_match_count=2', result.stderr)
        self.assertIsNone(digest)

    def test_missing_blob_fails_closed(self):
        result, digest, _ = self.run_selector({'io.containerd.image.name': self.image}, missing=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(digest)

    def test_blob_checksum_still_required(self):
        result, digest, _ = self.run_selector({'io.containerd.image.name': self.image}, corrupt=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('base_manifest_checksum_failed', result.stderr)
        self.assertIsNone(digest)

    def test_other_digest_algorithm_fails_closed(self):
        result, digest, _ = self.run_selector({'io.containerd.image.name': self.image}, digest='sha512:' + '0' * 128)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('invalid_base_digest', result.stderr)
        self.assertIsNone(digest)


if __name__ == '__main__':
    unittest.main()
