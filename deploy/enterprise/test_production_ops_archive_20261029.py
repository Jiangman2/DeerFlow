"""Archive qualification uses bytes only, never a production maintenance entry."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('ops_archive', Path(__file__).with_name('verify-production-ops-archive-20261029.py'))
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class ArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = verifier.ROOT / verifier.PREFIX
        cls.payload = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*')
                       if p.is_file() and '__pycache__' not in p.parts}

    def test_archived_installer_reproduces_delivery_and_acceptance(self):
        result = verifier.verify_payload(self.payload.__getitem__, self.payload.keys())
        self.assertTrue(result['policy_installation_accepted'])
        self.assertFalse(result['first_scheduled_execution_confirmed'])
        self.assertFalse(result['archived_tools_executed'])

    def test_modified_delivered_script_is_rejected(self):
        payload = dict(self.payload)
        payload['delivered/install-portal-0015-auth-log-policy-20261028-r1.py'] += b'\n'
        with self.assertRaisesRegex(ValueError, 'ARCHIVE_BYTES_CHANGED'):
            verifier.verify_payload(payload.__getitem__, payload.keys())

    def test_rewritten_inventory_cannot_accept_changed_source(self):
        payload = dict(self.payload)
        payload['MANIFEST.json'] = payload['MANIFEST.json'].replace(b'ecs-zdz-pro-0015', b'datax-01')
        with self.assertRaisesRegex(ValueError, 'ARCHIVE_MANIFEST_CHANGED'):
            verifier.verify_payload(payload.__getitem__, payload.keys())

    def test_missing_or_unrecorded_artifact_is_rejected(self):
        for names in (set(self.payload) - {'evidence/dispatcher-recovery-field.json'}, set(self.payload) | {'unexpected.py'}):
            with self.assertRaisesRegex(ValueError, 'ARCHIVE_FILE_SET_CHANGED'):
                verifier.verify_payload(self.payload.__getitem__, names)

    def test_changed_operator_receipt_is_rejected(self):
        payload = dict(self.payload)
        name = 'historical/0015-auth-log-policy-install-field-20261028-r1.json'
        payload[name] = payload[name].replace(b'"first_scheduled_execution_confirmed": false', b'"first_scheduled_execution_confirmed": true')
        with self.assertRaisesRegex(ValueError, 'ARCHIVE_BYTES_CHANGED'):
            verifier.verify_payload(payload.__getitem__, payload.keys())


if __name__ == '__main__':
    unittest.main()
