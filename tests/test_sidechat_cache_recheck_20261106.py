"""Synthetic readonly cache recheck boundaries; never contact containerd or K8s."""
import ast
from collections import Counter
from contextlib import ExitStack
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'deploy/enterprise/recheck-portal-frontend-sidechat-cache-datax02-20261106-r1.py'
spec=importlib.util.spec_from_file_location('sidechat_recheck',SCRIPT)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def prior_fixture():
    commands=[]
    for phase,count in m.EXPECTED_PRIOR_PHASES.items():
        commands.extend({'phase':phase,'returncode':0,'error_code':None,
            'containerd_mutation_requested':phase in ('phase_a','phase_b','publish_canonical_reference')} for _ in range(count))
    row=next(r for r in commands if r['phase']=='image_reference');commands.remove(row);commands.append(row)
    return {'schema':'portal-frontend-ui-cache-preload.v1','status':'INCOMPLETE','error_code':'command_limit',
      'operation':m.PRIOR_OPERATION,'hostname':m.HOST,'expected_node_uid':m.NODE_UID,
      'manifest_digest':m.MANIFEST,'config_digest':m.CONFIG,'phase_a':'PASS','phase_b':'PASS',
      'image_import_requested':True,'transport_pin':m.TRANSPORT_PIN,
      'references_before':dict(m.PROTECTED,**{name:None for name in m.CANONICAL}),
      'canonical_names_created':list(m.CANONICAL),
      'verified_archive':{'all_blob_hashes_verified':True,'all_layer_diff_ids_verified':True,'layers':len(m.LAYER_DESCRIPTORS)},
      'commands':commands}


def valid_prior(raw):
    with patch.object(m,'PRIOR_BYTES',len(raw)),patch.object(m,'PRIOR_SHA',m.sha(raw)):
        return m.validate_prior(raw)


class CacheFixture:
    """Synthetic content hashes plus a real 31-reference protected baseline."""
    def __enter__(self):
        self.stack=ExitStack()
        self.config_raw=m.canonical({'architecture':'arm64','os':'linux','rootfs':{'type':'layers','diff_ids':m.DIFF_IDS}})
        self.config='sha256:'+m.sha(self.config_raw)
        self.manifest_raw=m.canonical({'schemaVersion':2,'config':{'digest':self.config,'size':len(self.config_raw)},'layers':m.LAYER_DESCRIPTORS})
        self.manifest='sha256:'+m.sha(self.manifest_raw)
        self.canonical=(m.TAG,m.REPO+'@'+self.manifest)
        self.references=dict(m.PROTECTED,**{name:self.manifest for name in self.canonical+(m.TEMP_TAG,)})
        values={'CONFIG':self.config,'MANIFEST':self.manifest,'CANONICAL':self.canonical,'REFERENCES':self.references,
                'BLOBS':{self.manifest,self.config}|{d['digest'] for d in m.LAYER_DESCRIPTORS}}
        for key,value in values.items():self.stack.enter_context(patch.object(m,key,value))
        return self
    def __exit__(self,*args):return self.stack.__exit__(*args)
    def response(self,phase,args,is_cri=False):
        if args[:2]==['images','ls']:
            name=args[2][len('name=='):]
            return ('REF TYPE DIGEST SIZE PLATFORMS LABELS\n'+name+' application/vnd.oci.image.manifest.v1+json '+self.references[name]+' 1 linux/arm64 -\n').encode()
        if args==['content','get',self.manifest]:return self.manifest_raw
        if args==['content','get',self.config]:return self.config_raw
        if args[:3]==['content','ls','--quiet']:return ('\n'.join(sorted(m.BLOBS))+'\n').encode()
        if args[:2]==['content','ls']:
            digest=args[2][len('digest=='):]
            labels={'containerd.io/gc.ref.snapshot.overlayfs':m.FINAL_CHAIN} if digest==self.config else dict({'containerd.io/gc.ref.content.config':self.config},**{'containerd.io/gc.ref.content.l.'+str(i):d['digest'] for i,d in enumerate(m.LAYER_DESCRIPTORS)})
            return ('DIGEST SIZE AGE LABELS\n'+digest+' 1B 1m '+','.join(k+'='+v for k,v in labels.items())+'\n').encode()
        if args[:1]==['snapshots']:
            return ('KEY PARENT KIND\n'+'\n'.join(r['chain_id']+' '+r['parent']+' Committed' for r in m.chains())+'\n').encode()
        if args[:2]==['images','check']:return (args[-1][len('name=='):]+'\n').encode()
        if is_cri:return m.canonical({'status':{'id':self.config,'repoTags':[m.TAG],'repoDigests':[self.canonical[1]]}})
        raise AssertionError('Unexpected synthetic request '+str(args))


class FakeReader:
    def __init__(self,fixture,change=None):self.fixture=fixture;self.change=change;self.commands=[]
    def call(self,phase,args,is_cri=False,limit=m.MIB):
        assert m.allowed_command(args,is_cri),args
        self.commands.append({'phase':phase,'args':args,'mutation_requested':False})
        raw=self.fixture.response(phase,args,is_cri)
        return self.change(phase,args,raw) if self.change else raw


class CacheRecheckTests(unittest.TestCase):
    def test_python37_syntax(self):
        ast.parse(SCRIPT.read_text(encoding='utf-8'),feature_version=(3,7))
    def test_production_receipt_pin_and_node_are_exact(self):
        self.assertEqual(m.PRIOR_BYTES,115986)
        self.assertEqual(m.PRIOR_SHA,'b23b2d1f07eb5b2d5c00d72b8393e5b9503c1a51b90107e1d7179c1097139120')
        self.assertEqual(m.HOST,'datax-02')
        self.assertEqual(m.PRIOR_OPERATION,'281ac71347fa44a59ef78d56c690c8f3')
        self.assertEqual(len(m.PROTECTED),31)
    def test_bound_200_command_tail_is_accepted_without_changing_old_status(self):
        fixture=prior_fixture();raw=m.canonical(fixture);result=valid_prior(raw)
        self.assertEqual(result['status'],'INCOMPLETE')
        self.assertEqual(len(result['commands']),200)
        self.assertEqual(m.canonical(result),raw)
    def test_tampered_raw_receipt_fails_before_parsing(self):
        raw=m.canonical(prior_fixture())
        with patch.object(m,'PRIOR_BYTES',len(raw)),patch.object(m,'PRIOR_SHA',m.sha(raw)),self.assertRaisesRegex(m.Refused,'PRIOR_RECEIPT_PIN_CHANGED'):
            m.validate_prior(raw[:-1]+b' ')
    def test_identity_or_import_or_transport_drift_rejected(self):
        cases=[('operation','different','PRIOR_IDENTITY_CHANGED'),('hostname','ecs-zdz-pro-0015','PRIOR_IDENTITY_CHANGED'),
          ('expected_node_uid','new-node','PRIOR_IDENTITY_CHANGED'),('phase_b','REQUESTED','PRIOR_IMPORT_NOT_COMPLETED'),
          ('transport_pin',{},'PRIOR_TRANSPORT_CHANGED'),('canonical_names_created',[],'PRIOR_REFERENCES_CHANGED')]
        for key,value,error in cases:
            with self.subTest(key=key):
                fixture=prior_fixture();fixture[key]=value
                with self.assertRaisesRegex(m.Refused,error):valid_prior(m.canonical(fixture))
    def test_unverified_archive_rejected(self):
        fixture=prior_fixture();fixture['verified_archive']['all_layer_diff_ids_verified']=False
        with self.assertRaisesRegex(m.Refused,'PRIOR_ARCHIVE_NOT_VERIFIED'):valid_prior(m.canonical(fixture))
    def test_other_stop_or_real_command_failure_rejected(self):
        for mode,error in [('count','PRIOR_STOP_LOCATION_CHANGED'),('failure','PRIOR_COMMAND_FAILURE'),('mutation','PRIOR_MUTATION_SCOPE_CHANGED')]:
            with self.subTest(mode=mode):
                fixture=prior_fixture()
                if mode=='count':fixture['commands'].pop()
                if mode=='failure':fixture['commands'][0]['returncode']=1
                if mode=='mutation':fixture['commands'][-1]['containerd_mutation_requested']=True
                with self.assertRaisesRegex(m.Refused,error):valid_prior(m.canonical(fixture))
    def test_duplicate_or_nonfinite_json_rejected(self):
        for raw,error in [(b'{"a":1,"a":2}','DUPLICATE_JSON_KEY'),(b'{"a":NaN}','NONFINITE_JSON'),(b'[]','JSON_OBJECT_REQUIRED')]:
            with self.subTest(raw=raw),self.assertRaisesRegex(m.Refused,error):m.parse(raw)
    def test_all_mutating_or_broad_commands_rejected_before_subprocess(self):
        reader=object.__new__(m.Reader);reader.commands=[];reader.started=time.monotonic();reader.ctr='ctr';reader.cri='crictl'
        commands=[['images','import','x.tar'],['images','tag','--local',m.TAG,'x'],['images','rm',m.TAG],
          ['snapshots','remove',m.FINAL_CHAIN],['content','delete',m.MANIFEST],['run',m.TAG,'x'],
          ['images','ls'],['content','get','sha256:'+'0'*64],['exec','x']]
        with patch.object(m.subprocess,'run') as run:
            for args in commands:
                with self.subTest(args=args),self.assertRaisesRegex(m.Refused,'READ_ONLY_COMMAND_SCOPE'):
                    reader.call('forbidden',args)
        run.assert_not_called();self.assertEqual(reader.commands,[])
    def test_cri_mutation_or_other_name_rejected(self):
        for args in (['pull',m.TAG],['rmi',m.TAG],['inspecti','--output=json','other']):
            self.assertFalse(m.allowed_command(args,True))
    def test_full_synthetic_success_is_80_readonly_commands(self):
        with CacheFixture() as f:
            reader=FakeReader(f);result=m.verify_cache(reader,prior_fixture())
            self.assertEqual(len(reader.commands),80)
            self.assertLess(len(reader.commands),m.MAX_COMMANDS)
            self.assertTrue(all(not c['mutation_requested'] for c in reader.commands))
            self.assertEqual(result['protected_reference_count'],31)
            self.assertEqual(result['content_and_unpack'],'PASS');self.assertEqual(result['cri_visibility'],'PASS')
            self.assertTrue(result['old_references_preserved']);self.assertTrue(result['stable_readback'])
            self.assertFalse(result['layer_payloads_rehashed_now']);self.assertFalse(result['storage_capacity_rechecked'])
    def test_old_reference_changed_during_check_rejected(self):
        name=next(iter(m.PROTECTED))
        with CacheFixture() as f:
            def change(phase,args,raw):
                return raw.replace(m.PROTECTED[name].encode(),('sha256:'+'0'*64).encode()) if phase=='references_after' and args[-1]=='name=='+name else raw
            with self.assertRaisesRegex(m.Refused,'IMAGE_REFERENCE_CHANGED'):m.verify_cache(FakeReader(f,change),prior_fixture())
    def test_metadata_tampering_rejected(self):
        with CacheFixture() as f:
            def change(phase,args,raw):return raw+b' ' if phase=='manifest_readback' else raw
            with self.assertRaisesRegex(m.Refused,'CACHED_METADATA_DIGEST'):m.verify_cache(FakeReader(f,change),prior_fixture())
    def test_missing_content_or_gc_or_unpack_or_cri_rejected(self):
        cases=[('content_closure','CONTENT_CLOSURE_MISSING'),('manifest_gc','MANIFEST_GC_CLOSURE_MISSING'),
          ('config_gc','CONFIG_SNAPSHOT_GC_MISSING'),('image_content_and_unpack','IMAGE_CONTENT_OR_UNPACK_NOT_READY'),('cri_visibility','CRI_IDENTITY_CHANGED')]
        for phase,error in cases:
            with self.subTest(phase=phase),CacheFixture() as f:
                def change(actual,args,raw):
                    if actual!=phase:return raw
                    if phase=='content_closure':return b''
                    if phase in ('manifest_gc','config_gc'):return raw.replace(b'containerd.io/gc.ref.',b'broken.gc.ref.')
                    if phase=='image_content_and_unpack':return b''
                    return m.canonical({'status':{'id':'wrong','repoTags':[m.TAG],'repoDigests':[m.CANONICAL[1]]}})
                with self.assertRaisesRegex(m.Refused,error):m.verify_cache(FakeReader(f,change),prior_fixture())
    def test_missing_or_wrong_snapshot_parent_or_kind_rejected(self):
        with CacheFixture() as f:
            good=f.response('snapshot_closure',['snapshots'])
            for bad,error in [(b'KEY PARENT KIND\n','SNAPSHOT_CHAIN_MISSING'),(good.replace(b'Committed',b'Active',1),'SNAPSHOT_NOT_COMMITTED'),
                              (good.replace(m.chains()[1]['parent'].encode(),b'wrong-parent',2),'SNAPSHOT_NOT_COMMITTED')]:
                with self.subTest(error=error),self.assertRaisesRegex(m.Refused,error):m.verify_snapshots(bad)
    def test_cached_metadata_changes_at_final_read_rejected(self):
        with CacheFixture() as f:
            def change(phase,args,raw):return raw+b' ' if phase=='stable_config' else raw
            with self.assertRaisesRegex(m.Refused,'CACHE_CHANGED_DURING_READ'):m.verify_cache(FakeReader(f,change),prior_fixture())
    def test_wrong_host_executes_no_commands_and_writes_no_report(self):
        with patch.object(m,'os',SimpleNamespace(name='nt')),patch.object(sys,'argv',['probe','--verify-imported-cache-readonly']),             patch.object(m,'Reader') as reader,patch.object(m,'read_prior') as prior,patch.object(m,'persist_report') as persist,             patch('sys.stdout',new_callable=io.StringIO) as stdout:
            self.assertEqual(m.run(),1)
        reader.assert_not_called();prior.assert_not_called();persist.assert_not_called()
        out=json.loads(stdout.getvalue());self.assertEqual(out['queries'],0)
        self.assertEqual(out['error_code'],'DATAX02_ISOLATED_ROOT_REQUIRED');self.assertFalse(out['report_directory_created'])
    def test_runner_limits_commands_and_bounds_output(self):
        reader=object.__new__(m.Reader);reader.commands=[{}]*m.MAX_COMMANDS;reader.started=time.monotonic();reader.ctr='ctr';reader.cri='crictl'
        with patch.object(m.subprocess,'run') as run,self.assertRaisesRegex(m.Refused,'READ_COMMAND_BOUND'):
            reader.call('read',['content','get',m.MANIFEST])
        run.assert_not_called()
        reader.commands=[]
        def oversized(argv,stdout,stderr,**kwargs):stdout.write(b'xxxxxxxxxx');return subprocess.CompletedProcess(argv,0)
        with patch.object(m.subprocess,'run',side_effect=oversized),self.assertRaisesRegex(m.Refused,'READ_OUTPUT_BOUND'):
            reader.call('read',['content','get',m.MANIFEST],limit=2)
    def test_runner_hides_command_diagnostic(self):
        reader=object.__new__(m.Reader);reader.commands=[];reader.started=time.monotonic();reader.ctr='ctr';reader.cri='crictl'
        def failed(argv,stdout,stderr,**kwargs):stderr.write(b'private-secret');return subprocess.CompletedProcess(argv,1)
        with patch.object(m.subprocess,'run',side_effect=failed),self.assertRaisesRegex(m.Refused,'^READ_COMMAND_FAILED$'):
            reader.call('read',['content','get',m.MANIFEST])
        self.assertNotIn('private-secret',str(reader.commands))
    def test_independent_report_never_overwrites_old_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);old=root/'old-receipt.json';old.write_bytes(b'preserve original')
            class SafeParent:
                def lstat(self):return SimpleNamespace(st_mode=stat.S_IFDIR|0o700,st_uid=0)
                def __truediv__(self,name):return root/name
            out={'operation':'a'*32,'status':'INCOMPLETE','original_receipt_modified':False}
            with patch.object(m,'REPORT_PARENT',SafeParent()),patch.object(m.os,'O_NOFOLLOW',getattr(os,'O_NOFOLLOW',0),create=True):
                result=m.persist_report(out)
                self.assertNotEqual(result['report_path'],str(old))
                self.assertEqual(old.read_bytes(),b'preserve original')
                self.assertEqual(Path(result['report_path']).read_bytes(),m.canonical(dict(out,report_directory_created=True))+b'\n')
                with self.assertRaises(FileExistsError):m.persist_report(out)
            self.assertEqual(old.read_bytes(),b'preserve original')

if __name__=='__main__':unittest.main()
