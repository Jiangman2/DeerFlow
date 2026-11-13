"""The operator probe is constrained to metadata and non-mutating exec."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / 'deployment/inspect-energy-browser-permissions-20261112-r1.py'
SPEC = importlib.util.spec_from_file_location('browser_probe', PATH)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class BrowserProbeTests(unittest.TestCase):
    def row(self, **changes):
        row = dict(name='sandbox-abcd1234', uid='12345678-1234-1234-1234-123456789abc', node='ecs-zdz-pro-0015',
                   phase='Running', deleting='<none>', ready='true', image=probe.IMAGE, image_id=probe.IMAGE_ID)
        row.update(changes)
        return ' '.join(row.values())

    def test_selects_only_current_ready_expected_runtime(self):
        raw = '\n'.join((self.row(), self.row(name='sandbox-failed', phase='Failed'), self.row(name='sandbox-deleting', deleting='2026-11-12T00:00:00Z'),
                         self.row(name='sandbox-pending', ready='false'), self.row(name='sandbox-other', image_id='sha256:' + '0' * 64)))
        self.assertEqual(len(probe.rows(raw)), 1)

    def test_malformed_pod_identity_is_rejected(self):
        with self.assertRaises(probe.Refused):
            probe.rows(self.row(name='gateway-abcdef'))

    def test_mutating_kubectl_commands_are_rejected(self):
        for args in (['patch', 'deployment', 'gateway'], ['delete', 'pod', 'sandbox-abcd1234'], ['create', '-f', '-']):
            with patch.object(probe.subprocess, 'run', side_effect=AssertionError('must not execute')):
                with self.assertRaises(probe.Refused):
                    probe.command(args)

    def test_only_exact_fixed_child_can_execute(self):
        args = ['exec', '-i', 'sandbox-abcd1234', '-c', 'sandbox', '--', 'python3', '-I', '-B', '-c', probe.CHILD, probe.PERMISSIONS]
        with patch.object(probe.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=b'{}', stderr=b'')) as run:
            self.assertEqual(probe.command(args), '{}')
        self.assertEqual(run.call_args.kwargs['timeout'], 35)
        args[-2] = 'print("changed")'
        with self.assertRaises(probe.Refused):
            probe.command(args)

    def test_embedded_programs_compile(self):
        compile(probe.CHILD, '<child>', 'exec')
        compile(probe.PERMISSIONS, '<permissions>', 'exec')

    def test_permission_child_has_no_browser_launch_or_credentials(self):
        self.assertNotIn('playwright', probe.CHILD)
        self.assertNotIn('JKX_', probe.CHILD + probe.PERMISSIONS)
        self.assertNotIn('os.environ', probe.CHILD)
        self.assertNotIn("/'environ'", probe.CHILD)
        self.assertNotIn('write_', probe.PERMISSIONS)


if __name__ == '__main__':
    unittest.main()
