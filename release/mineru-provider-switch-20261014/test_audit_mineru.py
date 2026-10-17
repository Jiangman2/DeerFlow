import ast
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import audit_mineru as a


class AuditTests(unittest.TestCase):
    def test_python37_controller_syntax(self):
        ast.parse(Path(a.__file__).read_text(), feature_version=(3, 7))
        ast.parse(a.PROBE, feature_version=(3, 7))

    def test_credentials_and_unselected_config_never_print(self):
        c = {'env': [{'name': 'MINERU_API_TOKEN', 'value': 'private-test-token'},
                     {'name': 'UNRELATED_SECRET', 'value': 'private-unrelated'},
                     {'name': 'MINERU_TIMEOUT_SECONDS', 'value': '1800'}],
             'envFrom': [{'secretRef': {'name': 'deer-flow-mineru'}}]}
        text = json.dumps(a.env_summary(c))
        self.assertNotIn('private-test-token', text)
        self.assertNotIn('private-unrelated', text)
        self.assertIn('1800', text)
        self.assertIn('deer-flow-mineru', text)

    def test_url_query_fragment_and_userinfo_never_print(self):
        self.assertEqual(a.display_value('MINERU_API_URL', 'https://example.test/tasks?key=secret#private'),
                         'https://example.test/tasks')
        self.assertEqual(a.display_value('MINERU_API_URL', 'https://user:secret@example.test/tasks'),
                         'REDACTED_NONCANONICAL_URL')

    def test_nonnumeric_concurrency_redacted(self):
        self.assertEqual(a.display_value('WEKNORA_ASYNQ_CORE_CONCURRENCY', 'private-value'),
                         'UNRECOGNIZED_REDACTED')

    def test_selector_is_scoped_and_empty_refused(self):
        self.assertEqual(a.selector_text({'matchLabels': {'app': 'mineru'}}), 'app=mineru')
        with self.assertRaises(a.CheckFailure):
            a.selector_text({})
        self.assertEqual(a.selector_text({'matchExpressions': [{'key': 'role', 'operator': 'In', 'values': ['worker']}]}),
                         'role in (worker)')

    def test_ownership_requires_controller(self):
        obj = {'metadata': {'ownerReferences': [{'uid': 'rs', 'controller': True}]}}
        self.assertTrue(a.controlled_by(obj, 'rs'))
        self.assertFalse(a.controlled_by(obj, 'other'))

    def test_command_failure_does_not_echo_stderr(self):
        with patch.object(a.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, b'', b'private token')):
            with self.assertRaisesRegex(a.CheckFailure, '^kubernetes_read_failed$'):
                a.capture(['get', 'deployment', 'x'])

    def test_no_secret_api_or_mutation_commands_in_controller(self):
        tree = ast.parse(Path(a.__file__).read_text())
        commands = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        self.assertNotIn('secret', commands)
        for verb in ('apply', 'patch', 'delete', 'scale', 'rollout', 'create'):
            self.assertNotIn(verb, commands)

    def probe_fixture(self, source):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'adapter.py'
            path.write_text(source)
            original_path = Path
            def substituted_path(value):
                return path if value == '/app/deerflow_mineru_adapter.py' else original_path(value)
            class Health:
                status = 200
                def __enter__(self): return self
                def __exit__(self, *args): return False
                def read(self, maximum):
                    return b'{"parser":{"active":1,"waiting":0,"rejected":2,"raw":"PRIVATE"}}'
            class Opener:
                def open(self, request, timeout):
                    self_outer.assertEqual(request.full_url, 'http://127.0.0.1:50052/health')
                    self_outer.assertEqual(timeout, 4)
                    return Health()
            self_outer = self
            output = io.StringIO()
            env = {'MINERU_API_URL': 'https://example.test/api?token=PRIVATE', 'MINERU_API_TOKEN': 'PRIVATE',
                   'MINERU_REQUEST_MODE': 'async', 'MINERU_TIMEOUT_SECONDS': '1800'}
            with patch.dict(os.environ, env, clear=True), patch('pathlib.Path', side_effect=substituted_path), \
                    patch('urllib.request.build_opener', return_value=Opener()), contextlib.redirect_stdout(output):
                exec(compile(a.PROBE, '<test-probe>', 'exec'), {})
            raw = output.getvalue()
            self.assertNotIn('PRIVATE', raw)
            return json.loads(raw)

    def test_ast_probe_matches_current_client_without_importing_or_executing_it(self):
        r = self.probe_fixture('''
raise RuntimeError("must never execute this file")
class ParseCoordinator:
    pass
class EnterpriseMinerUClient:
    def __init__(self, normalized_url):
        self.tasks_url = f"{normalized_url}/tasks"
    def parse(self, task_id):
        status_url = f"{self.tasks_url}/{task_id}"
        result_url = f"{status_url}/result"
''')
        self.assertTrue(r['source']['same_base_url_pattern_present'])
        self.assertEqual(r['source']['separate_task_route_config_names'], [])
        self.assertTrue(r['source']['parse_coordinator_declared'])
        self.assertEqual(r['local_health']['parser']['active'], 1)
        self.assertFalse(r['files_modified'])
        self.assertEqual(r['provider_requests'], 0)

    def test_separate_route_pattern_is_not_falsely_classified(self):
        r = self.probe_fixture('''
class EnterpriseMinerUClient:
    def parse(self, task_id):
        status_url = os.getenv("MINERU_TASK_STATUS_BASE_URL") + "/" + task_id
''')
        self.assertFalse(r['source']['same_base_url_pattern_present'])
        self.assertEqual(r['source']['separate_task_route_config_names'], ['MINERU_TASK_STATUS_BASE_URL'])

    def test_local_adapter_is_only_parsed_never_imported(self):
        source = Path('/baseline/deerflow_mineru_adapter.py').read_text()
        r = self.probe_fixture(source)
        self.assertTrue(r['source']['same_base_url_pattern_present'])
        self.assertEqual(r['source']['separate_task_route_config_names'], [])
        self.assertFalse(r['source']['provider_dynamic_prefix_literal_present'])
        self.assertFalse(r['source']['executed_or_imported'])


if __name__ == '__main__':
    unittest.main()
