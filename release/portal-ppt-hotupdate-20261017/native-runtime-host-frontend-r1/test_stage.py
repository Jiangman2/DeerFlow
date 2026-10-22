import base64
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('stage',HERE/'host_frontend_stage.py')
S = importlib.util.module_from_spec(spec); spec.loader.exec_module(S)
OWNER = 'a'*32
NAMESPACE = 'portal-ppt-' + OWNER
HOST_INPUTS = json.loads((HERE.parent/'native-preload-complete-evidence-r1/HOST-RUNTIME-INPUTS.json').read_bytes())


class StageTests(unittest.TestCase):
    def observed(self, role):
        repo = 'deerflow.local/deer-flow-aio-sandbox' if role == 'host' else 'deerflow.local/deer-flow-frontend'
        return dict(pod_uid='12345678-1234-1234-1234-123456789abc',node_name=S.NODE['name'],node_uid=S.NODE['uid'],
                    image_id='containerd://' + S.IMAGES[role]['config'],image_reference=repo+'@'+S.IMAGES[role]['manifest'],
                    pod_phase='Succeeded',exit_code=0,restart_count=0,container_id='containerd://'+'b'*64)

    def receipt(self, role):
        observed = self.observed(role)
        value = dict(pod_uid=observed['pod_uid'],requested_binding=dict(namespace=NAMESPACE,node=S.NODE,**S.IMAGES[role]),
                     native_arm64_qualified=False,ready_for_rollout=False,model_submitted=False)
        assets = S.load_assets()
        if role == 'host':
            value.update(schema='portal-ppt-native-host-adapter.v1',status='ISOLATED_CHECKS_PASS_EXTERNAL_NATIVE_IDENTITY_BINDING_REQUIRED',child_exit_code=0)
            value['probe'] = dict(status=value['status'],source_file_count=13,platform_machine_observed='aarch64',model_submitted=False,
                source_checks=[dict(row,status='MATCH') for row in assets['SOURCE-EXPECTATIONS.json']['files']],
                checks=dict(imports_and_driver=dict(status='PASS'),default_chromium_fixture=dict(status='PASS',external_requests=0,
                    context_closed=True,browser_closed=True,cache_mapping_active_driver_verified=True,default_launch=dict(headless=True,timeout=5000,executable_path=None)),
                    codex_version=dict(status='PASS',arguments=['--version'],model_submitted=False)))
        else:
            value.update(schema='portal-ppt-native-frontend-adapter.v1',status='HTTP_14_PASS_EXTERNAL_NATIVE_IDENTITY_BINDING_REQUIRED',
                         frozen_harness_sha256=S.FROZEN_HTTP_SHA,layout=dict(exit_code=0),http=dict(exit_code=0),
                         phases=[dict(phase=phase,exit_code=0,signal=None) for phase in ('layout','http')])
            value['frozen_harness_receipt'] = dict(schema='portal-ppt-built-frontend-http.v3',status='PASS',checks=assets['FRONTEND-CHECK-NAMES'],
                                                  production_accessed=False,model_requests=0)
        return value

    def validate(self,role,value,observed=None):
        return S.validate_terminal(role,(json.dumps(value)+'\n').encode(),observed or self.observed(role),NAMESPACE)

    def test_render_uses_fixed_assets_separate_bindings_owner_and_both_frontend_container_pins(self):
        output = S.render(OWNER,NAMESPACE,HOST_INPUTS)
        self.assertEqual([stage['name'] for stage in output['stages']],['host','frontend'])
        self.assertEqual([stage['terminal_container'] for stage in output['stages']],['host-probe','frontend-probe'])
        self.assertEqual(set(output['stages'][1]['images']),{'synthetic-projection','frontend-probe'})
        assets = S.load_assets()
        for stage in output['stages']:
            self.assertEqual(S.validate_stage_objects(stage,OWNER,NAMESPACE)['status'],'EXACT_STAGE_REQUESTS')
            self.assertEqual([obj['kind'] for obj in stage['objects']],['ConfigMap','ConfigMap','Pod'])
            for obj in stage['objects']:
                self.assertEqual(obj['metadata']['namespace'],NAMESPACE); self.assertEqual(obj['metadata']['labels'][S.OWNER_LABEL],OWNER)
            self.assertEqual(stage['objects'][1]['metadata']['name'],'native-'+stage['name']+'-binding')
            original = next(obj for obj in assets['configmaps.template.json']['items'] if obj['metadata']['name'] == stage['objects'][0]['metadata']['name'])
            self.assertEqual(stage['objects'][0]['binaryData'],original['binaryData'])
            data = stage['objects'][1]['binaryData']
            self.assertEqual(json.loads(base64.b64decode(data['HOST-RUNTIME-INPUTS.json'])),HOST_INPUTS)
            self.assertEqual(json.loads(base64.b64decode(data['BINDING.json']))['state'],'BOUND_FOR_ISOLATED_RUNTIME')

    def test_render_refuses_unbound_private_env_or_unsafe_namespace(self):
        for inputs in (dict(HOST_INPUTS,token='secret'),dict(HOST_INPUTS,state='UNBOUND'),dict(HOST_INPUTS, image_config_digest='sha256:'+'0'*64),
                       dict(HOST_INPUTS,runtime_paths={'PATH':'.:/bin'}),dict(HOST_INPUTS,codex_selector='codex --api-key secret')):
            with self.assertRaises(ValueError): S.render(OWNER,NAMESPACE,inputs)
        with self.assertRaises(ValueError): S.render(OWNER,'deer-flow',HOST_INPUTS)
        with self.assertRaises(ValueError): S.render(OWNER,'portal-ppt-'+'b'*32,HOST_INPUTS)
        with self.assertRaises(ValueError): S.render('invalid-owner',NAMESPACE,HOST_INPUTS)

    def test_request_drift_volume_command_or_owner_refused(self):
        stage = S.render(OWNER,NAMESPACE,HOST_INPUTS)['stages'][0]
        for kind in ('volume','command','owner'):
            changed = copy.deepcopy(stage)
            if kind == 'volume': changed['objects'][-1]['spec']['volumes'].append(dict(name='private',hostPath=dict(path='/')))
            elif kind == 'command': changed['objects'][-1]['spec']['containers'][0]['command'] = ['bash']
            else: changed['objects'][-1]['metadata']['labels'][S.OWNER_LABEL] = 'b'*32
            with self.assertRaisesRegex(ValueError,'stage_request_manifest_changed'): S.validate_stage_objects(changed,OWNER,NAMESPACE)

    def test_host_receipt_checks_all_fixed_sources_runtime_steps_and_identity(self):
        value = self.receipt('host')
        result = self.validate('host',value)
        self.assertEqual(result['checks'],3); self.assertFalse(result['native_arm64_qualified'])
        changed = copy.deepcopy(value); changed['probe']['source_checks'][0]['sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError,'host_source_checks'): self.validate('host',changed)
        changed = copy.deepcopy(value); changed['probe']['checks']['default_chromium_fixture']['external_requests'] = 1
        with self.assertRaisesRegex(ValueError,'host_browser_scope'): self.validate('host',changed)
        changed = copy.deepcopy(value); changed['probe']['checks']['codex_version']['status'] = 'PENDING_EXACT_LAUNCHER_REVIEW'
        with self.assertRaisesRegex(ValueError,'host_runtime_checks'): self.validate('host',changed)

    def test_frontend_requires_exact_fourteen_check_names_and_both_child_exits(self):
        value = self.receipt('frontend'); self.assertEqual(self.validate('frontend',value)['checks'],14)
        changed = copy.deepcopy(value); changed['frozen_harness_receipt']['checks'][-1] = changed['frozen_harness_receipt']['checks'][0]
        with self.assertRaisesRegex(ValueError,'frontend_http_checks'): self.validate('frontend',changed)
        changed = copy.deepcopy(value); changed['phases'][1]['exit_code'] = 1
        with self.assertRaisesRegex(ValueError,'frontend_child_exits'): self.validate('frontend',changed)

    def test_wrong_pod_node_image_exit_restart_or_receipt_uid_refused(self):
        value = self.receipt('host')
        for key,item in [('node_uid','changed'),('image_id','containerd://sha256:'+'0'*64),('exit_code',1),('restart_count',1),('pod_phase','Running')]:
            observed = dict(self.observed('host'),**{key:item})
            with self.assertRaises(ValueError): self.validate('host',value,observed)
        value['pod_uid'] = 'different'
        with self.assertRaisesRegex(ValueError,'runtime_receipt_binding'): self.validate('host',value)

    def test_asset_drift_and_duplicate_json_keys_rejected(self):
        with mock.patch.object(S,'ASSET_SHA','0'*64):
            with self.assertRaisesRegex(ValueError,'stage_assets_identity'): S.load_assets()
        with self.assertRaisesRegex(ValueError,'duplicate_json_key'): S.decode_json(b'{"a":1,"a":2}')


if __name__ == '__main__': unittest.main(verbosity=2)
