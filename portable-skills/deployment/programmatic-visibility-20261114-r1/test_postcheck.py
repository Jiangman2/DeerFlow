"""Read-only publication checks, with no live Kubernetes or provider calls."""
import ast
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('programmatic_visibility_build', HERE / 'build.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
RAW, CHILD = builder.build()
visibility = types.ModuleType('programmatic_visibility_tested')
exec(compile(RAW, '<sealed-readonly-postcheck>', 'exec'), visibility.__dict__)

# Reuse the existing JSON-list, UID race and fixed-command boundary cases
# against the actual newly sealed source; no previous postcheck is executed.
prior = (HERE.parent / 'freight-link-20261114-r1/test_visibility.py').read_text(encoding='utf-8')
start = prior.index('HERE = ')
end = prior.index('\n\ndef pod():', start)
prior = prior[:start] + 'visibility = CURRENT_VISIBILITY\n' + prior[end:]
prior = prior.replace("ast.parse((HERE / 'verify_visibility.py').read_text(encoding='utf-8'), feature_version=(3, 7))",
                      'ast.parse(CURRENT_RAW.decode(), feature_version=(3, 7))')
inherited = {'__name__': 'inherited_postcheck_tests', 'CURRENT_VISIBILITY': visibility, 'CURRENT_RAW': RAW}
exec(compile(prior, '<inherited-postcheck-tests>', 'exec'), inherited)
VisibilityTests = inherited['VisibilityTests']


def receipt():
    return {'operation': visibility.OPERATION, 'skill_updated': True,
            'publication': {'after_tree_sha256': visibility.TREE, 'after_files': 108},
            'schema': 'energy-programmatic-skill-install.v1',
            'status': 'ENERGY_DELIVERY_SKILL_INSTALLED_VISIBILITY_PENDING',
            'maintenance_pod_absent': True, 'maintenance_configmap_absent': True,
            'before': {'gateway': 'same'}, 'after_deployments': {'gateway': 'same'}, 'gateway_modified': False,
            'shared_runtime_qualification': {'status': 'NONROOT_ARM64_PROGRAMMATIC_SKILL_QUALIFIED',
                                             'offline_tests': {'run': 27, 'passed': True}}}


class CurrentInstallationTests(unittest.TestCase):
    def run_main(self, data):
        with patch.object(visibility.os, 'name', 'posix'), \
             patch.object(visibility.os, 'uname', return_value=types.SimpleNamespace(nodename='datax-01'), create=True), \
             patch.object(visibility.os, 'geteuid', return_value=0, create=True), \
             patch.object(visibility, 'fixed_read', return_value=json.dumps(data).encode()) as read, \
             patch.object(visibility.Inspector, 'run', return_value={'status': 'PUBLISHED_SKILL_VISIBLE_IN_ORDINARY_SANDBOX'}) as run, \
             patch.object(sys, 'argv', ['postcheck', '--verify-readonly']), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            code = visibility.main()
        return code, json.loads(output.getvalue()), run.call_count, read.call_args_list

    def test_correct_receipt_read_twice_and_no_installer_import(self):
        code, out, calls, reads = self.run_main(receipt())
        self.assertEqual(code, 0)
        self.assertEqual(calls, 1)
        self.assertEqual(len(reads), 2)
        self.assertTrue(all(x.args == (visibility.RECEIPT, visibility.RECEIPT_SHA, 9093) for x in reads))
        self.assertEqual(out['accepted_operation'], visibility.OPERATION)
        self.assertFalse(out['workload_modified'])
        self.assertNotIn('INSTALLER =', RAW.decode())
        self.assertNotIn('exec(compile(', RAW.decode())

    def test_failed_or_unqualified_receipt_stops_before_cluster(self):
        for field, value in [('maintenance_pod_absent', False), ('maintenance_configmap_absent', False),
                             ('gateway_modified', True), ('after_deployments', {'changed': True}),
                             ('status', 'INCOMPLETE')]:
            data = receipt(); data[field] = value
            with self.subTest(field=field):
                code, out, calls, _ = self.run_main(data)
                self.assertEqual(code, 1)
                self.assertEqual(out['error_code'], 'INSTALLED_QUALIFICATION_MISMATCH')
                self.assertEqual(calls, 0)

    def test_wrong_publication_tree_stops_before_cluster(self):
        data = receipt(); data['publication']['after_tree_sha256'] = '0' * 64
        code, out, calls, _ = self.run_main(data)
        self.assertEqual(code, 1)
        self.assertEqual(out['error_code'], 'INSTALLED_RECEIPT_MISMATCH')
        self.assertEqual(calls, 0)

    def test_exact_child_count_and_python37_syntax(self):
        ast.parse(CHILD, feature_version=(3, 7))
        self.assertIn('EXPECTED_SKILL_COUNT = 108', CHILD)
        self.assertEqual(visibility.VISIBILITY_CHILD, CHILD)
        self.assertNotIn('socket', CHILD)
        self.assertNotIn('subprocess', CHILD)


if __name__ == '__main__':
    unittest.main()
