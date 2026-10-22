import contextlib
import ctypes
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
from unittest.mock import Mock,patch

import stage as m


def load_helper():
    path=Path('/audit/audit.py')
    spec=importlib.util.spec_from_file_location('readonly_audit_fixture',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class Tests(unittest.TestCase):
    def setUp(self):
        self.old_umask=os.umask(0o022)
        self.tmp=tempfile.TemporaryDirectory(dir='/qualify');self.base=Path(self.tmp.name)
        self.stack=contextlib.ExitStack()
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.opt=self.base/'opt';self.opt.mkdir(mode=0o755);os.chown(self.opt,1000,1000)
        self.parent=self.opt/'deerflow-storage';self.parent.mkdir(mode=0o755)
        self.files=self.parent/'weknora-files';self.doc=self.parent/'weknora-docreader'
        for p in (self.files,self.doc):
            p.mkdir(mode=0o770);p.chmod(0o770);os.chown(p,1000,1000)
        self.etc=self.base/'etc';self.etc.mkdir(mode=0o755)
        self.exports=self.etc/'exports';self.exports.write_bytes(b'');self.exports.chmod(0o644)
        self.audit=self.base/'audit';self.audit.mkdir(mode=0o700)
        self.audit.joinpath('audit.py').write_bytes(Path('/audit/audit.py').read_bytes())
        self.audit.joinpath('audit.py').chmod(0o600)
        summary=dict(storage_share_audit='PARTIAL',utc=m.AUDIT_UTC,node=m.HOST,unavailable_sections=['paths'],
            results=dict(paths=dict(reason='unsafe_ancestor')))
        (self.audit/'audit.log').write_text(json.dumps(summary)+'\n');(self.audit/'audit.log').chmod(0o600)
        self.pins=tuple((str(p),)+m.signature(p.stat()) for p in (self.opt,self.parent,self.files,self.doc))
        values=dict(PARENT=self.parent,PINS=self.pins,STATE=self.base/'state',ANCHOR=self.base/'anchor',
            TARGET=self.base/'anchor/probe',EXPORTS=self.exports,EXPORT_DIR=self.etc/'exports.d',
            CONFIG=self.etc/'exports.d/probe.exports',AUDIT_CONTROL=self.audit,H=load_helper(),OWNED=False,LAST={})
        for name,value in values.items():self.stack.enter_context(patch.object(m,name,value))
        self.driver=self.base/'driver.py';self.driver.write_bytes(Path(m.__file__).read_bytes());self.driver.chmod(0o600)
        self.stack.enter_context(patch.object(m,'__file__',str(self.driver)))
        self.table={};self.calls=[];self.mountcalls=[];self.added=0;self.fail_at=None
        self.stack.enter_context(patch.object(m,'tool',side_effect=lambda name:'/mock/'+name))
        self.stack.enter_context(patch.object(m,'command',side_effect=self.command))
        self.stack.enter_context(patch.object(m,'mount_call',side_effect=self.mount))
        self.stack.enter_context(patch.object(m,'check_bound',side_effect=self.bound))
        self.stack.enter_context(patch.object(m,'services',return_value={'active':True}))
        self.stack.enter_context(patch.object(m.os,'statvfs',return_value=types.SimpleNamespace(
            f_bavail=2*1024**3,f_frsize=1,f_favail=100000)))
        self.stack.enter_context(patch.object(m,'mounts',return_value=[dict(point='/opt',fs='xfs',source='/dev/vdb1',
            options={'rw'},root='/',device='1:1',propagation=[])]))

    def tearDown(self):
        self.stack.close();self.tmp.cleanup();os.umask(self.old_umask)

    def command(self,args,timeout=20):
        self.calls.append(args)
        if args==['/mock/exportfs','-s']:
            return ''.join(p+' '+c+'('+','.join(o)+')\n' for (p,c),o in sorted(self.table.items())).encode()
        if args==['/mock/ip','-o','-4','addr','show']:
            return b'2: eth0 inet 192.168.111.18/24 brd 192.168.111.255\n'
        self.assertEqual(args[:4],['/mock/exportfs','-i','-o',m.OPTIONS])
        self.added+=1
        self.assertTrue((m.STATE/('EXPORT-%d-REQUESTED.json'%self.added)).exists())
        client,target=args[4].split(':',1)
        self.assertEqual(target,str(m.TARGET));self.assertIn(client,m.CLIENTS)
        self.table[(target,client)]=tuple(sorted(m.OPTIONS.split(',')+['wdelay','hide']))
        if self.fail_at==self.added:raise m.Failure('uncertain_export_outcome')
        return b''

    def mount(self,source,target,flags):
        self.assertTrue((m.STATE/'BIND-REQUESTED.json').exists())
        self.assertEqual(target,str(m.TARGET));self.mountcalls.append((source,target,flags))
        if source is not None:
            self.assertTrue(source.startswith('/proc/self/fd/'))
            s=os.fstat(int(source.rsplit('/',1)[1]))
            self.assertEqual(s.st_ino,(m.PARENT/m.TEST_NAME).stat().st_ino)
            self.assertEqual(s.st_uid,1000)

    def bound(self,s):
        self.assertEqual(m.signature(s),m.signature((m.PARENT/m.TEST_NAME).stat()))
        self.assertEqual(list((m.PARENT/m.TEST_NAME).iterdir()),[])

    def run_once(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return m.once(Path('/audit/audit.py').read_bytes())

    def test_success_adds_exact_empty_test_scope(self):
        before=m.pin_paths();raw=self.exports.read_bytes()
        self.assertEqual(self.run_once(),0)
        self.assertEqual(m.pin_paths(),before);self.assertEqual(self.exports.read_bytes(),raw)
        self.assertEqual(self.added,2);self.assertEqual(len(self.mountcalls),3)
        self.assertEqual(self.mountcalls[0][2],4096)
        self.assertEqual(self.mountcalls[1][2],4096|32|2|4|8)
        self.assertEqual(self.mountcalls[2][2],1<<18)
        result=json.loads((m.STATE/'RESULT.json').read_bytes())
        self.assertEqual(result['datax03_test_export_stage'],'PASS')
        self.assertFalse(result['business_directories_exported']);self.assertFalse(result['business_data_copied'])
        self.assertFalse(result['ready_for_production_switch']);self.assertFalse(result['ready_to_scale'])
        self.assertEqual(result['cross_node_io'],'NOT_TESTED')
        self.assertEqual(list(self.files.iterdir()),[]);self.assertEqual(list(self.doc.iterdir()),[])

    def test_preflight_accepts_exact_pinned_nonroot_opt(self):
        before=m.pin_paths();result=m.preflight()
        self.assertEqual(result['paths'],before)
        self.assertFalse(m.STATE.exists());self.assertEqual(self.added,0);self.assertEqual(self.mountcalls,[])

    def test_changed_opt_owner_or_inode_blocks(self):
        for replacement in (0,1234):
            os.chown(self.opt,replacement,1000)
            try:
                with self.assertRaisesRegex(m.Failure,'pinned_path'):m.preflight()
            finally:os.chown(self.opt,1000,1000)

    def test_renamed_source_parent_blocks_before_writes(self):
        moved=self.opt/'old-storage';self.parent.rename(moved);self.parent.mkdir(mode=0o755)
        with self.assertRaises(m.Failure):m.preflight()
        self.assertFalse(m.STATE.exists())

    def test_symlink_in_source_chain_refused(self):
        moved=self.opt/'saved';self.parent.rename(moved);self.parent.symlink_to(moved)
        with self.assertRaises(OSError):m.pin_paths()

    def test_existing_new_source_never_adopted(self):
        (self.parent/m.TEST_NAME).mkdir()
        with self.assertRaisesRegex(m.Failure,'test_source_exists'):self.run_once()
        self.assertFalse(m.STATE.exists())

    def test_existing_anchor_never_adopted(self):
        m.ANCHOR.mkdir()
        with self.assertRaisesRegex(m.Failure,'test_target_exists'):self.run_once()
        self.assertFalse(m.STATE.exists())

    def test_existing_config_never_overwritten(self):
        m.EXPORT_DIR.mkdir();m.CONFIG.write_bytes(b'unrelated')
        with self.assertRaisesRegex(m.Failure,'test_target_exists'):self.run_once()
        self.assertEqual(m.CONFIG.read_bytes(),b'unrelated')

    def test_existing_active_export_blocks(self):
        self.table[('/old','*')]=('rw',)
        with self.assertRaisesRegex(m.Failure,'active_exports_changed'):self.run_once()
        self.assertFalse(m.STATE.exists())

    def test_existing_config_drift_blocks(self):
        self.exports.write_bytes(b'/old *(rw)\n')
        with self.assertRaisesRegex(m.Failure,'empty_export_config_changed'):self.run_once()

    def test_new_directories_correct_owners_existing_unchanged(self):
        self.run_once()
        child=self.parent/m.TEST_NAME
        self.assertEqual((child.stat().st_uid,child.stat().st_gid,stat.S_IMODE(child.stat().st_mode)),(1000,1000,0o770))
        self.assertEqual((m.ANCHOR.stat().st_uid,stat.S_IMODE(m.ANCHOR.stat().st_mode)),(0,0o755))
        self.assertEqual((self.opt.stat().st_uid,stat.S_IMODE(self.opt.stat().st_mode)),(1000,0o755))

    def test_repeat_blocks_before_preflight(self):
        self.run_once()
        with patch.object(m,'preflight',side_effect=AssertionError('must not run')):
            with self.assertRaisesRegex(m.Failure,'attempt_exists'):self.run_once()

    def test_mount_error_retains_intent_without_export(self):
        with patch.object(m,'mount_call',side_effect=m.Failure('mount_error')):
            with self.assertRaises(m.Failure):self.run_once()
        self.assertTrue((m.STATE/'BIND-REQUESTED.json').exists())
        self.assertFalse((m.STATE/'BIND-ACK.json').exists());self.assertEqual(self.added,0)

    def test_uncertain_export_not_retried(self):
        self.fail_at=1
        with self.assertRaisesRegex(m.Failure,'uncertain_export'):self.run_once()
        self.assertEqual(self.added,1);self.assertEqual(len(self.table),1)
        self.assertTrue((m.STATE/'EXPORT-1-REQUESTED.json').exists())
        self.assertFalse((m.STATE/'EXPORT-1-ACK.json').exists())

    def test_config_drift_after_creation_stops_before_export(self):
        real=m.write_new
        def write(path,raw,mode=0o600):
            real(path,raw,mode)
            if path==m.CONFIG:self.exports.write_bytes(b'# concurrent admin\n')
        with patch.object(m,'write_new',side_effect=write):
            with self.assertRaisesRegex(m.Failure,'configuration_drift'):self.run_once()
        self.assertEqual(self.added,0)

    def test_targeted_exports_cannot_weaken_options(self):
        good=m.OPTIONS.split(',')
        for extra in ('no_root_squash','async','insecure','crossmnt','anonuid=0'):
            with self.subTest(extra=extra),self.assertRaises(m.Failure):
                m.validate_exports({(str(m.TARGET),m.CLIENTS[0]):good+[extra]},{m.CLIENTS[0]})
        with self.assertRaises(m.Failure):
            m.validate_exports({(str(m.TARGET),m.CLIENTS[0]):[x for x in good if x!='mountpoint']},{m.CLIENTS[0]})

    def test_unknown_or_subnet_client_blocks(self):
        with self.assertRaises(m.Failure):
            m.validate_exports({(str(m.TARGET),'192.168.111.0/24'):m.OPTIONS.split(',')},set(m.CLIENTS))

    def test_root_private_checks_stay_strict(self):
        with self.assertRaises(m.Failure):m.safe_dir(self.opt)

    def test_code_and_audit_receipt_binding(self):
        value=m.audit_receipt();self.assertEqual(value['utc'],m.AUDIT_UTC)
        (self.audit/'audit.py').write_bytes(b'changed')
        with self.assertRaisesRegex(m.Failure,'audit_code_changed'):m.audit_receipt()

    def test_wrong_audit_timestamp_blocks(self):
        (self.audit/'audit.log').write_text('{"storage_share_audit":"PARTIAL","utc":"other"}\n')
        with self.assertRaises(m.Failure):m.audit_receipt()

    def test_audit_helper_hash_before_execution(self):
        h,raw=m.load_helper();self.assertEqual(m.sha(raw),m.AUDIT_SHA)
        (self.audit/'audit.py').write_text('raise AssertionError("must not execute")')
        with self.assertRaisesRegex(m.Failure,'helper_changed'):m.load_helper()

    def test_status_readonly_after_partial_export(self):
        self.fail_at=1
        with self.assertRaises(m.Failure):self.run_once()
        before={p.name:p.read_bytes() for p in m.STATE.iterdir()};count=self.added
        m.status()
        self.assertEqual(self.added,count)
        self.assertEqual(before,{p.name:p.read_bytes() for p in m.STATE.iterdir()})

    def test_status_before_start_never_creates(self):
        m.status();self.assertFalse(m.STATE.exists());self.assertFalse(m.ANCHOR.exists())

    def test_new_directory_exclusive_does_not_change_existing(self):
        target=self.base/'existing';target.mkdir(mode=0o755)
        before=m.signature(target.stat())
        with self.assertRaises(FileExistsError):m.new_dir(target,0o700,1000,1000)
        self.assertEqual(m.signature(target.stat()),before)

    def test_private_file_symlink_hardlink_rejected(self):
        link=self.base/'link';link.symlink_to(self.exports)
        with self.assertRaises(OSError):m.read_safe(link)
        link.unlink();os.link(str(self.exports),str(link))
        with self.assertRaises(m.Failure):m.read_safe(self.exports)

    def test_parse_exports_rejects_ambiguous_records(self):
        for raw in (b'garbage',b'/x h',b'/x h(rw)\n/x h(ro)',b'/x/../y h(rw)'):
            with self.assertRaises(m.Failure):m.parse_exports(raw)

    def test_pin_open_does_not_need_business_contents(self):
        (self.files/'business-secret').write_bytes(b'do-not-read')
        with patch.object(os,'listdir',side_effect=AssertionError('no business traversal')):
            self.assertEqual(len(m.pin_paths()),4)

    def test_actual_mount_syscall_arguments_and_errno(self):
        # restore only the function under test, never issue a real mount.
        lib=types.SimpleNamespace(mount=Mock(return_value=-1))
        source=Path(m.__file__).read_text()
        # Load an unmocked instance for a real ctypes call wrapper with mocked libc.
        module=types.ModuleType('mount_wrapper');exec(compile(source,'stage.py','exec'),module.__dict__)
        with patch.object(module.ctypes,'CDLL',return_value=lib),patch.object(module.ctypes,'get_errno',return_value=1):
            with self.assertRaisesRegex(module.Failure,'mount_syscall_failed'):module.mount_call('/proc/self/fd/9','/target',4096)
        lib.mount.assert_called_once_with(b'/proc/self/fd/9',b'/target',None,4096,None)
        self.assertEqual(module.LAST['errno'],1)

    def test_command_uncertain_timeout_no_kill_or_retry(self):
        module=types.ModuleType('command_wrapper');exec(compile(Path(m.__file__).read_bytes(),'stage.py','exec'),module.__dict__)
        child=Mock(pid=123);child.communicate.side_effect=subprocess.TimeoutExpired('exportfs',20)
        with patch.object(module.subprocess,'Popen',return_value=child) as popen:
            with self.assertRaisesRegex(module.Failure,'uncertain_no_retry'):module.command(['/usr/sbin/exportfs'])
        popen.assert_called_once();child.kill.assert_not_called();child.terminate.assert_not_called()

    def test_main_failure_journaled_without_raw_error(self):
        with patch.object(sys,'argv',['stage.py','--stage-once']),patch.object(m.socket,'gethostname',return_value=m.HOST), \
             patch.object(m,'load_helper',return_value=(m.H,Path('/audit/audit.py').read_bytes())), \
             patch.object(m,'mount_call',side_effect=OSError(1,'SENSITIVE_DETAIL')):
            self.assertEqual(m.main(),1)
        raw=(m.STATE/'FAILED.json').read_bytes()
        self.assertNotIn(b'SENSITIVE_DETAIL',raw)
        self.assertEqual(json.loads(raw)['errno'],1)

    def test_source_paths_not_in_export_configuration(self):
        raw=m.config_bytes()
        self.assertIn(b'mountpoint',raw);self.assertIn(b'root_squash',raw)
        self.assertNotIn(str(self.files).encode(),raw);self.assertNotIn(str(self.doc).encode(),raw)
        self.assertEqual(raw.count(b'root_squash'),2)

    def bound_fixture(self):
        module=types.ModuleType('bound_fixture')
        exec(compile(Path(m.__file__).read_bytes(),'stage.py','exec'),module.__dict__)
        module.ANCHOR=m.ANCHOR;module.TARGET=m.TARGET
        m.new_dir(m.ANCHOR,0o755);m.new_dir(m.TARGET,0o700)
        actual_fd=os.open(str(self.files),os.O_RDONLY|os.O_DIRECTORY)
        self.stack.callback(os.close,actual_fd)
        real_open=os.open
        def opened(path,*args,**kwargs):
            return os.dup(actual_fd) if str(path)==str(m.TARGET) else real_open(path,*args,**kwargs)
        self.stack.enter_context(patch.object(module.os,'open',side_effect=opened))
        row=dict(point=str(m.TARGET),root='/fixture',fs='xfs',source='/dev/vdb1',
                 options={'rw','nosuid','nodev','noexec'},propagation=[])
        return module,row

    def test_bind_verifier_checks_flags_inode_and_emptiness(self):
        module,row=self.bound_fixture()
        with patch.object(module,'mounts',return_value=[row]):module.check_bound(self.files.stat())
        with patch.object(module,'mounts',return_value=[row]):
            with self.assertRaisesRegex(module.Failure,'source_inode_mismatch'):module.check_bound(self.doc.stat())
        (self.files/'unexpected').write_bytes(b'fixture')
        with patch.object(module,'mounts',return_value=[row]):
            with self.assertRaisesRegex(module.Failure,'not_empty'):module.check_bound(self.files.stat())

    def test_bind_verifier_rejects_weakened_flags_or_shared_mount(self):
        module,row=self.bound_fixture()
        for key,value in (('options',{'rw'}),('propagation',['shared:2']),('fs','nfs4')):
            bad=dict(row);bad[key]=value
            with patch.object(module,'mounts',return_value=[bad]):
                with self.assertRaises(module.Failure):module.check_bound(self.files.stat())

    def test_bind_verifier_rejects_descendant_mounts(self):
        module,row=self.bound_fixture()
        child=dict(row,point=str(m.TARGET)+'/foreign')
        with patch.object(module,'mounts',return_value=[row,child]):
            with self.assertRaisesRegex(module.Failure,'submount'):module.check_bound(self.files.stat())


if __name__=='__main__':unittest.main()
