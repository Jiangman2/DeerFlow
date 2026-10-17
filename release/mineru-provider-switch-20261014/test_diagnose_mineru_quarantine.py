import ast
import contextlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import diagnose_mineru_quarantine as d


class DiagnosticTests(unittest.TestCase):
    def test_known_backend_and_reason_only(self):
        raw = (b'2026-10-16T06:01:02Z WARNING MinerU dispatcher quarantined '
               b'backend=10.244.2.3 cooldown_seconds=60.0 reason=ReadTimeout\n')
        result = d.quarantine_events(raw, {'10.244.2.3': 'worker-a'})
        self.assertEqual(result['events'][0]['pod'], 'worker-a')
        self.assertEqual(result['events'][0]['reason'], 'ReadTimeout')
        self.assertFalse(result['proves_current_quarantine_membership'])
        self.assertNotIn('10.244', json.dumps(result))

    def test_unknown_reason_is_never_printed(self):
        raw = (b'2026-10-16T06:01:02Z WARNING MinerU dispatcher quarantined '
               b'backend=10.244.2.3 cooldown_seconds=60.0 reason=secret-key document-name\n'
               b'Bearer secret-key path=/private/document\n')
        result = d.quarantine_events(raw, {'10.244.2.3': 'worker-a'})
        self.assertEqual(result['events'][0]['reason'], 'UNCLASSIFIED')
        self.assertNotIn('secret-key', json.dumps(result))
        self.assertNotIn('document', json.dumps(result))

    def test_http_codes_allowlisted(self):
        for reason in ('HTTP 429 busy admission race', 'HTTP 502', 'HTTP 503'):
            raw = ('x MinerU dispatcher quarantined backend=10.0.0.1 '
                   'cooldown_seconds=60.0 reason=' + reason).encode()
            self.assertEqual(d.quarantine_events(raw, {'10.0.0.1':'p'})['events'][0]['reason'], reason)

    def test_unknown_backend_not_exposed(self):
        raw = b'x MinerU dispatcher quarantined backend=10.0.0.99 cooldown_seconds=60.0 reason=ReadTimeout'
        result = d.quarantine_events(raw, {})
        self.assertEqual(result['unmapped_event_count'], 1)
        self.assertEqual(result['events'], [])
        self.assertNotIn('10.0.0.99', json.dumps(result))

    def test_limits_last_event_per_pod(self):
        raw = ('x MinerU dispatcher quarantined backend=10.0.0.1 cooldown_seconds=60.0 reason=ReadTimeout\n' * 8000).encode()
        result = d.quarantine_events(raw, {'10.0.0.1':'p'})
        self.assertEqual(len(result['events']), 1)
        self.assertEqual(result['matched_event_count'], 8000)

    def test_counter_sanitizer(self):
        self.assertEqual(d.safe_counters({'active':1,'waiting':0,'api_key':'never'}), {'active':1,'waiting':0})
        for bad in (True, -1, '1', None):
            with self.assertRaises(d.AuditFailure):
                d.safe_counters({'active':bad,'waiting':0})

    def test_safe_remote_result(self):
        raw = json.dumps([{'index':0, 'path':'/ready', 'status':503, 'role':'worker',
                           'counters':{'active':1,'waiting':0}, 'secret':'never'}]).encode()
        # Both endpoints must appear for every exact target.
        with self.assertRaises(d.AuditFailure):
            d.remote_rows(raw, ['p'])
        rows = json.loads(raw)
        rows.append(dict(rows[0], path='/health', status=200))
        safe = d.remote_rows(json.dumps(rows).encode(), ['p'])
        self.assertNotIn('never', json.dumps(safe))
        self.assertEqual(safe[0]['pod'], 'p')

    def test_duplicate_remote_target_refused(self):
        rows = [{'index':0,'path':'/health','status':200,'role':'worker',
                 'counters':{'active':0,'waiting':0}}]*2
        with self.assertRaises(d.AuditFailure):
            d.remote_rows(json.dumps(rows).encode(), ['p'])

    def test_remote_error_message_never_survives(self):
        rows = [{'index':0,'path':path,'error_type':'secret data','raw_error':'never'}
                for path in ('/health','/ready')]
        safe = d.remote_rows(json.dumps(rows).encode(), ['p'])
        self.assertEqual(safe[0]['error_type'], 'UNCLASSIFIED')
        self.assertNotIn('secret', json.dumps(safe))

    def test_remote_program_is_bounded_get_only(self):
        ast.parse(d.REMOTE, feature_version=(3,7))
        self.assertIn("c.request('GET', path", d.REMOTE)
        self.assertIn('max_workers=4', d.REMOTE)
        self.assertNotIn('requests.', d.REMOTE)
        self.assertNotIn('Authorization', d.REMOTE)

    def test_wrong_host_does_not_load_or_execute(self):
        with patch.object(d.socket, 'gethostname', return_value='not-datax-01'), \
             patch.object(d, 'load_helpers') as load, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(d.main(), 1)
        load.assert_not_called()
        self.assertEqual(json.loads(out.getvalue())['failed_check'], 'run_on_datax01_as_root')

    def test_full_script_python37(self):
        ast.parse(Path(d.__file__).read_text(encoding='utf-8'), feature_version=(3,7))

    def test_installed_helper_pin_matches_delivered_artifact(self):
        artifact = Path('C:/Users/15285/.codex/visualizations/2026/09/24/'
                        '01a0d2d7-0015-7a73-93c3-791b713a47f6/'
                        'weknora-tune88-20261016-r1/weknora-tune88-20261016-r1.py')
        self.assertEqual(d.hashlib.sha256(artifact.read_bytes()).hexdigest(), d.TOOL_SHA)
        tree = ast.parse(artifact.read_text(encoding='utf-8'))
        self.assertIsInstance(tree.body[-1], ast.If)
        self.assertEqual(ast.unparse(tree.body[-1].test), "__name__ == '__main__'")

    def fixture(self):
        workers = [{'metadata':{'name':'worker-%d'%i}, 'status':{'podIP':'10.0.0.%d'%(i+1)},
                    'spec':{'nodeName':'node-%d'%i}} for i in range(16)]
        pods = {'d':[{'metadata':{'name':'dispatcher', 'uid':d.DISPATCH_UID}}],
                'weknora-app':[{'metadata':{'uid':d.APP_UID}}], 'w':workers}
        obs = SimpleNamespace(DISPATCHER='d', WORKERS=['w'],
              baseline=lambda: ({},pods,'signature',{}), utc=lambda:'2026-10-16T06:00:00Z',
              health=lambda p:{'pod':p['metadata']['name'], 'counters':{'active':0,'waiting':0}})
        return obs

    def test_full_run_collects_no_auto_continue(self):
        obs = self.fixture()
        logs = {'quarantine':{'events':[]}}
        with patch.object(d, 'remote_health', return_value=[]), patch.object(d, 'read_logs', return_value=logs), \
             patch.object(d.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
            report = {}
            d.run(obs, report)
        self.assertEqual(len(report['samples']), 3)
        self.assertFalse(report['consumers_resumed'])
        self.assertFalse(report['ready_to_continue_88'])
        self.assertEqual(report['diagnostic'], 'COMPLETE')

    def test_changed_baseline_fails_closed(self):
        obs = self.fixture()
        before = obs.baseline()
        with patch.object(obs, 'baseline', side_effect=[before, ({}, {}, 'changed', {})]), \
             patch.object(d, 'remote_health', return_value=[]), \
             patch.object(d, 'read_logs', return_value={'quarantine':{'events':[]}}), \
             patch.object(d.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(d.AuditFailure, 'baseline_changed'):
                d.run(obs, {})


if __name__ == '__main__':
    unittest.main()
