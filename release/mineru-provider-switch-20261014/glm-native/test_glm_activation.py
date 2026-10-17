import ast
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import activate_glm56 as a
import glm_activation_plan as r
import glm_document_migration as d
import observe_pipeline as obs
import tune_weknora88 as e


def catalog():
    return dict(columns=dict(id='varchar',tenant_id='int4',metadata='jsonb',updated_at='timestamptz',deleted_at='timestamptz'),
        ordinary_table=True,id_unique=True,triggers=[],function_count=0)


def batch(): return [dict(id='synthetic-document',tenant=10000,before_sha='a'*64,after_sha='b'*64)]


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.stack=contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir='/root')))
        self.stack.enter_context(patch.object(a,'ROOT',self.root))
        self.stack.enter_context(patch.object(a.sw,'obs',obs))
        self.stack.enter_context(patch.object(a.sw,'e',e))
        self.stack.enter_context(patch.object(a.sw,'a',SimpleNamespace(native=SimpleNamespace(PHASE='test'))))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def mock(self,obj,name,**kw): return self.stack.enter_context(patch.object(obj,name,**kw))

    def test_atomic_journal_and_same_reuse(self):
        a.save('snapshot.json',{'stable':True}); a.save_same('snapshot.json',{'stable':True})
        self.assertEqual(a.read('snapshot.json'),{'stable':True})
        self.assertEqual((self.root/'snapshot.json').stat().st_mode&0o777,0o600)
        with self.assertRaises(FileExistsError): a.save('snapshot.json',{'stable':False})
        with self.assertRaises(Exception): a.save_same('snapshot.json',{'stable':False})
        self.assertEqual(list(self.root.glob('.receipt-*')),[])

    def test_interruption_before_link_leaves_no_partial_marker(self):
        self.mock(a.os,'link',side_effect=KeyboardInterrupt)
        with self.assertRaises(KeyboardInterrupt): a.save('request.json',{'request':1})
        self.assertFalse((self.root/'request.json').exists())
        self.assertEqual(list(self.root.iterdir()),[])

    def test_unsafe_private_root_not_repaired(self):
        self.root.chmod(0o777)
        with self.assertRaises(Exception): a.save('x.json',{})
        self.assertEqual(self.root.stat().st_mode&0o777,0o777)
        self.root.chmod(0o700)

    def test_journal_rejects_traversal(self):
        for name in ('../x','..','a/b'):
            with self.subTest(name=name),self.assertRaises(Exception): a.save(name,{})

    def test_python37_grammar(self):
        # Packaged modules have virtual filenames, checked by the packager.
        for module in (a,r,d):
            source=Path(__file__).parent/(module.__name__+'.py')
            ast.parse(source.read_text(),feature_version=(3,7))

    def test_exact_config_edits_and_config_only_patch(self):
        repo=Path(__file__).resolve().parents[3]
        data={'config.yaml':'unchanged: true\n',
              'builtin_agents.yaml':(repo/'k8s/weknora/builtin_agents.yaml').read_text(),
              'builtin_models.yaml':(repo/'k8s/enterprise/builtin-models-internal-only.yaml').read_text()}
        cm=dict(metadata=dict(name='weknora-config',uid='9e1b3b93-4a53-49f2-8ec1-177761df46b3'),data=data)
        self.mock(a.sw,'CONFIG_HASHES',new={k:r.sha(v) for k,v in data.items()})
        target=r.config_plan(cm)
        self.assertEqual(target['data']['config.yaml'],data['config.yaml'])
        self.assertEqual(target['data']['builtin_agents.yaml'].count(r.NEW),5)
        self.assertEqual(target['data']['builtin_agents.yaml'].replace(r.NEW,r.OLD),data['builtin_agents.yaml'])
        self.assertEqual(target['data']['builtin_models.yaml'].count('is_default: true'),4)
        self.assertNotIn(r.NEW,target['data']['builtin_models.yaml'])
        mounts=[dict(name='config',readOnly=True,subPath=k,mountPath='/app/config/'+k) for k in data]
        dep=dict(metadata=dict(uid=e.process.DEP_UID,resourceVersion='17'),spec=dict(replicas=1,
            strategy=dict(type='Recreate'),template=dict(spec=dict(volumes=[dict(name='config',configMap=dict(name='weknora-config'))],
                containers=[dict(name='app',image=a.sw.image.IMAGE,volumeMounts=mounts,env=[dict(name='AUTO_MIGRATE',value='false')])]))))
        new=r.spec_plan(dep['spec']); ops=r.patch_ops(dep,dep['spec'],new)
        self.assertEqual([x['op'] for x in ops],['test','test','test','replace'])
        self.assertEqual(ops[-1]['path'],'/spec/template/spec/volumes')
        new['template']['spec']['volumes'][0]['configMap']['name']='weknora-config'
        self.assertEqual(new,dep['spec'])

    def test_config_change_fails_before_dryrun(self):
        with self.assertRaises(Exception): r.config_plan(dict(metadata={'name':'other'}))

    def test_document_schema_and_guard_drift(self):
        before=catalog(); after=d.installed_catalog(before)
        self.assertEqual(d.check_catalog(before,False),before)
        self.assertEqual(d.check_catalog(after,True),after)
        for key,value in [('columns',dict(before['columns'],metadata='text')),('ordinary_table',False),('id_unique',False),('function_count',1)]:
            with self.subTest(key=key),self.assertRaises(Exception): d.check_catalog(dict(before,**{key:value}),False)
        for key,value in [('definition',d.TRIGGER_DEFINITION.replace(' EXECUTE',' WHEN (false) EXECUTE')),('enabled','D'),('security_definer',True),('settings',None)]:
            changed=copy.deepcopy(after); changed['triggers'][0][key]=value
            with self.subTest(key=key),self.assertRaises(Exception): d.check_catalog(changed,True)

    def test_complete_predecessor_trigger_definition(self):
        rows=[]
        for (table,fn),name in r.TRIGGERS.items():
            typ=23 if table in ('models','custom_agents') else 7
            definition='CREATE TRIGGER '+name+' BEFORE INSERT'+(' OR UPDATE' if typ==23 else '')+' ON public.'+table+' FOR EACH ROW EXECUTE FUNCTION '+fn+'()'
            rows.append(dict(table_name=table,function_name=fn,trigger_name=name,trigger_type=typ,trigger_definition=definition))
        r.check_triggers(rows)
        for change in (' WHEN (false)',' OF metadata',' DEFERRABLE'):
            modified=copy.deepcopy(rows); modified[0]['trigger_definition']+=change
            with self.subTest(change=change),self.assertRaises(Exception): r.check_triggers(modified)

    def test_batch_identity_and_bound(self):
        d.validate_batch(batch())
        for value in ([],batch()*501,batch()*2,[dict(batch()[0],tenant=True)],[dict(batch()[0],before_sha='x')],
                      [dict(batch()[0],id='\x00')],[dict(batch()[0],extra='content')]):
            with self.subTest(value=str(value)[:30]),self.assertRaises(Exception): d.validate_batch(value)

    def test_reconcile_is_exact_not_just_model_id(self):
        self.assertEqual(d.classify_state(dict(rows=1,before=1,after=0),batch()),'BEFORE')
        self.assertEqual(d.classify_state(dict(rows=1,before=0,after=1),batch()),'AFTER')
        for value in (dict(rows=1,before=0,after=0),dict(rows=0,before=0,after=0),dict(rows=1,before=1,after=1)):
            with self.assertRaises(Exception): d.classify_state(value,batch())

    def test_no_document_contents_in_sql_projection(self):
        self.assertNotIn('api_key',d.NEXT_SQL)
        self.assertNotIn("'metadata',metadata",d.NEXT_SQL)
        self.assertIn('sha256',d.NEXT_SQL)
        self.assertIn('LIMIT 500',d.NEXT_SQL)
        text=d.batch_sql(batch(),d.installed_catalog(catalog()))
        self.assertIn('COMMIT;',text); self.assertIn('NOWAIT',text)
        self.assertNotIn('SET parse_status',text); self.assertNotIn('SET updated_at',text)
        self.assertNotIn('DELETE ',text); self.assertNotIn('chunks',text)
        self.assertIn('document_batch_postimage_changed',text)

    def test_snapshot_interruption_reuses_existing_before(self):
        self.mock(a,'phase'); self.mock(a,'fresh_original',return_value=({},{}))
        self.mock(a,'sql_read',side_effect=[dict(inline=0,legacy=23964),[]])
        self.mock(r,'activation_sql',return_value='SYNTHETIC-SQL')
        self.mock(a,'sql_write',return_value={'glm_default_commit':True})
        self.mock(a,'database_state',return_value='GLM')
        a.save('REFERENCES-BEFORE.private.json',[])
        a.commit_defaults(dict(policies_before=[],model={},document_catalog_before=catalog()))
        self.assertTrue((self.root/'DB-ACK.json').exists())

    def test_capacity_database_revalidated_before_commit(self):
        # Verify the live function stops at the persisted-settings check, before
        # it can consider readiness or return a patchable Deployment.
        p={'original':{},'old_identity':['bound'],'original_cm':{}}
        self.mock(e,'deployment',return_value={}); self.mock(e,'app_for',return_value={})
        self.mock(obs,'identity',return_value=['bound']); self.mock(e,'runtime_env'); self.mock(a,'mounted')
        self.mock(e,'database',side_effect=RuntimeError('persisted_settings_changed_requires_review'))
        later=self.mock(a,'database_state')
        with self.assertRaisesRegex(RuntimeError,'persisted_settings'): a.fresh_original(p)
        later.assert_not_called()

    def migration_mocks(self):
        self.mock(a,'phase'); self.mock(a,'fresh_original',return_value=({},{}))
        return dict(document_catalog_after=d.installed_catalog(catalog()))

    def test_one_batch_changes_only_bound_rows(self):
        p=self.migration_mocks()
        self.mock(a,'sql_read',side_effect=[batch(),dict(rows=1,before=1,after=0),dict(rows=1,before=0,after=1),[],dict(legacy=0,inline=0)])
        write=self.mock(a,'sql_write',return_value={'document_batch_committed':1}); report={}
        a.migrate_documents(p,report,True)
        self.assertEqual(write.call_count,1); self.assertEqual(report['document_rows_migrated'],1)
        self.assertTrue((self.root/'DOCUMENTS-COMPLETE.json').exists())

    def existing_batch(self,p):
        a.save('DOC-0001-PLAN.private.json',batch())
        binding=dict(rows=1,plan_sha256=a.sw.recovery.read(self.root/'DOC-0001-PLAN.private.json')[1],
            sql_sha256=r.sha(d.batch_sql(batch(),p['document_catalog_after'])))
        a.save('DOC-0001-REQUESTED.json',binding); return binding

    def test_lost_batch_commit_ack_reconciled_without_mutation(self):
        p=self.migration_mocks(); self.existing_batch(p)
        self.mock(a,'sql_read',side_effect=[dict(rows=1,before=0,after=1),dict(rows=1,before=0,after=1),[],dict(legacy=0,inline=0)])
        write=self.mock(a,'sql_write'); a.migrate_documents(p,{},False)
        write.assert_not_called(); self.assertTrue((self.root/'DOC-0001-ACK.json').exists())

    def test_uncertain_batch_never_automatically_retried(self):
        p=self.migration_mocks(); self.existing_batch(p)
        self.mock(a,'sql_read',return_value=dict(rows=1,before=1,after=0)); write=self.mock(a,'sql_write')
        with self.assertRaises(Exception): a.migrate_documents(p,{},True)
        write.assert_not_called()

    def test_manual_reconcile_verified_before_retries_once(self):
        p=self.migration_mocks(); self.existing_batch(p)
        self.mock(a,'sql_read',side_effect=[dict(rows=1,before=1,after=0),dict(rows=1,before=0,after=1),[],dict(legacy=0,inline=0)])
        write=self.mock(a,'sql_write',return_value={'document_batch_committed':1})
        a.migrate_documents(p,{},False)
        self.assertEqual(write.call_count,1); self.assertTrue((self.root/'DOC-0001-MANUAL-RETRY.json').exists())

    def test_manual_reconcile_cannot_repeat_uncertain_retry(self):
        p=self.migration_mocks(); self.existing_batch(p); a.save('DOC-0001-MANUAL-RETRY.json',{})
        self.mock(a,'sql_read',return_value=dict(rows=1,before=1,after=0)); write=self.mock(a,'sql_write')
        with self.assertRaises(Exception): a.migrate_documents(p,{},False)
        write.assert_not_called()

    def test_unrequested_after_not_claimed_as_our_write(self):
        p=self.migration_mocks(); a.save('DOC-0001-PLAN.private.json',batch())
        self.mock(a,'sql_read',return_value=dict(rows=1,before=0,after=1)); write=self.mock(a,'sql_write')
        with self.assertRaises(Exception): a.migrate_documents(p,{},True)
        write.assert_not_called(); self.assertFalse((self.root/'DOC-0001-ACK.json').exists())

    def test_inline_reference_blocks_completion(self):
        p=self.migration_mocks(); self.mock(a,'sql_read',side_effect=[[],dict(legacy=0,inline=1)])
        with self.assertRaises(Exception): a.migrate_documents(p,{},True)
        self.assertFalse((self.root/'DOCUMENTS-COMPLETE.json').exists())

    def test_recorded_patch_cannot_be_replayed(self):
        a.save('PATCH-REQUESTED.json',{})
        self.mock(a,'ensure_config'); self.mock(e,'deployment',return_value={'spec':{'candidate':True}})
        accept=self.mock(a,'accept'); write=self.mock(a,'deployment_patch'); signal=self.mock(a,'signal_once')
        a.advance({'candidate':{'candidate':True}},{},False)
        accept.assert_called_once(); write.assert_not_called(); signal.assert_not_called()

    def test_uncertain_patch_with_original_spec_stops(self):
        a.save('PATCH-REQUESTED.json',{}); self.mock(a,'ensure_config')
        self.mock(e,'deployment',return_value={'spec':{'original':True}}); write=self.mock(a,'deployment_patch')
        with self.assertRaises(Exception): a.advance({'candidate':{}},{},False)
        write.assert_not_called()

    def test_forward_order_and_exactly_one_signal_and_patch(self):
        order=[]; plan=dict(candidate={'new':True},original={'old':True},configmap={},old_identity=['uid'],process={})
        self.mock(a,'ensure_config',side_effect=lambda *_:order.append('config'))
        self.mock(a,'signal_once',side_effect=lambda _:order.append('signal'))
        self.mock(a.sw,'wait_drain',side_effect=lambda *_:order.append('drain'))
        self.mock(a,'commit_defaults',side_effect=lambda _:order.append('defaults'))
        self.mock(a,'migrate_documents',side_effect=lambda *_:order.append('documents'))
        self.mock(a,'fresh_original',return_value=({},{})); self.mock(a,'config_object')
        a.sw.a.native.get=Mock(return_value={}); a.save('CONFIG-ACK.json',dict(uid='cm'))
        self.mock(a,'deployment_patch',side_effect=lambda dep,plan,dry:(order.append('dryrun' if dry else 'patch') or
            dict(metadata={'uid':e.process.DEP_UID,'generation':99})))
        self.mock(a,'accept',side_effect=lambda *_:order.append('accept'))
        a.advance(plan,{},True)
        self.assertEqual(order,['config','signal','drain','defaults','documents','dryrun','patch','accept'])

    def test_migration_failure_never_restarts_app(self):
        plan=dict(candidate={},original={})
        self.mock(a,'ensure_config'); self.mock(a,'signal_once'); self.mock(a.sw,'wait_drain'); self.mock(a,'commit_defaults')
        self.mock(a,'migrate_documents',side_effect=RuntimeError('batch_failed'))
        write=self.mock(a,'deployment_patch')
        with self.assertRaisesRegex(RuntimeError,'batch_failed'): a.advance(plan,{},True)
        write.assert_not_called(); self.assertFalse((self.root/'PATCH-REQUESTED.json').exists())


if __name__=='__main__': unittest.main()
