import ast
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('db_stage', HERE / 'db_stage.py')
D = importlib.util.module_from_spec(spec); spec.loader.exec_module(D)
OWNER = 'a' * 32
NS = 'portal-ppt-' + OWNER


def profile():
    return dict(schema='portal-native-pg-profile.v1', owner=OWNER, uid=999, gid=999,
                server_binary_version='16.15', database_started=False, **D.PG_FILES)


def summary():
    contract = json.loads(D.asset_bytes('RECEIPT-CONTRACT.json'))
    rows = []
    for step in contract['steps']:
        rows.append(dict(schema='portal-ppt-native-db-step.v1', status='PASS', ordinal=step['ordinal'],
                         label=step['label'], probe_sha256=contract['probe_sha256'],
                         contract_sha256=hashlib.sha256(D.asset_bytes('RECEIPT-CONTRACT.json')).hexdigest(),
                         process=dict(returncode=0, failure_code=None, child_exit_confirmed=True,
                                      whole_output_captured=True, bytes=100), probe_receipt=step['expected_receipt']))
    return dict(schema='portal-ppt-native-db-container-summary.v1',
                status='FIVE_PROBE_RECEIPTS_PASS_EXTERNAL_NATIVE_IDENTITY_REVIEW_REQUIRED',
                probe_sha256=contract['probe_sha256'], contract_sha256=rows[0]['contract_sha256'], steps=rows,
                snapshot_sha256=contract['expected_snapshot_sha256'], native_arm64_passed=False,
                ready_for_rollout=False, production_accessed=False, model_requests=0,
                operator_configuration_loading='NOT_TESTED')


class DbStageTests(unittest.TestCase):
    def test_probe_is_fixed_read_only_unprivileged_and_uses_original_index(self):
        pod = D.render_pg_probe(OWNER, NS, D.PG_CACHE); ps = pod['spec']; c = ps['containers'][0]
        self.assertEqual(c['image'], D.PG_CACHE['reference']); self.assertEqual(c['imagePullPolicy'], 'Never')
        self.assertEqual(c['args'][:2], ['-i', 'PATH=' + D.PG_PATH]); self.assertEqual(c['securityContext']['runAsUser'], 65532)
        self.assertTrue(c['securityContext']['readOnlyRootFilesystem']); self.assertFalse(ps['automountServiceAccountToken'])
        self.assertNotIn('volumes', ps); self.assertNotIn('env', c); self.assertNotIn('nodeName', ps)
        self.assertEqual(c['resources']['requests'], c['resources']['limits'])
        self.assertIn('/postgres --version', c['args'][-1]); self.assertNotIn('initdb -', c['args'][-1])

    def test_pg_profile_rejects_wrong_identity_paths_zero_uid_and_unexpected_fields(self):
        self.assertEqual(D.validate_pg_probe(profile(), D.PG_CACHE, OWNER), profile())
        for key, value in [('uid',0),('uid',True),('gid',-1),('owner','b'*32),('entrypoint','/tmp/entrypoint'),
                           ('server_binary_version','17.1'),('database_started',True),('extra','forbidden')]:
            row=profile(); row[key]=value
            with self.subTest(key=key), self.assertRaises((ValueError, TypeError)): D.validate_pg_probe(row,D.PG_CACHE,OWNER)
        changed=copy.deepcopy(D.PG_CACHE); changed['manifest_digest']='sha256:'+'0'*64
        with self.assertRaises(ValueError): D.render_pg_probe(OWNER,NS,changed)

    def test_db_renderer_keeps_five_steps_and_binds_only_disposable_pg(self):
        pod=D.render_db(OWNER,NS,profile()); ps=pod['spec']; pg=ps['initContainers'][0]
        self.assertEqual(pod['metadata']['labels'],{D.OWNER_LABEL:OWNER})
        self.assertEqual([c['name'] for c in ps['initContainers'][1:]],
                         ['gateway-upgrade','runner-restart','gateway-rollback-restart','runner-rollback-restart','gateway-reforward-restart'])
        self.assertEqual(pg['image'],D.PG_CACHE['reference']); self.assertEqual(pg['command'],['/usr/bin/env'])
        self.assertEqual(pg['args'][0],'-i'); self.assertNotIn('env',pg)
        self.assertEqual([a for a in pg['args'] if a.startswith(('LANG=','LC_'))],['LANG=C.UTF-8'])
        self.assertEqual(pg['securityContext']['runAsUser'],999); self.assertEqual(pg['securityContext']['runAsGroup'],999)
        self.assertIn('listen_addresses=127.0.0.1',pg['args']); self.assertEqual(pg['restartPolicy'],'Always')
        self.assertEqual(pg['startupProbe']['exec']['command'][0],D.PG_FILES['pg_isready'])
        self.assertTrue(all(set(v)=={'name','emptyDir'} and v['emptyDir']['medium']=='Memory' for v in ps['volumes']))
        self.assertNotIn('__UNBOUND_',json.dumps(pod)); self.assertNotIn('nodeName',ps)
        self.assertEqual(ps['hostAliases'],[{'hostnames':['portal-image-pg'],'ip':'127.0.0.1'}])
        for c in ps['initContainers']+ps['containers']:
            self.assertEqual(c['imagePullPolicy'],'Never'); self.assertEqual(c['resources']['requests'],c['resources']['limits'])

    def test_inline_adapters_compile_and_do_not_copy_application_sources(self):
        for ordinal in ('1','2','3','4','5','final'):
            code=D.fixture_inline(ordinal); ast.parse(code,feature_version=(3,7))
            self.assertIn('os.O_EXCL|os.O_NOFOLLOW',code)
            self.assertIn("'/qualification/'+name",code)
            self.assertNotIn('/app/backend/packages/',code)
        self.assertEqual(set(D.assets()),{'POD-TEMPLATE.json','probe_supervisor.py','bootstrap_probe.py','RECEIPT-CONTRACT.json'})

    def test_success_summary_uses_exact_existing_receipt_contract(self):
        value=summary(); self.assertEqual(D.validate_db_summary(value),value)
        value['steps'][0]['probe_receipt']['dotenv_load_calls_skipped']=0
        self.assertEqual(D.validate_db_summary(value),value)

    def test_summary_cannot_hide_nonzero_swapped_step_or_changed_business_receipt(self):
        for mutate in (lambda r:r['steps'][1]['process'].update(returncode=3),
                       lambda r:r['steps'].reverse(),
                       lambda r:r['steps'][0]['probe_receipt'].update(snapshot_sha256='0'*64),
                       lambda r:r['steps'][0]['probe_receipt']['module_origins'][0].update(sha256='0'*64),
                       lambda r:r.update(native_arm64_passed=True),
                       lambda r:r['steps'][0]['process'].update(child_exit_confirmed=False)):
            value=summary(); mutate(value)
            with self.assertRaises(Exception): D.validate_db_summary(value)

    def test_owner_and_namespace_must_match_exact_one_session(self):
        for owner,ns in [('a'*31,NS),(OWNER,'deer-flow'),(OWNER,'portal-ppt-'+'b'*32)]:
            with self.assertRaises(ValueError): D.render_pg_probe(owner,ns,D.PG_CACHE)


if __name__=='__main__': unittest.main(verbosity=2)
