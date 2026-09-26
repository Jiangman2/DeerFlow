import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('oci_fix1', ROOT / 'tools' / 'repair_oci_name_selection.py')
fix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fix)


class OciControlRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='oci-repair-')
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.original = (ROOT / 'build-candidate-datax-01.sh').read_text(encoding='utf-8').replace(fix.NEW, fix.OLD, 1).encode('utf-8')
        self.assertEqual(hashlib.sha256(self.original).hexdigest(), fix.OLD_HASH)
        self.script = self.root / 'build-candidate-datax-01.sh'
        self.script.write_bytes(self.original)
        (self.root / 'unchanged-payload.py').write_bytes(b'# retained application code\n')
        self.write_manifest()

    def write_manifest(self):
        content = ''.join(hashlib.sha256((self.root / name).read_bytes()).hexdigest() + '  ' + name + '\n'
                          for name in ('build-candidate-datax-01.sh', 'unchanged-payload.py'))
        (self.root / 'PACKAGE.SHA256').write_bytes(content.encode())

    def test_exact_repair_backup_and_idempotence(self):
        manifest_before = (self.root / 'PACKAGE.SHA256').read_bytes()
        fix.repair(self.root)
        fixed = self.script.read_bytes()
        self.assertEqual(fixed, (ROOT / 'build-candidate-datax-01.sh').read_text(encoding='utf-8').encode())
        self.assertEqual(self.root.joinpath(self.script.name + '.before-oci-fix1').read_bytes(), self.original)
        self.assertEqual(self.root.joinpath('PACKAGE.SHA256.before-oci-fix1').read_bytes(), manifest_before)
        self.assertNotIn(b'\r', self.root.joinpath('PACKAGE.SHA256').read_bytes())
        for row in self.root.joinpath('PACKAGE.SHA256').read_text().splitlines():
            expected, name = row.split('  ', 1)
            self.assertEqual(expected, hashlib.sha256((self.root / name).read_bytes()).hexdigest())
        fix.repair(self.root)
        self.assertEqual(self.script.read_bytes(), fixed)
        self.assertEqual((self.root / 'unchanged-payload.py').read_bytes(), b'# retained application code\n')

    def test_tampered_other_file_stops_before_mutation(self):
        (self.root / 'unchanged-payload.py').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            fix.repair(self.root)
        self.assertEqual(self.script.read_bytes(), self.original)
        self.assertFalse(self.root.joinpath(self.script.name + '.before-oci-fix1').exists())

    def test_rehashed_unknown_builder_is_not_accepted(self):
        self.script.write_bytes(self.original + b'\n# unknown edit\n')
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'Unknown build-script'):
            fix.repair(self.root)
        self.assertFalse(self.root.joinpath(self.script.name + '.before-oci-fix1').exists())

    def test_existing_backup_is_not_overwritten(self):
        backup = self.root.joinpath(self.script.name + '.before-oci-fix1')
        backup.write_bytes(b'keep evidence')
        with self.assertRaisesRegex(ValueError, 'Backup already exists'):
            fix.repair(self.root)
        self.assertEqual(backup.read_bytes(), b'keep evidence')
        self.assertEqual(self.script.read_bytes(), self.original)


if __name__ == '__main__':
    unittest.main()
