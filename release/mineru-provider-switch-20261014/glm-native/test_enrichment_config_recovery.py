import copy
import hashlib
import json
from unittest.mock import Mock, patch
import unittest

import accept_enrichment as a
import recover_enrichment_config as r
from fixture_config_fix import corrected_config
from gateway_enrichment_baseline import GatewayBaseline
import test_enrichment_startup_diagnostic as base


class ConfigTests(unittest.TestCase):
    def test_only_pointer_leaves_changed_and_inputs_unchanged(self):
        before=a.clean_config(); original=copy.deepcopy(before); fixed=corrected_config(before)
        self.assertEqual(before,original)
        self.assertEqual(fixed['extract']['extract_entity'],{'description':''})
        self.assertEqual(fixed['extract']['extract_graph'],{'description':''})
        self.assertEqual(fixed['extract']['fabri_text'],{'with_tag':'','with_no_tag':''})
        self.assertEqual(fixed['conversation']['summary'],{'max_input_chars':16384})
        fixed['extract']=copy.deepcopy(before['extract']); fixed['conversation']['summary']={}
        self.assertEqual(fixed,before)

    def test_unknown_old_config_shape_rejected(self):
        for key in ('extract','summary'):
            cfg=a.clean_config()
            if key=='extract': cfg['extract']['new']={}
            else: cfg['conversation']['summary']={'max_tokens':200}
            with self.assertRaisesRegex(RuntimeError,'shape_changed'): corrected_config(cfg)

    def test_no_models_or_extra_service_endpoints(self):
        cfg=corrected_config(a.clean_config()); text=json.dumps(cfg)
        self.assertEqual(cfg['models'],[]); self.assertFalse(cfg['oidc_auth']['enable'])
        self.assertEqual(cfg['server']['host'],'127.0.0.1')
        self.assertNotIn('https://',text); self.assertNotIn('api_key',text)
        self.assertFalse(cfg['conversation']['enable_rerank'])


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        base.DiagnosticTests.setUp(self)
        self.stack.enter_context(patch.object(a,'clean_config',a.clean_config))
        self.fixture=self.root/'diagnosed-fixture'; self.fixture.mkdir(mode=0o700)
        self.diag=self.root/'diagnostic.json'; self.diagtool=self.root/'diagnose.py'; self.diagtool.write_text('never executed')
        self.diagrow=dict(startup_diagnostic='COMPLETE',native_bootstrap='FAILED_PANIC_CAPTURED',native_bootstrap_exit_code=2,
            provider_requests=0,production_writes=0,credentials_requested=False,cleanup='NAMESPACE_ABSENT_VERIFIED',
            startup_evidence={'classes':{'nil_pointer':True,'config_loaded':True},'stack':[
                dict(file='extract_entity.go',line=41,function='github.com/Tencent/WeKnora/internal/application/service/chat_pipeline.NewPluginExtractEntity')]})
        self.failrow=dict(document_submission_attempted=False,provider_requests=0,production_baseline='UNCHANGED',
            cleanup='NAMESPACE_ABSENT_VERIFIED',failed_check='isolated_bootstrap_exited_before_health')
        self.diag.write_text(json.dumps(self.diagrow)); (self.fixture/'RESULT.json').write_text(json.dumps(self.failrow))
        ack=dict(name='weknora-enrich-check-'+'b'*16,uid='diagnostic-uid')
        (self.fixture/'NAMESPACE-ACK.json').write_text(json.dumps(ack))
        (self.fixture/'CLEANUP-VERIFIED.json').write_text(json.dumps(dict(namespace=ack['name'],uid=ack['uid'],absent=True)))
        for name,value in dict(DIAGNOSTIC_TOOL=self.diagtool,DIAGNOSTIC_TOOL_SHA=hashlib.sha256(self.diagtool.read_bytes()).hexdigest(),
            DIAGNOSTIC_RESULT=self.diag,DIAGNOSTIC_RESULT_SHA=hashlib.sha256(self.diag.read_bytes()).hexdigest(),
            FIXTURE=self.fixture,FIXTURE_RESULT_SHA=hashlib.sha256((self.fixture/'RESULT.json').read_bytes()).hexdigest(),
            STATE=self.root/'config-recovery',NEW_STATE=self.root/'acceptance').items():
            self.stack.enter_context(patch.object(r,name,value))

    def tearDown(self): base.DiagnosticTests.tearDown(self)

    def test_both_previous_namespaces_absent_and_receipts_bound(self):
        binding=r.bind_previous(a)
        self.assertEqual(self.get.call_count,2); self.assertTrue(binding['diagnostic_namespace_absent'])
        self.assertEqual(binding['previous_provider_submissions'],'ZERO_VERIFIED_PRE_PROVIDER_BOUNDARY')

    def test_changed_diagnostic_receipt_blocks(self):
        self.diag.write_bytes(self.diag.read_bytes()+b' ')
        with self.assertRaises(a.audit.AuditFailure): r.bind_previous(a)

    def test_wrong_panic_location_blocks_even_if_report_rehashed(self):
        self.diagrow['startup_evidence']['stack'][0]['line']=42
        self.diag.write_text(json.dumps(self.diagrow))
        with patch.object(r,'DIAGNOSTIC_RESULT_SHA',hashlib.sha256(self.diag.read_bytes()).hexdigest()):
            with self.assertRaisesRegex(a.native.AcceptanceFailure,'not_exact_extract_manager'): r.bind_previous(a)

    def test_new_provider_marker_blocks_recovery(self):
        for name in ('PROVIDER-REQUESTED.json','TEST-MODELS.json','UPLOAD.json','SEARCH.json'):
            p=self.fixture/name; p.write_text('{}')
            with self.assertRaisesRegex(a.native.AcceptanceFailure,'provider_boundary'): r.bind_previous(a)
            p.unlink()

    def test_foreign_cleanup_uid_blocks(self):
        p=self.fixture/'CLEANUP-VERIFIED.json'; value=json.loads(p.read_text()); value['uid']='foreign'
        p.write_text(json.dumps(value))
        with self.assertRaisesRegex(a.native.AcceptanceFailure,'cleanup_binding'): r.bind_previous(a)

    def test_fix_does_not_replace_provider_or_acceptance_functions(self):
        originals={name:getattr(a,name) for name in ('run','remote','schema_receipt','grants','index_pass','search_pass','REQUEST_BUDGET','validate','cleanup')}
        oldconfig=a.clean_config(); fix=r.install_fix(a)
        self.assertEqual(a.clean_config(),corrected_config(oldconfig)); self.assertEqual(a.STATE,r.NEW_STATE)
        self.assertIsInstance(a.gateway_baseline,GatewayBaseline)
        self.assertEqual(a.RUNTIME_SOURCE.count('startup=safe_startup('),2)
        for name,obj in originals.items(): self.assertIs(getattr(a,name),obj,name)
        self.assertFalse(fix['candidate_image_changed']); self.assertTrue(fix['real_glm_and_embedding_acceptance_preserved'])

    def test_runtime_hash_drift_rejected(self):
        a.RUNTIME_SOURCE=base.RUNTIME+'\n'
        with self.assertRaisesRegex(a.native.AcceptanceFailure,'frozen_runtime_changed'): r.install_fix(a)

    def full_receipt(self):
        return dict(native_enrichment_acceptance='PASS',native_schema_migration='PASS',real_asynq_glm_enrichment='PASS',
            real_embedding_1024d_index='PASS',vector_only_retrieval='PASS',production_baseline='UNCHANGED',
            cleanup='NAMESPACE_ABSENT_VERIFIED',document_submission_attempted=True)

    def run_fixture(self, receipt, rc):
        r.NEW_STATE.mkdir(mode=0o700); a.record('RESULT.json',receipt); return rc

    def test_success_requires_all_real_evidence_and_never_repeats(self):
        before=self.diag.read_bytes()
        with patch.object(a,'run',side_effect=lambda:self.run_fixture(self.full_receipt(),0)) as run:
            self.assertEqual(r.run(a),0)
            with self.assertRaisesRegex(a.native.AcceptanceFailure,'USE_STATUS'): r.run(a)
        self.assertEqual(run.call_count,1); self.assertEqual(self.diag.read_bytes(),before)
        report=json.loads((r.STATE/'RESULT.json').read_text())
        self.assertEqual(report['fixture_config_recovery'],'PASS'); self.assertFalse(report['ready_for_rollout'])

    def test_missing_vectors_cannot_be_accepted(self):
        receipt=self.full_receipt(); receipt['real_embedding_1024d_index']='NOT_TESTED'
        with patch.object(a,'run',side_effect=lambda:self.run_fixture(receipt,0)):
            self.assertEqual(r.run(a),1)
        report=json.loads((r.STATE/'RESULT.json').read_text())
        self.assertEqual(report['failed_check'],'full_native_acceptance_evidence_required')

    def test_failure_report_and_no_auto_retry(self):
        receipt=dict(native_enrichment_acceptance='UNCONFIRMED',failed_check='test_only',failed_stage='pre-model',
            document_submission_attempted=False,cleanup='NAMESPACE_ABSENT_VERIFIED')
        with patch.object(a,'run',side_effect=lambda:self.run_fixture(receipt,1)) as run:
            self.assertEqual(r.run(a),1)
        self.assertEqual(run.call_count,1)
        self.assertEqual(json.loads((r.STATE/'RESULT.json').read_text())['failed_check'],'test_only')

    def test_status_no_exec_models_or_state_creation(self):
        with patch.object(a,'status') as status,patch.object(a.native,'call') as call,patch.object(a,'run') as run:
            r.status(a)
        status.assert_called_once(); call.assert_not_called(); run.assert_not_called()
        self.assertFalse(r.STATE.exists()); self.assertEqual(a.STATE,r.NEW_STATE)


if __name__=='__main__': unittest.main()
