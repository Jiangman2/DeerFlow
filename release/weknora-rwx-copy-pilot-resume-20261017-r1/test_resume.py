import contextlib
import hashlib
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
from unittest.mock import patch, Mock

import resume as m


def old_unit():
    return dict(LoadState='loaded', ActiveState='failed', SubState='failed', Type='oneshot',
                Restart='no', TimeoutStartUSec='0', MainPID='0', ControlPID='0', Result='timeout',
                ExecMainCode='2', ExecMainStatus='15', Transient='yes', KillMode='control-group',
                ExecStart='{ path=/usr/bin/unshare ; argv[]=/usr/bin/unshare --mount --propagation private '
                          '/usr/bin/python3.7 -I -B -u '+str(m.OLD/'pilot.py')+' --run '+m.OLD_TOKEN+
                          ' ; ignore_errors=no ; start_time=[x] ; pid=123 ; code=killed ; status=15 }')


def new_unit():
    return dict(Type='exec', Restart='no', TimeoutStartUSec='30s', RuntimeMaxUSec='infinity',
                KillMode='control-group', MainPID=str(os.getpid()), Transient='yes', ActiveState='active')


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/qualify')
        self.base = Path(self.temp.name)
        self.old = self.base/'old'; self.old.mkdir(mode=0o700)
        self.state = self.base/'new'
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(patch.object(m, 'OLD', self.old))
        self.stack.enter_context(patch.object(m, 'STATE', self.state))
        self.stack.enter_context(patch.object(m, 'CAN_JOURNAL', False))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def tearDown(self):
        self.stack.close(); self.temp.cleanup()

    def file(self, name, data):
        path = self.old/name
        m.write(path, data)
        return path

    def receipt_fixture(self):
        self.file('pilot.py', b'qualified fixture')
        self.stack.enter_context(patch.object(m, 'ENGINE_SHA', m.sha(b'qualified fixture')))
        self.file('STARTED.json', json.dumps(dict(token=m.OLD_TOKEN, script_sha256=m.ENGINE_SHA)).encode())
        self.stack.enter_context(patch.object(m, 'OLD_STARTED_SHA', m.sha(m.read(self.old/'STARTED.json'))))
        self.file('START-REQUESTED.json', json.dumps(dict(unit=m.OLD_UNIT)).encode())
        self.stack.enter_context(patch.object(m, 'unit_state', return_value=old_unit()))
        self.stack.enter_context(patch.object(m, 'process_absence', return_value={'matching_old_unit_processes': 0}))
        self.stack.enter_context(patch.object(m, 'mount_absence', return_value={'old_state_mounts_observed': 0}))

    def test_old_exact_failure_passes(self):
        m.validate_old_unit(old_unit())

    def test_old_failure_identity_field_changes_rejected(self):
        for key, replacement in dict(Type='exec', TimeoutStartUSec='infinity', MainPID='100',
             ControlPID='100', ActiveState='active', SubState='running', Result='exit-code',
             ExecMainCode='1', ExecMainStatus='1', Transient='no', Restart='always', KillMode='process').items():
            with self.subTest(key=key), self.assertRaises(m.Failure):
                value = old_unit(); value[key] = replacement; m.validate_old_unit(value)

    def test_old_command_wrong_token_rejected(self):
        value = old_unit(); value['ExecStart'] = value['ExecStart'].replace(m.OLD_TOKEN, 'other')
        with self.assertRaisesRegex(m.Failure, 'command_identity'):
            m.validate_old_unit(value)

    def test_old_receipt_and_state_accepted_without_writes(self):
        self.receipt_fixture()
        before = {p.name: p.read_bytes() for p in self.old.iterdir()}
        result = m.old_snapshot(None)
        self.assertTrue(result['operation_entry_markers_absent'])
        self.assertNotIn('ExecStart', result['old_unit'])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.old.iterdir()})

    def test_old_any_entry_marker_blocks(self):
        self.receipt_fixture()
        for name in m.MARKERS:
            with self.subTest(name=name):
                path = self.old/name; path.write_text('{}')
                try:
                    with self.assertRaisesRegex(m.Failure, 'entry_or_unreviewed'):
                        m.old_snapshot(None)
                finally:
                    path.unlink()

    def test_old_unreviewed_file_blocks(self):
        self.receipt_fixture(); (self.old/'unknown').touch()
        with self.assertRaises(m.Failure): m.old_snapshot(None)

    def test_old_receipt_bytes_changed(self):
        self.receipt_fixture(); (self.old/'STARTED.json').write_text('{}')
        with self.assertRaisesRegex(m.Failure, 'receipt_changed'): m.old_snapshot(None)

    def test_old_engine_bytes_changed(self):
        self.receipt_fixture(); (self.old/'pilot.py').write_text('changed')
        with self.assertRaisesRegex(m.Failure, 'old_engine_changed'): m.old_snapshot(None)

    def test_old_request_unit_changed(self):
        self.receipt_fixture(); (self.old/'START-REQUESTED.json').write_text('{"unit":"other"}')
        with self.assertRaisesRegex(m.Failure, 'request_identity'): m.old_snapshot(None)

    def test_new_exec_properties_accept(self):
        m.validate_new_unit(new_unit(), os.getpid())

    def test_new_effective_mismatch_rejected(self):
        for key, value in dict(Type='oneshot', TimeoutStartUSec='0', Restart='always',
             RuntimeMaxUSec='30s', MainPID='0', KillMode='process', ActiveState='failed').items():
            with self.subTest(key=key), self.assertRaises(m.Failure):
                row = new_unit(); row[key] = value; m.validate_new_unit(row, os.getpid())

    def test_launch_is_exec_nonzero_start_no_wait_no_retry(self):
        with patch.object(m, 'executable', side_effect=lambda x: '/usr/bin/'+x):
            args = m.launch_args(self.state/'resume.py', 'test-token')
        self.assertIn('--property=Type=exec', args)
        self.assertIn('--property=TimeoutStartSec=30s', args)
        self.assertIn('--property=RuntimeMaxSec=infinity', args)
        self.assertIn('--property=Restart=no', args)
        self.assertIn('--property=UMask=0077', args)
        for bad in ('--wait', '--no-block', '--collect', '--property=TimeoutStartSec=0', '--property=Type=oneshot'):
            self.assertNotIn(bad, args)
        self.assertEqual(args[-3:], [str(self.state/'resume.py'), '--run', 'test-token'])
        self.assertIn('--propagation', args); self.assertIn('private', args)

    def test_real_process_cgroups_read_only(self):
        value = m.process_absence()
        self.assertGreater(value['scanned_processes'], 0)
        self.assertEqual(value['matching_old_unit_processes'], 0)

    def test_matching_cgroup_process_blocks(self):
        proc = self.base/'proc'; (proc/'10').mkdir(parents=True)
        (proc/'10'/'cgroup').write_text('1:name=systemd:/system.slice/'+m.OLD_UNIT+'/child\n')
        with self.assertRaisesRegex(m.Failure, 'still_present'): m.process_absence(proc)

    def test_empty_process_inventory_fails(self):
        proc = self.base/'proc'; proc.mkdir()
        with self.assertRaisesRegex(m.Failure, 'scan_empty'): m.process_absence(proc)

    def test_mount_path_blocks(self):
        with patch.object(Path, 'read_text', return_value='1 2 0:1 / '+str(self.old)+'/nfs rw - nfs server rw\n'):
            with self.assertRaisesRegex(m.Failure, 'mount_present'): m.mount_absence(None)

    def test_historical_engine_sha_unchanged(self):
        data = Path('/engine/pilot.py').read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), m.ENGINE_SHA)

    def test_engine_hash_checked_before_code_execution(self):
        path = self.file('evil.py', b'raise AssertionError("must not execute")')
        with self.assertRaisesRegex(m.Failure, 'engine_hash_changed'): m.load_engine(path)

    def test_real_pinned_engine_loading_preserves_limits(self):
        path = self.file('engine.py', Path('/engine/pilot.py').read_bytes())
        engine, data = m.load_engine(path)
        self.assertEqual(engine.MAX_BYTES, 1024**3)
        self.assertEqual(engine.MAX_FILES, 5000)
        self.assertEqual(engine.RATE, 10*1024**2)
        self.assertEqual(engine.STATE, Path('/var/lib/weknora-rwx-copy-pilot-20261017-r1'))
        self.assertEqual(data, Path('/engine/pilot.py').read_bytes())

    def test_symlink_private_file_rejected(self):
        (self.old/'link').symlink_to('/etc/passwd')
        with self.assertRaises(OSError): m.read(self.old/'link')

    def test_broken_link_is_present(self):
        (self.old/'link').symlink_to('absent')
        self.assertTrue(m.present(self.old/'link'))

    def test_group_writable_private_file_rejected(self):
        path = self.file('f', b'abc'); path.chmod(0o660)
        with self.assertRaises(m.Failure): m.read(path)

    def test_hardlinked_private_file_rejected(self):
        path = self.file('f', b'abc'); os.link(str(path), str(self.old/'hardlink'))
        with self.assertRaises(m.Failure): m.read(path)

    def test_exclusive_write_never_overwrites(self):
        path = self.file('f', b'abc')
        with self.assertRaises(FileExistsError): m.write(path, b'def')
        self.assertEqual(path.read_bytes(), b'abc')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_launch_failure_has_safe_diagnostics_without_stderr(self):
        code = 'import sys; sys.stderr.write("start operation timed out SENSITIVE_SENTINEL"); sys.exit(1)'
        with self.assertRaises(m.Failure): m.command([sys.executable, '-c', code])
        self.assertEqual(m.LAST['exit_code'], 1)
        self.assertTrue(m.LAST['stderr_classes']['timeout'])
        self.assertNotIn('SENSITIVE_SENTINEL', json.dumps(m.LAST))

    def test_uncertain_start_not_killed_or_replayed(self):
        process = Mock()
        process.communicate.side_effect = subprocess.TimeoutExpired('systemd-run', 45)
        process.pid = 12
        with patch.object(m.subprocess, 'Popen', return_value=process) as create:
            with self.assertRaisesRegex(m.Failure, 'uncertain_no_retry'):
                m.command(['/usr/bin/systemd-run'])
        create.assert_called_once(); process.kill.assert_not_called(); process.terminate.assert_not_called()

    def test_status_never_starts_copies_or_changes_state(self):
        self.state.mkdir(mode=0o700); m.save(self.state/'RESULT.json', {'pilot': 'PASS'})
        before = {p.name: p.read_bytes() for p in self.state.iterdir()}
        with (patch.object(m, 'unit_state', return_value={'LoadState':'not-found'}) as unit,
              patch.object(m, 'resume') as mutate):
            m.status()
        self.assertEqual(unit.call_count, 2); mutate.assert_not_called()
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.state.iterdir()})

    def test_repeated_resume_does_not_write_failure_or_call_start(self):
        self.state.mkdir(mode=0o700)
        with (patch.object(sys, 'argv', ['resume.py', '--resume-once']),
              patch.object(m.socket, 'gethostname', return_value='datax-03'),
              patch.object(m, 'command') as command):
            self.assertEqual(m.main(), 1)
        command.assert_not_called(); self.assertEqual(list(self.state.iterdir()), [])

    def test_status_failure_stays_read_only(self):
        self.state.mkdir(mode=0o700)
        with (patch.object(sys, 'argv', ['resume.py', '--status']),
              patch.object(m.socket, 'gethostname', return_value='datax-03'),
              patch.object(m, 'unit_state', side_effect=m.Failure('test_read_failed'))):
            self.assertEqual(m.main(), 1)
        self.assertEqual(list(self.state.iterdir()), [])

    def prepare_resume(self):
        self.engine = types.SimpleNamespace(verify_audit=lambda: {'source':'fixture'}, resources=lambda: {})
        self.stack.enter_context(patch.object(m, 'load_engine', return_value=(self.engine, b'fixture-engine')))
        self.stack.enter_context(patch.object(m, 'old_snapshot', return_value=dict(old_unit={}, old_request_sha256='pinned')))
        self.stack.enter_context(patch.object(m.time, 'sleep'))
        self.stack.enter_context(patch.object(m, 'unit_state', return_value={'LoadState':'not-found'}))
        self.stack.enter_context(patch.object(m, 'executable', side_effect=lambda x: '/usr/bin/'+x))
        script = self.file('driver.py', b'fixture-driver')
        self.stack.enter_context(patch.object(m, '__file__', str(script)))

    def test_resume_records_before_single_launch(self):
        self.prepare_resume()
        def start(args, timeout):
            self.assertTrue((self.state/'START-REQUESTED.json').exists())
            self.assertFalse((self.state/'START-ACK.json').exists())
            self.assertEqual(timeout, 45)
            self.assertIn('--property=Type=exec', args)
            return b''
        with patch.object(m, 'command', side_effect=start) as command:
            m.resume()
        command.assert_called_once()
        self.assertTrue((self.state/'START-ACK.json').exists())
        self.assertEqual((self.state/'engine.py').read_bytes(), b'fixture-engine')
        self.assertFalse((self.state/'RESULT.json').exists())

    def test_resume_uncertain_launch_has_request_no_ack(self):
        self.prepare_resume()
        with patch.object(m, 'command', side_effect=m.Failure('command_timeout_outcome_uncertain_no_retry')) as command:
            with self.assertRaises(m.Failure): m.resume()
        command.assert_called_once()
        self.assertTrue((self.state/'START-REQUESTED.json').exists())
        self.assertFalse((self.state/'START-ACK.json').exists())

    def test_preflight_failure_never_creates_new_state(self):
        self.prepare_resume()
        with patch.object(m, 'old_snapshot', side_effect=m.Failure('old_unit_process_still_present')):
            with self.assertRaises(m.Failure): m.resume()
        self.assertFalse(self.state.exists())

    def prepare_run(self):
        self.state.mkdir(mode=0o700)
        driver = self.state/'resume.py'; m.write(driver, b'fixture-driver')
        self.stack.enter_context(patch.object(m, '__file__', str(driver)))
        m.save(self.state/'RECOVERY.json', {'previous_copy':'ZERO_VERIFIED_PRE_ENTRY'})
        m.save(self.state/'STARTED.json', dict(token='new-token', script_sha256=m.ENGINE_SHA,
               driver_sha256=m.sha(driver.read_bytes()), recovery_sha256=m.sha(m.read(self.state/'RECOVERY.json'))))
        self.engine = types.SimpleNamespace(isolated_namespace=Mock(), internal_run=Mock(return_value=0),
                                            start=Mock(), Failure=m.Failure)
        self.stack.enter_context(patch.object(m, 'load_engine', return_value=(self.engine, b'fixture')))
        self.stack.enter_context(patch.object(m, 'old_snapshot', return_value={}))
        self.stack.enter_context(patch.object(m, 'unit_state', return_value=new_unit()))

    def test_worker_gate_before_engine_and_fresh_state_binding(self):
        self.prepare_run()
        def internal(token):
            self.assertTrue((self.state/'ENGINE-GATE.json').exists())
            self.assertEqual(self.engine.STATE, self.state)
            self.assertEqual(self.engine.UNIT, m.UNIT)
            self.assertEqual(token, 'new-token')
            return 0
        self.engine.internal_run.side_effect = internal
        self.assertEqual(m.run('new-token'), 0)
        self.engine.start.assert_not_called()
        self.engine.internal_run.assert_called_once()
        self.engine.isolated_namespace.assert_called_once()

    def test_wrong_new_token_cannot_enter_engine(self):
        self.prepare_run()
        with self.assertRaisesRegex(m.Failure, 'new_run_identity'): m.run('wrong-token')
        self.engine.internal_run.assert_not_called()
        self.assertFalse(m.CAN_JOURNAL)

    def test_changed_new_effective_timeout_cannot_mount(self):
        self.prepare_run(); row = new_unit(); row['TimeoutStartUSec'] = '0'
        with patch.object(m, 'unit_state', return_value=row):
            with self.assertRaisesRegex(m.Failure, 'effective_properties'): m.run('new-token')
        self.engine.internal_run.assert_not_called()
        self.assertFalse((self.state/'ENGINE-GATE.json').exists())

    def test_repeated_worker_cannot_reenter(self):
        self.prepare_run(); m.save(self.state/'ENGINE-GATE.json', {'unit':'test'})
        with self.assertRaisesRegex(m.Failure, 'already_entered'): m.run('new-token')
        self.engine.internal_run.assert_not_called()
        self.assertFalse(m.CAN_JOURNAL)

    def test_runtime_old_drift_cannot_enter_engine(self):
        self.prepare_run()
        with patch.object(m, 'old_snapshot', side_effect=m.Failure('old_operation_entry_not_absent')):
            with self.assertRaises(m.Failure): m.run('new-token')
        self.engine.internal_run.assert_not_called()

    def test_new_driver_changed_cannot_enter_engine(self):
        self.prepare_run(); (self.state/'resume.py').write_text('changed')
        with self.assertRaisesRegex(m.Failure, 'new_run_identity'): m.run('new-token')
        self.engine.internal_run.assert_not_called()

    def test_successful_launch_never_claims_copy_pass(self):
        self.prepare_resume()
        out = io.StringIO()
        with patch.object(m, 'command', return_value=b''), contextlib.redirect_stdout(out):
            m.resume()
        final = json.loads(out.getvalue().splitlines()[-1])
        self.assertEqual(final['copy_acceptance'], 'PENDING_RESULT')
        self.assertEqual(final['pilot_recovery'], 'BACKGROUND_LAUNCH_ACKNOWLEDGED')


if __name__ == '__main__':
    unittest.main()
