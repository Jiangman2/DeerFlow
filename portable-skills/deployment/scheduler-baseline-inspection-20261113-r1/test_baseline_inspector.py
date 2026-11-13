import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, str(HERE / filename))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


host = load('baseline_host', 'inspect-scheduler-baseline-20261113-r1.py')
child = load('baseline_child', 'inspect_baseline_child.py')


def deployment():
    return {'metadata': {'name': 'gateway', 'namespace': 'deer-flow', 'uid': '00000000-0000-4000-8000-000000000000',
        'generation': 417, 'resourceVersion': '999'}, 'spec': {'replicas': 2, 'template': {'spec': {'containers': []}}},
        'status': {'observedGeneration': 416, 'replicas': 3, 'updatedReplicas': 1, 'readyReplicas': 2, 'availableReplicas': 2}}


class BaselineTests(unittest.TestCase):
    def test_new_uid_and_generation_are_observed_not_refused(self):
        value = host.deployment_summary(deployment())
        self.assertFalse(value['matches_old_uid']); self.assertEqual(value['generation'], 417)
        self.assertFalse(host.converged(deployment()))

    def test_rolling_gateway_returns_metadata_without_pod_exec(self):
        inspector = host.Inspector('fixed-child')
        with mock.patch.object(inspector, 'call', return_value=deployment()) as call:
            inspector.run()
        self.assertEqual(call.call_count, 2)
        self.assertTrue(inspector.out['runtime_pending']); self.assertTrue(inspector.out['stable_readback'])

    def test_sparse_list_shape_and_bound(self):
        value = 'gateway-x 00000000-0000-4000-8000-000000000001 Running <none>\n'
        self.assertEqual(len(host.parse_list(value)), 1)
        for raw in (value + value, value.replace('Running', 'unknown'), value.replace('<none>', 'bad'), 'x' * 262145):
            with self.assertRaises(host.Refused): host.parse_list(raw)

    def test_secret_and_mixed_projection_names_values_are_redacted_and_skipped(self):
        spec = {'containers': [{'name': 'gateway', 'volumeMounts': [
            {'name': 'ca', 'mountPath': '/etc/deerflow-ca', 'subPath': 'never-print-key'},
            {'name': 'code', 'mountPath': '/app/backend'}]}], 'volumes': [
            {'name': 'ca', 'secret': {'secretName': 'never-print-secret'}},
            {'name': 'code', 'projected': {'sources': [{'secret': {'name': 'never-print-other'}}, {'configMap': {'name': 'irrelevant'}}]}}]}
        rows, skip = host.mount_summary(spec)
        self.assertNotIn('never-print', json.dumps(rows)); self.assertIn(host.CONFIG, skip)
        self.assertEqual({row['source_type'] for row in rows}, {'Secret'})

    def test_readonly_allowlist_refuses_secret_api_or_mutations(self):
        inspector = host.Inspector('fixed-child')
        with mock.patch.object(host.subprocess, 'Popen') as execute:
            for args in (['get', 'secrets', '-o', 'json'], ['patch', 'deployment', 'gateway'], ['create', '-f', '-']):
                with self.assertRaises(host.Refused): inspector.call(args)
            execute.assert_not_called()

    def test_child_config_hash_survives_yaml_error_and_readback_is_real(self):
        raw = b'max_recursion_limit: 10000\nscheduler:\n  recursion_limit: 300\n  recursion_limit: 900\n'
        def read(path, maximum):
            if path == child.CONFIG: return raw
            raise FileNotFoundError()
        output = io.StringIO()
        source = type('Input', (), {'buffer': io.BytesIO(b'{"skip_secret_paths":[]}')})()
        with mock.patch.object(child.sys, 'stdin', source), mock.patch.object(child.sys, 'addaudithook'), \
             mock.patch.object(child, 'read', side_effect=read), mock.patch.object(child.importlib.metadata, 'version', return_value='1.2.3'), \
             contextlib.redirect_stdout(output):
            self.assertEqual(child.main(), 0)
        value = json.loads(output.getvalue())
        self.assertEqual(value['configuration']['bytes'], len(raw))
        self.assertEqual(value['configuration']['sha256'], child.hashlib.sha256(raw).hexdigest())
        self.assertEqual(value['configuration']['budget_parse_error']['error_code'], 'YAML_DUPLICATE_KEY')
        self.assertTrue(value['runtime_config_stable_readback'])

    def test_child_rejects_ancestor_alias_and_total_bound_before_open(self):
        with mock.patch.object(Path, 'is_symlink', autospec=True, side_effect=lambda path: path == Path('/app/backend')), \
             mock.patch.object(child.os, 'open') as opened:
            with self.assertRaisesRegex(child.Refused, 'FIXED_FILE_LINK'):
                child.read(child.CONFIG, 65536)
            opened.assert_not_called()
        with mock.patch.object(child, 'READ_BYTES', child.MAX_READ_BYTES), mock.patch.object(child.os, 'open') as opened:
            with self.assertRaisesRegex(child.Refused, 'TOTAL_READ_BOUND_REACHED'):
                child.read(child.CONFIG, 65536)
            opened.assert_not_called()

    def test_wrong_host_guard_does_not_load_asset_or_execute(self):
        output = io.StringIO()
        with mock.patch.object(host.os, 'name', 'nt'), mock.patch.object(sys, 'argv', ['inspect', '--inspect-readonly']), \
             mock.patch.object(host.Inspector, 'run') as run, contextlib.redirect_stdout(output):
            self.assertEqual(host.main(), 1)
        run.assert_not_called()
        value = json.loads(output.getvalue())
        self.assertEqual(value['error_code'], 'DATAX01_ROOT_REQUIRED'); self.assertEqual(value['queries'], 0)


if __name__ == '__main__': unittest.main()
