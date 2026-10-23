"""Offline receipt, CAS, runtime-probe, and preservation tests. No cluster access."""
import ast
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
PRIOR = Path(os.environ.get('FRONTEND_PRIOR_ACTIVATOR', 'C:/Users/15285/Downloads/activate-portal-frontend-employee-roster-20261020-r2.py'))


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), HERE / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ActivationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pack = load('package-data-qa-progress-frontend-activation.py')
        cls.host = load('activate-data-qa-progress-frontend.py')
        cls.proven = cls.pack.prior_common(PRIOR)
        cls.backend_source = load('package-oa-feature-activation.py').build()
        cls.preloads, unused = cls.pack.preload_evidence(HERE / 'data-qa-progress-frontend-preloads-field-20261022-r2.json')
        cls.backend_pin = {'path': '/var/lib/portal-gateway-oa-data-qa-activation-20261022-r1/REPORT.json',
                           'bytes': 500000, 'sha256': 'a' * 64}
        cls.source = cls.pack.assemble(cls.preloads, cls.backend_pin, cls.proven, cls.backend_source, {'synthetic_test_only': True})
        cls.mod = types.ModuleType('packaged_test_only')
        exec(compile(cls.source, '<test-package>', 'exec'), cls.mod.__dict__)

    def setUp(self):
        self.front, self.helper, self.backend = self.mod.load_modules()
        self.original_front_pin = copy.deepcopy(self.mod.FRONT)
        self.d = {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': {'name': 'frontend', 'namespace': 'deer-flow',
            'uid': self.mod.FRONT['uid'], 'generation': 225, 'resourceVersion': '101'},
            'spec': {'replicas': 2, 'template': {'spec': {'containers': [{'name': 'frontend', 'image': self.mod.OLD,
                'imagePullPolicy': 'Never', 'env': [{'name': 'KEEP', 'value': 'yes'}]}]}}},
            'status': {'observedGeneration': 225, 'replicas': 2, 'updatedReplicas': 2, 'readyReplicas': 2, 'availableReplicas': 2}}
        self.mod.FRONT = self.mod.binding(self.d)
        self.front.OLD_SPEC = self.mod.FRONT['spec_sha256']

    def tearDown(self):
        self.mod.FRONT = self.original_front_pin

    def good_backend(self):
        bindings = {k: {x: v[x] for x in ('uid', 'generation', 'spec_sha256')} for k, v in self.backend.EXPECTED.items()}
        before = {'deployments': bindings, 'frontend': copy.deepcopy(self.mod.FRONT),
                  'configuration_metadata': {'secret/test': ['uid', '1']}, 'services': {}, 'node_coverage': {'status': 'PASS'}}
        after = copy.deepcopy(before)
        after['deployments']['gateway'].update(generation=384, spec_sha256='b' * 64)
        obs = {'status': 'PASS', 'sources_verified': True, 'v2_enabled': False, 'local_health_http_status': 200,
               'scheduler_budget': 300, 'oa_budget': 1000, 'provider_requests': 0, 'oa_callback_requests': 0}
        return {'status': self.mod.BACKEND_ACCEPTED, 'runtime_accepted': True, 'feature_image_deployed': True,
                'v2_enabled': False, 'frontend_preserved': True, 'runner_preserved': True,
                'configuration_metadata_preserved': True, 'gateway_other_spec_preserved': True, 'patch_acknowledged': True,
                'patch_request_count': 1, 'schema_migration_requested': False, 'schema_revision': self.mod.TARGET,
                'provider_requests': 0, 'oa_callback_requests': 0, 'automatic_retry': False,
                'before': before, 'after': after, 'gateway_after': after['deployments']['gateway'],
                'runtime': [{'pod': 'gateway-%d' % n, 'uid': 'uid-%d' % n, 'observation': copy.deepcopy(obs)} for n in range(70)],
                'database_after': {'status': 'PASS', 'database_readonly': True, 'database_fingerprint': self.backend.DATABASE_FINGERPRINT,
                                   'revisions': [self.mod.TARGET]},
                'runner_compatibility_after': {'status': 'PASS', 'database_readonly': True,
                    'database_fingerprint': self.backend.DATABASE_FINGERPRINT, 'schema_revision': self.mod.TARGET}}

    def nodes(self):
        return [{'metadata': {'name': name, 'uid': uid}, 'status': {'conditions': [
            {'type': key, 'status': 'True' if key == 'Ready' else 'False'} for key in ('Ready', 'MemoryPressure', 'DiskPressure', 'PIDPressure')],
            'nodeInfo': {'architecture': 'arm64'}, 'images': [{'names': [self.mod.NEW]}]}} for name, uid in self.mod.NODES.items()]

    def test_actual_preloads_pass_and_are_embedded_exactly(self):
        self.mod.validate_preloads(self.preloads)
        self.assertEqual(self.mod.PRELOADS, self.preloads)
        self.assertEqual({r['report_bytes'] for r in self.preloads.values()}, {56171, 50559})

    def test_preloads_fail_on_hash_identity_status_uid_and_preservation(self):
        for key, value in [('report_sha256', ''), ('manifest_digest', self.mod.OLD), ('expected_node_uid', 'changed'),
                           ('preclaim_passed', False), ('old_references_preserved', False), ('phase_b', 'FAIL'),
                           ('report_path', '/tmp/REPORT.json'), ('production_workload_mutation', True)]:
            rows = copy.deepcopy(self.preloads)
            rows['datax-02'][key] = value
            with self.subTest(key=key), self.assertRaises(self.mod.Refused):
                self.mod.validate_preloads(rows)

    def test_unknown_or_failed_backend_refuses_packaging_without_output(self):
        with tempfile.TemporaryDirectory() as td:
            absent = Path(td) / 'absent.json'
            with self.assertRaisesRegex(ValueError, 'not_yet_available'):
                self.pack.build(HERE / 'data-qa-progress-frontend-preloads-field-20261022-r2.json', absent, PRIOR)
            bad = Path(td) / 'failed.json'
            bad.write_text(json.dumps({'status': 'INCOMPLETE'}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'receipt_required'):
                self.pack.backend_evidence(bad)
            self.assertEqual({p.name for p in Path(td).iterdir()}, {'failed.json'})

    def test_backend_pin_accepts_bounded_full_report_over_64k(self):
        self.mod.validate_backend_pin(self.backend_pin)
        for bad in (dict(self.backend_pin, bytes=1024**2 + 1), dict(self.backend_pin, path='/tmp/x'), dict(self.backend_pin, sha256='')):
            with self.assertRaises(self.mod.Refused):
                self.mod.validate_backend_pin(bad)

    def test_superseded_candidate_cannot_be_packaged_even_with_backend_receipt(self):
        with patch.object(self.pack, 'backend_evidence', return_value=(self.backend_pin, {'bytes': 1, 'sha256': 'b' * 64})):
            with self.assertRaisesRegex(ValueError, 'candidate_superseded'):
                self.pack.build(Path('unused'), Path('synthetic-accepted'), PRIOR)

    def test_full_backend_acceptance_requires_all_70_runner55_schema0021(self):
        good = self.good_backend()
        self.mod.backend_gate(good, self.backend)
        changes = [lambda r: r['runtime'].pop(), lambda r: r['runtime'][0]['observation'].update(sources_verified=False),
                   lambda r: r['after']['deployments']['ppt-master-runner'].update(generation=56),
                   lambda r: r['database_after'].update(revisions=['0020_ppt_confirmation']),
                   lambda r: r.update(v2_enabled=True), lambda r: r.update(configuration_metadata_preserved=False)]
        for mutate in changes:
            r = copy.deepcopy(good)
            mutate(r)
            with self.assertRaises(self.mod.Refused):
                self.mod.backend_gate(r, self.backend)

    def test_native_proven_functions_are_ast_identical(self):
        original = ast.parse(PRIOR.read_text(encoding='utf-8'))
        selected = ast.parse(self.proven)
        for name in self.pack.PRIOR_NAMES:
            a = next(n for n in original.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == name)
            b = next(n for n in selected.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == name)
            self.assertEqual(ast.dump(a), ast.dump(b), name)
        ast.parse(self.source, feature_version=(3, 7))

    def test_patch_changes_only_image_and_tests_complete_old_spec_rv(self):
        actions = self.front.prepare_patch(self.d, copy.deepcopy(self.d))
        self.assertEqual([p for p in actions if p['op'] != 'test'], [
            {'op': 'replace', 'path': '/spec/template/spec/containers/0/image', 'value': self.mod.NEW}])
        tests = {p['path']: p['value'] for p in actions if p['op'] == 'test'}
        self.assertEqual(tests['/spec'], self.d['spec'])
        self.assertEqual(tests['/metadata/resourceVersion'], '101')
        self.assertEqual(tests['/metadata/generation'], 225)
        drift = copy.deepcopy(self.d)
        drift['spec']['template']['spec']['containers'][0]['env'][0]['value'] = 'changed'
        with self.assertRaises(self.mod.Refused):
            self.front.prepare_patch(drift, self.d)

    def test_fresh_node_gate_rejects_uid_pressure_missing_cache(self):
        self.front.node_gate(self.nodes())
        for mutate in (lambda n: n[0]['metadata'].update(uid='other'),
                       lambda n: n[0]['status']['conditions'][2].update(status='True'),
                       lambda n: n[0]['status'].update(images=[])):
            nodes = self.nodes()
            mutate(nodes)
            with self.assertRaises(self.mod.Refused):
                self.front.node_gate(nodes)

    def patch_execution(self, result_ready=True):
        actions = self.front.prepare_patch(self.d, self.d)
        current = copy.deepcopy(self.d)
        current['metadata'].update(generation=226, resourceVersion='102')
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

    def test_full_flow_two_runtime_checks_and_preservation_gates(self):
        self.flow(False)

    def test_configuration_drift_after_dispatch_blocks_acceptance(self):
        self.flow(True)

    def flow(self, drift):
        before = {name: copy.deepcopy(self.d) for name in self.mod.ROLES}
        for name, d in before.items():
            d['metadata']['name'] = name
        final = copy.deepcopy(before)
        final['frontend']['metadata']['generation'] = 226
        final['frontend']['spec']['template']['spec']['containers'][0]['image'] = self.mod.NEW
        pods = [{'metadata': {'name': 'frontend-' + str(i), 'uid': 'pod-' + str(i)}, 'spec': {'nodeName': name},
                 'status': {'containerStatuses': [{'containerID': 'containerd://id' + str(i), 'restartCount': 0, 'imageID': self.mod.NEW}]}}
                for i, name in enumerate(sorted(self.mod.NODES))]
        writes, calls = [], []
        test = self
        class E:
            report = {'operation': 'a' * 32, 'directory': '/var/lib/test', 'patch_request_count': 0}
            started = time.monotonic()
            parent = 1
            H = types.SimpleNamespace(write_new=lambda *args: writes.append(args[1]))
            def get(self, phase, args):
                if args[0] == 'nodes':
                    return {'items': test.nodes()}
                if args[0] == 'service':
                    return {'same': True}
                if args[0] == 'deployment':
                    return final['frontend']
                return {'items': list((final if phase == 'after-deployments' else before).values())}
            def write(self, name, value):
                writes.append(name)
            def patch_once(self, value):
                test.assertIn(test.mod.ONCE, writes)
                self.report['patch_request_count'] += 1
                return final['frontend']
            def call(self, phase, args):
                calls.append(args)
                return json.dumps({'status': 'PASS', 'sources_verified': True, 'static_assets_verified': 2,
                    'provider_requests': 0, 'http_status': 200, 'bytes': 10}).encode(), {}
        e = E()
        cfg = {'secret/a': ['uid', '1']}
        configs = [cfg, cfg, {'secret/a': ['uid', '2']} if drift else cfg]
        backend_obs = {'pods': {'gateway': 'same', 'runner': 'same'}}
        with patch.object(self.mod, 'backend_live', return_value=backend_obs), \
             patch.object(self.mod, 'metadata', side_effect=configs), patch.object(self.mod, 'services', return_value={}), \
             patch.object(self.mod, 'frontend_snapshot', return_value=(pods, {'same': True})):
            if drift:
                with self.assertRaisesRegex(self.mod.Refused, 'configuration_or_frontend_changed'):
                    self.mod.execute(e, self.front, self.backend, {'configuration_metadata': cfg, 'services': {}})
                self.assertNotEqual(e.report.get('status'), self.mod.ACCEPTED)
            else:
                self.mod.execute(e, self.front, self.backend, {'configuration_metadata': cfg, 'services': {}})
                self.assertEqual(e.report['status'], self.mod.ACCEPTED)
                self.assertTrue(e.report['browser_acceptance_pending'])
        self.assertEqual(e.report['patch_request_count'], 1)
        self.assertEqual(len(calls), 2)

    @unittest.skipUnless(shutil.which('node'), 'local Node unavailable; no installation requested')
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
    unittest.main(verbosity=2)
