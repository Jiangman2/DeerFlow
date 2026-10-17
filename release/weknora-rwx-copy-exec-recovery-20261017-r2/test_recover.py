import contextlib
import errno
import fcntl
import termios
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import recover as m


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def unit(token):
    return dict(LoadState='loaded', ActiveState='failed', SubState='failed', Type='exec',
                TimeoutStartUSec='30s', Restart='no', MainPID='0', ControlPID='0',
                Result='exit-code', ExecMainCode='1', ExecMainStatus='1', Transient='yes',
                KillMode='control-group', RuntimeMaxUSec='infinity',
                ExecStart='argv[]=/usr/bin/python3.7 -I -B -u '+str(m.PREVIOUS/'resume.py')+' --run '+token+' ;')


# Only offline qualification modifies the child module after loading its exact
# delivered bytes. Model source-RO/NFS headroom and old-enough fixture metadata;
# actual fork/drop/exec, fdinfo, descriptor walking, writes/fsync/readback remain real.
COPY_FIXTURE_INJECTION = '''
 original_loader=m.module_bytes
 def fixture_loader(raw,name,filename):
  engine=original_loader(raw,name,filename)
  if name=='pinned_exec_engine':
   from types import SimpleNamespace
   engine.MIN_AGE=-1
   engine.os.fstatvfs=lambda fd: SimpleNamespace(f_flag=os.ST_RDONLY,f_bavail=200*1024**3,f_frsize=1,f_favail=200000)
  return engine
 m.module_bytes=fixture_loader
'''


def legacy_python37_inheritable(fd, value):
    # Exact Linux syscall/error decision of CPython3.7's fast path.
    request = getattr(termios, 'FIONCLEX', 0x5450) if value else getattr(termios, 'FIOCLEX', 0x5451)
    try:
        fcntl.ioctl(fd, request, 0)
        return
    except OSError as exc:
        if exc.errno not in (errno.ENOTTY, errno.EACCES):
            raise
    flags = fcntl.fcntl(fd, fcntl.F_GETFD)
    fcntl.fcntl(fd, fcntl.F_SETFD, flags & ~fcntl.FD_CLOEXEC if value else flags | fcntl.FD_CLOEXEC)


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/qualify')
        self.base = Path(self.temp.name)
        self.old = self.base/'old'; self.old.mkdir(mode=0o700)
        self.state = self.base/'new'
        self.h = load('/previous/resume.py', 'test_helpers')
        self.engine_path = self.base/'engine.py'
        self.h.write(self.engine_path, Path('/engine/pilot.py').read_bytes())
        self.engine, _ = self.h.load_engine(self.engine_path)
        self.driver = self.base/'driver.py'
        self.h.write(self.driver, Path(m.__file__).read_bytes())
        self.stack = contextlib.ExitStack()
        for name, value in (('H', self.h), ('STATE', self.state), ('PREVIOUS', self.old), ('FAILED_EXEC', self.base/'failed-exec'), ('OWNED', False)):
            self.stack.enter_context(patch.object(m, name, value))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def tearDown(self):
        self.stack.close(); self.temp.cleanup()

    def test_pinned_sources_unchanged(self):
        self.assertEqual(m.sha(Path('/previous/resume.py').read_bytes()), m.HELPER_SHA)
        self.assertEqual(m.sha(Path('/engine/pilot.py').read_bytes()), m.ENGINE_SHA)

    def test_real_uid1000_exec_selftest(self):
        result = m.launch_worker(self.engine, -1, -1, '', 'selftest', self.driver)
        self.assertEqual(result['pilot'], 'PASS', result)
        self.assertEqual(result['dumpable'], 1)
        self.assertEqual(result['fdinfo_read'], 'PASS')
        self.assertTrue(result['capabilities_zero'])
        self.assertTrue(result['no_new_privs'])
        self.assertFalse(result['business_files_read'])
        self.assertFalse(self.state.exists())

    def fixture(self):
        self.src, self.dst = self.base/'source', self.base/'target'
        for path in (self.src, self.dst):
            path.mkdir(mode=0o770); path.chmod(0o770); os.chown(path, 1000, 1000)
        sub = self.src/'nested'; sub.mkdir(mode=0o755); os.chown(sub, 1000, 1000)
        self.payload = b'synthetic nonbusiness payload\x00'*4000
        path = sub/'sample.bin'; path.write_bytes(self.payload); path.chmod(0o644); os.chown(path, 1000, 1000)
        self.source_identity = path.stat()
        self.state.mkdir(mode=0o700)
        self.sfd = os.open(str(self.src), os.O_RDONLY | os.O_DIRECTORY)
        self.dfd = os.open(str(self.dst), os.O_PATH | os.O_DIRECTORY)
        self.stack.callback(os.close, self.sfd); self.stack.callback(os.close, self.dfd)

    def copy(self, injection=COPY_FIXTURE_INJECTION):
        bootstrap = m.BOOTSTRAP.replace(' rc=m.exec_child', injection+' rc=m.exec_child')
        with patch.object(m, 'BOOTSTRAP', bootstrap):
            return m.launch_worker(self.engine, self.sfd, self.dfd, 'a'*32, 'copy', self.driver)

    def test_real_exec_synthetic_copy_and_checksum(self):
        self.fixture(); result = self.copy()
        self.assertEqual(result['pilot'], 'PASS', result)
        self.assertEqual(result['verified_files'], 1)
        self.assertEqual(result['verified_bytes'], len(self.payload))
        self.assertEqual(result['payload_io_bytes'], 2*len(self.payload))
        dest = self.dst/result['pilot_directory']/'nested'/'sample.bin'
        self.assertEqual(dest.read_bytes(), self.payload)
        self.assertEqual(dest.stat().st_uid, 1000)
        self.assertEqual(dest.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.src/'nested'/'sample.bin').stat().st_mtime_ns, self.source_identity.st_mtime_ns)
        self.assertEqual(result['exec_identity']['dumpable'], 1)

    def test_writable_source_rejected_before_copy(self):
        self.fixture(); result = self.copy('')
        self.assertEqual(result['failed_check'], 'business_source_not_readonly')
        self.assertEqual(list(self.dst.iterdir()), [])

    def test_existing_target_rejected_no_overwrite(self):
        self.fixture(); (self.dst/'existing').write_text('preserve')
        result = self.copy()
        self.assertEqual(result['failed_check'], 'target_export_not_empty_review_required')
        self.assertEqual([p.name for p in self.dst.iterdir()], ['existing'])
        self.assertEqual((self.dst/'existing').read_text(), 'preserve')

    def test_fdinfo_denied_blocks_before_business(self):
        self.fixture()
        injection = "\n def denied(): raise PermissionError(13,'SENSITIVE_PATH')\n m.identity_probe=denied\n"
        result = self.copy(injection)
        self.assertEqual(result['pilot'], 'UNCONFIRMED')
        self.assertEqual(result['failed_step'], 'EXEC_IDENTITY_SELFTEST')
        self.assertEqual(result['errno'], 13)
        self.assertNotIn('SENSITIVE_PATH', json.dumps(result))
        self.assertEqual(list(self.dst.iterdir()), [])

    def test_source_symlink_not_followed(self):
        self.fixture(); (self.src/'link').symlink_to('/etc/passwd')
        result = self.copy()
        self.assertEqual(result['pilot'], 'PASS', result)
        self.assertEqual(result['verified_files'], 1)
        self.assertFalse((self.dst/result['pilot_directory']/'link').exists())

    def test_unsafe_engine_descriptor_rejected(self):
        self.engine_path.chmod(0o666)
        with self.assertRaisesRegex(m.Failure, 'unsafe_code_descriptor'):
            m.launch_worker(self.engine, -1, -1, '', 'selftest', self.driver)

    def test_corrupt_engine_rejected_before_fork(self):
        self.engine_path.write_text('raise RuntimeError("not executed")')
        with patch.object(os, 'fork') as fork:
            with self.assertRaisesRegex(m.Failure, 'code_hash_changed'):
                m.launch_worker(self.engine, -1, -1, '', 'selftest', self.driver)
        fork.assert_not_called()

    def test_symlink_code_rejected(self):
        link = self.base/'link'; link.symlink_to(self.engine_path)
        with self.assertRaises(OSError): m.code_fd(link, m.ENGINE_SHA)

    def test_hardlinked_code_rejected(self):
        os.link(str(self.engine_path), str(self.base/'link'))
        with self.assertRaises(m.Failure): m.code_fd(self.engine_path, m.ENGINE_SHA)

    def test_private_directory_cannot_be_writable(self):
        self.old.chmod(0o777)
        with self.assertRaises(m.Failure): m.safe_dir(self.old)

    def test_no_permission_bypass_or_dumpable_setter(self):
        text = Path(m.__file__).read_text()
        for forbidden in ('os.chmod(', 'os.chown(', 'prctl(4,', 'suid_dumpable', 'reset-failed', 'os.kill('):
            self.assertNotIn(forbidden, text)

    def test_previous_unit_exact(self):
        m.validate_previous_unit(unit('a'*32), 'a'*32)

    def test_previous_unit_changed_fields_block(self):
        for key, value in dict(MainPID='1', ControlPID='2', Type='oneshot', TimeoutStartUSec='0',
                               Result='success', ExecMainStatus='0', ActiveState='active', Restart='always').items():
            with self.subTest(key=key), self.assertRaises(m.Failure):
                row = unit('a'*32); row[key] = value; m.validate_previous_unit(row, 'a'*32)

    def test_previous_wrong_token_block(self):
        with self.assertRaises(m.Failure): m.validate_previous_unit(unit('b'*32), 'a'*32)

    def receipt_fixture(self):
        self.h.write(self.old/'resume.py', Path('/previous/resume.py').read_bytes())
        self.h.write(self.old/'engine.py', Path('/engine/pilot.py').read_bytes())
        self.h.save(self.old/'RECOVERY.json', {'fixture':True})
        self.h.save(self.old/'STARTED.json', dict(token='a'*32, driver_sha256=m.HELPER_SHA,
            script_sha256=m.ENGINE_SHA, recovery_sha256=m.sha(self.h.read(self.old/'RECOVERY.json'))))
        for name in ('START-REQUESTED.json', 'START-ACK.json'):
            self.h.save(self.old/name, {'unit': self.old.name+'.service'})
        for name in ('ENGINE-GATE.json', 'RUN-ENTERED.json', 'MOUNT-REQUESTED.json'):
            self.h.save(self.old/name, {})
        self.h.save(self.old/'WORKER-REQUESTED.json', {'pilot_directory':'.weknora-copy-pilot-'+'a'*32})
        self.h.save(self.old/'RESULT.json', dict(pilot='UNCONFIRMED', errno=13,
            error_type='PermissionError', source_files_modified=False, source_contents_read='NOT_CONFIRMED'))
        self.h.save(self.old/'PROGRESS.json', dict(last_progress=dict(phase='SAMPLE_METADATA', uid=1000, gid=1000)))
        for name in ('source-ro', 'nfs'): (self.old/name).mkdir(mode=0o700)
        self.stack.enter_context(patch.object(m, 'RESULT_SHA', m.sha(self.h.read(self.old/'RESULT.json'))))
        self.stack.enter_context(patch.object(m, 'PROGRESS_SHA', m.sha(self.h.read(self.old/'PROGRESS.json'))))
        self.stack.enter_context(patch.object(self.h, 'old_snapshot', return_value={'old_unit':{}, 'old_request_sha256':'x'}))
        self.stack.enter_context(patch.object(self.h, 'unit_state', return_value=unit('a'*32)))
        self.stack.enter_context(patch.object(m, 'previous_absence', return_value={}))

    def test_receipt_bound_snapshot_readonly(self):
        self.receipt_fixture()
        before = {p.name:p.read_bytes() for p in self.old.iterdir() if p.is_file()}
        result = m.permission_snapshot(self.engine)
        self.assertNotIn('ExecStart', result['unit'])
        self.assertEqual(before, {p.name:p.read_bytes() for p in self.old.iterdir() if p.is_file()})

    def test_changed_result_blocks(self):
        self.receipt_fixture(); (self.old/'RESULT.json').write_text('{}')
        with self.assertRaisesRegex(m.Failure, 'failure_or_progress_changed'): m.permission_snapshot(self.engine)

    def test_changed_progress_blocks(self):
        self.receipt_fixture(); (self.old/'PROGRESS.json').write_text('{}')
        with self.assertRaisesRegex(m.Failure, 'failure_or_progress_changed'): m.permission_snapshot(self.engine)

    def test_unexpected_file_blocks(self):
        self.receipt_fixture(); (self.old/'unknown').touch()
        with self.assertRaisesRegex(m.Failure, 'state_shape'): m.permission_snapshot(self.engine)

    def test_wrong_started_binding_blocks(self):
        self.receipt_fixture(); (self.old/'STARTED.json').write_text('{}')
        with self.assertRaisesRegex(m.Failure, 'started_binding'): m.permission_snapshot(self.engine)

    def test_local_mountpoint_not_empty_blocks(self):
        self.receipt_fixture(); (self.old/'nfs'/'unexpected').touch()
        with self.assertRaisesRegex(m.Failure, 'mountpoint_not_empty'): m.permission_snapshot(self.engine)

    def test_process_absence(self):
        self.assertGreater(m.previous_absence(self.engine)['scanned_processes'], 0)

    def test_previous_cgroup_blocks(self):
        proc = self.base/'proc'; (proc/'4').mkdir(parents=True)
        (proc/'4'/'cgroup').write_text('0::/system.slice/'+self.old.name+'.service/child\n')
        with self.assertRaisesRegex(m.Failure, 'worker_still_present'): m.previous_absence(self.engine, proc)

    def test_previous_mount_blocks(self):
        fake = types.SimpleNamespace(mount_rows=lambda text: [{'point':str(self.old)+'/nfs'}])
        with self.assertRaisesRegex(m.Failure, 'mount_still_present'): m.previous_absence(fake)

    def test_snapshot_drift_blocks(self):
        a = dict(hashes={'a':'x'},unit={},original={'old_unit':{},'old_request_sha256':'x'})
        b = dict(a, hashes={'a':'y'})
        with self.assertRaises(m.Failure): m.equal_snapshot(a, b)

    def test_launch_nonzero_timeout_private_namespace(self):
        with patch.object(self.h, 'executable', side_effect=lambda x:'/usr/bin/'+x):
            args = m.launch_args('a'*32)
        for value in ('--property=Type=exec', '--property=TimeoutStartSec=30s', '--property=Restart=no',
                      '--property=LimitCORE=0', '--mount', 'private'):
            self.assertIn(value, args)
        for value in ('--wait', '--collect', '--property=TimeoutStartSec=0'): self.assertNotIn(value, args)

    def prepare_launch(self):
        self.stack.enter_context(patch.object(m,'__file__',str(self.driver)))
        self.snapshot = dict(hashes={},unit={},original={'old_unit':{},'old_request_sha256':'x'})
        self.stack.enter_context(patch.object(self.h,'load_engine',return_value=(self.engine,Path('/engine/pilot.py').read_bytes())))
        self.stack.enter_context(patch.object(m,'previous_snapshot',return_value=self.snapshot))
        self.stack.enter_context(patch.object(self.h,'unit_state',return_value={'LoadState':'not-found'}))
        self.stack.enter_context(patch.object(self.engine,'verify_audit',return_value={}))
        self.stack.enter_context(patch.object(self.engine,'resources',return_value={}))
        self.stack.enter_context(patch.object(m,'launch_worker',return_value=dict(pilot='PASS',identity_selftest='PASS',
            opath_exec_inheritance='PASS',opath_reopen_as_uid1000='PASS')))
        self.stack.enter_context(patch.object(self.h,'executable',side_effect=lambda x:'/usr/bin/'+x))

    def test_launch_once_request_before_ack(self):
        self.prepare_launch()
        def command(args, timeout):
            self.assertTrue((self.state/'START-REQUESTED.json').exists())
            self.assertFalse((self.state/'START-ACK.json').exists())
            self.assertEqual(timeout,45)
        with patch.object(self.h,'command',side_effect=command) as start:
            m.recover()
        start.assert_called_once()
        self.assertTrue((self.state/'START-ACK.json').exists())
        self.assertFalse((self.state/'RESULT.json').exists())

    def test_failed_selftest_never_creates_state(self):
        self.prepare_launch()
        with patch.object(m,'launch_worker',return_value={'pilot':'UNCONFIRMED'}), patch.object(self.h,'command') as start:
            with self.assertRaises(m.Failure): m.recover()
        start.assert_not_called(); self.assertFalse(self.state.exists())

    def test_repeated_launch_never_mutates(self):
        self.state.mkdir(mode=0o700)
        with patch.object(self.h,'command') as start:
            with self.assertRaises(m.Failure): m.recover()
        start.assert_not_called(); self.assertEqual(list(self.state.iterdir()),[])

    def test_uncertain_launch_not_replayed(self):
        self.prepare_launch()
        with patch.object(self.h,'command',side_effect=self.h.Failure('uncertain')) as start:
            with self.assertRaises(self.h.Failure): m.recover()
        start.assert_called_once()
        self.assertTrue((self.state/'START-REQUESTED.json').exists())
        self.assertFalse((self.state/'START-ACK.json').exists())

    def test_status_does_not_mutate(self):
        self.state.mkdir(mode=0o700); self.h.save(self.state/'RESULT.json', {'pilot':'UNCONFIRMED'})
        before = (self.state/'RESULT.json').read_bytes()
        with patch.object(self.h,'unit_state',return_value={}), patch.object(m,'launch_worker') as worker:
            m.status()
        worker.assert_not_called(); self.assertEqual((self.state/'RESULT.json').read_bytes(),before)
        self.assertEqual([p.name for p in self.state.iterdir()],['RESULT.json'])

    def prepare_run(self):
        self.state.mkdir(mode=0o700)
        self.h.write(self.state/'recover.py', self.driver.read_bytes())
        self.stack.enter_context(patch.object(m,'__file__',str(self.state/'recover.py')))
        previous = dict(hashes={}, unit={}, original={'old_unit':{},'old_request_sha256':'x'})
        self.h.save(self.state/'RECOVERY.json',dict(previous=previous))
        self.h.save(self.state/'STARTED.json',dict(token='a'*32,script_sha256=m.ENGINE_SHA,
            driver_sha256=m.sha(self.driver.read_bytes()), recovery_sha256=m.sha(self.h.read(self.state/'RECOVERY.json'))))
        self.stack.enter_context(patch.object(self.h,'load_engine',return_value=(self.engine,b'')))
        self.stack.enter_context(patch.object(m,'previous_snapshot',return_value=previous))
        self.stack.enter_context(patch.object(self.h,'unit_state',return_value=dict(Type='exec',Restart='no',TimeoutStartUSec='30s',
            RuntimeMaxUSec='infinity',KillMode='control-group',MainPID=str(os.getpid()),Transient='yes',ActiveState='active')))
        self.stack.enter_context(patch.object(self.engine,'isolated_namespace'))
        self.internal = self.stack.enter_context(patch.object(self.engine,'internal_run',return_value=0))

    def test_run_gate_and_exact_engine_binding(self):
        self.prepare_run(); self.assertEqual(m.run('a'*32),0)
        self.assertEqual(self.engine.STATE,self.state)
        self.assertTrue((self.state/'ENGINE-GATE.json').exists())
        self.internal.assert_called_once_with('a'*32)

    def test_run_wrong_token_cannot_mount(self):
        self.prepare_run()
        with self.assertRaises(m.Failure): m.run('b'*32)
        self.internal.assert_not_called()

    def test_run_repeat_cannot_mount(self):
        self.prepare_run(); self.h.save(self.state/'ENGINE-GATE.json',{})
        with self.assertRaises(m.Failure): m.run('a'*32)
        self.internal.assert_not_called()

    def test_run_old_evidence_drift_blocks(self):
        self.prepare_run()
        with patch.object(m,'previous_snapshot',side_effect=m.Failure('changed')):
            with self.assertRaises(m.Failure): m.run('a'*32)
        self.internal.assert_not_called()

    def test_run_effective_timeout_blocks(self):
        self.prepare_run()
        with patch.object(self.h,'unit_state',return_value={'TimeoutStartUSec':'0'}):
            with self.assertRaises(self.h.Failure): m.run('a'*32)
        self.internal.assert_not_called()

    def test_safe_diagnostic_no_exception_text(self):
        try: raise PermissionError(13,'BUSINESS_FILENAME_AND_SECRET')
        except PermissionError as exc: result=m.diagnostic(exc)
        self.assertEqual(result['errno'],13)
        self.assertNotIn('BUSINESS_FILENAME_AND_SECRET',json.dumps(result))
        self.assertTrue(result['code_frames'])

    def test_bootstrap_hash_mismatch_no_copy(self):
        self.fixture()
        with patch.object(m,'BOOTSTRAP',m.BOOTSTRAP.replace('!=sys.argv[2]', "!='invalid-hash'")):
            result=m.launch_worker(self.engine,self.sfd,self.dfd,'a'*32,'copy',self.driver)
        self.assertEqual(result['failed_check'],'EXEC_BOOTSTRAP_FAILED')
        self.assertEqual(list(self.dst.iterdir()),[])

    def test_extra_root_descriptor_not_inherited(self):
        secret=self.base/'root-only'; self.h.write(secret,b'fixture-not-a-real-secret')
        fd=os.open(str(secret),os.O_RDONLY); os.set_inheritable(fd,True)
        injection="\n if "+str(fd)+" in [int(x) for x in os.listdir('/proc/self/fd') if x.isdigit() and int(x)>2]:\n  try:\n   unexpected=os.readlink('/proc/self/fd/'+str("+str(fd)+"))\n  except FileNotFoundError:\n   unexpected=''\n  if unexpected.endswith('/root-only'): raise RuntimeError('extra_fd_leaked')\n"
        try:
            with patch.object(m,'BOOTSTRAP',m.BOOTSTRAP.replace(' rc=m.exec_child',injection+' rc=m.exec_child')):
                result=m.launch_worker(self.engine,-1,-1,'','selftest',self.driver)
        finally: os.close(fd)
        self.assertEqual(result['pilot'],'PASS',result)

    def test_status_failure_does_not_write(self):
        self.state.mkdir(mode=0o700)
        with patch.object(sys,'argv',['recover.py','--status']), patch.object(m.socket,'gethostname',return_value='datax-03'), \
             patch.object(m,'helpers',return_value=self.h), patch.object(self.h,'unit_state',side_effect=self.h.Failure('read_failed')):
            self.assertEqual(m.main(),1)
        self.assertEqual(list(self.state.iterdir()),[])

    def test_repeat_cli_does_not_add_failure_record(self):
        self.state.mkdir(mode=0o700)
        with patch.object(sys,'argv',['recover.py','--recover-once']), patch.object(m.socket,'gethostname',return_value='datax-03'), \
             patch.object(m,'helpers',return_value=self.h), patch.object(self.h,'command') as command:
            self.assertEqual(m.main(),1)
        command.assert_not_called(); self.assertEqual(list(self.state.iterdir()),[])

    def test_old_python37_opath_failure_reproduced(self):
        self.fixture()
        old = load('/exec1/recover.py', 'previous_exec_driver')
        previous_driver = self.base/'previous-driver.py'
        self.h.write(previous_driver, Path('/exec1/recover.py').read_bytes())
        old.H, old.STATE = self.h, self.state
        with patch.object(os, 'set_inheritable', side_effect=legacy_python37_inheritable):
            result = old.launch_worker(self.engine, self.sfd, self.dfd, 'a'*32, 'copy', previous_driver)
        self.assertEqual(result['errno'], 9, result)
        self.assertEqual(result['failed_step'], 'DROP_EXEC')
        self.assertEqual(result['code_frames'][0]['line'], 291)
        self.assertEqual(list(self.dst.iterdir()), [])

    def test_native_python37_original_failure_and_fixed_copy(self):
        probe=m.opath_legacy_probe()
        self.assertEqual(probe['fcntl_opath_inheritance'],'PASS')
        if sys.version_info[:2] != (3,7):
            self.assertIn(probe['legacy_os_set_inheritable'],('PASS','FAIL'))
            return
        self.assertEqual(probe['errno'],9)
        self.fixture()
        old=load('/exec1/recover.py','native37_original_driver')
        previous_driver=self.base/'previous-driver.py'
        self.h.write(previous_driver,Path('/exec1/recover.py').read_bytes())
        old.H,old.STATE=self.h,self.state
        result=old.launch_worker(self.engine,self.sfd,self.dfd,'a'*32,'copy',previous_driver)
        self.assertEqual(result['errno'],9,result)
        self.assertEqual(result['failed_step'],'DROP_EXEC')
        self.assertEqual(list(self.dst.iterdir()),[])
        fixed=self.copy()
        self.assertEqual(fixed['pilot'],'PASS',fixed)
        self.assertEqual(fixed['verified_bytes'],len(self.payload))

    def test_fixed_copy_with_legacy_inheritable_api(self):
        self.fixture()
        with patch.object(os, 'set_inheritable', side_effect=legacy_python37_inheritable):
            result = self.copy()
        self.assertEqual(result['pilot'], 'PASS', result)
        self.assertEqual(result['verified_bytes'], len(self.payload))
        self.assertEqual((self.dst/result['pilot_directory']/'nested'/'sample.bin').read_bytes(), self.payload)

    def test_opath_inheritance_selftest_in_legacy_mode(self):
        with patch.object(os, 'set_inheritable', side_effect=legacy_python37_inheritable):
            probe = m.opath_legacy_probe()
            result = m.launch_worker(self.engine, -1, -1, '', 'selftest', self.driver)
        self.assertEqual(probe['legacy_os_set_inheritable'], 'FAIL')
        self.assertEqual(probe['errno'], 9)
        self.assertEqual(probe['fcntl_opath_inheritance'], 'PASS')
        self.assertEqual(result['opath_exec_inheritance'], 'PASS', result)
        self.assertEqual(result['opath_reopen_as_uid1000'], 'PASS')

    def test_invalid_fd_not_silently_accepted(self):
        fd = os.open('/dev/null', os.O_RDONLY)
        os.close(fd)
        with self.assertRaises(OSError) as raised:
            m.inherit_fd(fd)
        self.assertEqual(raised.exception.errno, errno.EBADF)

    def test_inherit_preserves_other_fd_flags_and_validity(self):
        fd = os.open('/', os.O_PATH | os.O_DIRECTORY)
        try:
            before = fcntl.fcntl(fd, fcntl.F_GETFD)
            identity = os.fstat(fd)
            m.inherit_fd(fd)
            self.assertEqual(fcntl.fcntl(fd, fcntl.F_GETFD), before & ~fcntl.FD_CLOEXEC)
            self.assertEqual(os.fstat(fd).st_ino, identity.st_ino)
        finally: os.close(fd)

    def exec_receipt_fixture(self):
        self.receipt_fixture()
        prior = m.permission_snapshot(self.engine)
        failed = m.FAILED_EXEC; failed.mkdir(mode=0o700)
        self.h.write(failed/'recover.py', Path('/exec1/recover.py').read_bytes())
        self.h.write(failed/'engine.py', Path('/engine/pilot.py').read_bytes())
        self.h.save(failed/'RECOVERY.json', dict(previous=prior, selftest={'identity_selftest':'PASS'}))
        self.h.save(failed/'STARTED.json', dict(token='b'*32, script_sha256=m.ENGINE_SHA,
            driver_sha256=m.FAILED_EXEC_SHA, recovery_sha256=m.sha(self.h.read(failed/'RECOVERY.json'))))
        for name in ('START-REQUESTED.json','START-ACK.json'):
            self.h.save(failed/name, dict(unit=failed.name+'.service'))
        for name in ('ENGINE-GATE.json','RUN-ENTERED.json','MOUNT-REQUESTED.json'):
            self.h.save(failed/name,{})
        self.h.save(failed/'WORKER-REQUESTED.json',dict(pilot_directory='.weknora-copy-pilot-'+'b'*32))
        self.h.save(failed/'RESULT.json',dict(pilot='UNCONFIRMED',errno=9,failed_step='DROP_EXEC',
            source_contents_read='NOT_CONFIRMED',source_files_modified=False,
            code_frames=[dict(file='recover.py',function='launch_worker',line=291)]))
        for name in ('source-ro','nfs'): (failed/name).mkdir(mode=0o700)
        self.stack.enter_context(patch.object(m,'FAILED_RESULT_SHA',m.sha(self.h.read(failed/'RESULT.json'))))
        def states(name,with_exec=False):
            value=unit('a'*32)
            if name==failed.name+'.service':
                value['ExecStart']='argv[]=/usr/bin/python3.7 -I -B -u '+str(failed/'recover.py')+' --run '+'b'*32+' ;'
            return value
        self.stack.enter_context(patch.object(self.h,'unit_state',side_effect=states))

    def test_new_snapshot_binds_both_failures_readonly(self):
        self.exec_receipt_fixture()
        before={p.name:p.read_bytes() for p in m.FAILED_EXEC.iterdir() if p.is_file()}
        value=m.previous_snapshot(self.engine)
        self.assertEqual(value['exec_failure']['hashes']['recover.py'],m.FAILED_EXEC_SHA)
        self.assertEqual(before,{p.name:p.read_bytes() for p in m.FAILED_EXEC.iterdir() if p.is_file()})

    def test_new_snapshot_changed_failure_blocks(self):
        self.exec_receipt_fixture()
        (m.FAILED_EXEC/'RESULT.json').write_text('{}')
        with self.assertRaisesRegex(m.Failure,'pinned_bytes_changed'):m.previous_snapshot(self.engine)

    def test_new_snapshot_any_worker_progress_blocks(self):
        self.exec_receipt_fixture();(m.FAILED_EXEC/'PROGRESS.json').write_text('{}')
        with self.assertRaisesRegex(m.Failure,'state_shape_changed'):m.previous_snapshot(self.engine)

    def test_new_snapshot_token_binding_blocks(self):
        self.exec_receipt_fixture();(m.FAILED_EXEC/'STARTED.json').write_text('{}')
        with self.assertRaisesRegex(m.Failure,'start_binding_changed'):m.previous_snapshot(self.engine)

    def test_new_snapshot_nonempty_mountpoint_blocks(self):
        self.exec_receipt_fixture();(m.FAILED_EXEC/'nfs'/'unexpected').touch()
        with self.assertRaisesRegex(m.Failure,'mountpoint_not_empty'):m.previous_snapshot(self.engine)

    def test_new_snapshot_effective_unit_change_blocks(self):
        self.exec_receipt_fixture()
        with patch.object(self.h,'unit_state',return_value={}):
            with self.assertRaises(m.Failure):m.previous_snapshot(self.engine)

    def test_new_snapshot_failure_drift_blocks(self):
        a=dict(hashes={},unit={},original={'old_unit':{},'old_request_sha256':'x'},exec_failure={'hashes':{'x':'a'}})
        b=dict(a,exec_failure={'hashes':{'x':'b'}})
        with self.assertRaisesRegex(m.Failure,'exec_failure_drift'):m.equal_snapshot(a,b)

    def test_failed_exec_unit_command_token_checked(self):
        row=unit('b'*32)
        row['ExecStart']='argv[]=/usr/bin/python3.7 -I -B -u '+str(m.FAILED_EXEC/'recover.py')+' --run '+'b'*32+' ;'
        m.validate_failed_exec_unit(row,'b'*32)
        with self.assertRaises(m.Failure):m.validate_failed_exec_unit(row,'a'*32)

    def test_failed_exec_cgroup_blocks(self):
        proc=self.base/'proc';(proc/'7').mkdir(parents=True)
        (proc/'7'/'cgroup').write_text('0::/system.slice/'+m.FAILED_EXEC.name+'.service/child\\n')
        with self.assertRaisesRegex(m.Failure,'worker_still_present'):
            m.previous_absence(self.engine,proc)

    def test_opath_selftest_cannot_substitute_regular_fd(self):
        fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
        try:
            with self.assertRaisesRegex(m.Failure,'opath_fd_required'):m.opath_after_exec(fd,fd)
        finally:os.close(fd)


if __name__ == '__main__':
    unittest.main()

