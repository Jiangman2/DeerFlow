import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import types
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('dryrun',Path(__file__).with_name('verify-portal-0015-auth-log-rotation-20261028-r1.py'))
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class Tests(unittest.TestCase):
    def test_policy_preserves_permissions_and_never_deletes_old_logs(self):
        s=types.SimpleNamespace(st_uid=0,st_gid=0,st_mode=0o100640)
        raw=mod.policy(s)
        self.assertIn(b'create 0640 root root',raw)
        self.assertIn(b'rotate -1',raw)
        self.assertIn(b'nocopytruncate',raw)
        self.assertNotIn(b'restart',raw)

    def test_only_debug_and_no_state_are_dispatched(self):
        def fake(args,**kwargs):
            self.assertEqual(args[1:4],['--debug','--state','/dev/null'])
            self.assertEqual(len(args),5)
            self.assertEqual(len(kwargs['pass_fds']),1)
            self.assertEqual(os.read(kwargs['pass_fds'][0],1024),b'synthetic')
            return types.SimpleNamespace(returncode=0,stdout=b'considering log /var/log/auth.log',stderr=b'')
        with tempfile.TemporaryDirectory() as d,patch.object(mod.subprocess,'run',side_effect=fake):
            result=mod.native_debug('/usr/sbin/logrotate',b'synthetic',d)
            self.assertTrue(result['auth_target_observed'])
            self.assertFalse(result['postrotate_executed'])

    def test_native_errors_are_retained_without_raw_export(self):
        response=types.SimpleNamespace(returncode=1,stdout=b'',stderr=b'error: unknown option private-value')
        with tempfile.TemporaryDirectory() as d,patch.object(mod.subprocess,'run',return_value=response):
            out=mod.native_debug('/usr/sbin/logrotate',b'synthetic',d)
        self.assertEqual(out['error_line_count'],1)
        self.assertTrue(out['directive_unknown'])
        self.assertNotIn('private-value',str(out))

    @unittest.skipUnless(shutil.which('logrotate'),'Native logrotate unavailable')
    def test_real_logrotate_does_not_rotate_or_execute_hook(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); log=root/'auth.log'; log.write_bytes(b'x'*1024)
            marker=root/'hook-executed'
            st=log.stat()
            raw=mod.policy(st).replace(b'/var/log/auth.log',str(log).encode()).replace(b'size 100M',b'size 1')
            raw=raw.replace(b'/usr/bin/systemctl kill --kill-who=main --signal=HUP rsyslog.service',
                            ('touch '+str(marker)).encode())
            result=mod.native_debug(shutil.which('logrotate'),raw,d)
            self.assertEqual(result['returncode'],0,result)
            self.assertEqual(log.read_bytes(),b'x'*1024)
            self.assertEqual(log.stat().st_ino,st.st_ino)
            self.assertFalse(marker.exists())
            self.assertEqual(sorted(x.name for x in root.iterdir()),['auth.log'])


if __name__=='__main__':
    unittest.main(verbosity=2)
