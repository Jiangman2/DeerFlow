"""Only local guard fixtures. No SSH, container daemon calls or image import."""
import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import stat
import tempfile
import types
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
PREP = HERE.parent


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value


G = load(HERE / 'resume_target.py', 'guard_under_test')
B = load(PREP / 'native-preload-r2/preload_native_r2.py', 'fixed_helpers_for_guard_fixture')
F = load(PREP / 'native-preload-attempt-r3/derived_target_code.py', 'fixed_target_for_guard_fixture')
F.B = B


@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'Linux root-private directory-FD fixtures')
class GuardFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.root = Path(self.temp.name)
        self.target = self.root / 'target';self.target.mkdir(mode=0o700)
        self.control = self.root / 'control';self.control.mkdir(mode=0o700)
        self.upload = self.root / 'upload';self.upload.mkdir(mode=0o700)
        self.once = self.root / 'original-once.json'
        self.new_once = self.root / 'resume-once.json'
        raw = (PREP / 'native-preload-attempt-r3/PREPARED.json').read_bytes()
        self.prepared = json.loads(raw)
        (self.target / 'PREPARED.json').write_bytes(raw)
        started = dict(self.prepared, status='INCOMPLETE')
        (self.target / 'STARTED.json').write_bytes(G.encoded(started))
        self.once.write_bytes(G.encoded(dict(prior_operation=G.PRIOR, operation=G.OP, automatic_retry=False)))
        (self.control / 'PLAN-WIRE.json').write_bytes(b'{"fixture":true}\n')
        for path in (self.target / 'PREPARED.json', self.target / 'STARTED.json', self.once, self.control / 'PLAN-WIRE.json'):
            path.chmod(0o600)
        self.rows=[]
        for i in range(6):
            name='fixed-'+str(i)+'.oci.tar';path=self.upload/name;path.write_bytes(b'fixture');path.chmod(0o600)
            self.rows.append(dict(role='role-'+str(i),basename=name,archive_bytes=7,archive_sha256='a'*64))
        self.profile={'images':self.rows}
        value=self.upload.stat()
        self.upload_prepared=dict(upload=str(self.upload),upload_device=value.st_dev,upload_inode=value.st_ino,upload_uid=value.st_uid)
        self.module=types.SimpleNamespace(B=B,ONCE=self.once,fd_regular=F.fd_regular,
            state_paths=lambda token:dict(upload=self.upload,sealed_name='fixture-absent-'+self.root.name),
            unwire_plan=mock.Mock(return_value={'fixture':True}),validate_plan=mock.Mock(),
            IMPORT_REQUESTED=False,REPORT=None)
        self.patches=[mock.patch.object(G,'TARGET',self.target),mock.patch.object(G,'CONTROL',self.control),
                      mock.patch.object(G,'ROOT',self.root),mock.patch.object(G,'ONCE',self.new_once)]
        for patch in self.patches:patch.start()
        self.signals={sig:signal.getsignal(sig) for sig in (signal.SIGTERM,signal.SIGHUP)}
    def tearDown(self):
        for sig,handler in self.signals.items():signal.signal(sig,handler)
        for patch in reversed(self.patches):patch.stop()
        self.temp.cleanup()
    def test_exact_retained_bindings_and_private_plan_provenance(self):
        raw=(PREP/'native-preload-attempt-r3/derived_target_code.py').read_bytes()
        self.assertEqual(len(raw),G.TARGET_CODE_BYTES);self.assertEqual(G.sha(raw),G.TARGET_CODE_SHA)
        report=(PREP/'native-preload-attempt-r3/REPORT.json').read_bytes()
        self.assertEqual(len(report),6274);self.assertEqual(G.sha(report),G.SOURCE_REPORT_SHA)
        with mock.patch.object(G,'uploaded_files',return_value=[]):
            plan,checks=G.inspect_prepared(self.module,self.profile)
        self.assertEqual(checks['prepared_sha256'],G.PREPARED_SHA)
        self.assertEqual(checks['plan_wire_sha256'],G.sha(b'{"fixture":true}\n'))
        self.module.validate_plan.assert_called_once_with(self.profile,plan)
    def test_any_existing_target_entry_including_dangling_link_blocks(self):
        for name in ('RUN-REQUESTED.json','FAIL.json','frontend.SEALED.json','PHASE-A-PASS.json'):
            path=self.target/name;path.symlink_to(self.root/'missing')
            with self.assertRaisesRegex(ValueError,'target_attempt_already_entered_or_changed'):
                G.inspect_prepared(self.module,self.profile)
            path.unlink()
    def test_changed_prepared_or_original_once_is_rejected(self):
        path=self.target/'PREPARED.json';original=path.read_bytes();path.write_bytes(original+b' ')
        with self.assertRaisesRegex(ValueError,'prepared_receipt_changed'):G.inspect_prepared(self.module,self.profile)
        path.write_bytes(original);self.once.write_bytes(G.encoded(dict(operation='wrong')))
        with self.assertRaisesRegex(ValueError,'original_once_binding'):G.inspect_prepared(self.module,self.profile)
    def test_upload_exact_files_identity_and_link_rejection(self):
        rows=G.uploaded_files(self.module,self.profile,self.upload_prepared)
        self.assertEqual(len(rows),6)
        path=self.upload/self.rows[0]['basename'];other=self.root/'other';path.rename(other);path.symlink_to(other)
        with self.assertRaises(OSError):G.uploaded_files(self.module,self.profile,self.upload_prepared)
        path.unlink();os.link(other,path)
        with self.assertRaisesRegex(ValueError,'fd_regular_required'):G.uploaded_files(self.module,self.profile,self.upload_prepared)
    def test_upload_unexpected_member_and_size_rejected(self):
        extra=self.upload/'extra';extra.write_bytes(b'x')
        with self.assertRaisesRegex(ValueError,'upload_file_set_changed'):G.uploaded_files(self.module,self.profile,self.upload_prepared)
        extra.unlink();(self.upload/self.rows[0]['basename']).write_bytes(b'wrong')
        with self.assertRaisesRegex(ValueError,'fd_regular_required'):G.uploaded_files(self.module,self.profile,self.upload_prepared)
    def test_once_is_exclusive_and_does_not_change_original_marker(self):
        original=self.once.read_bytes();G.claim(self.module,self.root)
        with self.assertRaisesRegex(ValueError,'local_resume_already_started'):G.claim(self.module,self.root)
        self.assertEqual(self.once.read_bytes(),original)
    def prepare_run(self, error=None, requested=False):
        import fcntl
        lockpath=self.root/'lock'
        def locked():
            fd=os.open(lockpath,os.O_RDWR|os.O_CREAT,0o600);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);return fd
        self.module.common_lock=locked
        self.module.write_receipt=lambda name,value:B.write_new(self.target/name,G.encoded(value))
        def import_target(profile,plan,token):
            fd=os.open(lockpath,os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            finally:os.close(fd)
            B.write_new(self.target/'RUN-REQUESTED.json',b'{}\n')
            self.module.IMPORT_REQUESTED=requested
            self.module.REPORT=dict(operation=G.OP,phase_a='REQUESTED',phase_b='NOT_REQUESTED')
            if error:raise error
            return dict(status='COMPLETE_PRELOAD',phase_a='PASS',phase_b='PASS',image_rows=[{}]*6)
        self.module.import_target=mock.Mock(side_effect=import_target)
        self.results=[]
        def handoff(raw):self.results.append(json.loads(raw));return '/var/lib/fixture/REPORT.json'
        self.module.handoff_report=handoff
        return lockpath
    def test_local_entry_holds_common_lock_and_success_is_not_native_pass(self):
        self.prepare_run()
        with mock.patch.object(G,'host_identity',return_value={'fixture':True}),mock.patch.object(G,'load_target',return_value=(self.module,self.profile)),mock.patch.object(G,'inspect_prepared',return_value=({},{})),contextlib.redirect_stdout(io.StringIO()),mock.patch.object(B,'progress'):
            rc=G.run()
        self.assertEqual(rc,0);self.assertFalse(self.results[-1]['native_runtime_passed']);self.assertFalse(self.results[-1]['ready_for_rollout'])
        self.module.import_target.assert_called_once()
    def test_preentry_failure_never_touches_original_target(self):
        self.prepare_run();before={p.name:p.read_bytes() for p in self.target.iterdir()}
        with mock.patch.object(G,'host_identity',return_value={}),mock.patch.object(G,'load_target',return_value=(self.module,self.profile)),mock.patch.object(G,'inspect_prepared',side_effect=ValueError('target_attempt_already_entered_or_changed')),contextlib.redirect_stdout(io.StringIO()):
            rc=G.run()
        self.assertEqual(rc,1);self.module.import_target.assert_not_called()
        self.assertEqual({p.name:p.read_bytes() for p in self.target.iterdir()},before)
        self.assertFalse(self.results[-1]['frozen_target_entered'])
        self.assertIsNone(self.results[-1]['target_import_requested']);self.assertIsNone(self.results[-1]['image_import_may_be_partial'])
    def test_busy_common_lock_preserves_unknown_operation_state(self):
        self.prepare_run();self.module.common_lock=mock.Mock(side_effect=BlockingIOError())
        with mock.patch.object(G,'host_identity',return_value={}),mock.patch.object(G,'load_target',return_value=(self.module,self.profile)),contextlib.redirect_stdout(io.StringIO()):
            rc=G.run()
        self.assertEqual(rc,1);self.module.import_target.assert_not_called()
        self.assertIsNone(self.results[-1]['target_import_requested']);self.assertIsNone(self.results[-1]['image_import_may_be_partial'])
    def test_interrupt_after_entry_preserves_uncertain_import_and_fail(self):
        self.prepare_run(error=KeyboardInterrupt(),requested=True)
        with mock.patch.object(G,'host_identity',return_value={}),mock.patch.object(G,'load_target',return_value=(self.module,self.profile)),mock.patch.object(G,'inspect_prepared',return_value=({},{})),contextlib.redirect_stdout(io.StringIO()),mock.patch.object(B,'progress'):
            rc=G.run()
        self.assertEqual(rc,1);self.assertTrue(self.results[-1]['image_import_may_be_partial'])
        self.assertTrue((self.target/'FAIL.json').exists());self.assertTrue((self.target/'RUN-REQUESTED.json').exists())


if __name__=='__main__':unittest.main(verbosity=2)
