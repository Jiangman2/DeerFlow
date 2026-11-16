"""Exercise deployed fixture relocation and candidate imports without a browser."""
import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
CANDIDATE = REPO / 'portable-skills/candidates/energy-market-daily-2.1.2'
spec = importlib.util.spec_from_file_location('context_skill_verifier', HERE / 'verify_child.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class RelocatedRegressionTests(unittest.TestCase):
    def test_all_55_cases_bind_to_readonly_candidate_from_relocated_fixtures(self):
        with tempfile.TemporaryDirectory(prefix='context-qual-fixtures-') as name:
            temporary = Path(name)
            for relative in ('test_energy_market_daily.py','test_energy_single_delivery_20261114.py',
                             'fixtures/approved.json','fixtures/original_renderer.py',
                             'fixtures/dxy-source.txt','fixtures/cny-source.txt'):
                destination = temporary / 'tests' / relative
                destination.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(REPO / 'portable-skills/tests' / relative,destination)
            sys.path.insert(0,str(CANDIDATE / 'scripts'))
            # Windows has no O_NOFOLLOW; only this local test replaces the
            # regular-file reader. Linux qualification retains the real guard.
            with patch.object(verifier,'SKILL',CANDIDATE), patch.object(verifier,'read',side_effect=lambda path: path.read_bytes()):
                result = verifier.offline_regressions(temporary)
            self.assertEqual(result['run'],55)
            self.assertTrue(result['passed'])
            self.assertTrue(result['candidate_import_bound'])


if __name__ == '__main__':
    unittest.main()
