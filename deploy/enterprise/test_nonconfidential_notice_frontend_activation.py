"""Notice rollout transport checks with synthetic receipts; no production operations."""
import ast
import copy
import importlib.util
import json
import os
from pathlib import Path
import time
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent

def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), HERE / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

prior = load('test_data_qa_progress_frontend_activation.py')

class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('NOTICE_ACTIVATION_SCRIPT'):
            cls.mod = types.ModuleType('actual_notice_package')
            exec(compile(Path(os.environ['NOTICE_ACTIVATION_SCRIPT']).read_bytes(), '<packaged>', 'exec'), cls.mod.__dict__)
        else:
            cls.mod = load('activate-nonconfidential-notice-frontend.py')
            cls.mod.FRONT_COMMON = prior.load('package-data-qa-progress-frontend-activation.py').prior_common(prior.PRIOR)
        cls.mod.MANIFEST = 'sha256:' + 'a' * 64
        cls.mod.CONFIG = 'sha256:' + 'b' * 64
        cls.mod.NEW = 'deerflow.local/deer-flow-frontend@' + cls.mod.MANIFEST
        cls.mod.TAG = 'deerflow.local/deer-flow-frontend:synthetic-notice-test'
        cls.mod.SOURCE_PINS = {'/app/frontend/.next/static/chunks/a.js': {'bytes': 1, 'sha256': 'c' * 64}}
        cls.mod.PROVENANCE = {'synthetic': True}
        cls.rows = {}
        for host, uid in cls.mod.NODES.items():
            cls.rows[host] = {'schema': 'portal-frontend-ui-cache-preload.v1', 'hostname': host,
                'expected_node_uid': uid, 'report_path': cls.mod.PRELOAD_PATHS[host],
                'status': 'COMPLETE_FRONTEND_UI_CACHE_PRELOAD_REVIEW_REQUIRED', 'exitcode': 0,
                'operation': 'd' * 32, 'report_bytes': 100, 'report_sha256': 'e' * 64,
                'manifest_digest': cls.mod.MANIFEST, 'config_digest': cls.mod.CONFIG,
                'phase_a': 'PASS', 'phase_b': 'PASS', 'content_and_unpack': 'PASS', 'cri_visibility': 'PASS',
                'old_references_preserved': True, 'preclaim_passed': True,
                'image_import_may_be_partial': False, 'production_workload_mutation': False,
                'automatic_retry': False, 'kubernetes_node_uid_freshly_checked': False,
                'canonical_names_created': [cls.mod.NEW, cls.mod.TAG]}
        cls.mod.PRELOADS = cls.rows

    def setUp(self):
        self.front, self.helper = self.mod.load_modules()
        self.original_front_pin = copy.deepcopy(self.mod.FRONT)
        self.d = {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': {'name': 'frontend', 'namespace': 'deer-flow',
            'uid': self.mod.FRONT['uid'], 'generation': 226, 'resourceVersion': '101'},
            'spec': {'replicas': 2, 'template': {'spec': {'containers': [{'name': 'frontend', 'image': self.mod.OLD,
                'imagePullPolicy': 'Never', 'env': [{'name': 'KEEP', 'value': 'yes'}]}]}}},
            'status': {'observedGeneration': 226, 'replicas': 2, 'updatedReplicas': 2, 'readyReplicas': 2, 'availableReplicas': 2}}
        self.mod.FRONT = self.mod.binding(self.d)
        self.front.OLD_SPEC = self.mod.FRONT['spec_sha256']

    def tearDown(self):
        self.mod.FRONT = self.original_front_pin

    nodes = prior.ActivationTests.nodes
    patch_execution = prior.ActivationTests.patch_execution
    test_patch_timeout_is_never_repeated_and_journal_precedes_dispatch = prior.ActivationTests.test_patch_timeout_is_never_repeated_and_journal_precedes_dispatch
    test_uncertain_patch_with_old_readback_requires_manual_review = prior.ActivationTests.test_uncertain_patch_with_old_readback_requires_manual_review
    test_non_frontend_patch_and_mutation_verbs_are_blocked = prior.ActivationTests.test_non_frontend_patch_and_mutation_verbs_are_blocked
    test_node_runtime_probe_hashes_files_and_actual_http_response_bytes = prior.ActivationTests.test_node_runtime_probe_hashes_files_and_actual_http_response_bytes

    def test_fresh_node_gate_keeps_health_but_does_not_assume_complete_image_list(self):
        nodes = self.nodes()
        nodes[0]['status']['images'] = []
        self.assertFalse(self.front.node_gate(nodes)[0]['candidate_in_node_status'])
        for mutate in (lambda n: n[0]['metadata'].update(uid='other'),
                       lambda n: n[0]['status']['conditions'][2].update(status='True'),
                       lambda n: n[0].update(spec={'unschedulable': True})):
            nodes = self.nodes()
            mutate(nodes)
            with self.assertRaises(self.mod.Refused):
                self.front.node_gate(nodes)

    def test_receipt_manifest_and_status_binding(self):
        self.mod.validate_preloads(self.rows)
        for key, value in [('report_sha256', ''), ('expected_node_uid', 'other'), ('manifest_digest', 'old'),
                           ('phase_b', 'FAIL'), ('preclaim_passed', False), ('old_references_preserved', False)]:
            rows = copy.deepcopy(self.rows)
            rows['datax-02'][key] = value
            with self.subTest(key=key), self.assertRaises(self.mod.Refused):
                self.mod.validate_preloads(rows)

    def test_patch_changes_only_frontend_image_with_whole_spec_cas(self):
        actions = self.front.prepare_patch(self.d, copy.deepcopy(self.d))
        self.assertEqual([x for x in actions if x['op'] != 'test'],
            [{'op': 'replace', 'path': '/spec/template/spec/containers/0/image', 'value': self.mod.NEW}])
        self.assertIn({'op': 'test', 'path': '/spec', 'value': self.d['spec']}, actions)
        self.assertIn({'op': 'test', 'path': '/metadata/resourceVersion', 'value': '101'}, actions)
        drift = copy.deepcopy(self.d)
        drift['spec']['template']['spec']['containers'][0]['env'][0]['value'] = 'changed'
        with self.assertRaises(self.mod.Refused):
            self.front.prepare_patch(drift, self.d)

    def test_host_python37_and_no_backend_dependencies(self):
        source = (HERE / 'activate-nonconfidential-notice-frontend.py').read_text(encoding='utf-8')
        if os.sys.version_info >= (3, 8):
            ast.parse(source, feature_version=(3, 7))
        else:
            ast.parse(source)
        self.assertNotIn('BACKEND_COMMON', source)
        self.assertNotIn('schema_check(', source)
        self.assertEqual(self.mod.FRONT['generation'], 226)

    def flow(self, drift=False, cache_failure=False):
        before = {name: copy.deepcopy(self.d) for name in self.mod.ROLES}
        for name,d in before.items():
            d['metadata']['name'] = name
        final = copy.deepcopy(before)
        final['frontend']['metadata']['generation'] = 227
        final['frontend']['spec']['template']['spec']['containers'][0]['image'] = self.mod.NEW
        if drift:
            final['gateway']['metadata']['generation'] += 1
        pods = [{'metadata': {'name': 'frontend-' + str(i), 'uid': 'pod-' + str(i)}, 'spec': {'nodeName': n},
                 'status': {'containerStatuses': [{'name': 'frontend', 'containerID': str(i), 'restartCount': 0, 'imageID': self.mod.NEW}]}}
                for i,n in enumerate(sorted(self.mod.NODES))]
        writes, calls = [], []
        test = self
        class E:
            report = {'operation': 'a' * 32, 'directory': '/var/lib/test', 'patch_request_count': 0}
            started = time.monotonic()
            parent = 1
            H = types.SimpleNamespace(write_new=lambda *a: writes.append(a[1]))
            def get(self, phase, args):
                if args[0] == 'nodes': return {'items': test.nodes()}
                if args[0] == 'service': return {'same': True}
                if args[0] == 'deployment': return final['frontend']
                return {'items': list((final if phase == 'after-deployments' else before).values())}
            def write(self, name, value): writes.append(name)
            def patch_once(self, value):
                test.assertIn(test.mod.ONCE, writes)
                self.report['patch_request_count'] += 1
                return final['frontend']
            def call(self, phase, args):
                calls.append(args)
                return json.dumps({'status':'PASS','sources_verified':True,'static_assets_verified':1,
                    'provider_requests':0,'http_status':307,'bytes':10}).encode(),{}
        e = E()
        def verify_cache(execution, sleep):
            self.assertIn(self.mod.ONCE, writes)
            if cache_failure:
                raise self.mod.Refused('local_image_start_failed')
            execution.report['cache_checks'] = [{'node': n, 'status': 'PASS'} for n in self.mod.NODES]
        with patch.object(self.mod,'services',return_value={}), patch.object(self.mod,'frontend_snapshot',return_value=(pods,{'same':True})), patch.object(self.mod, 'verify_local_cache', side_effect=verify_cache):
            if cache_failure:
                with self.assertRaisesRegex(self.mod.Refused, 'local_image_start_failed'):
                    self.mod.execute(e,self.front)
            elif drift:
                with self.assertRaisesRegex(self.mod.Refused,'unrelated_spec_changed'):
                    self.mod.execute(e,self.front)
            else:
                self.mod.execute(e,self.front)
                self.assertEqual(e.report['status'],self.mod.ACCEPTED)
                self.assertTrue(e.report['services_preserved'])
        self.assertEqual(e.report['patch_request_count'],0 if cache_failure else 1)
        self.assertEqual(len(calls),0 if cache_failure else 2)

    def test_one_patch_two_pods_static_http_and_preservation(self):
        self.flow()

    def test_concurrent_gateway_change_blocks_acceptance(self):
        self.flow(True)

    def test_failed_local_image_check_never_patches_frontend(self):
        self.flow(cache_failure=True)

    def test_jobs_never_pull_and_do_not_mount_production_credentials_or_join_service(self):
        for host in self.mod.NODES:
            job = self.mod.check_spec(host, 'a'*32, 0)
            self.assertEqual(job['spec']['backoffLimit'], 0)
            self.assertEqual(job['spec']['activeDeadlineSeconds'], 180)
            pod = job['spec']['template']['spec']
            self.assertEqual(pod['nodeName'], host)
            self.assertFalse(pod['automountServiceAccountToken'])
            self.assertFalse(pod['enableServiceLinks'])
            self.assertNotIn('volumes', pod)
            self.assertNotIn('app', job['spec']['template']['metadata']['labels'])
            c = pod['containers'][0]
            self.assertEqual(c['imagePullPolicy'], 'Never')
            self.assertEqual(c['image'], self.mod.NEW)
            self.assertNotIn('envFrom', c)
            self.assertNotIn('ports', c)
            self.assertEqual(c['securityContext']['runAsUser'], 1000)
            self.assertTrue(c['securityContext']['readOnlyRootFilesystem'])

    def cache_flow(self, bad=None):
        test = self
        class E:
            report = {'operation': 'f'*32, 'cache_checks': []}
            H = types.SimpleNamespace(parse=json.loads)
            specs = []
            def create_check_once(self, spec):
                self.specs.append(spec)
                self.spec = spec
                self.job = {'kind':'Job','metadata':dict(spec['metadata'],uid='job-'+spec['spec']['template']['spec']['nodeName'])}
                return self.job
            def get(self, phase, args):
                if args[0]=='nodes': return {'items':test.nodes()}
                if args[0]=='job':
                    return dict(self.job,status={'conditions':[{'type':'Complete','status':'True'}]})
                spec = copy.deepcopy(self.spec['spec']['template']['spec'])
                status = {'phase':'Succeeded','containerStatuses':[{'imageID':test.mod.NEW,'restartCount':0,'state':{'terminated':{'exitCode':0}}}]}
                if bad=='missing': status['containerStatuses'][0]['state']={'waiting':{'reason':'ErrImageNeverPull'}}
                if bad=='digest': status['containerStatuses'][0]['imageID']='sha256:'+'0'*64
                if bad=='pull': spec['containers'][0]['imagePullPolicy']='Always'
                return {'items':[{'metadata':{'name':'check-pod','namespace':'deer-flow','uid':'pod-1','ownerReferences':[{'uid':self.job['metadata']['uid'],'kind':'Job','controller':True}]},'spec':spec,'status':status}]}
            def call(self, phase, args):
                return json.dumps({'status':'LOCAL_PRELOADED_IMAGE_EXECUTED','sources_verified':bad!='sources','files_verified':len(test.mod.SOURCE_PINS),'provider_requests':0}).encode(),{}
        e = E()
        if bad:
            with self.assertRaises(self.mod.Refused): self.mod.verify_local_cache(e, sleep=lambda _:None)
            self.assertLess(len(e.specs), 2)
        else:
            self.mod.verify_local_cache(e,sleep=lambda _:None)
            self.assertEqual({row['node'] for row in e.report['cache_checks']},set(self.mod.NODES))
            self.assertTrue(all(row['status']=='PASS' for row in e.report['cache_checks']))

    def test_two_actual_execution_receipts_required(self):
        self.cache_flow()

    def test_missing_image_wrong_digest_pull_policy_and_sources_stop_before_rollout(self):
        for bad in ('missing','digest','pull','sources'):
            with self.subTest(bad=bad): self.cache_flow(bad)

    def test_only_proven_noop_r2_failure_can_resume(self):
        r = dict(schema='portal-frontend-nonconfidential-notice-activation.v1',status='INCOMPLETE',
            error_code='new_image_not_observed_in_kubelet_cache',stage_passed=False,patch_request_count=0,
            once_claimed=False,patch_requested=False,patch_may_have_applied=False,other_workload_mutation=False,
            schema_migration_requested=False,cache_manifest=self.mod.MANIFEST,cache_config=self.mod.CONFIG)
        self.mod.validate_failed_r2(r)
        for key,value in [('patch_request_count',1),('patch_may_have_applied',True),('once_claimed',True),('error_code','unknown')]:
            with self.assertRaises(self.mod.Refused): self.mod.validate_failed_r2(dict(r,**{key:value}))

if __name__ == '__main__':
    unittest.main(verbosity=2)
