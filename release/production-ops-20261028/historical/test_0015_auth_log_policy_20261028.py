"""Synthetic files, mocked service transport, and real local logrotate only."""
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import types
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
path=Path(os.environ.get('AUTH_POLICY_SCRIPT',str(HERE/'portal-auth-log-policy-0015-20261028-r1.py')))
spec=importlib.util.spec_from_file_location('policy',path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
if mod.BASE_SOURCE is None:mod.BASE_SOURCE=(HERE/'rotate-relocate-portal-0015-auth-log-20261028-r1.py').read_text()


class Tests(unittest.TestCase):
    @contextlib.contextmanager
    def fixture(self,install=False):
        with tempfile.TemporaryDirectory() as d,tempfile.TemporaryDirectory(dir='/dev/shm') as data,contextlib.ExitStack() as stack:
            root=Path(d);source=root/'auth.log';source.write_bytes(b'whole historical auth log\n');source.chmod(0o644)
            os.setxattr(str(source),'user.kysec-test',b'label')
            constants=dict(OPT=str(root/'opt'),RUNNER=str(root/'opt'/'maintenance.py'),MANIFEST=str(root/'opt'/'INSTALL.json'),
                           STATE=str(root/'state'),PENDING=str(root/'pending'),ARCHIVES=str(Path(data)/'archives'),
                           SOURCE=str(source),CONFIG=str(root/'policy.conf'),CRON=str(root/'cron'),
                           ACCEPTED_REPORT=str(root/'accepted.json'),ACCEPTED_ARCHIVE=str(Path(data)/'historical.log'),
                           ROOT_DEVICE=root.stat().st_dev,DATA_DEVICE=Path(data).stat().st_dev,THRESHOLD=8)
            stack.enter_context(patch.multiple(mod,**constants))
            real_statvfs=os.statvfs
            stack.enter_context(patch.object(mod.os,'statvfs',side_effect=lambda p:
                types.SimpleNamespace(f_bavail=100*1024**3//4096,f_frsize=4096)
                if str(p)==mod.ARCHIVES else real_statvfs(p)))
            base=mod.engine();base.RATE=0
            logger=(7,'8');state=dict(reopened=False,hups=0,commands=[])
            real_command=base.command
            def command(args,timeout=20):
                state['commands'].append(args)
                if args[:2]==['/usr/bin/systemctl','kill']:
                    state.update(reopened=True,hups=state['hups']+1)
                    return types.SimpleNamespace(returncode=0,stdout=b'',stderr=b'')
                if args[:2]==['/usr/bin/systemctl','show']:
                    return types.SimpleNamespace(returncode=0,stdout=b'ActiveState=active',stderr=b'')
                return real_command(args,timeout)
            base.command=command;base.logger_identity=lambda:logger;base.config_changed=lambda:None
            oldkey=(source.stat().st_dev,source.stat().st_ino)
            def handles(key):
                current=(source.stat().st_dev,source.stat().st_ino)
                if state['reopened'] and key==current:return [dict(pid=7,writable=True)]
                if not state['reopened'] and key==oldkey:return [dict(pid=7,writable=True)]
                return []
            base.handles=handles
            actual_reopen=base.reopen
            base.reopen=lambda *a,**kw:actual_reopen(*a,emit=lambda:None,**kw)
            if not install:
                for p in (mod.OPT,mod.STATE,mod.PENDING,mod.ARCHIVES):Path(p).mkdir(mode=0o700)
                Path(mod.CONFIG).write_bytes(mod.rule().replace(b'size 100M',b'size 1'))
            yield types.SimpleNamespace(root=root,source=source,base=base,state=state,logger=logger,
                                        binary=shutil.which('logrotate'))

    def report(self):return dict(rotation_requests=0,service_signal_requests=0)

    def test_verified_base_and_narrow_cron_scope(self):
        self.assertEqual(hashlib.sha256(mod.BASE_SOURCE.encode()).hexdigest(),mod.BASE_SHA)
        self.assertIn(b'size 100M',mod.rule());self.assertNotIn(b'postrotate',mod.rule())
        self.assertNotIn(b'include',mod.rule());self.assertIn(b'rotate -1',mod.rule())
        self.assertIn(b'17 * * * * root ',mod.cron('/usr/bin/python3'))

    def test_exclusive_lock_defers_second_run(self):
        with self.fixture():
            first=mod.lock()
            try:self.assertIsNone(mod.lock())
            finally:os.close(first)
            second=mod.lock();self.assertIsNotNone(second);os.close(second)

    def test_small_log_does_not_rotate_signal_or_create_cycle(self):
        with self.fixture() as f:
            f.source.write_bytes(b'x');out=self.report()
            mod.maintain(f.base,out,f.source.stat(),f.logger,f.binary)
            self.assertEqual(out['status'],'AUTH_LOG_BELOW_100M_NO_ROTATION')
            self.assertEqual(out['rotation_requests'],0);self.assertEqual(f.state['hups'],0)
            self.assertFalse(Path(mod.STATE,'CYCLE.json').exists())

    def test_data_headroom_failure_stops_before_rotation(self):
        with self.fixture() as f,patch.object(mod.os,'statvfs',return_value=types.SimpleNamespace(f_bavail=1,f_frsize=4096)):
            out=self.report()
            with self.assertRaisesRegex(ValueError,'DATA_HEADROOM'):
                mod.maintain(f.base,out,f.source.stat(),f.logger,f.binary)
            self.assertEqual(out['rotation_requests'],0);self.assertEqual(f.state['hups'],0)

    @unittest.skipUnless(shutil.which('logrotate'),'Native logrotate required')
    def test_real_rotation_signal_transfer_and_no_duplicate_next_run(self):
        with self.fixture() as f:
            original=f.source.read_bytes();out=self.report()
            mod.maintain(f.base,out,f.source.stat(),f.logger,f.binary)
            self.assertEqual(out['status'],'AUTH_LOG_ROTATED_AND_ARCHIVED');self.assertEqual(f.state['hups'],1)
            self.assertEqual(Path(out['result']['destination']).read_bytes(),original)
            self.assertEqual(os.getxattr(str(f.source),'user.kysec-test'),b'label')
            self.assertFalse(Path(mod.PENDING,'auth.log.1').exists());self.assertFalse(Path(mod.STATE,'CYCLE.json').exists())
            again=self.report();mod.maintain(f.base,again,f.source.stat(),f.logger,f.binary)
            self.assertEqual(again['rotation_requests'],0);self.assertEqual(f.state['hups'],1)

    @unittest.skipUnless(shutil.which('logrotate'),'Native logrotate required')
    def test_crash_after_unlink_recovers_verified_archive_without_rotating_again(self):
        with self.fixture() as f:
            actual=mod.write_json
            def fail_receipt(path,value):
                if path.endswith('/RECEIPT.json'):raise OSError('synthetic power loss')
                return actual(path,value)
            with patch.object(mod,'write_json',side_effect=fail_receipt),self.assertRaises(OSError):
                mod.maintain(f.base,self.report(),f.source.stat(),f.logger,f.binary)
            self.assertFalse(Path(mod.PENDING,'auth.log.1').exists())
            self.assertTrue(Path(mod.STATE,'CYCLE.json').exists())
            out=self.report();mod.maintain(f.base,out,f.source.stat(),f.logger,f.binary)
            self.assertEqual(out['status'],'AUTH_LOG_ROTATED_AND_ARCHIVED');self.assertEqual(out['rotation_requests'],0)
            self.assertEqual(f.state['hups'],1)

    @unittest.skipUnless(shutil.which('logrotate'),'Native logrotate required')
    def test_partial_archive_retained_without_a_second_rotation(self):
        with self.fixture() as f:
            real=f.base.transfer
            def partial(src,dst,size,rate,phase):
                if dst is not None:os.write(dst,b'partial');raise OSError('synthetic disk failure')
                return real(src,dst,size,rate,phase)
            with patch.object(f.base,'transfer',side_effect=partial),self.assertRaises(OSError):
                mod.maintain(f.base,self.report(),f.source.stat(),f.logger,f.binary)
            old=Path(mod.PENDING,'auth.log.1');self.assertTrue(old.exists())
            out=self.report()
            with self.assertRaisesRegex(ValueError,'PARTIAL_ARCHIVE_RETAINED'):
                mod.maintain(f.base,out,f.source.stat(),f.logger,f.binary)
            self.assertTrue(old.exists());self.assertEqual(out['rotation_requests'],0);self.assertEqual(f.state['hups'],1)

    @unittest.skipUnless(shutil.which('logrotate'),'Native logrotate required')
    def test_metadata_failure_stops_before_hup_and_keeps_original(self):
        with self.fixture() as f:
            real=f.base.copy_metadata
            # Preflight empty probes succeed; restoring the live file fails.
            def fail_live(src,dst):
                if os.fstat(dst).st_ino==f.source.stat().st_ino:raise PermissionError('synthetic label failure')
                return real(src,dst)
            with patch.object(f.base,'copy_metadata',side_effect=fail_live),self.assertRaises(PermissionError):
                mod.maintain(f.base,self.report(),f.source.stat(),f.logger,f.binary)
            self.assertTrue(Path(mod.PENDING,'auth.log.1').exists());self.assertEqual(f.state['hups'],0)

    @unittest.skipUnless(shutil.which('logrotate'),'Native logrotate required')
    def test_fully_copied_archive_can_resume_after_interrupted_readback(self):
        with self.fixture() as f:
            real=f.base.transfer
            def fail_verify(src,dst,size,rate,phase):
                if dst is None:raise OSError('synthetic interruption')
                return real(src,dst,size,rate,phase)
            with patch.object(f.base,'transfer',side_effect=fail_verify),self.assertRaises(OSError):
                mod.maintain(f.base,self.report(),f.source.stat(),f.logger,f.binary)
            out=self.report();mod.maintain(f.base,out,f.source.stat(),f.logger,f.binary)
            self.assertTrue(out['result']['recovered_existing_archive']);self.assertEqual(f.state['hups'],1)

    def install_fixture(self,f):
        r=json.loads((HERE/'0015-auth-log-relocation-field-20261028-r1.json').read_bytes())['observation']
        raw=(json.dumps({k:v for k,v in r.items() if not k.startswith('report_')},sort_keys=True)+'\n').encode()
        Path(mod.ACCEPTED_REPORT).write_bytes(raw);Path(mod.ACCEPTED_REPORT).chmod(0o600)
        with open(mod.ACCEPTED_ARCHIVE,'wb') as stream:stream.truncate(r['bytes'])

    def test_install_publishes_cron_only_after_preflight_without_maintenance(self):
        with self.fixture(install=True) as f:
            self.install_fixture(f);calls=[]
            def preflight(_):
                calls.append(Path(mod.CRON).exists());return f.source.stat(),f.logger,f.binary
            with patch.object(mod,'source_stat',side_effect=f.source.stat),patch.object(mod,'interpreter',return_value='/usr/bin/python3'),patch.object(mod,'preflight',side_effect=preflight):
                result=mod.install(f.base)
            self.assertEqual(calls,[False,True]);self.assertEqual(result['rotation_requests'],0)
            self.assertEqual(f.state['hups'],0);self.assertTrue(Path(mod.CRON).exists())
            self.assertEqual(result['status'],'AUTH_LOG_PERMANENT_POLICY_INSTALLED_CHECKED_NO_ROTATION')
            self.assertEqual(mod.installed()['files'][mod.CONFIG],hashlib.sha256(mod.rule()).hexdigest())

    def test_rejected_preflight_never_enables_cron(self):
        with self.fixture(install=True) as f:
            self.install_fixture(f)
            with patch.object(mod,'source_stat',side_effect=f.source.stat),patch.object(mod,'interpreter',return_value='/usr/bin/python3'),patch.object(mod,'preflight',side_effect=ValueError('native debug rejected')):
                with self.assertRaisesRegex(ValueError,'native debug rejected'):mod.install(f.base)
            self.assertFalse(Path(mod.CRON).exists());self.assertEqual(f.state['hups'],0)

    def test_installed_rule_tampering_is_refused(self):
        with self.fixture(install=True) as f:
            self.install_fixture(f)
            with patch.object(mod,'source_stat',side_effect=f.source.stat),patch.object(mod,'interpreter',return_value='/usr/bin/python3'),patch.object(mod,'preflight',return_value=(f.source.stat(),f.logger,f.binary)):
                mod.install(f.base)
            Path(mod.CONFIG).write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'INSTALLED_FILE_CHANGED'):mod.installed()


if __name__=='__main__':unittest.main(verbosity=2)
