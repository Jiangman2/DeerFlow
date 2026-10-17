import ast
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import review_activation as r


def old(pin=False):
    return dict(image=r.io.IMAGE,config_digest=r.io.CONFIG,manifest_digest=r.OLD_MANIFEST,
                content_and_unpack='PASS',pinned=pin)


def candidate():
    return dict(image=r.cli.IMAGE,config_id=r.cli.CONFIG,manifest_digest=r.cli.MANIFEST,
                content_and_unpack='PASS',pinned=True)


def receipt():
    return dict(production_node_preload='PASS',production_writes=0,
                production_workloads_mutated=False,candidate=candidate())


def policies():
    return dict(read_only='on',policies=[dict(table_name='knowledge_bases',enabled='O',trigger_type=7,
        function_schema='public',function_name='deerflow_apply_enterprise_knowledge_base_defaults',
        trigger_name='defaults',identity_arguments='',body="x='deerflow-dashscope-vlm'; SECRET_TEST",definition='SECRET_TEST',
        security_definer=False,settings=None)],agent_refs=[dict(model_class='LEGACY',total=4,builtin=3,image_enabled=4)],
        kb_multimodal_refs=dict(total=4,legacy=4,proposed=0,other=0),proposed_id_rows=0)


def deployment():
    return dict(metadata=dict(uid='44aca5b9-8fc1-44d6-af61-37c6d62723d5',resourceVersion='123'),
        spec=dict(replicas=1,strategy=dict(type='Recreate'),template=dict(spec=dict(containers=[
            dict(name='app',image=r.io.IMAGE,env=[dict(name='SECRET',value='DO_NOT_PRINT')])]))))


class PolicyTests(unittest.TestCase):
    def test_projection_never_prints_private_source_or_unrelated_ids(self):
        row=policies(); row['api_key']='SHOULD_NOT_RETURN'
        row['agent_refs'][0]['name']='PRIVATE_AGENT'
        got=r.clean_policies(row); text=json.dumps(got)
        self.assertEqual(got['policies'][0]['legacy_literal_count'],1)
        for private in ('SECRET_TEST','SHOULD_NOT_RETURN','PRIVATE_AGENT'):
            self.assertNotIn(private,text)

    def test_unknown_function_redacted(self):
        row=policies(); row['policies'][0]['function_name']='private_name'
        got=r.clean_policies(row)
        self.assertEqual(got['policies'][0]['function'],'OTHER_REQUIRES_REVIEW')
        self.assertNotIn('private_name',json.dumps(got))

    def test_policy_fail_closed_shapes(self):
        for key,value in [('read_only','off'),('policies',[policies()['policies'][0]]*65),
                          ('agent_refs',[dict(model_class='SECRET')]),('proposed_id_rows',True)]:
            row=policies(); row[key]=value
            with self.subTest(key=key),self.assertRaises(r.io.CheckFailure): r.clean_policies(row)

    def test_sql_is_readonly_bounded_no_model_keys_or_documents(self):
        self.assertTrue(r.POLICY_SQL.startswith('BEGIN READ ONLY;'))
        self.assertTrue(r.POLICY_SQL.endswith('ROLLBACK;'))
        for x in ('api_key','parameters','public.knowledges','public.chunks','INSERT ','DELETE ','UPDATE '):
            self.assertNotIn(x,r.POLICY_SQL)
        self.assertIn("statement_timeout='5s'",r.POLICY_SQL)
        self.assertIn('LIMIT 65',r.POLICY_SQL)

    def test_image_patch_exact_image_only_and_cas(self):
        dep=deployment(); original=copy.deepcopy(dep)
        patch_ops,expected=r.image_patch(dep)
        self.assertEqual(dep,original)
        self.assertEqual([x['op'] for x in patch_ops],['test','test','test','replace'])
        self.assertEqual(patch_ops[-1]['path'],'/spec/template/spec/containers/0/image')
        expected['template']['spec']['containers'][0]['image']=r.io.IMAGE
        self.assertEqual(expected,dep['spec'])

    def test_image_patch_rejects_wrong_baseline(self):
        for kind in ('replicas','strategy','image','uid','duplicate'):
            dep=deployment()
            if kind=='replicas': dep['spec']['replicas']=2
            elif kind=='strategy': dep['spec']['strategy']['type']='RollingUpdate'
            elif kind=='image': dep['spec']['template']['spec']['containers'][0]['image']='other'
            elif kind=='uid': dep['metadata']['uid']='other'
            else: dep['spec']['template']['spec']['containers']*=2
            with self.subTest(kind=kind),self.assertRaises(r.io.CheckFailure): r.image_patch(dep)

    def test_receipt_checks(self):
        self.assertEqual(r.check_candidate_receipt(receipt()),candidate())
        for key,value in [('manifest_digest','other'),('pinned',False),('config_id','other')]:
            row=receipt(); row['candidate'][key]=value
            with self.assertRaises(r.io.CheckFailure): r.check_candidate_receipt(row)

    def test_python37_probe_syntax_no_model_execution(self):
        ast.parse(r.CONFIG_PROBE,feature_version=(3,7))
        for token in ('requests','subprocess','environ','API_KEY','urllib','yaml.load'):
            self.assertNotIn(token,r.CONFIG_PROBE)


class PrivateAndPinTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='/root'); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'attempt'; self.root.mkdir(mode=0o700)
        changer=patch.object(r,'ROOT',self.root); changer.start(); self.addCleanup(changer.stop)

    def test_private_exclusive_records(self):
        r.save('x.json',dict(test=True))
        self.assertEqual((self.root/'x.json').stat().st_mode&0o777,0o600)
        with self.assertRaises(FileExistsError): r.save('x.json',{})
        with self.assertRaises(r.io.CheckFailure): r.save('../x',{})

    def test_unsafe_directory_is_not_repaired(self):
        self.root.chmod(0o777)
        with self.assertRaises(r.io.CheckFailure): r.save('x.json',{})
        self.assertEqual(self.root.stat().st_mode&0o777,0o777)

    def pin(self,initial=False,command_error=None):
        stack=contextlib.ExitStack(); self.addCleanup(stack.close)
        stack.enter_context(patch.object(r.recovery,'read',return_value=(receipt(),'abc')))
        stack.enter_context(patch.object(r.cli,'image_check',return_value=candidate()))
        stack.enter_context(patch.object(r,'exact_old',side_effect=[old(initial),old(initial),old(True)] if not initial else [old(True),old(True)]))
        command=stack.enter_context(patch.object(r.cli,'command',side_effect=command_error))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        return command

    def test_label_once_after_journal_and_no_import(self):
        command=self.pin()
        def apply(argv,**kw):
            self.assertTrue((self.root/'PIN-REQUESTED.json').exists())
            self.assertEqual(argv,r.cli.CTR+['images','label',r.io.IMAGE,r.cli.PIN])
        command.side_effect=apply
        result=r.pin_once()
        self.assertEqual(result['rollback_image_protection'],'PASS')
        self.assertEqual(command.call_count,1)
        self.assertTrue((self.root/'PIN-ACK.json').exists())

    def test_already_pinned_no_write(self):
        command=self.pin(initial=True)
        result=r.pin_once()
        command.assert_not_called(); self.assertTrue(result['already_pinned'])
        self.assertFalse((self.root/'PIN-REQUESTED.json').exists())

    def test_uncertain_pin_not_repeated(self):
        command=self.pin(command_error=RuntimeError('timeout'))
        with self.assertRaises(RuntimeError): r.pin_once()
        self.assertEqual(command.call_count,1)
        self.assertTrue((self.root/'PIN-REQUESTED.json').exists())
        self.assertFalse((self.root/'PIN-ACK.json').exists())

    def test_exact_old_identity_blocker(self):
        with patch.object(r.node,'rollback',return_value=dict(old(),manifest_digest='wrong')):
            with self.assertRaisesRegex(r.io.CheckFailure,'identity'): r.exact_old()

    def test_readonly_status_cannot_retry_label(self):
        with patch.object(r,'exact_old',return_value=old(True)),patch.object(r.cli,'image_check',return_value=candidate()), \
             patch.object(r.cli,'command') as command,patch.object(r,'save') as save,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(r.status('datax-03'),0)
        command.assert_not_called(); save.assert_not_called()

    def test_repeat_prepare_rejected_before_actions(self):
        with patch.object(r,'SOURCE',self.root),patch.object(r.socket,'gethostname',return_value='datax-01'), \
             patch.object(r.platform,'machine',return_value='aarch64'),patch.object(r,'source_review') as action:
            with self.assertRaisesRegex(r.io.CheckFailure,'previous_attempt'): r.main(['--prepare-once'])
        action.assert_not_called()


class MountTests(unittest.TestCase):
    def fixture(self):
        names=['config.yaml','builtin_models.yaml','builtin_agents.yaml']
        pod=dict(metadata=dict(name='pod'),spec=dict(containers=[dict(name='app',volumeMounts=[
            dict(name='config',mountPath='/app/config/'+name,subPath=name,readOnly=True) for name in names])],
            volumes=[dict(name='config',configMap=dict(name='cm'))]))
        cm=dict(metadata=dict(uid='uid'),data={name:r.LEGACY for name in names})
        runtime={'/app/config/'+name:dict(sha256=r.sha(r.LEGACY.encode()),bytes=len(r.LEGACY),
                                        legacy_id_count=1,proposed_id_count=0) for name in names}
        a=SimpleNamespace(native=SimpleNamespace(get=Mock(return_value=cm),call=Mock(return_value=(0,json.dumps(runtime)))))
        return pod,cm,a

    def test_live_mount_configmap_exact_bytes(self):
        pod,cm,a=self.fixture()
        with patch.object(r,'save'):
            summaries,maps=r.mounted_configs(a,pod)
        self.assertTrue(all(x['mounted_matches_configmap'] for x in summaries))
        self.assertEqual(a.native.get.call_count,1)
        self.assertEqual(maps,{'cm':cm})

    def test_stale_subpath_detected_not_silently_accepted(self):
        pod,cm,a=self.fixture(); cm['data']['builtin_agents.yaml']='different'
        with patch.object(r,'save'):
            summaries,_=r.mounted_configs(a,pod)
        self.assertFalse(summaries[2]['mounted_matches_configmap'])

    def test_secret_volume_rejected(self):
        pod,cm,a=self.fixture(); pod['spec']['volumes'][0]=dict(name='config',secret=dict(secretName='private'))
        with patch.object(r,'save'),self.assertRaises(r.io.CheckFailure): r.mounted_configs(a,pod)
        a.native.call.assert_not_called()


class SourceFlowTests(unittest.TestCase):
    def setup_flow(self, mutate_dryrun=False, wrong_env=False, model_exists=False):
        import recover_enrichment_gateway as frozen
        stack=contextlib.ExitStack(); self.addCleanup(stack.close)
        temp=stack.enter_context(tempfile.TemporaryDirectory(dir='/root'))
        root=Path(temp)/'attempt'; root.mkdir(mode=0o700)
        stack.enter_context(patch.object(r,'ROOT',root))
        dep=deployment(); pod,cm,_=MountTests().fixture()
        pod['metadata'].update(uid=r.io.POD_UID)
        env={'WEKNORA_ASYNQ_'+k+'_CONCURRENCY':str(v) for k,v in r.review.EXPECTED_POOLS.items()}
        env.update(WEKNORA_WIKI_ASYNQ_CONCURRENCY='12',WEKNORA_MODEL_MAX_CONCURRENCY='64')
        if wrong_env: env['WEKNORA_WIKI_ASYNQ_CONCURRENCY']='88'
        pod['spec']['containers'][0]['env']=[dict(name=k,value=v) for k,v in env.items()]
        baseline=dict(node='datax-03',pod='pod')
        guard=SimpleNamespace(live=Mock(return_value=baseline))
        q=SimpleNamespace(package=lambda:(None,guard,{}))
        pg=dict(metadata=dict(name='pg',uid='pg-uid'))
        calls=[]
        def get(kind,name,ns):
            return copy.deepcopy({('pod','pod'):pod,('pod','pg'):pg,('deployment','weknora-app'):dep,('configmap','cm'):cm}[(kind,name)])
        def call(args,**kw):
            calls.append((args,kw))
            if args[0]=='patch':
                self.assertIn('--dry-run=server',args)
                _,expected=r.image_patch(dep)
                if mutate_dryrun: expected['replicas']=88
                return 0,json.dumps(dict(spec=expected))
            if args[-1]==r.POLICY_SQL:
                p=policies(); p['proposed_id_rows']=int(model_exists)
                return 0,json.dumps(p)
            if args[-1]==r.review.SQL:
                return 0,json.dumps(dict(read_only='on',schema=[dict(version=90,dirty=False)],
                    models=[],legacy_inline_kbs=0,unresolved_model_refs=0))
            if args[-1]==r.CONFIG_PROBE:
                return 0,json.dumps({'/app/config/'+name:dict(sha256=r.sha(text.encode()),bytes=len(text),
                    legacy_id_count=1,proposed_id_count=0) for name,text in cm['data'].items()})
            raise AssertionError('unexpected command')
        a=SimpleNamespace(native=SimpleNamespace(initialize=lambda:(None,q),get=get,call=call))
        stack.enter_context(patch.object(frozen,'load_original',return_value=a))
        stack.enter_context(patch.object(r.review,'accepted',return_value={}))
        stack.enter_context(patch.object(r.review,'db_binding',return_value=(pg,'pg')))
        stack.enter_context(patch.object(r.recovery,'read',side_effect=[
            (dict(production_preload_recovery='PASS',production_writes=0),r.SOURCE_PASS),(receipt(),'abc')]))
        stream=io.StringIO(); stack.enter_context(contextlib.redirect_stdout(stream))
        return root,calls,stream

    def test_complete_readonly_source_flow_no_live_patch(self):
        root,calls,stream=self.setup_flow()
        result=r.source_review()
        self.assertEqual(result['activation_review'],'PASS')
        self.assertEqual(result['blockers'],[])
        self.assertEqual(len([x for x,kw in calls if x[0]=='patch']),1)
        self.assertNotIn('SECRET_TEST',stream.getvalue())
        self.assertNotIn('DO_NOT_PRINT',stream.getvalue())
        self.assertTrue((root/'POLICIES.private.json').exists())

    def test_dryrun_other_mutation_rejected(self):
        self.setup_flow(mutate_dryrun=True)
        with self.assertRaisesRegex(r.io.CheckFailure,'dryrun_changed_other_spec_fields'): r.source_review()

    def test_wrong_slots_stop_before_db(self):
        _,calls,_=self.setup_flow(wrong_env=True)
        with self.assertRaisesRegex(r.io.CheckFailure,'56_slot'): r.source_review()
        self.assertEqual(calls,[])

    def test_existing_glm_id_reported_blocker_not_overwritten(self):
        _,calls,_=self.setup_flow(model_exists=True)
        result=r.source_review()
        self.assertEqual(result['blockers'],['PROPOSED_MODEL_ID_ALREADY_EXISTS_REQUIRES_REVIEW'])
        self.assertTrue(all(x[0]=='exec' or '--dry-run=server' in x for x,kw in calls))


if __name__=='__main__': unittest.main()
