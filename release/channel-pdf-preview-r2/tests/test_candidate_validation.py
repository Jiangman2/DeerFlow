import ast
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import tarfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('candidate_validation', ROOT / 'validate-candidates.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class CandidateValidationTests(unittest.TestCase):
    def test_scope_and_no_production_credentials_or_storage(self):
        self.assertEqual(len(mod.TARGETS), 4)
        for component, node in mod.TARGETS:
            pod = mod.manifest(component, node, '12345678')
            ps = pod['spec']
            self.assertEqual(pod['metadata']['labels'], {mod.LABEL:'12345678'})
            self.assertFalse(ps['automountServiceAccountToken'])
            self.assertFalse(ps['enableServiceLinks'])
            self.assertNotIn('hostNetwork', ps)
            self.assertNotIn('hostPID', ps)
            self.assertNotIn('volumes', ps)
            self.assertEqual(ps['restartPolicy'], 'Never')
            self.assertEqual(ps['activeDeadlineSeconds'], 900)
            self.assertEqual(ps['nodeSelector']['kubernetes.io/hostname'], node)
            c = ps['containers'][0]
            self.assertNotIn('envFrom', c)
            self.assertNotIn('volumeMounts', c)
            self.assertNotIn('ports', c)
            self.assertEqual(c['imagePullPolicy'], 'Never')
            self.assertEqual(c['image'], mod.CANDIDATE[component])
            self.assertEqual(c['resources']['limits']['cpu'], '500m')
            self.assertEqual(c['resources']['limits']['memory'], '1Gi')
            self.assertEqual(len(ps['tolerations']), 1 if component == 'gateway' else 0)

    def test_probe_syntax_and_no_worker_or_online_install(self):
        ast.parse(mod.GATEWAY_PROBE)
        out = subprocess.run(['node', '--check', '-'], input=mod.FRONTEND_PROBE, text=True, capture_output=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        for token in ('uvicorn', 'app.main', 'lifespan', 'pip install', 'pnpm build', 'pnpm install'):
            self.assertNotIn(token, mod.GATEWAY_PROBE + mod.FRONTEND_PROBE)
        self.assertIn('target_sha256', mod.GATEWAY_PROBE)
        self.assertIn('target_sha256', mod.FRONTEND_PROBE)

    def test_probe_paths_icon_and_smoke_match_delivered_archive(self):
        archive = Path.home() / 'Downloads/deerflow-channel-pdf-preview-20260923-r2.tar.gz'
        import hashlib
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), '4ff8749c99dd736e923a9b90e52eb34e45bd39eebd82bdb93faafc955673ad37')
        with tarfile.open(archive) as tar:
            prefix = 'deerflow-channel-pdf-preview-20260923-r2/'
            def read(name): return tar.extractfile(prefix + name).read()
            digest = hashlib.sha256(read('cnooc-haineng-mark.png')).hexdigest()
            self.assertIn(digest, mod.FRONTEND_PROBE)
            for component, count in (('gateway',11), ('frontend',8)):
                self.assertEqual(len(json.loads(read(component + '-delta.json'))), count)
                self.assertIn(b'/tmp/channel-pdf-r2/', read('Dockerfile.' + component))
            self.assertIn(mod.MARKERS['gateway'].encode(), read('smoke-gateway.py'))
            self.assertEqual(len(json.loads(read('compressed-fixtures.json'))),6)

    def test_default_invocation_does_not_run(self):
        out = subprocess.run([__import__('sys').executable, str(ROOT/'validate-candidates.py')], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn('--run', out.stdout)
        self.assertNotIn('evidence_dir=', out.stdout)

    def test_live_image_and_exit_and_receipt_all_required(self):
        for component in mod.BASE:
            for digest in mod.IDENTITIES[component]:
                obj = {'status': {'phase':'Succeeded', 'containerStatuses':[
                    {'imageID':'containerd://sha256:' + digest, 'state':{'terminated':{'exitCode':0}}}]}}
                mod.verify_success(obj, component, mod.MARKERS[component])
                bad = copy.deepcopy(obj)
                bad['status']['containerStatuses'][0]['imageID'] = 'sha256:' + '0'*64
                with self.assertRaises(RuntimeError):
                    mod.verify_success(bad, component, mod.MARKERS[component])
                with self.assertRaises(RuntimeError):
                    mod.verify_success(obj, component, '')
                bad = copy.deepcopy(obj)
                bad['status']['containerStatuses'][0]['state']['terminated']['exitCode'] = 1
                with self.assertRaises(RuntimeError):
                    mod.verify_success(bad, component, mod.MARKERS[component])

    def test_no_matching_service_allowed(self):
        with patch.object(mod, 'get', return_value={'items':[{'metadata':{'name':'gateway'}, 'spec':{'selector':{'app':'gateway'}}}]}):
            mod.check_service_selectors({mod.LABEL:'12345678'})
        with patch.object(mod, 'get', return_value={'items':[{'metadata':{'name':'bad'}, 'spec':{'selector':{mod.LABEL:'12345678'}}}]}):
            with self.assertRaises(RuntimeError):
                mod.check_service_selectors({mod.LABEL:'12345678'})

    def test_cleanup_rejects_changed_uid_or_label(self):
        for metadata in ({'uid':'other', 'labels':{mod.LABEL:'token'}}, {'uid':'uid', 'labels':{'app':'gateway'}}):
            with patch.object(mod, 'get', return_value={'metadata':metadata}), patch.object(mod, 'kubectl') as ctl:
                with self.assertRaises(RuntimeError):
                    mod.cleanup('pod','test-pod','token','uid')
                ctl.assert_not_called()

    def test_cleanup_only_exact_owned_pod(self):
        obj = {'metadata':{'uid':'uid', 'labels':{mod.LABEL:'token'}}}
        with patch.object(mod, 'get', return_value=obj), patch.object(mod, 'kubectl') as ctl:
            mod.cleanup('pod','test-pod','token','uid')
            self.assertEqual(ctl.call_args.args[:3], ('delete','pod','test-pod'))

    def test_pressure_and_wrong_arch_fail_closed(self):
        healthy = {'metadata':{'labels':{'kubernetes.io/arch':'arm64'}}, 'spec':{}, 'status':{'conditions':[
            {'type':n, 'status':'True' if n == 'Ready' else 'False'} for n in ('Ready','DiskPressure','MemoryPressure','PIDPressure')]}}
        with patch.object(mod, 'get', return_value=healthy):
            mod.check_node('test')
        for key in ('pressure','architecture','cordon'):
            bad = copy.deepcopy(healthy)
            if key == 'pressure': bad['status']['conditions'][1]['status'] = 'True'
            elif key == 'architecture': bad['metadata']['labels']['kubernetes.io/arch'] = 'amd64'
            else: bad['spec']['unschedulable'] = True
            with patch.object(mod, 'get', return_value=bad):
                with self.assertRaises(RuntimeError): mod.check_node('test')

    def test_baseline_never_serializes_inline_credentials(self):
        def deployment(_, component):
            return {'metadata':{'uid':component,'generation':1}, 'spec':{'replicas':1, 'template':{'spec':{'containers':[
                {'name':component, 'image':mod.BASE[component], 'env':[{'name':'SECRET','value':'do-not-save-me'}]}]}}},
                'status':{'observedGeneration':1,'readyReplicas':1,'updatedReplicas':1,'availableReplicas':1}}
        with patch.object(mod,'get', side_effect=deployment):
            result = mod.baseline()
        self.assertNotIn('do-not-save-me', json.dumps(result))
        self.assertEqual(set(result), {'gateway','frontend'})
        for item in result.values(): self.assertEqual(len(item['spec_sha256']),64)

    def test_failed_probe_collects_evidence_then_cleans_up(self):
        failed = {'metadata':{'uid':'uid'}, 'status':{'phase':'Failed'}}
        def ctl(*args, **kwargs):
            if args[0] == 'create': return json.dumps({'metadata':{'uid':'uid'}})
            if args[0] == 'logs': return 'synthetic failure'
            if args[0] == 'get': return json.dumps({'items':[]})
            raise AssertionError(args)
        with tempfile.TemporaryDirectory() as temp, patch.object(mod, 'check_node'), patch.object(mod,'check_service_selectors'), patch.object(mod, 'kubectl', side_effect=ctl), patch.object(mod,'get',return_value=failed), patch.object(mod,'cleanup') as cleanup, patch.object(mod,'emit'):
            with self.assertRaises(RuntimeError):
                mod.run_probe('gateway','ecs-zdz-pro-0022','12345678',Path(temp))
            self.assertTrue(list(Path(temp).glob('*.status.json')))
            self.assertTrue(list(Path(temp).glob('*.log')))
            cleanup.assert_called_once()


if __name__ == '__main__':
    unittest.main()
