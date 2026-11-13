"""Plan-only trusted state checks and existing-file nonblocking lock fixtures."""
import asyncio
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

SOURCE=Path(__file__).resolve().parents[1]/'deployment/reclaim-plan-20261112-r1/inspect_reclaim_child.py'
spec=importlib.util.spec_from_file_location('reclaim_child',SOURCE);child=importlib.util.module_from_spec(spec);spec.loader.exec_module(child)
NOW=datetime.datetime(2026,11,12,4,tzinfo=datetime.timezone.utc)

def state(**changes):
    row={'thread_id':'synthetic-thread','user_id':'synthetic-owner','thread_status':'idle','run_count':1,'active_count':0,'unknown_status_count':0,
        'ppt_history_count':0,'stop_reason_count':0,'nonempty_run_metadata_count':0,'unknown_update_count':0,'last_updated':NOW-datetime.timedelta(hours=2)}
    row.update(changes);return row

def pod():
    row=state();name='sandbox-'+child.sha((row['user_id']+':'+row['thread_id']).encode())[:8]
    return {'name':name,'uid':'11111111-1111-1111-1111-111111111111','node':'ecs-zdz-pro-0015','phase':'Running','deleting':False,
        'sandbox_ready':True,'pod_ready':True,'thread_annotation':row['thread_id'],'user_annotation':row['user_id'],
        'userdata_subpath':'deer-flow/users/'+row['user_id']+'/threads/'+row['thread_id']+'/user-data','userdata_mount_name':'user-data','userdata_claim':'deer-flow-userdata-rwx-pvc',
        'cpu_requests':'750m,250m','memory_requests':'1Gi,512Mi'}

class StateTests(unittest.TestCase):
    def test_terminal_age_and_all_active_unknown_wait_ppt_states_excluded(self):
        self.assertEqual(child.run_exclusions(state(),NOW),[])
        for key in ('active_count','unknown_status_count','ppt_history_count','stop_reason_count','nonempty_run_metadata_count','unknown_update_count'):
            self.assertTrue(child.run_exclusions(state(**{key:1}),NOW),key)
        self.assertTrue(child.run_exclusions(state(last_updated=NOW-datetime.timedelta(minutes=29)),NOW))
        self.assertTrue(child.run_exclusions(state(last_updated=NOW+datetime.timedelta(minutes=5)),NOW))
    def test_inventory_excludes_pending_deleting_not_ready_and_unknown_binding(self):
        rows=[pod()];rows[0].update(node='<none>',phase='Pending',deleting=True,pod_ready=False,userdata_subpath=None)
        result,_=child.prepare_inventory({'pods':rows});self.assertEqual(len(result[0][1]['exclusion_reasons']),5)
    def test_inventory_bounds_and_duplicate_identity_refuse(self):
        with self.assertRaises(child.Refused):child.prepare_inventory({'pods':[pod(),pod()]})
        with self.assertRaises(child.Refused):child.prepare_inventory({'pods':[pod()]*257})
    def test_parameterized_queries_have_no_messages_or_events_and_use_statuses(self):
        self.assertIn('unnest($1::text[])',child.RUN_SQL);self.assertIn("r.status IN ('pending','running')",child.RUN_SQL)
        self.assertIn("assistant_id='ppt-master-runner'",child.RUN_SQL)
        for text in ('first_human_message','last_ai_message','run_events','kwargs_json'):self.assertNotIn(text,child.RUN_SQL)
    def test_audit_only_exact_existing_lock_open_exception(self):
        with self.assertRaises(child.Refused):child.audit('open',('unrelated.lock',None,os.O_RDWR))
        if hasattr(os,'O_NOFOLLOW'):
            with patch.object(child,'LOCK_BASENAME','expected.lock'):
                child.audit('open',('expected.lock',None,os.O_RDWR|os.O_NOFOLLOW))
                with self.assertRaises(child.Refused):child.audit('open',('expected.lock',None,os.O_RDWR|os.O_NOFOLLOW|os.O_CREAT))
        for event in ('os.mkdir','os.remove','subprocess.Popen','sqlite3.connect'):
            with self.assertRaises(child.Refused):child.audit(event,())

class FakeDatabaseTests(unittest.TestCase):
    def run_plan(self,changed=False,source_ok=True,index_ok=True):
        snapshots=0;log=[]
        class Tx:
            async def __aenter__(self):pass
            async def __aexit__(self,*args):return False
        class Conn:
            def transaction(self,**kwargs):log.append(('transaction',kwargs));return Tx()
            async def execute(self,sql):log.append(('execute',sql))
            async def fetchval(self,sql):return 'on' if sql.startswith('SHOW') else NOW
            async def fetch(self,sql,*args):
                nonlocal snapshots
                log.append(('fetch',sql,args))
                if sql==child.INDEX_SQL:return [{'indexname':'ix_runs_thread_status','indexdef':'CREATE INDEX ON runs (thread_id,status)'}] if index_ok else [{'indexname':'uq_runs_thread_active','indexdef':"CREATE INDEX ON runs (thread_id) WHERE status IN ('pending','running')"}]
                if sql in (child.DIAG_RUN_SQL,child.DIAG_OCCURRENCE_ID_SQL,child.DIAG_OCCURRENCE_TASK_SQL,child.DIAG_EVENT_SQL):return []
                snapshots+=1;return [state(active_count=1 if changed and snapshots>1 else 0)]
            async def close(self,timeout):pass
        async def connect(*args,**kwargs):return Conn()
        cfg=lambda:types.SimpleNamespace(database=types.SimpleNamespace(backend='postgres',postgres_url='postgresql://user:private-password@db.internal/fake'))
        paths=types.SimpleNamespace(get_paths=lambda:object())
        proof={name:{'matches_expected':source_ok} for name in child.EXPECTED}
        with patch.object(child,'source_proofs',return_value=proof),patch.object(child,'runtime_modules',return_value=(types.SimpleNamespace(connect=connect),cfg,paths)),patch.object(child,'safe_mounts',return_value=(Path('/safe'),{'claim':'data'},Path('deer-flow'))),patch.object(child.socket,'getaddrinfo',return_value=[(2,1,6,'',('192.0.2.1',5432))]),patch.object(child,'map_identity',return_value=(Path('/safe/execution.lock'),Path('/safe/creation.lock'))),patch.object(child,'lock_probe',return_value={'both_locks_available':True,'exclusive_hold_ms':.1}) as probe:
            result=asyncio.run(child.observe({'pods':[pod()],'userdata_mounts':[],'shadow_mount_paths':[]}))
        return result,log,probe.call_count
    def test_plan_is_only_review_metadata_no_identity_or_deletion_authorization(self):
        result,log,calls=self.run_plan();self.assertEqual(result['status'],'SANDBOX_RECLAIM_REVIEW_PLAN_OBSERVED_NO_CHANGE');self.assertEqual(result['candidate_count'],1)
        self.assertTrue(result['no_deletion_authorization']);self.assertTrue(result['unleased_nonrun_activity_not_excluded']);self.assertEqual(calls,1)
        self.assertEqual(result['review_candidates'][0]['eligibility'],'NO_ACTIVE_RUN_AND_LOCKS_AVAILABLE_REQUIRES_REVIEW')
        self.assertEqual(result['fixed_run_diagnostic']['requested_identifier'],child.FIXED_DIAGNOSTIC_ID)
        self.assertEqual(result['fixed_run_diagnostic']['status'],'FIXED_RUN_NOT_FOUND_OR_NOT_LAUNCHED')
        raw=json.dumps(result);self.assertNotIn('synthetic-thread',raw);self.assertNotIn('synthetic-owner',raw);self.assertNotIn('private-password',raw)
        self.assertEqual(len([row for row in log if row[0]=='transaction']),3)
        self.assertIn(('execute',"SET LOCAL lock_timeout='500ms'"),log)
    def test_any_state_change_or_unknown_source_excludes(self):
        result,_,_=self.run_plan(changed=True);self.assertEqual(result['candidate_count'],0)
        result,_,calls=self.run_plan(source_ok=False);self.assertEqual(result['candidate_count'],0);self.assertEqual(calls,0)
    def test_partial_active_index_cannot_authorize_history_aggregation(self):
        result,log,calls=self.run_plan(index_ok=False)
        self.assertEqual(result['status'],'INCOMPLETE');self.assertEqual(result['error_code'],'FULL_THREAD_RUN_INDEX_NOT_CONFIRMED')
        self.assertFalse(any(row[0]=='fetch' and row[1]==child.RUN_SQL for row in log));self.assertEqual(calls,0)

@unittest.skipUnless(os.name=='posix','Actual existing-file flock behavior')
class LockAndMappingTests(unittest.TestCase):
    def setUp(self):self.temporary=tempfile.TemporaryDirectory(prefix='reclaim-plan-');self.base=Path(self.temporary.name)
    def tearDown(self):self.temporary.cleanup()
    def files(self):
        execution=self.base/'execution.lock';creation=self.base/'creation.lock';execution.touch();creation.touch();return execution,creation
    def test_both_locks_available_without_changes_and_released_afterward(self):
        execution,creation=self.files();before=[path.stat() for path in (execution,creation)]
        result=child.lock_probe(execution,creation);self.assertTrue(result['both_locks_available'])
        for path,old in zip((execution,creation),before):
            new=path.stat();self.assertEqual((new.st_size,new.st_mtime_ns,new.st_ctime_ns),(old.st_size,old.st_mtime_ns,old.st_ctime_ns))
            with path.open('r+b') as stream:child.fcntl.flock(stream,child.fcntl.LOCK_EX|child.fcntl.LOCK_NB);child.fcntl.flock(stream,child.fcntl.LOCK_UN)
    def test_missing_lock_never_created(self):
        execution,creation=self.files();creation.unlink();result=child.lock_probe(execution,creation)
        self.assertFalse(result['both_locks_available']);self.assertFalse(creation.exists());self.assertIn('MISSING',result['exclusion_reason'])
    def test_busy_either_lock_excludes_and_releases_other(self):
        execution,creation=self.files()
        with creation.open('r+b') as stream:
            child.fcntl.flock(stream,child.fcntl.LOCK_SH|child.fcntl.LOCK_NB)
            result=child.lock_probe(execution,creation);self.assertFalse(result['both_locks_available']);self.assertEqual(result['exclusion_reason'],'CREATION_LOCK_IN_USE')
            with execution.open('r+b') as other:child.fcntl.flock(other,child.fcntl.LOCK_EX|child.fcntl.LOCK_NB);child.fcntl.flock(other,child.fcntl.LOCK_UN)
    def test_symlink_lock_excludes(self):
        execution,creation=self.files();creation.unlink();creation.symlink_to(execution)
        self.assertFalse(child.lock_probe(execution,creation)['both_locks_available'])
    def test_identity_requires_trusted_owner_pvc_prefix_and_user_annotation(self):
        resolver=types.SimpleNamespace(thread_dir=lambda thread,user_id:self.base/'users'/user_id/'threads'/thread)
        row=state();item=pod();owning={'claim':'deer-flow-userdata-rwx-pvc'}
        execution,creation=child.map_identity(item,row,resolver,self.base,owning,Path('deer-flow'))
        self.assertEqual(creation.name,item['name'].removeprefix('sandbox-')+'.lock');self.assertEqual(execution.parent,self.base/'.sandbox-execution-leases')
        for key,value in [('userdata_claim','other-pvc'),('user_annotation','different-owner'),('name','sandbox-ffffffff')]:
            wrong=dict(item,**{key:value})
            with self.assertRaises(child.Refused):child.map_identity(wrong,row,resolver,self.base,owning,Path('deer-flow'))
    def test_same_pvc_nested_shadow_is_rejected(self):
        resolver=types.SimpleNamespace(base_dir=self.base)
        mounts=[{'mount_path':str(self.base),'claim':'data','subpath':'deer-flow'},{'mount_path':str(self.base/'users'),'claim':'data','subpath':'other'}]
        with self.assertRaisesRegex(child.Refused,'SHADOW'):child.safe_mounts({'userdata_mounts':mounts,'shadow_mount_paths':[]},resolver)
    def test_installed_audit_allows_only_the_existing_lock_probe(self):
        execution,creation=self.files();source=SOURCE.read_text(encoding='utf-8')
        program="import sys,json;ns={'__name__':'audited_probe_fixture'};exec("+repr(source)+",ns);sys.addaudithook(ns['audit']);result=ns['lock_probe']("+repr(str(execution))+","+repr(str(creation))+");assert result['both_locks_available'];print(json.dumps(result))"
        result=subprocess.run([sys.executable,'-I','-B','-c',program],capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'));self.assertTrue(json.loads(result.stdout)['both_locks_available'])

if __name__=='__main__':unittest.main()
