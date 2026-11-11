"""Offline Frontend 241 -> 242 acceptance; no production commands or delivery files.

Only copies of existing operator evidence are used, in temporary test directories.
The executable source returned by the builder remains in memory. Runtime fixtures
are synthetic and external transport is replaced before any execution path runs.
"""
import ast
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import types
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / 'release/sidechat-skillhub-security-20261105-r1'
spec = importlib.util.spec_from_file_location('sidechat_activation_builder', ROOT / 'deploy/enterprise/package-sidechat-security-frontend-activation-20261106.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
BASELINE = json.loads((ARCHIVE / 'frontend-baseline-field-20261106.json').read_bytes())
FIXED_GATEWAY = {key: BASELINE['deployments']['gateway'][key] for key in ('uid', 'generation', 'spec_sha256')}
FIXED_GATEWAY_IMAGE = BASELINE['deployments']['gateway']['image']
FIXED_MANIFEST = 'sha256:172243f3abbb8c5a2ebff0242d4386f19ef63e9c9c9cffe9b238cbcfe6159ab3'
FIXED_CONFIG = 'sha256:0e24234e305f54a1e0aa160281ee7a463925b526ae358fbb66053fefd71ed821'


def synthetic_inputs(root):
    qualified = root / 'qualified'
    qualified.mkdir()
    copies = {'PACKAGE.json': 'preload-as-delivered/PACKAGE.json',
              'RESULT.json': 'frontend-RESULT.json', 'SOURCE.json': 'frontend-SOURCE.json',
              'BROWSER-QUALIFICATION.json': 'frontend-browser-qualification.json'}
    for dest, original in copies.items():
        (qualified / dest).write_bytes((ARCHIVE / original).read_bytes())
    result_pin = builder.pin((qualified / 'RESULT.json').read_bytes())
    assets = {'schema': 'portal-sidechat-security-frontend-assets.v1',
              'status': 'COMPILED_MARKERS_PINNED', 'image': FIXED_MANIFEST,
              'qualification_sha256': result_pin['sha256'],
              'source_inventory_sha256': builder.pin((qualified / 'SOURCE.json').read_bytes())['sha256'],
              'source_pins': {'/app/frontend/.next/static/chunks/' + str(i) + '.js':
                              {'bytes': 1, 'sha256': 'e' * 64} for i in range(4)},
              'feature_markers': {key: ['/app/frontend/.next/static/chunks/0.js'] for key in builder.REQUIRED_MARKERS},
              'synthetic_test_fixture': True}
    (qualified / 'COMPILED-ASSETS.json').write_text(json.dumps(assets), encoding='utf-8')
    field, baseline = root / 'field.json', root / 'baseline.json'
    field.write_bytes((ARCHIVE / 'preload-and-recheck-field-20261106.json').read_bytes())
    baseline.write_bytes((ARCHIVE / 'frontend-baseline-field-20261106.json').read_bytes())
    return qualified, field, baseline


with tempfile.TemporaryDirectory() as initial:
    SOURCE = builder.build(*synthetic_inputs(Path(initial))).decode()


def candidate():
    m = types.ModuleType('synthetic_sidechat_activation')
    exec(compile(SOURCE, '<synthetic-activation>', 'exec'), m.__dict__)
    return m


class Tests(unittest.TestCase):
    def test_wrong_host_cannot_create_claim_jobs_or_patch(self):
        m = candidate()
        with patch.object(m.socket, 'gethostname', return_value='synthetic-test-host'), \
                patch.object(m, 'load_modules', side_effect=AssertionError('must not load transport')), \
                patch.object(m.os, 'mkdir', side_effect=AssertionError('must not write')), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(m.run(), 1)


    def gateway_fixture(self, m):
        d = copy.deepcopy(self.d)
        d['metadata'].update(name='gateway', uid=FIXED_GATEWAY['uid'], generation=408)
        d['spec']['replicas'] = 70
        d['spec']['template']['spec']['containers'][0].update(name='gateway', image=m.GATEWAY_IMAGE)
        d['status'].update(observedGeneration=408, replicas=70, updatedReplicas=70, readyReplicas=70, availableReplicas=70)
        m.GATEWAY = m.binding(d)
        m.PRIOR_RECEIPT = dict(scope='operator_observed_deployment_identity', gateway=m.GATEWAY, frontend=m.FRONT)
        return d


    def test_live_gateway_readiness_identity_and_pause_are_required(self):
        m = candidate()
        d = self.gateway_fixture(m)
        e = types.SimpleNamespace(H=None, report={})
        m.check_backend_contract(e, d)
        for field, value in [('observedGeneration', 407), ('replicas', 71), ('updatedReplicas', 69),
                             ('readyReplicas', 69), ('availableReplicas', 69), ('terminatingReplicas', 1)]:
            changed = copy.deepcopy(d)
            changed['status'][field] = value
            with self.subTest(field=field), self.assertRaises(m.Refused):
                m.check_backend_contract(e, changed)
        for mutation in (lambda x: x['metadata'].update(uid='other'),
                         lambda x: x['metadata'].update(generation=409),
                         lambda x: x['spec'].update(paused=True),
                         lambda x: x['spec']['template']['spec']['containers'][0].update(image='other')):
            changed = copy.deepcopy(d)
            mutation(changed)
            with self.assertRaises(m.Refused):
                m.check_backend_contract(e, changed)


    def test_unready_gateway_stops_before_claim_job_or_patch(self):
        before = {name: copy.deepcopy(self.d) for name in self.mod.ROLES}
        for name, d in before.items():
            d['metadata']['name'] = name
        before['gateway'] = self.gateway_fixture(self.mod)
        before['gateway']['status']['readyReplicas'] = 69
        self.mod.PROTECTED_BASELINE = {name: self.mod.binding(d) for name, d in before.items()}
        effects = []
        e = types.SimpleNamespace(report={}, H=None,
            get=lambda *args: {'items': list(before.values())}, write=lambda *args: effects.append(args))
        with self.assertRaises(self.mod.Refused):
            self.mod.execute(e, self.front)
        self.assertEqual(effects, [])
        self.assertFalse(e.report.get('once_claimed'))


    def setUp(self):
        self.mod = candidate()
        self.rows = self.mod.PRELOADS
        self.mod.SOURCE_PINS = {'/app/frontend/.next/static/chunks/a.js': {'bytes': 1, 'sha256': 'c' * 64}}
        self.front, self.helper = self.mod.load_modules()
        self.d = {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': {'name': 'frontend', 'namespace': 'deer-flow',
            'uid': self.mod.FRONT['uid'], 'generation': 241, 'resourceVersion': '101'},
            'spec': {'replicas': 2, 'template': {'spec': {'containers': [{'name': 'frontend', 'image': self.mod.OLD,
                'imagePullPolicy': 'Never', 'env': [{'name': 'KEEP', 'value': 'yes'}]}]}}},
            'status': {'observedGeneration': 241, 'replicas': 2, 'updatedReplicas': 2, 'readyReplicas': 2, 'availableReplicas': 2}}
        self.mod.FRONT = self.mod.binding(self.d)
        self.front.OLD_SPEC = self.mod.FRONT['spec_sha256']


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


    def flow(self, drift=False, cache_failure=False):
        before = {name: copy.deepcopy(self.d) for name in self.mod.ROLES}
        for name,d in before.items():
            d['metadata']['name'] = name
        before['gateway'] = self.gateway_fixture(self.mod)
        self.mod.PROTECTED_BASELINE = {name: self.mod.binding(d) for name, d in before.items()}
        final = copy.deepcopy(before)
        final['frontend']['metadata']['generation'] = 242
        final['frontend']['spec']['template']['spec']['containers'][0]['image'] = self.mod.NEW
        if drift:
            final[drift if isinstance(drift, str) else 'gateway']['metadata']['generation'] += 1
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
        with patch.object(self.mod,'require_frontend_coverage'), patch.object(self.mod,'services',return_value={}), patch.object(self.mod,'frontend_snapshot',return_value=(pods,{'same':True})), patch.object(self.mod, 'verify_local_cache', side_effect=verify_cache):
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
                self.assertFalse(e.report['nginx_configuration_semantics_verified'])
                self.assertFalse(e.report['skillhub_native_auth_remediation_included'])
                self.assertEqual(e.report['after']['frontend']['generation'], 242)
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


    def nodes(self):
        return [{'metadata': {'name': name, 'uid': uid}, 'status': {'conditions': [
            {'type': key, 'status': 'True' if key == 'Ready' else 'False'} for key in ('Ready', 'MemoryPressure', 'DiskPressure', 'PIDPressure')],
            'nodeInfo': {'architecture': 'arm64'}, 'images': [{'names': [self.mod.NEW]}]}} for name, uid in self.mod.NODES.items()]


    def patch_execution(self, result_ready=True):
        actions = self.front.prepare_patch(self.d, self.d)
        current = copy.deepcopy(self.d)
        current['metadata'].update(generation=242, resourceVersion='102')
        current['spec']['template']['spec']['containers'][0]['image'] = self.mod.NEW
        if not result_ready:
            current = self.d
        events = []
        def write(fd, name, raw):
            events.append(('write', name))
        def runner(argv, **kwargs):
            verb = argv[5]
            events.append(('command', verb))
            return ((b'' if verb == 'patch' else json.dumps(current).encode()), b'',
                {'returncode': 124 if verb == 'patch' else 0, 'error_code': 'command_timeout' if verb == 'patch' else None})
        helper = types.SimpleNamespace(write_new=write, run_bounded=runner)
        report = {'operation': 'a' * 32, 'commands': [], 'once_claimed': True, 'patch_request_count': 0}
        e = self.mod.make_execution(self.front)(helper, 123, '/usr/bin/kubectl', report)
        with patch.object(os, 'O_NOFOLLOW', 0, create=True), patch.object(os, 'open', return_value=456), patch.object(os, 'close'):
            if result_ready:
                e.patch_once(actions)
                with self.assertRaisesRegex(self.mod.Refused, 'second_patch'):
                    e.patch_once(actions)
            else:
                with self.assertRaisesRegex(self.mod.Refused, 'manual_review_no_retry'):
                    e.patch_once(actions)
        return e, events


    def test_patch_timeout_is_never_repeated_and_journal_precedes_dispatch(self):
        e, events = self.patch_execution()
        self.assertEqual(events.count(('command', 'patch')), 1)
        self.assertLess(events.index(('write', 'DISPATCH.json')), events.index(('command', 'patch')))
        self.assertEqual(e.report['patch_request_count'], 1)
        self.assertTrue(e.report['patch_may_have_applied'])


    def test_uncertain_patch_with_old_readback_requires_manual_review(self):
        e, events = self.patch_execution(False)
        self.assertEqual(events.count(('command', 'patch')), 1)
        self.assertTrue(e.report['patch_may_have_applied'])


    def test_non_frontend_patch_and_mutation_verbs_are_blocked(self):
        helper = types.SimpleNamespace(write_new=lambda *x: None, run_bounded=lambda *x, **k: self.fail('must not dispatch'))
        e = self.mod.make_execution(self.front)(helper, 1, '/usr/bin/kubectl', {'commands': [], 'once_claimed': True, 'patch_request_count': 1})
        for args in (['delete', 'pod', 'x'], ['patch', 'deployment', 'gateway', '--type=json', '--patch-file=/proc/self/fd/5', '-o', 'json'],
                     ['patch', 'deployment', 'nginx-security-heai-20261012-r1', '--type=json', '--patch-file=/proc/self/fd/5', '-o', 'json']):
            with self.assertRaises(self.mod.Refused):
                e.call('forbidden', args, pass_fds=(5,))


    @unittest.skipUnless(shutil.which('node'), 'Node runtime required for the JS harness')
    def test_node_runtime_probe_hashes_files_and_actual_http_response_bytes(self):
        harness = r"""
const vm=require('vm'),EventEmitter=require('events'),crypto=require('crypto');
const mode=process.argv[1], code=JSON.parse(process.argv[2]);
const paths=['/app/frontend/.next/BUILD_ID','/app/frontend/.next/routes-manifest.json','/app/frontend/.next/static/chunks/a.js','/app/frontend/.next/static/chunks/b.js'];
const contents=Object.fromEntries(paths.map((p,i)=>[p,Buffer.from('content'+i)]));
const pins=Object.fromEntries(paths.map(p=>[p,{bytes:contents[p].length,sha256:crypto.createHash('sha256').update(contents[p]).digest('hex')}]));
const fs={constants:{O_RDONLY:0,O_NOFOLLOW:1},realpathSync:p=>p,openSync:p=>p,closeSync:()=>{},
 fstatSync:p=>({isFile:()=>true,size:contents[p].length,dev:1,ino:1,mtimeMs:1,ctimeMs:1}),
 readFileSync:p=>mode==='badfile'?Buffer.from('changed'):contents[p]};
const http={get:(opts,callback)=>{const q=new EventEmitter();q.destroy=e=>q.emit('error',e);
 process.nextTick(()=>{const r=new EventEmitter();r.statusCode=(mode==='redirect'&&opts.path!=='/')?302:200;callback(r);
 const path=opts.path.replace('/_next/static/','/app/frontend/.next/static/');
 r.emit('data',opts.path==='/'?Buffer.from('home'):mode==='badhttp'?Buffer.from('wrong'):contents[path]);r.emit('end')});return q}};
vm.runInNewContext(code,{require:n=>n==='fs'?fs:n==='http'?http:require(n),console,process:{argv:['probe',JSON.stringify(pins)],exit:c=>process.exit(c)},setTimeout});
"""
        for mode, expected in (('good', 0), ('badfile', 1), ('badhttp', 1), ('redirect', 1)):
            result = subprocess.run([shutil.which('node'), '-e', harness, mode, json.dumps(self.mod.runtime_code())],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
            self.assertEqual(result.returncode, expected, result.stderr.decode(errors='replace'))
            if expected == 0:
                self.assertEqual(json.loads(result.stdout)['static_assets_verified'], 2)


    def test_bound_generation_and_distinct_original_and_recheck_evidence(self):
        m = candidate()
        self.assertEqual(m.FRONT, builder.FRONT_EXPECTED)
        self.assertEqual(m.FRONT['generation'], 241)
        self.assertEqual(m.OLD, builder.OLD)
        self.assertEqual(m.MANIFEST, FIXED_MANIFEST)
        self.assertEqual(m.CONFIG, FIXED_CONFIG)
        self.assertEqual(m.GATEWAY, FIXED_GATEWAY)
        self.assertEqual(m.GATEWAY_IMAGE, FIXED_GATEWAY_IMAGE)
        self.assertEqual(m.PRELOADS['ecs-zdz-pro-0015']['status'], 'COMPLETE_FRONTEND_UI_CACHE_PRELOAD_REVIEW_REQUIRED')
        self.assertEqual(m.PRELOADS['datax-02']['status'], 'INCOMPLETE')
        self.assertEqual(m.PRELOADS['datax-02']['report_bytes'], 115986)
        self.assertEqual(m.RECHECKS['datax-02']['schema'], 'portal-frontend-sidechat-cache-recheck.v1')
        self.assertEqual(m.RECHECKS['datax-02']['status'], 'FRONTEND_SIDECHAT_CACHE_RECHECK_ACCEPTED_NOT_DEPLOYED')
        self.assertEqual(m.RECHECKS['datax-02']['report_bytes'], 21915)
        self.assertNotEqual(m.RECHECKS['datax-02']['report_path'], m.PRELOADS['datax-02']['report_path'])
        self.assertEqual(m.RECHECKS['datax-02']['prior_receipt']['sha256'], m.PRELOADS['datax-02']['report_sha256'])
        m.validate_preloads(m.PRELOADS, m.RECHECKS)
        ast.parse(SOURCE, feature_version=(3, 7))

    def test_preload_report_pin_identity_status_and_integrity_drift_rejected(self):
        m = candidate()
        changes = [('schema', 'other'), ('hostname', 'other'), ('expected_node_uid', 'other'),
                   ('report_bytes', 1), ('report_sha256', '0' * 64), ('operation', '0' * 32),
                   ('report_path', '/var/lib/unrelated/REPORT.json'),
                   ('manifest_digest', 'sha256:' + '0' * 64), ('config_digest', 'sha256:' + '0' * 64),
                   ('phase_a', 'FAIL'), ('phase_b', 'FAIL'), ('preclaim_passed', False),
                   ('production_workload_mutation', True)]
        for host in m.NODES:
            for key, value in changes:
                rows = copy.deepcopy(m.PRELOADS)
                rows[host][key] = value
                with self.subTest(host=host, key=key), self.assertRaises(m.Refused):
                    m.validate_preloads(rows, m.RECHECKS)
        for host, key, value in [('ecs-zdz-pro-0015', 'status', 'INCOMPLETE'),
                                 ('ecs-zdz-pro-0015', 'old_references_preserved', False),
                                 ('ecs-zdz-pro-0015', 'cri_visibility', None),
                                 ('ecs-zdz-pro-0015', 'image_import_may_be_partial', True),
                                 ('datax-02', 'status', 'COMPLETE_FRONTEND_UI_CACHE_PRELOAD_REVIEW_REQUIRED'),
                                 ('datax-02', 'error_code', 'other_failure'),
                                 ('datax-02', 'failure_phase', 'other_phase')]:
            rows = copy.deepcopy(m.PRELOADS)
            rows[host][key] = value
            with self.subTest(host=host, key=key), self.assertRaises(m.Refused):
                m.validate_preloads(rows, m.RECHECKS)

    def test_independent_recheck_and_prior_failure_binding_cannot_be_omitted_or_relabelled(self):
        m = candidate()
        with self.assertRaises(m.Refused):
            m.validate_preloads(m.PRELOADS, {})
        changes = [('schema', 'portal-frontend-ui-cache-preload.v1'), ('status', 'INCOMPLETE'),
                   ('hostname', 'other'), ('expected_node_uid', 'other'),
                   ('report_bytes', 1), ('report_sha256', '0' * 64), ('operation', '0' * 32),
                   ('report_path', '/var/lib/other/REPORT.json'),
                   ('manifest_digest', 'sha256:' + '0' * 64), ('config_digest', 'sha256:' + '0' * 64),
                   ('stable_readback', False), ('old_references_preserved', False),
                   ('original_marker_preserved', False), ('original_receipt_modified', True),
                   ('image_import_requested', True), ('image_retag_requested', True), ('image_deleted', True),
                   ('configuration_modified', True), ('production_workload_mutation', True),
                   ('content_and_unpack', 'FAIL'), ('cri_visibility', 'FAIL'),
                   ('content_blobs_present', 12), ('snapshot_chains_verified', 10),
                   ('protected_reference_count', 30), ('queries', 79)]
        for key, value in changes:
            rechecks = copy.deepcopy(m.RECHECKS)
            rechecks['datax-02'][key] = value
            with self.subTest(key=key), self.assertRaises(m.Refused):
                m.validate_preloads(m.PRELOADS, rechecks)
        for key, value in [('bytes', 1), ('sha256', '0' * 64), ('operation', '0' * 32),
                           ('path', '/var/lib/other/REPORT.json'), ('commands', 199),
                           ('status', 'PASS'), ('error_code', 'other'), ('verified_stop', 'unknown')]:
            rechecks = copy.deepcopy(m.RECHECKS)
            rechecks['datax-02']['prior_receipt'][key] = value
            with self.subTest(prior_key=key), self.assertRaises(m.Refused):
                m.validate_preloads(m.PRELOADS, rechecks)

    def test_builder_requires_all_six_converged_deployments(self):
        for name in BASELINE['deployments']:
            for key, value in [('ready', 0), ('updated', 0), ('terminating', 1), ('paused', True),
                               ('observed_generation', 0), ('total', 999)]:
                value_record = copy.deepcopy(BASELINE)
                value_record['deployments'][name][key] = value
                with self.subTest(name=name, key=key), self.assertRaises(ValueError):
                    builder.baseline_binding(value_record)
            value_record = copy.deepcopy(BASELINE)
            del value_record['deployments'][name]
            with self.subTest(missing=name), self.assertRaises(ValueError):
                builder.baseline_binding(value_record)
        for key, value in [('generation', 242), ('uid', 'other'), ('spec_sha256', '0' * 64),
                           ('image', 'deerflow.local/deer-flow-frontend@sha256:' + '0' * 64)]:
            value_record = copy.deepcopy(BASELINE)
            value_record['deployments']['frontend'][key] = value
            if key == 'generation':
                value_record['deployments']['frontend']['observed_generation'] = value
            with self.subTest(frontend_key=key), self.assertRaises(ValueError):
                builder.baseline_binding(value_record)

    def test_builder_refuses_changed_qualification_source_and_browser_hashes(self):
        for name in ('RESULT.json', 'SOURCE.json', 'BROWSER-QUALIFICATION.json'):
            with tempfile.TemporaryDirectory() as td:
                args = synthetic_inputs(Path(td))
                path = args[0] / name
                path.write_bytes(path.read_bytes() + b' ')
                with self.subTest(file=name), self.assertRaises(ValueError):
                    builder.build(*args)

    def test_builder_revalidates_operator_field_and_actual_image_scope(self):
        for section, field, value in [('preloads', 'report_sha256', '0' * 64),
                                      ('preloads', 'status', 'PASS'),
                                      ('rechecks', 'report_sha256', '0' * 64),
                                      ('rechecks', 'status', 'PASS')]:
            with tempfile.TemporaryDirectory() as td:
                args = synthetic_inputs(Path(td))
                value_record = json.loads(args[1].read_bytes())
                value_record[section]['datax-02'][field] = value
                args[1].write_text(json.dumps(value_record), encoding='utf-8')
                with self.subTest(section=section, field=field), self.assertRaises(Exception):
                    builder.build(*args)
        for section, key, value in [('PACKAGE.json', 'manifest', 'sha256:' + '0' * 64),
                                  ('PACKAGE.json', 'config', 'sha256:' + '0' * 64),
                                  ('COMPILED-ASSETS.json', 'image', 'sha256:' + '0' * 64),
                                  ('COMPILED-ASSETS.json', 'status', 'PENDING')]:
            with tempfile.TemporaryDirectory() as td:
                args = synthetic_inputs(Path(td))
                path = args[0] / section
                value_record = json.loads(path.read_bytes())
                value_record[key] = value
                path.write_text(json.dumps(value_record), encoding='utf-8')
                with self.subTest(file=section, key=key), self.assertRaises(ValueError):
                    builder.build(*args)


    def test_concurrent_nginx_changes_block_acceptance(self):
        for role in ('nginx', 'nginx-security-heai-20261012-r1'):
            with self.subTest(role=role):
                self.flow(role)

    def test_fresh_protected_baseline_drift_stops_before_claim_jobs_or_patch(self):
        for role in ('gateway', 'nginx', 'nginx-security-heai-20261012-r1', 'provisioner', 'frontend'):
            m = candidate()
            before = {name: copy.deepcopy(self.d) for name in m.ROLES}
            for name, d in before.items():
                d['metadata']['name'] = name
            m.FRONT = m.binding(before['frontend'])
            m.PROTECTED_BASELINE = {name: m.binding(d) for name, d in before.items()}
            before[role]['metadata']['generation'] += 1
            effects = []
            execution = types.SimpleNamespace(report={}, H=None,
                get=lambda *args: {'items': list(before.values())}, write=lambda *args: effects.append(args))
            with self.subTest(role=role), self.assertRaises(m.Refused):
                m.execute(execution, self.front)
            self.assertEqual(effects, [])
            self.assertFalse(execution.report.get('once_claimed'))

    def test_builder_requires_original_transport_template_and_complete_asset_markers(self):
        with tempfile.TemporaryDirectory() as td:
            args = synthetic_inputs(Path(td))
            with patch.object(builder, 'TEMPLATE_SHA', '0' * 64), self.assertRaisesRegex(ValueError, 'activation_template_changed'):
                builder.build(*args)
            assets_path = args[0] / 'COMPILED-ASSETS.json'
            original = json.loads(assets_path.read_bytes())
            for key in builder.REQUIRED_MARKERS:
                changed = copy.deepcopy(original)
                del changed['feature_markers'][key]
                assets_path.write_text(json.dumps(changed), encoding='utf-8')
                with self.subTest(marker=key), self.assertRaisesRegex(ValueError, 'required_sidechat_and_preserved_markers_missing'):
                    builder.build(*args)
            assets_path.write_text(json.dumps(original), encoding='utf-8')
            changed = copy.deepcopy(original)
            changed['source_inventory_sha256'] = '0' * 64
            assets_path.write_text(json.dumps(changed), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'compiled_asset_proof_required'):
                builder.build(*args)

    def test_duplicate_or_nonfinite_evidence_rejected(self):
        for raw in (b'{"schema": "one", "schema": "two"}', b'{"value": NaN}', b'[]'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                builder.parse(raw)


if __name__ == '__main__':
    unittest.main(verbosity=2)
