"""Frozen package/asset checks; never call the operator's run/main methods."""
import importlib.util
import io
from pathlib import Path
import unittest
import zipfile


ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('delivery_installer_sealer', ROOT / 'seal.py')
seal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seal)


class SealedPackageTests(unittest.TestCase):
    def test_frozen_package_is_deterministic_and_every_member_is_exact(self):
        outputs, metadata = seal.build()
        again, repeated = seal.build()
        self.assertEqual(outputs, again)
        self.assertEqual(metadata, repeated)
        code = outputs['energy-market-daily-2.1.0-public-code.zip']
        files = seal.source_files()
        with zipfile.ZipFile(io.BytesIO(code)) as archive:
            self.assertEqual(len(archive.namelist()), 107)
            self.assertEqual({name.removeprefix('energy-market-daily/'): archive.read(name) for name in archive.namelist()}, files)
            self.assertTrue(all(item.date_time == (1980, 1, 1, 0, 0, 0) for item in archive.infolist()))
        self.assertNotIn('assets/browser-runtime.json', files)

    def test_sealed_host_decodes_exact_child_assets_and_installed_count(self):
        outputs, metadata = seal.build()
        raw = outputs['install-energy-delivery-skill-20261112-r1.py']
        scope = {'__name__': 'sealed_delivery_operator_for_readonly_test'}
        exec(compile(raw, '<sealed-delivery-operator>', 'exec'), scope)
        assets = scope['load_assets']()
        self.assertEqual(assets['package'], outputs['energy-market-daily-2.1.0-public-code.zip'])
        self.assertEqual(assets['publisher'], outputs['publisher.sealed.py'])
        self.assertEqual(assets['verifier'], outputs['verify_child.sealed.py'])
        self.assertEqual(scope['CANDIDATE_FILES'], 108)
        self.assertIn('EXPECTED_SKILL_COUNT = 108', scope['VISIBILITY_CHILD'])
        self.assertEqual(scope['CANDIDATE_VERSION'], '2.1.0-portable')
        for name, data in assets.items():
            self.assertEqual(seal.sha(data), metadata['assets'][name]['sha256'])
        self.assertNotIn(b'UNSEALED_', raw)


if __name__ == '__main__':
    unittest.main()
