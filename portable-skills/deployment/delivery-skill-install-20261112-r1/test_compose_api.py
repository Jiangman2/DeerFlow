"""Exercise the compose qualifier against the real candidate API, offline.

The PNG bytes here are a header-only unit-test fixture. Real ARM64 rendering is
independently required by verify_child.py before this same probe may run.
"""
import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]
CANDIDATE = REPO / 'portable-skills/candidates/energy-market-daily-2.1.0'
sys.path.insert(0, str(CANDIDATE / 'scripts'))
import daily

spec = importlib.util.spec_from_file_location('delivery_installer_fixtures', REPO / 'portable-skills/tests/test_energy_market_daily.py')
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
spec = importlib.util.spec_from_file_location('delivery_installer_compose_probe', ROOT / 'compose_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class ComposeApiTests(unittest.TestCase):
    def test_real_candidate_api_complete_missing_and_unconfirmed_cases(self):
        self.assertEqual(Path(daily.__file__).resolve(), (CANDIDATE / 'scripts/daily.py').resolve())
        self.assertEqual(daily.VERSION, '2.1.0-portable')
        with tempfile.TemporaryDirectory(prefix='delivery-compose-api-') as location:
            temporary = Path(location)
            project = temporary / 'project'
            project.mkdir()
            fixtures.project_fixture(project)
            def header_only_png(html):
                html.with_suffix('.png').write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 8 + struct.pack('>II', 780, 5250) + b'\x00' * 1200)
                return {'width': 780, 'height': 5250, 'browser_fallback_count': 0}
            with patch.object(daily, 'png', side_effect=header_only_png):
                report = daily.build(SimpleNamespace(workspace=str(project)))
            result = probe.qualify(daily, project, report, temporary)
            self.assertEqual(result['status'], 'COMPOSE_ONLY_SUCCESS_AND_MISSING_CASES_QUALIFIED')
            self.assertTrue(result['complete_artifacts_accepted'])
            self.assertTrue(result['missing_artifact_rejected'])
            self.assertTrue(result['missing_projection_marked_unconfirmed'])
            self.assertTrue(result['missing_data_marked'])
            self.assertTrue(result['accepted_four_files_unchanged'])
            self.assertFalse(result['real_publication_proven'])
            self.assertEqual(result['network_requests'], 0)
            self.assertEqual(result['messages_sent'], 0)


if __name__ == '__main__':
    unittest.main()
