"""No production paths or services are used by these tests."""
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('relocator',Path(__file__).with_name('rotate-relocate-portal-0015-auth-log-20261028-r1.py'))
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)


class Tests(unittest.TestCase):
    def test_policy_has_no_global_include_no_deletion_no_early_hup(self):
        raw=mod.rotation_policy()
        self.assertIn(b'rotate -1',raw)
        self.assertIn(b'nocopytruncate',raw)
        self.assertNotIn(b'include',raw)
        self.assertNotIn(b'postrotate',raw)
        self.assertNotIn(b'restart',raw)

    def test_permissions_and_security_attribute_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            a=Path(d)/'old';b=Path(d)/'new';a.write_bytes(b'data');b.touch()
            a.chmod(0o640);os.setxattr(str(a),'user.kysec-test',b'label-bytes')
            with a.open('rb') as src,b.open('rb') as dst:mod.copy_metadata(src.fileno(),dst.fileno())
            self.assertEqual(os.getxattr(str(b),'user.kysec-test'),b'label-bytes')
            self.assertEqual(b.stat().st_mode&0o777,0o640)

    def test_label_failure_prevents_any_logger_signal(self):
        with tempfile.TemporaryDirectory() as d:
            a=Path(d)/'old';b=Path(d)/'new';a.write_bytes(b'data');b.touch()
            os.setxattr(str(a),'user.kysec-test',b'label')
            calls=[]
            with a.open('rb') as src,b.open('rb') as dst,patch.object(mod.os,'setxattr',side_effect=PermissionError):
                with self.assertRaises(PermissionError):
                    mod.reopen(src.fileno(),dst.fileno(),(1,'2'),calls.append,signal_logger=lambda:calls.append('SIGNAL'))
            self.assertNotIn('SIGNAL',calls)
            self.assertEqual(a.read_bytes(),b'data')

    def test_empty_preflight_probes_preserve_attributes_without_rotating(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);a=root/'auth.log';a.write_bytes(b'untouched')
            os.setxattr(str(a),'user.kysec-test',b'label')
            x=root/'root-probe';y=root/'data-probe';x.mkdir();y.mkdir()
            result=mod.probe_metadata(str(a),(str(x),str(y)))
            self.assertIn('user.kysec-test',result)
            self.assertEqual(a.read_bytes(),b'untouched')
            self.assertEqual(list(x.iterdir())+list(y.iterdir()),[])

    def test_logger_reopen_verifies_metadata_before_signal(self):
        with tempfile.TemporaryDirectory() as d:
            a=Path(d)/'old';b=Path(d)/'new';a.write_bytes(b'data');b.touch()
            os.setxattr(str(a),'user.kysec-test',b'label')
            events=[]
            def signal():
                self.assertEqual(os.getxattr(str(b),'user.kysec-test'),b'label')
                events.append('SIGNAL')
            with a.open('rb') as src,b.open('rb') as dst,patch.object(mod,'logger_identity',return_value=(7,'8')),patch.object(mod,'handles',side_effect=[[dict(pid=7,writable=True)],[]]):
                mod.reopen(src.fileno(),dst.fileno(),(7,'8'),events.append,signal_logger=signal,emit=lambda:events.append('EMIT'))
            self.assertEqual(events[0]['phase'],'NEW_LOG_METADATA_VERIFIED_BEFORE_HUP')
            self.assertEqual(events[1:3],['SIGNAL','EMIT'])
            self.assertEqual(events[-1]['phase'],'LOGGER_REOPEN_ACCEPTED')

    @unittest.skipUnless(Path('/dev/shm').is_dir(),'Separate tmpfs unavailable')
    def test_verified_cross_device_copy_releases_only_old_file(self):
        with tempfile.TemporaryDirectory() as a,tempfile.TemporaryDirectory(dir='/dev/shm') as b:
            old=Path(a)/'auth.log.1';old.write_bytes(os.urandom(2048));original=old.read_bytes()
            live=Path(a)/'auth.log';live.write_bytes(b'live remains')
            os.setxattr(str(old),'user.kysec-test',b'label')
            events=[]
            with patch.object(mod,'RATE',0):result=mod.relocate_closed(str(old),b,events.append,check_unused=lambda _:None)
            self.assertFalse(old.exists());self.assertEqual(live.read_bytes(),b'live remains')
            copy=Path(result['destination'])
            self.assertEqual(copy.read_bytes(),original)
            self.assertEqual(result['sha256'],hashlib.sha256(original).hexdigest())
            self.assertEqual(os.getxattr(str(copy),'user.kysec-test'),b'label')
            self.assertEqual(events[-1]['phase'],'VERIFIED_ARCHIVE_BEFORE_ROOT_UNLINK')

    @unittest.skipUnless(Path('/dev/shm').is_dir(),'Separate tmpfs unavailable')
    def test_bad_archive_digest_never_removes_root_original(self):
        with tempfile.TemporaryDirectory() as a,tempfile.TemporaryDirectory(dir='/dev/shm') as b:
            p=Path(a)/'old';p.write_bytes(b'retain entire log')
            real=mod.transfer
            def corrupt(src,dst,*args):return 'bad' if dst is None else real(src,dst,*args)
            with patch.object(mod,'RATE',0),patch.object(mod,'transfer',side_effect=corrupt):
                with self.assertRaisesRegex(mod.Refused,'ARCHIVE_HASH_MISMATCH'):
                    mod.relocate_closed(str(p),b,lambda _:None,check_unused=lambda _:None)
            self.assertEqual(p.read_bytes(),b'retain entire log')

    @unittest.skipUnless(Path('/dev/shm').is_dir(),'Separate tmpfs unavailable')
    def test_changed_live_logger_keeps_verified_root_original(self):
        with tempfile.TemporaryDirectory() as a,tempfile.TemporaryDirectory(dir='/dev/shm') as b:
            p=Path(a)/'old';p.write_bytes(b'retain entire log')
            def changed():raise mod.Refused('LOGGER_CHANGED_BEFORE_ROOT_RELEASE')
            with patch.object(mod,'RATE',0),self.assertRaisesRegex(mod.Refused,'LOGGER_CHANGED'):
                mod.relocate_closed(str(p),b,lambda _:None,check_unused=lambda _:None,before_release=changed)
            self.assertEqual(p.read_bytes(),b'retain entire log')
            self.assertEqual((Path(b)/'auth.log.20261028-r1').read_bytes(),b'retain entire log')

    @unittest.skipUnless(Path('/dev/shm').is_dir(),'Separate tmpfs unavailable')
    def test_still_open_file_never_copied_or_removed(self):
        with tempfile.TemporaryDirectory() as a,tempfile.TemporaryDirectory(dir='/dev/shm') as b:
            p=Path(a)/'old';p.write_bytes(b'retain')
            def busy(_):raise mod.Refused('OLD_LOG_STILL_OPEN_RETAINED')
            with self.assertRaisesRegex(mod.Refused,'OLD_LOG_STILL_OPEN'):
                mod.relocate_closed(str(p),b,lambda _:None,check_unused=busy)
            self.assertEqual(list(Path(b).iterdir()),[]);self.assertTrue(p.exists())

    def test_actual_open_child_file_is_detected(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'old';path.write_bytes(b'x')
            child=subprocess.Popen(['python','-c','import sys;f=open(sys.argv[1],"rb");print("READY",flush=True);sys.stdin.read()',str(path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE)
            try:
                self.assertEqual(child.stdout.readline().strip(),b'READY')
                with self.assertRaisesRegex(mod.Refused,'OLD_LOG_STILL_OPEN'):
                    mod.unused(path.stat())
            finally:child.stdin.close();child.wait(timeout=5);child.stdout.close()

    def test_same_device_refused_before_copy(self):
        with tempfile.TemporaryDirectory() as a,tempfile.TemporaryDirectory() as b:
            p=Path(a)/'old';p.write_bytes(b'retain')
            with self.assertRaisesRegex(mod.Refused,'DESTINATION_MUST_DIFFER'):
                mod.relocate_closed(str(p),b,lambda _:None,check_unused=lambda _:None)
            self.assertTrue(p.exists())

    @unittest.skipUnless(shutil.which('logrotate'),'Native logrotate unavailable')
    def test_native_rotation_then_preserved_label_and_closed_archive(self):
        with tempfile.TemporaryDirectory() as d,tempfile.TemporaryDirectory(dir='/dev/shm') as dest:
            root=Path(d);live=root/'auth.log';old_dir=root/'rotated';old_dir.mkdir()
            data=b'whole historical log\n'*100;live.write_bytes(data)
            live.chmod(0o644);os.setxattr(str(live),'user.kysec-test',b'label')
            prior=live.stat().st_ino
            with patch.object(mod,'SOURCE',str(live)),patch.object(mod,'OLD_DIR',str(old_dir)):
                raw=mod.rotation_policy().replace(b'size 100M',b'size 1')
            config=root/'rule';config.write_bytes(raw);config.chmod(0o600)
            p=subprocess.run([shutil.which('logrotate'),'--state',str(root/'state'),str(config)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            self.assertEqual(p.returncode,0,p.stderr.decode())
            old=old_dir/'auth.log.1';self.assertEqual(old.stat().st_ino,prior)
            self.assertNotEqual(live.stat().st_ino,prior)
            with old.open('rb') as a,live.open('rb') as b:mod.copy_metadata(a.fileno(),b.fileno())
            self.assertEqual(os.getxattr(str(live),'user.kysec-test'),b'label')
            with patch.object(mod,'RATE',0):result=mod.relocate_closed(str(old),dest,lambda _:None,check_unused=lambda _:None)
            self.assertEqual(Path(result['destination']).read_bytes(),data)
            live.write_bytes(b'new logger data');self.assertTrue(live.exists())


if __name__=='__main__':unittest.main(verbosity=2)
