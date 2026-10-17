import ast
import base64
import contextlib
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = Path(__file__).absolute().parent
if os.name != 'posix':
    sys.modules.setdefault('pwd', types.SimpleNamespace(getpwnam=lambda _: None))


def module(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


m = module('wrapper_bootstrap')
generator = module('generate_wrapper')


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def payload(self):
        return {name: base64.b64encode(gzip.compress((HERE / name).read_bytes(), mtime=0)).decode()
                for name in m.EXPECTED_FILES}

    def report(self):
        return {'schema': 'portal-ppt-host-existing-oci-diagnostic.v1', 'status': 'DIAGNOSTIC_COMPLETE_REVIEW_REQUIRED',
                'frozen_inputs': m.COLLECTOR_INPUTS, 'image_env_or_runtime_config': 'NOT_PROJECTED', 'errors': [],
                **{name: False for name in ['ready_for_build', 'ready_for_rollout', 'image_execution',
                   'rootfs_extracted', 'production_mutation', 'network_or_subprocess', 'automatic_retry']}}

    def test_exact_four_blobs_and_tampering_refused(self):
        blobs = self.payload()
        self.assertEqual(set(m.decode_payload(blobs)), set(m.EXPECTED_FILES))
        for name in blobs:
            broken = dict(blobs)
            broken[name] = base64.b64encode(gzip.compress(b'changed', mtime=0)).decode()
            with self.assertRaises(m.Refused):
                m.decode_payload(broken)
        with self.assertRaises(m.Refused):
            m.decode_payload(dict(blobs, unexpected='secret'))

    def test_single_shell_embeds_only_locked_files_and_python37_source(self):
        raw = generator.render()
        self.assertTrue(raw.startswith(b'#!/usr/bin/env bash\nset -eu\n'))
        source = raw.split(b"<<'PORTAL_HOST_BASE_BOOTSTRAP_PY'\n", 1)[1].rsplit(b'\nPORTAL_HOST_BASE_BOOTSTRAP_PY', 1)[0]
        tree = ast.parse(source.decode(), feature_version=(3, 7))
        payload = [ast.literal_eval(x.value) for x in tree.body if isinstance(x, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == 'PAYLOAD' for t in x.targets)][0]
        self.assertEqual(set(m.decode_payload(payload)), set(m.EXPECTED_FILES))
        self.assertNotIn(b'curl ', raw)
        self.assertNotIn(b'tar -', raw)

    def test_process_group_timeout_no_retry_and_exact_argv(self):
        process = mock.Mock(pid=123456)
        process.wait.side_effect = [subprocess.TimeoutExpired('fixture', 1860), -15]
        with mock.patch.object(m.subprocess, 'Popen', return_value=process) as popen, \
             mock.patch.object(m.os, 'killpg', create=True) as kill:
            result = m.run_once(self.root, self.root / 'output')
        self.assertEqual(result, {'collector_exit_code': -15, 'outer_timeout': True, 'attempts': 1})
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(process.wait.call_args_list[0].kwargs, {'timeout': 1850})
        self.assertEqual(kill.call_args.args, (123456, m.signal.SIGTERM))
        args, kwargs = popen.call_args
        self.assertEqual(args[0][1:4], ['-I', '-B', '-u'])
        self.assertTrue(kwargs['start_new_session'])
        self.assertEqual(set(kwargs['env']), {'PATH', 'LANG', 'PYTHONIOENCODING'})
        self.assertNotIn('shell', kwargs)

    def test_completed_diagnostic_exit2_exports_once_without_retry(self):
        raw = json.dumps(self.report()).encode()
        with mock.patch.object(m.os, 'get_euid', create=True), \
             mock.patch.object(m.os, 'geteuid', return_value=0, create=True), \
             mock.patch.object(m.os, 'uname', return_value=types.SimpleNamespace(nodename='datax-01'), create=True), \
             mock.patch.object(m, 'decode_payload', return_value={}), \
             mock.patch.object(m, 'stage_tool', return_value=self.root), \
             mock.patch.object(m, 'run_once', return_value={'collector_exit_code': 2, 'outer_timeout': False, 'attempts': 1}) as run, \
             mock.patch.object(m, 'read_report', return_value=(raw, self.report())), \
             mock.patch.object(m, 'export_report', return_value=self.root / 'download.json') as export, \
             contextlib.redirect_stdout(io.StringIO()):
            # The native platform gate is intentionally exercised only on Linux.
            if os.name != 'posix':
                self.assertEqual(m.main(), 2)
                self.assertEqual(run.call_count, 0)
                return
            self.assertEqual(m.main(), 0)
        self.assertEqual(run.call_count, 1)
        export.assert_called_once_with(raw)

    @unittest.skipUnless(os.name == 'posix', 'POSIX FD-relative private handoff tested on Linux')
    def test_stage_four_files_root_private_and_link_parent_rejected(self):
        with mock.patch.object(m, 'VAR_LIB', self.root):
            control = m.stage_tool(m.decode_payload(self.payload()))
        self.assertEqual(stat.S_IMODE(control.stat().st_mode), 0o700)
        self.assertEqual({p.name for p in control.iterdir()}, set(m.EXPECTED_FILES))
        self.assertTrue(all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in control.iterdir()))
        linked = self.root / 'link'
        linked.symlink_to(control, target_is_directory=True)
        with self.assertRaises(OSError):
            m.directory_fd(linked)

    @unittest.skipUnless(os.name == 'posix', 'POSIX no-follow report read tested on Linux')
    def test_report_read_rejects_symlink_private_flags_and_drifted_binding(self):
        base = self.root / 'reports'
        base.mkdir(mode=0o700)
        output = base / 'host-base-0123456789abcdef'
        output.mkdir(mode=0o700)
        report = output / 'REPORT.json'
        with mock.patch.object(m, 'OUTPUT_BASE', base):
            report.write_text(json.dumps(self.report()))
            report.chmod(0o600)
            raw, result = m.read_report(output)
            self.assertEqual(result['status'], 'DIAGNOSTIC_COMPLETE_REVIEW_REQUIRED')
            report.chmod(0o644)
            with self.assertRaises(m.Refused):
                m.read_report(output)
            report.chmod(0o600)
            bad = self.report(); bad['ready_for_build'] = True
            report.write_text(json.dumps(bad))
            with self.assertRaises(m.Refused):
                m.read_report(output)
            report.unlink()
            report.symlink_to(self.root / 'unknown')
            with self.assertRaises(OSError):
                m.read_report(output)

    @unittest.skipUnless(os.name == 'posix', 'POSIX chown and home-directory anchoring tested on Linux')
    def test_handoff_only_safe_report_directory_chown_last(self):
        parent = self.root / 'homes'
        parent.mkdir(mode=0o700)
        home = parent / 'cnooc'
        home.mkdir(mode=0o700)
        os.chown(str(home), 65534, 65534)
        account = types.SimpleNamespace(pw_name='cnooc', pw_uid=65534, pw_gid=65534, pw_dir=str(home))
        raw = json.dumps(self.report()).encode()
        real_chown = os.fchown
        order = []
        def track(fd, uid, gid):
            order.append('directory' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file')
            real_chown(fd, uid, gid)
        with mock.patch.object(m, 'HOME_BASE', parent), mock.patch.object(m, 'HOME', str(home)), \
             mock.patch.object(m.pwd, 'getpwnam', return_value=account), mock.patch.object(m.os, 'fchown', side_effect=track):
            exported = m.export_report(raw)
        self.assertEqual(exported.read_bytes(), raw)
        self.assertEqual(order, ['file', 'directory'])
        self.assertEqual(exported.stat().st_uid, 65534)
        self.assertEqual(exported.parent.stat().st_uid, 65534)
        self.assertEqual(stat.S_IMODE(exported.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(exported.parent.stat().st_mode), 0o700)
        self.assertEqual([x.name for x in exported.parent.iterdir()], ['REPORT.json'])
        self.assertFalse((self.root / 'REPORT.json').exists())
        account.pw_dir = '/wrong'
        with mock.patch.object(m.pwd, 'getpwnam', return_value=account), self.assertRaises(m.Refused):
            m.export_report(raw)


if __name__ == '__main__':
    unittest.main(verbosity=2)
