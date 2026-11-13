"""Read-only preflight and complete-generation publication behavior."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

TESTS = Path(__file__).resolve().parent
sys.path[:0] = [str(TESTS), str(TESTS.parent / 'energy-market-daily/scripts')]
import common
import daily
from test_energy_market_daily import project_fixture


def fake_png(html_path):
    html_path.with_suffix('.png').write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 8 + struct.pack('>II',780,1200) + b'\x00' * 1024)
    return {'width':780,'height':1200,'browser_fallback_count':0}


class EnergyFailureHandlingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='energy-failure-handling-')
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def prepared(self):
        project_fixture(self.root)
        return {name:(self.root / name).read_bytes() for name in ('PROJECT.json','market.json','financial.json','research.json','news.json')}

    def output_bytes(self):
        outputs = self.root / 'outputs'
        return {p.relative_to(outputs).as_posix():p.read_bytes() for p in outputs.rglob('*') if p.is_file()} if outputs.exists() else {}

    def cli(self, argv):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = daily.main(argv)
        return code,json.loads(stdout.getvalue())

    def test_missing_runtime_stops_readonly_preflight_without_project_creation(self):
        project = self.root / 'not-created'
        observed = {'operator_binding_present':True,'operator_binding_applicable':True,'operator_runtime_ready':False,
                    'error_code':'BROWSER_RUNTIME_ROOT_NOT_MOUNTED','recovery_scope':'operator'}
        with patch.object(daily,'observe_runtime',return_value=observed),patch.object(daily,'credentials',return_value={}),\
             patch.object(daily.importlib.util,'find_spec',return_value=object()),patch.object(daily,'browser_candidates',return_value=['/another/browser']),\
             patch.object(daily,'runtime_browser',side_effect=AssertionError('must not copy runtime')):
            code,result = self.cli(['doctor','--workspace',str(project)])
        self.assertEqual(code,0)  # A successful read-only observation, not a successful build.
        self.assertEqual(result['status'],'ENVIRONMENT_OBSERVED')
        self.assertFalse(result['ready_for_report'])
        self.assertEqual(result['next_action'],'stop_and_report_environment')
        self.assertIn('BROWSER_RUNTIME_ROOT_NOT_MOUNTED',result['readiness_error_codes'])
        self.assertNotIn('next_command',result)
        self.assertFalse(project.exists())

    def test_generic_playwright_default_does_not_need_discovered_system_browser(self):
        observed = {'operator_binding_present':False,'operator_runtime_ready':None,'error_code':None,'recovery_scope':None}
        with patch.object(daily,'observe_runtime',return_value=observed),patch.object(daily,'credentials',return_value={}),\
             patch.object(daily.importlib.util,'find_spec',return_value=object()),patch.object(daily,'browser_candidates',return_value=[]):
            result = daily.doctor(workspace=str(self.root / 'project'),edition_date='2026-11-07',news_as_of='2026-11-07T07:00:00+08:00')
        self.assertTrue(result['ready_for_report'])
        self.assertEqual(result['next_action'],'prepare')
        self.assertIn('2026-11-07',result['next_command'])
        self.assertIn('--news-as-of',result['next_command'])
        self.assertTrue(result['network_and_browser_launch_not_yet_tested'])

    def test_nonapplicable_arm_binding_does_not_block_other_architecture(self):
        observed = {'operator_binding_present':True,'operator_binding_applicable':False,'operator_runtime_ready':None}
        with patch.object(daily,'observe_runtime',return_value=observed),patch.object(daily,'credentials',return_value={}),\
             patch.object(daily.importlib.util,'find_spec',return_value=object()),patch.object(daily,'browser_candidates',return_value=[]):
            self.assertTrue(daily.doctor()['ready_for_report'])

    def test_preflight_next_command_preserves_explicit_private_config_path(self):
        config = self.root / 'private settings.json'
        with patch.object(daily,'observe_runtime',return_value={'operator_binding_present':False}),patch.object(daily,'credentials',return_value={}),\
             patch.object(daily.importlib.util,'find_spec',return_value=object()),patch.object(daily,'browser_candidates',return_value=[]):
            result = daily.doctor(str(config),str(self.root / 'project'))
        self.assertTrue(result['ready_for_report'])
        self.assertIn('--credentials',result['next_command'])
        self.assertIn(str(config),result['next_command'])

    def test_missing_credentials_and_python_dependency_stop_preflight(self):
        with patch.object(daily,'observe_runtime',return_value={'operator_binding_present':False}),\
             patch.object(daily,'credentials',side_effect=common.Failure('CREDENTIALS_REQUIRED')),\
             patch.object(daily.importlib.util,'find_spec',return_value=None),patch.object(daily,'browser_candidates',return_value=[]):
            result = daily.doctor()
        self.assertFalse(result['ready_for_report'])
        self.assertEqual(set(result['readiness_error_codes']),{'CREDENTIALS_NOT_CONFIGURED','PLAYWRIGHT_REQUIRED'})
        self.assertFalse(result['retry_allowed'])

    def test_png_failure_never_exposes_fresh_partial_outputs(self):
        before = self.prepared()
        with patch.object(daily,'png',side_effect=common.Failure('BROWSER_RUNTIME_ROOT_NOT_MOUNTED')):
            code,result = self.cli(['build','--workspace',str(self.root)])
        self.assertEqual(code,1)
        self.assertEqual(result['files'],[])
        self.assertFalse(result['artifacts_ready'])
        self.assertFalse(result['publication_allowed'])
        self.assertEqual(result['recovery_scope'],'operator')
        self.assertFalse(result['retry_allowed'])
        self.assertFalse(result['dependency_installation_can_repair'])
        self.assertIn(str(self.root),result['resume_command'])
        self.assertTrue(result['prepared_sources_preserved'])
        self.assertFalse((self.root / 'outputs').exists())
        self.assertFalse((self.root / 'RESULT.json').exists())
        self.assertEqual(before,{name:(self.root / name).read_bytes() for name in before})
        self.assertEqual(list(self.root.glob('.energy-build-*')),[])

    def test_failed_rerender_preserves_previous_complete_generation_and_receipt(self):
        self.prepared()
        with patch.object(daily,'png',side_effect=fake_png):
            daily.build(argparse.Namespace(workspace=str(self.root)))
        daily.accept(argparse.Namespace(workspace=str(self.root),visual_reviewed=True))
        original_files = self.output_bytes()
        original_receipt = (self.root / 'RESULT.json').read_bytes()
        news = common.load(self.root / 'news.json')
        news['market_news'][0]['text'] += ' 本次材料补充了说明。'
        common.save(self.root / 'news.json',news)
        with patch.object(daily,'png',side_effect=common.Failure('NO_USABLE_BROWSER')):
            code,result = self.cli(['build','--workspace',str(self.root)])
        self.assertEqual(code,1)
        self.assertFalse(result['publication_allowed'])
        self.assertEqual(self.output_bytes(),original_files)
        self.assertEqual((self.root / 'RESULT.json').read_bytes(),original_receipt)
        self.assertEqual(list(self.root.glob('.energy-build-*')),[])

    def test_complete_generation_has_exactly_four_files_and_can_be_accepted(self):
        self.prepared()
        with patch.object(daily,'png',side_effect=fake_png):
            result = daily.build(argparse.Namespace(workspace=str(self.root)))
        generation = self.root / 'outputs' / result['outputs_generation']
        self.assertEqual({p.suffix for p in generation.iterdir()},{'.png','.html','.json','.txt'})
        self.assertEqual({Path(item['path']).parent for item in result['files']},{generation})
        self.assertFalse(any(p.is_file() for p in (self.root / 'outputs').iterdir()))
        self.assertTrue(result['artifacts_ready'])
        self.assertFalse(result['publication_allowed'])
        daily.validate_result(result,self.root)
        accepted = daily.accept(argparse.Namespace(workspace=str(self.root),visual_reviewed=True))
        self.assertEqual(accepted['status'],'REPORT_ACCEPTED')
        self.assertTrue(accepted['publication_allowed'])

    def test_missing_staged_png_cannot_reuse_stale_canonical_png(self):
        self.prepared()
        outputs = self.root / 'outputs';outputs.mkdir()
        stale = outputs / '油气与金融市场日报_2026-11-07.png';stale.write_bytes(b'stale')
        with patch.object(daily,'png',return_value={'width':780,'height':1200}):
            with self.assertRaisesRegex(common.Failure,'GENERATED_FILE_SET_INVALID'):
                daily.build(argparse.Namespace(workspace=str(self.root)))
        self.assertEqual(stale.read_bytes(),b'stale')
        self.assertEqual({p.name for p in outputs.iterdir()},{stale.name})
        self.assertFalse((self.root / 'RESULT.json').exists())

    def test_legacy_complete_receipts_remain_reusable(self):
        self.prepared()
        with patch.object(daily,'png',side_effect=fake_png):
            result = daily.build(argparse.Namespace(workspace=str(self.root)))
        result.pop('outputs_generation')
        for item in result['files']:
            old = Path(item['path']);new = self.root / 'outputs' / old.name
            old.replace(new);item['path'] = str(new)
        for version in ('2.0.0-portable','2.0.1-portable'):
            result['version'] = version
            common.save(self.root / 'RESULT.json',result)
            with patch.object(daily,'png',side_effect=AssertionError('must reuse legacy accepted files')):
                self.assertTrue(daily.build(argparse.Namespace(workspace=str(self.root)))['reused'])

    def test_generation_pointer_cannot_escape_outputs(self):
        self.prepared()
        with patch.object(daily,'png',side_effect=fake_png):
            result = daily.build(argparse.Namespace(workspace=str(self.root)))
        result['outputs_generation'] = '../outside'
        with self.assertRaisesRegex(common.Failure,'RESULT_IDENTITY_INVALID'):
            daily.validate_result(result,self.root)

    def test_prepared_project_receipt_explicitly_says_quotes_were_not_refreshed(self):
        before = self.prepared()
        with patch.object(daily,'acquisition_job',side_effect=AssertionError('must not fetch again')):
            result = daily.begin(argparse.Namespace(workspace=str(self.root),date=None,news_as_of=None))
        self.assertEqual(result['status'],'SOURCES_ALREADY_PREPARED')
        self.assertEqual(result['data_date'],'2026-11-06')
        self.assertTrue(result['quotes_not_refreshed'])
        self.assertEqual(result['reuse_reason'],'PROJECT_ALREADY_PREPARED')
        self.assertIn('prepared_at',result)
        self.assertFalse(result['collection_restarted'])
        self.assertEqual(before,{name:(self.root / name).read_bytes() for name in before})


if __name__ == '__main__':
    unittest.main()
