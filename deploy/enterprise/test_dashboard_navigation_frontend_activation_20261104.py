"""Synthetic frontend rollout transport, field bindings and live Gateway 406 gates."""
import ast
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('navigation_activation_builder', HERE / 'package-dashboard-navigation-frontend-activation-20261104.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
SOURCE = builder.build().decode()

def candidate():
    m = types.ModuleType('synthetic_navigation_activation')
    exec(compile(SOURCE, '<activation>', 'exec'), m.__dict__)
    return m

class Tests(unittest.TestCase):
    def test_actual_field_and_local_image_are_bound(self):
        m = candidate()
        self.assertEqual(m.PRELOADS, json.loads((builder.RELEASE / 'PRELOAD-FIELD.json').read_bytes())['preloads'])
        self.assertEqual(m.FRONT, builder.FRONT)
        self.assertEqual(m.GATEWAY, builder.GATEWAY)
        self.assertEqual(m.GATEWAY_IMAGE, builder.GATEWAY_IMAGE)
        self.assertEqual(m.MANIFEST, builder.MANIFEST)
        self.assertEqual(len(m.SOURCE_PINS), 10)
        m.validate_preloads(m.PRELOADS)
        self.assertNotIn('read_gateway_private', SOURCE)
        self.assertNotIn('gateway_403_acceptance_required', SOURCE)
        ast.parse(SOURCE, feature_version=(3, 7))

    def test_wrong_host_cannot_create_claim_jobs_or_patch(self):
        m = candidate()
        with patch.object(m.socket, 'gethostname', return_value='synthetic-test-host'), \
                patch.object(m, 'load_modules', side_effect=AssertionError('must not load transport')), \
                patch.object(m.os, 'mkdir', side_effect=AssertionError('must not write')):
            self.assertEqual(m.run(), 1)

    def gateway_fixture(self, m):
        d = copy.deepcopy(self.d)
        d['metadata'].update(name='gateway', uid=builder.GATEWAY['uid'], generation=406)
        d['spec']['replicas'] = 70
        d['spec']['template']['spec']['containers'][0].update(name='gateway', image=m.GATEWAY_IMAGE)
        d['status'].update(observedGeneration=406, replicas=70, updatedReplicas=70, readyReplicas=70, availableReplicas=70)
        m.GATEWAY = m.binding(d)
        m.PRIOR_RECEIPT = dict(scope='operator_observed_deployment_identity', gateway=m.GATEWAY, frontend=m.FRONT)
        return d

    def test_live_gateway_readiness_identity_and_pause_are_required(self):
        m = candidate()
        d = self.gateway_fixture(m)
        e = types.SimpleNamespace(H=None, report={})
        m.check_backend_contract(e, d)
        for field, value in [('observedGeneration', 405), ('replicas', 71), ('updatedReplicas', 69),
                             ('readyReplicas', 69), ('availableReplicas', 69), ('terminatingReplicas', 1)]:
            changed = copy.deepcopy(d)
            changed['status'][field] = value
            with self.subTest(field=field), self.assertRaises(m.Refused):
                m.check_backend_contract(e, changed)
        for mutation in (lambda x: x['metadata'].update(uid='other'),
                         lambda x: x['metadata'].update(generation=407),
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
        effects = []
        e = types.SimpleNamespace(report={}, H=None,
            get=lambda *args: {'items': list(before.values())}, write=lambda *args: effects.append(args))
        with self.assertRaises(self.mod.Refused):
            self.mod.execute(e, self.front)
        self.assertEqual(effects, [])
        self.assertFalse(e.report.get('once_claimed'))

    def test_fresh_gateway_contract_is_checked_at_all_boundaries(self):
        execute = next(n for n in ast.parse(SOURCE).body if isinstance(n, ast.FunctionDef) and n.name == 'execute')
        calls = [n for n in ast.walk(execute) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == 'check_backend_contract']
        self.assertEqual(len(calls), 3)

    def setUp(self):
        self.mod = candidate()
        self.rows = self.mod.PRELOADS
        self.mod.SOURCE_PINS = {'/app/frontend/.next/static/chunks/a.js': {'bytes': 1, 'sha256': 'c' * 64}}
        self.front, self.helper = self.mod.load_modules()
        self.d = {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': {'name': 'frontend', 'namespace': 'deer-flow',
            'uid': self.mod.FRONT['uid'], 'generation': 239, 'resourceVersion': '101'},
            'spec': {'replicas': 2, 'template': {'spec': {'containers': [{'name': 'frontend', 'image': self.mod.OLD,
                'imagePullPolicy': 'Never', 'env': [{'name': 'KEEP', 'value': 'yes'}]}]}}},
            'status': {'observedGeneration': 239, 'replicas': 2, 'updatedReplicas': 2, 'readyReplicas': 2, 'availableReplicas': 2}}
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

    def flow(self, drift=False, cache_failure=False):
        before = {name: copy.deepcopy(self.d) for name in self.mod.ROLES}
        for name,d in before.items():
            d['metadata']['name'] = name
        final = copy.deepcopy(before)
        final['frontend']['metadata']['generation'] = 240
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
        with patch.object(self.mod,'check_backend_contract'), patch.object(self.mod,'require_frontend_coverage'), patch.object(self.mod,'services',return_value={}), patch.object(self.mod,'frontend_snapshot',return_value=(pods,{'same':True})), patch.object(self.mod, 'verify_local_cache', side_effect=verify_cache):
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

    def nodes(self):
        return [{'metadata': {'name': name, 'uid': uid}, 'status': {'conditions': [
            {'type': key, 'status': 'True' if key == 'Ready' else 'False'} for key in ('Ready', 'MemoryPressure', 'DiskPressure', 'PIDPressure')],
            'nodeInfo': {'architecture': 'arm64'}, 'images': [{'names': [self.mod.NEW]}]}} for name, uid in self.mod.NODES.items()]

    def patch_execution(self, result_ready=True):
        actions = self.front.prepare_patch(self.d, self.d)
        current = copy.deepcopy(self.d)
        current['metadata'].update(generation=240, resourceVersion='102')
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
        for args in (['delete', 'pod', 'x'], ['patch', 'deployment', 'gateway', '--type=json', '--patch-file=/proc/self/fd/5', '-o', 'json']):
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

if __name__ == '__main__':
    unittest.main()
