import ast
import copy
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import accept_enrichment as a
import diagnose_enrichment_startup as d
import startup_safe_diagnostics as s

HERE = Path(__file__).resolve().parent
RUNTIME = a.RUNTIME_SOURCE or (HERE/'enrichment_fixture_runtime.py').read_text()
PANIC = b'''panic: secret=DO_NOT_PRINT postgres://user:secret@host/db

goroutine 1 [running]:
github.com/Tencent/WeKnora/internal/container.must({0x1, 0x2})
    /private/source/internal/container/container.go:537 +0x40
go.uber.org/dig.(*Container).Invoke(0xabcd, 0xef)
    /go/pkg/mod/go.uber.org/dig@v1/invoke.go:100 +0x123
main.main()
    /private/source/cmd/server/main.go:80 +0x99
'''


class ProjectionTests(unittest.TestCase):
    def test_stack_no_arguments_or_paths(self):
        r = s.safe_startup(PANIC)
        self.assertEqual(r['stack'], [
            dict(function='github.com/Tencent/WeKnora/internal/container.must', file='container.go', line=537),
            dict(function='go.uber.org/dig.(*Container).Invoke', file='invoke.go', line=100),
            dict(function='main.main', file='main.go', line=80)])
        for value in ('DO_NOT_PRINT', 'secret', 'postgres://', '/private', '0xabcd'):
            self.assertNotIn(value, json.dumps(r))

    def test_nested_argument_parentheses_not_exported(self):
        r = s.safe_startup(b'main.main(secret(value))\n    /x/main.go:1 +0x1\n')
        self.assertEqual(r['stack'], [dict(function='main.main', file='main.go', line=1)])

    def test_unknown_packages_and_non_stack_lines_not_exported(self):
        for text in (b'example.com/private.password(secret)\n    /x/main.go:1\n',
                     b'main.main(secret)\nraw message with secret\n', b'panic: password=secret'):
            self.assertEqual(s.safe_startup(text)['stack'], [])

    def test_fixed_classification_only(self):
        raw = b'panic: invalid memory address or nil pointer dereference\nunable to decode config into struct: secret\n'
        r = s.safe_startup(raw)
        self.assertTrue(r['classes']['nil_pointer']); self.assertTrue(r['classes']['config_decode'])
        self.assertNotIn('secret', json.dumps(r))
        self.assertTrue(all(isinstance(x, bool) for x in r['classes'].values()))

    def test_invalid_utf8_and_bounded_frames(self):
        r = s.safe_startup(b'\xff'+PANIC*100)
        self.assertEqual(len(r['stack']), 40)

    def test_runtime_patched_compiles_python37(self):
        self.assertEqual(hashlib.sha256(RUNTIME.encode()).hexdigest(), d.RUNTIME_SHA)
        fixed = s.augment_runtime(RUNTIME)
        ast.parse(fixed, feature_version=(3, 7))
        self.assertEqual(fixed.count('startup=safe_startup('), 2)
        # All non-diagnostic functions must remain byte-for-byte AST identical.
        before = {f.name: ast.dump(f) for f in ast.parse(RUNTIME).body if isinstance(f, ast.FunctionDef)}
        after = {f.name: ast.dump(f) for f in ast.parse(fixed).body if isinstance(f, ast.FunctionDef)}
        for name in before:
            if name not in ('progress', 'supervise'): self.assertEqual(before[name], after[name], name)

    def test_patched_live_and_stop_diagnostics_executed(self):
        scope = {'__name__': 'offline_fixture'}
        exec(compile(s.augment_runtime(RUNTIME), 'runtime.py', 'exec'), scope)
        with tempfile.TemporaryDirectory() as temp:
            scope['STATE'] = Path(temp)
            scope['progress']('bootstrap', PANIC, False)
            scope['save']('STOPPED-bootstrap.json', dict(exit_code=2, classes=scope['diagnostics'](PANIC),
                startup=scope['safe_startup'](PANIC), logs_truncated=False, raw_logs_saved=False))
            for name in ('LIVE-bootstrap.json', 'STOPPED-bootstrap.json'):
                value = json.loads((Path(temp)/name).read_text())
                self.assertEqual(len(value['startup']['stack']), 3)
                self.assertNotIn('DO_NOT_PRINT', json.dumps(value))

    def test_changed_runtime_shape_rejected(self):
        for old in ('def progress(role,raw,overflow):', 'dict(classes=diagnostics(raw),logs_truncated=overflow,raw_logs_saved=False)',
                    "save('STOPPED-'+role+'.json',dict(exit_code=p.returncode,classes=diagnostics(tail),"):
            with self.assertRaisesRegex(RuntimeError, 'shape_changed'):
                s.augment_runtime(RUNTIME.replace(old, 'CHANGED'))

    def test_real_child_panic_safe_projection_survives_exit(self):
        scope = {'__name__': 'offline_fixture'}
        exec(compile(s.augment_runtime(RUNTIME), 'runtime.py', 'exec'), scope)
        with tempfile.TemporaryDirectory() as temp:
            scope['STATE'] = Path(temp); scope['ROOT'] = Path(temp)
            (Path(temp)/'app').mkdir(); (Path(temp)/'START-bootstrap').touch()
            scope['prepare'] = lambda: None; scope['environment'] = lambda role: {'PATH':'/usr/bin:/bin'}
            real_progress = scope['progress']; real_popen = subprocess.Popen
            def progress(*args):
                real_progress(*args); (Path(temp)/'STOP-ALL').touch()
            def child(args, **kwargs):
                self.assertEqual(args, ['/app/WeKnora'])
                return real_popen([sys.executable, '-I', '-B', '-c', 'import sys;sys.stdout.buffer.write('+repr(PANIC)+');sys.exit(2)'], **kwargs)
            scope['progress'] = progress
            with patch.object(subprocess,'Popen',side_effect=child): scope['supervise']()
            row=json.loads((Path(temp)/'STOPPED-bootstrap.json').read_text())
            self.assertEqual(row['exit_code'],2); self.assertEqual(row['classes']['panic'],1)
            self.assertEqual(len(row['startup']['stack']),3)
            self.assertNotIn('DO_NOT_PRINT',json.dumps(row))


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/root'); self.root = Path(self.temp.name)
        self.stack = ExitStack()
        self.old = self.root/'old'; self.old.mkdir(mode=0o700)
        self.tool = self.root/'oldtool.py'; self.tool.write_text('not executed')
        self.row = dict(failed_check='isolated_bootstrap_exited_before_health', failed_stage='NATIVE_FRESH_SCHEMA_MIGRATION',
            document_submission_attempted=False, production_writes=0, cleanup='NAMESPACE_ABSENT_VERIFIED', production_baseline='UNCHANGED')
        self.oldraw = json.dumps(self.row).encode(); (self.old/'RESULT.json').write_bytes(self.oldraw)
        self.stop = dict(exit_code=2, classes=dict(panic=1), logs_truncated=False, startup=s.safe_startup(PANIC))
        diag = dict(runtime={'STOPPED-bootstrap.json':dict(exit_code=2, classes=dict(panic=1), logs_truncated=False)},
                    postgres_startup=dict(database_ready=True))
        (self.old/'FAILURE-DIAGNOSTICS.json').write_text(json.dumps(diag))
        ack = dict(name='weknora-enrich-check-ba5d3a44a7984ea0', uid='29417cd4-36fa-42a7-980b-c7a677d4d7bf')
        (self.old/'NAMESPACE-ACK.json').write_text(json.dumps(ack))
        (self.old/'CLEANUP-VERIFIED.json').write_text(json.dumps(dict(namespace=ack['name'], uid=ack['uid'], absent=True)))
        for name, value in dict(OLD_STATE=self.old, OLD_RESULT_SHA=hashlib.sha256(self.oldraw).hexdigest(),
                RECOVERY_TOOL=self.tool, RECOVERY_TOOL_SHA=hashlib.sha256(self.tool.read_bytes()).hexdigest(),
                STATE=self.root/'diagnostic', NEW_STATE=self.root/'fixture').items():
            self.stack.enter_context(patch.object(d, name, value))
        # install_diagnostic deliberately mutates a disposable loaded module. Restore in tests.
        for name in ('RUNTIME_SOURCE', 'STATE', 'gateway_baseline', 'manifests', 'remote', 'schema_receipt',
                     'record', 'grants', 'REQUEST_BUDGET', 'normalized', 'validate', 'PHASE', 'emit', 'phase'):
            self.stack.enter_context(patch.object(a, name, getattr(a, name)))
        self.stack.enter_context(patch.object(a.getpass, 'getpass'))
        self.stack.enter_context(patch.object(a, 'RUNTIME_SOURCE', RUNTIME))
        self.stack.enter_context(patch.object(a, 'SAMPLE_B64', 'cG5n'))
        self.stack.enter_context(patch.object(a, 'emit'))
        self.get = self.stack.enter_context(patch.object(a.native, 'get', return_value=None))

    def tearDown(self): self.stack.close(); self.temp.cleanup()

    def fixture(self):
        return a.manifests('weknora-enrich-check-'+'a'*16,
            dict(app='candidate', postgres='postgres', redis='redis'), b'ca', {}, ['10.77.1.2'], [],
            a.gateway_baseline(), {k:'a'*32 for k in ('bootstrap', 'app', 'jwt', 'aes')})

    def test_previous_exact_panic_boundary(self):
        result = d.bind_previous(a)
        self.assertTrue(result['old_namespace_absent']); self.assertEqual((self.old/'RESULT.json').read_bytes(), self.oldraw)
        self.get.assert_called_once_with('namespace', 'weknora-enrich-check-ba5d3a44a7984ea0', missing=True)

    def test_previous_report_hash_mismatch_blocks(self):
        (self.old/'RESULT.json').write_bytes(self.oldraw+b' ')
        with self.assertRaises(a.audit.AuditFailure): d.bind_previous(a)

    def test_each_provider_marker_blocks(self):
        for name in ('PROVIDER-REQUESTED.json', 'TEST-MODELS.json', 'UPLOAD.json', 'SEARCH.json'):
            p=self.old/name; p.write_text('{}')
            with self.assertRaisesRegex(a.native.AcceptanceFailure, 'provider_boundary'): d.bind_previous(a)
            p.unlink()

    def test_unremoved_previous_namespace_blocks(self):
        self.get.return_value = {'metadata': {'uid': 'old'}}
        with self.assertRaisesRegex(a.native.AcceptanceFailure, 'not_absent'): d.bind_previous(a)

    def test_foreign_previous_namespace_blocks(self):
        (self.old/'NAMESPACE-ACK.json').write_text(json.dumps(dict(name='other', uid='other')))
        with self.assertRaisesRegex(a.native.AcceptanceFailure, 'namespace_identity'): d.bind_previous(a)

    def test_changed_runtime_hash_blocks(self):
        a.RUNTIME_SOURCE += '\n'
        with self.assertRaisesRegex(a.native.AcceptanceFailure, 'runtime_hash'): d.install_diagnostic(a)

    def test_network_denies_both_and_no_host_aliases_or_prod_stores(self):
        d.install_diagnostic(a); policy, cm, sec, pod = self.fixture()
        self.assertEqual(policy['spec']['egress'], []); self.assertEqual(policy['spec']['ingress'], [])
        self.assertEqual(set(policy['spec']['policyTypes']), {'Ingress', 'Egress'})
        self.assertNotIn('hostAliases', pod['spec']); self.assertEqual(pod['spec']['activeDeadlineSeconds'], 600)
        self.assertFalse(pod['spec']['automountServiceAccountToken'])
        self.assertEqual(set(sec['data']), {'bootstrap', 'app', 'jwt', 'aes'})
        for token in ('persistentVolumeClaim', 'hostPath', 'envFrom'):
            self.assertNotIn(token, json.dumps(pod))
        self.assertIn('def safe_startup', cm['data']['runtime.py'])

    def test_admission_empty_both_fields_normalize_but_rule_injection_fails(self):
        d.install_diagnostic(a); e=self.fixture()[0]; v=copy.deepcopy(e)
        del v['spec']['egress']; del v['spec']['ingress']; a.validate(v,e)
        for key in ('ingress','egress'):
            v=copy.deepcopy(e); v['spec'][key]=[{}]
            with self.assertRaises(a.native.AcceptanceFailure): a.validate(v,e)

    def test_admission_cannot_add_host_aliases_or_sidecar(self):
        d.install_diagnostic(a); e=self.fixture()[-1]; v=copy.deepcopy(e)
        v['spec']['hostAliases']=[dict(ip='10.0.0.1',hostnames=['gateway'])]
        with self.assertRaisesRegex(a.native.AcceptanceFailure, 'host_aliases'): a.validate(v,e)
        v=copy.deepcopy(e); v['spec']['containers'].append(copy.deepcopy(v['spec']['containers'][0]))
        with self.assertRaises(a.native.AcceptanceFailure): a.validate(v,e)

    def test_remote_blocks_app_and_all_submission_modes(self):
        with patch.object(a,'remote',return_value=(0,{})) as remote:
            d.install_diagnostic(a)
            for mode in ('app','setup','upload','search','stop-app','anything'):
                with self.assertRaisesRegex(a.native.AcceptanceFailure, 'forbids_app_model'): a.remote('ns',mode)
            remote.assert_not_called()
            for mode in ('status','bootstrap','health','stop-bootstrap','stop-all'): a.remote('ns',mode)
            self.assertEqual(remote.call_count,5)

    def test_healthy_schema_is_readonly_then_stops_before_grants(self):
        with patch.object(a,'schema_receipt',return_value={'version':90}) as schema, patch.object(a,'record') as record:
            d.install_diagnostic(a)
            with self.assertRaisesRegex(a.native.AcceptanceFailure, d.STOP_HEALTHY): a.schema_receipt('ns')
            schema.assert_called_once_with('ns'); record.assert_called_once_with('DIAGNOSTIC-SCHEMA.json',{'version':90})
            with self.assertRaisesRegex(a.native.AcceptanceFailure,'provider_boundary'): a.grants('ns','secret')
            with self.assertRaisesRegex(a.native.AcceptanceFailure,'provider_boundary'): a.getpass.getpass('key')

    def test_summary_complete_is_not_enrichment_acceptance(self):
        def fixture_run():
            d.NEW_STATE.mkdir(mode=0o700)
            a.record('RESULT.json',dict(document_submission_attempted=False, cleanup='NAMESPACE_ABSENT_VERIFIED',
                production_baseline='UNCHANGED', failed_check='isolated_bootstrap_exited_before_health'))
            a.record('FAILURE-DIAGNOSTICS.json',dict(runtime={'STOPPED-bootstrap.json': self.stop}))
            return 1
        with patch.object(a,'run',side_effect=fixture_run) as run:
            self.assertEqual(d.run(a),0)
            with self.assertRaisesRegex(a.native.AcceptanceFailure,'USE_STATUS'): d.run(a)
        self.assertEqual(run.call_count,1)
        report=json.loads((d.STATE/'RESULT.json').read_text())
        self.assertEqual(report['native_bootstrap'],'FAILED_PANIC_CAPTURED')
        self.assertEqual(report['native_enrichment_acceptance'],'NOT_TESTED')
        self.assertFalse(report['ready_for_rollout']); self.assertEqual(report['provider_requests'],0)
        self.assertEqual((self.old/'RESULT.json').read_bytes(),self.oldraw)

    def test_no_stack_or_baseline_drift_cannot_complete(self):
        def fixture_run():
            d.NEW_STATE.mkdir(mode=0o700)
            a.record('RESULT.json',dict(document_submission_attempted=False, cleanup='NAMESPACE_ABSENT_VERIFIED',
                production_baseline='RECHECK_UNCONFIRMED', failed_check='isolated_bootstrap_exited_before_health'))
            a.record('FAILURE-DIAGNOSTICS.json',dict(runtime={'STOPPED-bootstrap.json': self.stop}))
            return 1
        with patch.object(a,'run',side_effect=fixture_run): self.assertEqual(d.run(a),1)
        report=json.loads((d.STATE/'RESULT.json').read_text())
        self.assertEqual(report['failed_check'],'production_baseline_recheck_required')

    def test_frozen_run_panic_path_stops_before_models_and_cleans(self): self.exercise_flow(False)
    def test_frozen_run_healthy_path_stops_before_models_and_cleans(self): self.exercise_flow(True)

    def exercise_flow(self, healthy):
        guard=SimpleNamespace(live=Mock()); reads=[]; calls=[]
        def read_json(path,sha):
            if path==a.BYTE_REPORT: return {'classification':{'byte_differences_explained':True}}
            if path==a.audit.NATIVE: return {'native_glm_acceptance':'PASS'}
            return {'enrichment_store_preload_recovery':'PASS'}
        def create(obj,label):
            v=copy.deepcopy(obj); v['metadata']['uid']='ours'
            a.record(label+'-ACK.json',dict(name=v['metadata']['name'],uid='ours'))
            return v
        def command(args,data=None,**kwargs):
            calls.append(args)
            if args[0]=='create': return 0,data
            self.assertEqual(args[0],'exec'); self.assertIn('pg_isready',args)
            return 0,b'{}'
        def wait(predicate, seconds, label):
            if label=='FIXTURE_READY': return True
            return predicate()
        def remote(ns,mode,*args,**kwargs):
            reads.append(mode)
            return (0, {}) if healthy or mode!='status' else (0,{'STOPPED-bootstrap.json':self.stop})
        def capture(ns): a.record('FAILURE-DIAGNOSTICS.json',dict(runtime={'STOPPED-bootstrap.json':self.stop} if not healthy else {}))
        with ExitStack() as stack:
            for owner,name,value in (
                (a,'read_json',Mock(side_effect=read_json)), (a.native,'initialize',Mock(return_value=(SimpleNamespace(IMAGE='candidate'),None))),
                (a.native,'preflight',Mock(return_value=(guard,{},b'ca',{},['10.77.1.2'],None))),
                (a.frozen,'load',Mock(return_value=SimpleNamespace(ITEMS=[dict(role=k,image=k) for k in ('postgres','redis')], image_check=Mock()))),
                (a,'capacity',Mock(return_value=({},[]))), (a,'create',Mock(side_effect=create)),
                (a.native,'call',Mock(side_effect=command)), (a,'wait_for',Mock(side_effect=wait)),
                (a,'remote',Mock(side_effect=remote)), (a,'schema_receipt',Mock(return_value={'version':90})),
                (a,'capture_failure',Mock(side_effect=capture)), (a,'cleanup',Mock(return_value='NAMESPACE_ABSENT_VERIFIED'))):
                stack.enter_context(patch.object(owner,name,value))
            self.assertEqual(d.run(a),0)
            a.cleanup.assert_called_once()
        self.assertNotIn('app',reads); self.assertNotIn('setup',reads); self.assertNotIn('upload',reads)
        self.assertEqual(reads.count('bootstrap'),1)
        self.assertTrue(all(not (x[0]=='exec' and 'gateway' in x) for x in calls))
        report=json.loads((d.STATE/'RESULT.json').read_text())
        self.assertEqual(report['native_bootstrap'],'HEALTHY_NOT_FULL_ACCEPTANCE' if healthy else 'FAILED_PANIC_CAPTURED')
        self.assertEqual(report['provider_requests'],0); self.assertFalse(report['ready_for_rollout'])


if __name__ == '__main__': unittest.main()
