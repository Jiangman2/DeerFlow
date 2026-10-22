import ast
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import preload_transfer_transport as t
import recover_production_preload as r
import production_node_preload as n
import prepare_production_release as legacy


class TransportTests(unittest.TestCase):
    def exchange(self, remote, ask=None, body=b'abc', deadline=5, expected=None):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'image'
            path.write_bytes(body)
            rows = []
            result = t.send([sys.executable, '-u', '-c', remote], path, lambda p, m: open(str(p), 'rb'),
                            len(body), expected or hashlib.sha256(body).hexdigest(), 'WEKNORA-SUDO-'+'a'*32,
                            rows.append, ask or Mock(side_effect=AssertionError('password must not be requested')),
                            deadline=deadline)
            return result, rows

    def script(self, password=False, body=b'abc', skip=False):
        prefix = ''
        if password:
            prefix = '''sys.stderr.write("banner redacted\\nWEKNORA-SUDO-"+"a"*32+"\\n");sys.stderr.flush()
assert sys.stdin.buffer.readline()==b"secret\\n"
'''
        if skip:
            return 'import sys,json\n'+prefix+'''print(json.dumps({"receiver":"SKIP_TRANSFER_COMPLETE_IMAGE_PRESENT"}),flush=True)
assert sys.stdin.buffer.read()==b""
print(json.dumps({"production_node_preload":"PASS"}),flush=True)
'''
        return 'import sys,json,hashlib\n'+prefix+'''print(json.dumps({"receiver":"READY","bytes":'''+str(len(body))+'''}),flush=True)
assert sys.stdin.buffer.readline()=='''+repr(t.FRAME)+'''
assert hashlib.sha256(sys.stdin.buffer.read()).hexdigest()=='''+repr(hashlib.sha256(body).hexdigest())+'''
print(json.dumps({"production_node_preload":"PASS"}),flush=True)
'''

    def test_cached_or_nopasswd_no_secret_requested_or_sent(self):
        ask = Mock(side_effect=AssertionError('unexpected ask'))
        self.assertEqual(self.exchange(self.script(), ask)[0]['production_node_preload'], 'PASS')
        ask.assert_not_called()

    def test_password_only_after_exact_prompt_and_buffer_zeroed(self):
        password = bytearray(b'secret\n')
        ask = Mock(return_value=password)
        result, rows = self.exchange(self.script(password=True), ask)
        self.assertEqual(result['production_node_preload'], 'PASS')
        ask.assert_called_once_with()
        self.assertEqual(password, bytearray(len(password)))
        self.assertNotIn('secret', json.dumps(rows))
        self.assertNotIn('banner', json.dumps(rows))

    def test_existing_complete_image_skip_both_auth_branches(self):
        for auth in (False, True):
            with self.subTest(auth=auth):
                self.assertEqual(self.exchange(self.script(password=auth, skip=True),
                    Mock(return_value=bytearray(b'secret\n')))[0]['production_node_preload'], 'PASS')

    def test_binary_multichunk(self):
        body = bytes(range(256))*300
        self.assertEqual(self.exchange(self.script(body=body), body=body)[0]['production_node_preload'], 'PASS')

    def test_wrong_prompt_never_releases_secret(self):
        ask = Mock(side_effect=AssertionError('unexpected ask'))
        remote = 'import sys;sys.stderr.write("WRONG-PROMPT\\n");sys.stderr.flush();sys.stdin.buffer.read(1)'
        with self.assertRaisesRegex(RuntimeError, 'deadline'):
            self.exchange(remote, ask, deadline=.3)
        ask.assert_not_called()

    def test_duplicate_prompt_never_resends_password(self):
        remote = '''import sys,time
for i in range(2):
 sys.stderr.write("WEKNORA-SUDO-"+"a"*32+"\\n");sys.stderr.flush()
 sys.stdin.buffer.readline()
'''
        ask = Mock(return_value=bytearray(b'secret\n'))
        with self.assertRaisesRegex(RuntimeError, 'unexpected_sudo_prompt'):
            self.exchange(remote, ask)
        ask.assert_called_once_with()

    def test_bad_handshake_not_success(self):
        remote = 'import json; print(json.dumps({"receiver":"READY","bytes":9}),flush=True)'
        with self.assertRaisesRegex(RuntimeError, 'receiver_handshake'):
            self.exchange(remote)

    def test_truncated_remote_no_retry(self):
        with self.assertRaisesRegex(RuntimeError, 'remote_preload_not_passed'):
            self.exchange('import sys;sys.exit(1)')

    def test_frame_rejects_unconsumed_password_before_data_read(self):
        source = io.BytesIO(b'secret\n'+t.FRAME+b'abc')
        with self.assertRaisesRegex(RuntimeError, 'archive_frame_rejected'):
            t.FramedInput(source).read(3)

    def test_valid_frame_then_exact_eof(self):
        reader = t.FramedInput(io.BytesIO(t.FRAME+b'abc'))
        self.assertEqual(reader.read(3), b'abc')
        self.assertEqual(reader.read(1), b'')

    def test_source_change_after_open_detected(self):
        remote = self.script()
        expected = hashlib.sha256(b'abc').hexdigest()
        with patch.object(t.hashlib, 'sha256', return_value=SimpleNamespace(update=lambda b:None, hexdigest=lambda:'0'*64)):
            with self.assertRaisesRegex(RuntimeError, 'source_transfer_failed'):
                self.exchange(remote, expected=expected)

    def test_ssh_pinned_prompt_not_empty_no_auth_argv(self):
        args = t.ssh_command('pass', Path('/private/known_hosts'), '192.168.111.18', t.marker())
        self.assertIn('StrictHostKeyChecking=yes', args)
        self.assertIn('HostKeyAlias=datax-03', args)
        self.assertIn('sudo -S -p', args[-1])
        self.assertNotIn("-p ''", args[-1])
        self.assertNotIn('secret', args[-1])
        self.assertLess(len(args[-1].encode()), 120000)

    def test_legacy_sender_bug_reproduced_with_no_sudo_password_read(self):
        # Reproduce excess-byte failure without logging the contaminated bytes.
        remote = '''import sys,json
print(json.dumps({"receiver":"READY","bytes":3}),flush=True)
data=sys.stdin.buffer.read()
assert len(data)>3 and data.endswith(b"abc")
print(json.dumps({"production_node_preload":"UNCONFIRMED","failed_check":"archive_excess_bytes_NO_IMPORT"}),flush=True)
sys.exit(1)
'''
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'image'; path.write_bytes(b'abc')
            q=SimpleNamespace(opened=lambda p,m:open(str(p),'rb'))
            with patch.object(legacy,'remote_command',return_value=[sys.executable,'-u','-c',remote]), \
                 patch.object(legacy.trust_cli,'ARCHIVE_BYTES',3), \
                 patch.object(legacy.trust_cli,'ARCHIVE_SHA',hashlib.sha256(b'abc').hexdigest()), \
                 open(os.devnull,'w') as tty, contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError,'remote_preload_not_passed'):
                    legacy.send(path,q,bytearray(b'secret\n'),tty)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.old=self.root/'old'; self.old.mkdir(mode=0o700)
        self.target=self.root/'new'
        self.patches=[]
        for name,value in [('OLD_NODE',self.old),('TARGET',self.target)]:
            p=patch.object(r,name,value); p.start(); self.addCleanup(p.stop)

    def receipt(self, name, row):
        raw=json.dumps(row).encode(); p=self.old/name; p.write_bytes(raw); p.chmod(0o600)
        return hashlib.sha256(raw).hexdigest()

    def bind_fixture(self):
        sha=self.receipt('RESULT.json',dict(production_node_preload='UNCONFIRMED',imports_attempted=0,
            phase='RECEIVE_VERIFIED_ARCHIVE',failed_check='archive_excess_bytes_NO_IMPORT',
            production_writes=0,production_workloads_mutated=False))
        start=self.receipt('STARTED.json',dict(candidate=r.cli.IMAGE,already_present=False))
        self.receipt('status.py',{})
        self.receipt('candidate.oci.tar.partial','secret')
        size=(self.old/'candidate.oci.tar.partial').stat().st_size
        for obj,name,value in [(r,'OLD_NODE_SHA',sha),(r,'OLD_NODE_STARTED_SHA',start),(r.cli,'ARCHIVE_BYTES',size)]:
            p=patch.object(obj,name,value); p.start(); self.addCleanup(p.stop)

    def test_exact_failure_and_partial_metadata_only(self):
        self.bind_fixture()
        original=r.os.open
        def guarded(path,*args,**kwargs):
            self.assertFalse(str(path).endswith('.partial'))
            return original(path,*args,**kwargs)
        with patch.object(r.os,'open',side_effect=guarded):
            binding=r.bind_target()
        self.assertFalse(binding['old_partial_content_read'])
        self.assertIn('POTENTIALLY',binding['old_partial'])

    def test_any_import_marker_blocks_recovery(self):
        self.bind_fixture(); self.receipt('IMPORT-REQUESTED.json',{})
        with self.assertRaisesRegex(RuntimeError,'mutation_boundary'):
            r.bind_target()

    def test_changed_receipt_blocks_recovery(self):
        self.bind_fixture(); self.receipt('RESULT.json',{'imports_attempted':1})
        with self.assertRaisesRegex(RuntimeError,'receipt_hash'):
            r.bind_target()

    def test_symlink_partial_blocks_recovery(self):
        self.bind_fixture(); p=self.old/'candidate.oci.tar.partial'; p.unlink(); p.symlink_to(self.old/'RESULT.json')
        with self.assertRaises(r.node.verify.CheckFailure):
            r.bind_target()

    def test_world_readable_partial_blocks_recovery(self):
        self.bind_fixture(); (self.old/'candidate.oci.tar.partial').chmod(0o644)
        with self.assertRaises(r.node.verify.CheckFailure):
            r.bind_target()

    def test_existing_recovery_never_runs_again(self):
        self.target.mkdir(mode=0o700)
        with patch.object(r,'bind_target') as bind,patch.object(r.node,'accept') as accept:
            with self.assertRaisesRegex(RuntimeError,'previous_recovery'):
                r.node_main()
        bind.assert_not_called(); accept.assert_not_called()

    def test_frame_failure_never_calls_verifier(self):
        self.target.mkdir(mode=0o700)
        with patch.object(n,'ROOT',self.target),patch.object(n.cli,'ARCHIVE_BYTES',3),patch.object(n.verify,'verify') as verify:
            with contextlib.redirect_stdout(io.StringIO()),self.assertRaisesRegex(RuntimeError,'archive_frame_rejected'):
                n.receive_archive(t.FramedInput(io.BytesIO(b'secret\n'+t.FRAME+b'abc')))
        verify.assert_not_called()
        self.assertEqual((self.target/'candidate.oci.tar.partial').read_bytes(),b'')

    def node_exchange(self, data, present=False):
        old = dict(config='old')
        with patch.object(r,'REMOTE_SOURCES',{}),patch.object(r.node,'ROOT',self.target), \
             patch.object(r.node,'clear_operations'),patch.object(r.node,'rollback',return_value=old), \
             patch.object(r.node,'storage',return_value={}),patch.object(r.cli,'ARCHIVE_BYTES',3), \
             patch.object(r.cli,'ARCHIVE_SHA',hashlib.sha256(b'abc').hexdigest()), \
             patch.object(r.cli,'reference_present',return_value=present), \
             patch.object(r.cli,'image_check',return_value={'pinned':True}), \
             patch.object(r.cli,'command',return_value=b'') as command, \
             patch.object(r.node.verify,'verify',return_value={'uncompressed_layer_bytes':r.cli.EXPANDED}) as verify, \
             patch.object(r.sys,'stdin',SimpleNamespace(buffer=io.BytesIO(data))), \
             patch.object(r,'bind_target',return_value={'bound':'exact prior receipt'}), \
             contextlib.redirect_stdout(io.StringIO()):
            result=r.node_main()
        return result,command,verify

    def test_recovery_full_receive_verify_import_once(self):
        result, command, verify=self.node_exchange(t.FRAME+b'abc')
        self.assertEqual(result,0); command.assert_called_once(); verify.assert_called_once()
        self.assertEqual((self.target/'candidate.oci.tar').read_bytes(),b'abc')
        report=json.loads((self.target/'RESULT.json').read_bytes())
        self.assertEqual(report['imports_attempted'],1)
        self.assertFalse(report['production_workloads_mutated'])
        self.assertIn('recovery_binding',json.loads((self.target/'STARTED.json').read_bytes()))
        ast.parse((self.target/'status.py').read_text(),feature_version=(3,7))

    def test_recovery_frame_failure_never_imports(self):
        result, command, verify=self.node_exchange(b'wrong\nabc')
        self.assertEqual(result,1); command.assert_not_called(); verify.assert_not_called()
        self.assertFalse((self.target/'IMPORT-REQUESTED.json').exists())

    def test_recovery_complete_image_reuses_without_import(self):
        result, command, verify=self.node_exchange(b'',present=True)
        self.assertEqual(result,0); command.assert_not_called(); verify.assert_not_called()
        self.assertFalse((self.target/'candidate.oci.tar.partial').exists())

    def test_receipt_mode_and_duplicate_keys(self):
        self.receipt('RESULT.json',{})
        (self.old/'RESULT.json').write_bytes(b'{"a":1,"a":2}')
        with self.assertRaises(r.node.verify.CheckFailure): r.read(self.old/'RESULT.json')

    def test_no_raw_error_or_secret(self):
        self.assertEqual(r.safe_error(ValueError('SECRET')), 'SAFE_DIAGNOSTIC_REQUIRED')
        self.assertEqual(r.safe_error(RuntimeError('secret is here')), 'SAFE_DIAGNOSTIC_REQUIRED')

    def test_status_source_readonly_no_ssh(self):
        with patch.object(r,'SOURCE',self.target),patch.object(r,'save') as save,patch.object(t,'send') as send:
            with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(r.source_status(),0)
        save.assert_not_called();send.assert_not_called()

    def test_python37_and_no_broad_mutation(self):
        for module in (r,t):
            text=Path(module.__file__).read_text()
            ast.parse(text,feature_version=(3,7))
            for token in ('rmtree(', 'images prune', 'kubectl apply', 'SIGTSTP', 'rollout restart', 'shell=True'):
                self.assertNotIn(token,text)


if __name__ == '__main__': unittest.main()
