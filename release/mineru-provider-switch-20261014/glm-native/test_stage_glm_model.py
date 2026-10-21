import ast
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import stage_glm_model as r
import native_credential_transport as c

CIPHER='enc:v1:'+'A'*39


class CredentialTests(unittest.TestCase):
    def test_ascii_key_validation(self):
        self.assertTrue(c.valid_key('SYNTHETIC'))
        for value in ('','enc:v1:bad','a b','x\n','中文','a'*4097,None,True):
            self.assertFalse(c.valid_key(value),repr(value))

    def test_cipher_is_canonical_and_bounded(self):
        self.assertTrue(c.valid_cipher(CIPHER))
        for value in ('plaintext','enc:v1:A','enc:v1:'+'A'*5501,CIPHER+'=',CIPHER[:-1]+'B',None):
            self.assertFalse(c.valid_cipher(value),repr(value))

    def test_packet_requires_pinned_binary(self):
        with patch.object(c,'HELPER',b'synthetic'),patch.object(c,'HELPER_SHA',hashlib.sha256(b'synthetic').hexdigest()):
            self.assertEqual(json.loads(c.packet({'mode':'self-test'}))['request'],{'mode':'self-test'})
        with patch.object(c,'HELPER',b'corrupt'),self.assertRaises(RuntimeError): c.packet({'mode':'self-test'})

    def test_transport_key_only_on_stdin(self):
        answer=dict(credential_helper='PASS',mode='encrypt',native_roundtrip=True,ciphertext=CIPHER)
        n=SimpleNamespace(call=Mock(return_value=(0,json.dumps(answer).encode())))
        with patch.object(c,'packet',return_value=b'PRIVATE-STDIN'):
            self.assertEqual(c.run(n,{'metadata':{'name':'exact'}},{'mode':'encrypt','api_key':'FAKE-KEY'}),answer)
        argv=n.call.call_args[0][0]
        self.assertNotIn('FAKE-KEY',str(argv)); self.assertEqual(n.call.call_args.kwargs['data'],b'PRIVATE-STDIN')
        self.assertIn('-i',argv); self.assertIn('1000:1000',argv)

    def test_extra_output_fail_closed(self):
        n=SimpleNamespace(call=Mock(return_value=(0,b'{"credential_helper":"PASS","mode":"self-test","native_roundtrip":true,"extra":"x"}')))
        with patch.object(c,'packet',return_value=b'{}'),self.assertRaises(RuntimeError):
            c.run(n,{'metadata':{'name':'p'}},{'mode':'self-test'})

    def test_failure_reports_only_safe_step(self):
        n=SimpleNamespace(call=Mock(return_value=(1,b'{"credential_transport":"UNCONFIRMED","step":"native_execution","raw_error_printed":false}')))
        with patch.object(c,'packet',return_value=b'{}'),self.assertRaisesRegex(RuntimeError,'native_credential_failed:native_execution'):
            c.run(n,{'metadata':{'name':'p'}},{'mode':'self-test'})

    def test_launcher_writes_only_public_executable_no_network(self):
        ast.parse(c.LAUNCHER,feature_version=(3,7))
        for value in ('mkdtemp','os.unlink(path)','os.rmdir(directory)','RLIMIT_CORE','timeout=15','GOTRACEBACK'):
            self.assertIn(value,c.LAUNCHER)
        for value in ('requests','urllib','pip install','shell=True'):
            self.assertNotIn(value,c.LAUNCHER)


class StageTests(unittest.TestCase):
    def setUp(self):
        self.stack=contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir='/root')))/'attempt'
        self.root.mkdir(mode=0o700)
        self.stack.enter_context(patch.object(r,'ROOT',self.root))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def p(self,obj,name,**kw): return self.stack.enter_context(patch.object(obj,name,**kw))

    def row(self,model=None):
        return dict(read_only='on',schema=[dict(version=90,dirty=False)],new_references=0,model=model)

    def test_expected_never_stores_plain_or_cipher_value(self):
        row=r.expected(CIPHER)
        self.assertNotIn(CIPHER,json.dumps(row)); self.assertNotIn('api_key',row['parameters'])
        self.assertEqual(row['cipher_sha256'],hashlib.sha256(CIPHER.encode()).hexdigest())
        self.assertFalse(row['is_default']); self.assertEqual(row['status'],'inactive')
        self.assertEqual(row['managed_by'],''); self.assertEqual(row['parameters']['max_concurrency'],0)

    def test_projection_requires_no_references_and_schema90(self):
        self.assertIsNone(r.projection(self.row()))
        for key,value in [('new_references',1),('read_only','off'),('schema',[dict(version=91,dirty=False)])]:
            with self.subTest(key=key),self.assertRaises(Exception): r.projection(dict(self.row(),**{key:value}))

    def test_read_query_excludes_cipher(self):
        self.assertIn("parameters::jsonb-'api_key'",r.READ_SQL)
        self.assertIn('BEGIN READ ONLY',r.READ_SQL)
        self.assertNotIn('UPDATE ',r.READ_SQL); self.assertNotIn('INSERT ',r.READ_SQL)

    def test_sql_encrypt_only_single_insert_no_updates(self):
        sql=r.insert_sql(CIPHER)
        self.assertEqual(sql.count('INSERT INTO'),1)
        for token in ('UPDATE ','DELETE ','CREATE ','ALTER ','ON CONFLICT'):
            self.assertNotIn(token,sql)
        self.assertIn("statement_timeout='5s'",sql); self.assertIn("lock_timeout='1s'",sql)
        self.assertIn('pg_try_advisory_xact_lock',sql); self.assertIn('policies_changed',sql)
        self.assertIn('inserted_model_changed_by_policy',sql)

    def test_plaintext_refused_before_sql(self):
        with self.assertRaises(Exception): r.insert_sql('fake-unencrypted-secret')

    def test_private_journal_exclusive(self):
        r.save('x.json',{'a':1})
        self.assertEqual((self.root/'x.json').stat().st_mode&0o777,0o600)
        with self.assertRaises(FileExistsError): r.save('x.json',{})

    def setup_once(self,fail_write=False):
        pod={'metadata':{'name':r.POD,'uid':r.POD_UID}}
        pg={'metadata':{'name':'pg','uid':'pg-uid'}}
        before={'pod_identity':[r.POD_UID,['container']], 'server_ids':['one']}
        self.p(r,'baseline',return_value=(pod,before)); self.p(r,'phase')
        self.p(r.sw.release,'accepted',return_value={}); self.p(r.sw.release,'db_binding',return_value=(pg,'postgres'))
        native=SimpleNamespace(call=Mock(side_effect=RuntimeError('uncertain') if fail_write else None,
            return_value=(0,b'{"model_insert_committed":true}')),get=Mock(return_value=pg))
        self.p(r.sw,'a',new=SimpleNamespace(native=native))
        self.p(r.sw,'obs',new=SimpleNamespace(digest=lambda v:json.dumps(v,sort_keys=True)))
        crypto=self.p(c,'run',side_effect=[{'native_roundtrip':True},{'ciphertext':CIPHER},{'native_roundtrip':True}])
        self.p(r.getpass,'getpass',return_value='FAKE-KEY-NOT-REAL')
        reads=self.p(r.sw,'db_sql',side_effect=[self.row(),self.row(),self.row(r.expected(CIPHER)),{'ciphertext':CIPHER}])
        return native,crypto,reads

    def test_success_only_stages_model_and_never_logs_credentials(self):
        native,crypto,reads=self.setup_once()
        report={}; r.once(report)
        self.assertEqual(report['glm_model_stage'],'PASS'); self.assertEqual(native.call.call_count,1)
        self.assertTrue((self.root/'INSERT-REQUESTED.json').exists()); self.assertTrue((self.root/'SUCCESS.json').exists())
        saved=''.join(p.read_text() for p in self.root.iterdir())
        self.assertNotIn('FAKE-KEY-NOT-REAL',saved); self.assertNotIn(CIPHER,saved)
        argv=native.call.call_args[0][0]; self.assertIn('-i',argv)
        self.assertNotIn(CIPHER,str(argv)); self.assertNotIn('FAKE-KEY-NOT-REAL',str(native.call.call_args))
        self.assertIn(CIPHER,native.call.call_args.kwargs['data'].decode())

    def test_uncertain_insert_leaves_journal_no_retry(self):
        native,_,_=self.setup_once(fail_write=True)
        with self.assertRaises(RuntimeError): r.once({})
        self.assertTrue((self.root/'INSERT-REQUESTED.json').exists())
        self.assertFalse((self.root/'INSERT-ACK.json').exists()); self.assertEqual(native.call.call_count,1)

    def test_existing_model_stops_before_prompt_or_encrypt(self):
        native,crypto,reads=self.setup_once(); reads.side_effect=None; reads.return_value=self.row(r.expected(CIPHER))
        with self.assertRaises(Exception): r.once({})
        crypto.assert_not_called(); native.call.assert_not_called(); r.getpass.getpass.assert_not_called()

    def test_crypto_failure_never_inserts(self):
        native,crypto,_=self.setup_once(); crypto.side_effect=RuntimeError('crypto_failed')
        with self.assertRaises(RuntimeError): r.once({})
        native.call.assert_not_called()

    def test_readback_failure_never_retries_or_succeeds(self):
        native,_,reads=self.setup_once(); reads.side_effect=[self.row(),self.row(),self.row()]
        with self.assertRaises(Exception): r.once({})
        self.assertEqual(native.call.call_count,1); self.assertFalse((self.root/'SUCCESS.json').exists())

    def test_api_key_format_before_encrypt(self):
        native,crypto,_=self.setup_once(); r.getpass.getpass.return_value='bad\nkey'
        with self.assertRaises(Exception): r.once({})
        self.assertEqual(crypto.call_count,1); native.call.assert_not_called()

    def test_status_exact_without_decrypt_or_insert(self):
        value=r.expected(CIPHER); r.save('EXPECTED.private.json',value)
        r.save('INSERT-REQUESTED.json',dict(expected_sha256=r.sw.recovery.read(self.root/'EXPECTED.private.json')[1]))
        self.p(r,'current',return_value=({}, {}, {})); self.p(r.sw,'db_sql',return_value=self.row(value))
        emit=Mock(); self.p(r.sw,'obs',new=SimpleNamespace(emit=emit))
        native=self.p(c,'run'); r.status()
        self.assertEqual(emit.call_args[0][0]['staged_model'],'EXACT_INACTIVE_NONDEFAULT'); native.assert_not_called()
        self.assertNotIn(value['cipher_sha256'],json.dumps(emit.call_args[0][0]))

    def test_python37_grammar(self):
        for module in (r,c): ast.parse(Path(module.__file__).read_text(),feature_version=(3,7))


if __name__=='__main__': unittest.main()
