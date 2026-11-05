"""Test sealed executables with a synthetic Kubernetes API; never use a cluster."""
import base64
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

PACKAGES = Path(os.environ['V41_SPLIT_TEST_DIR'])


def load(mode):
    spec = importlib.util.spec_from_file_location('sealed_' + mode, str(PACKAGES / (mode + '.packaged.py')))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def deployment(image):
    return {'apiVersion': 'apps/v1', 'kind': 'Deployment',
        'metadata': {'uid': 'gateway-uid', 'generation': 389, 'resourceVersion': '111'},
        'spec': {'replicas': 70, 'selector': {'matchLabels': {'app': 'gateway'}}, 'template': {'spec': {
            'containers': [{'name': 'gateway', 'image': image, 'volumeMounts': [
                {'name': 'config', 'mountPath': '/app/backend/config.yaml', 'readOnly': True, 'subPath': 'config.yaml'}]}],
            'volumes': [{'name': 'config', 'configMap': {'name': 'old-config'}}]}}},
        'status': dict(observedGeneration=389, replicas=70, readyReplicas=70, updatedReplicas=70, availableReplicas=70)}


class Cluster:
    def __init__(self, image):
        self.original = deployment(image); self.current = copy.deepcopy(self.original)
        self.cms = {'old-config': {'metadata': {'name': 'old-config', 'uid': 'old-cm'}, 'data': {'config.yaml': 'models: []\n'}}}
        self.candidate = b'models: []\n# synthetic candidate only\n'
        self.mutations = []; self.runtime_probes = 0; self.patch_resource_versions = []
        self.lose_create_reply = False; self.lose_patch_reply = False; self.reject_last_pod = False


class FakeReader:
    def __init__(self, cluster, host, core, lib):
        self.cluster, self.host, self.core, self.lib = cluster, host, core, lib
        self.observations = []
    def get(self, kind, name):
        c = self.cluster
        if kind == 'deployment': return copy.deepcopy(c.current if name == 'gateway' else c.original)
        if kind == 'pod':
            return {'metadata': {'name': name, 'uid': name + '-uid', 'ownerReferences': [
                {'kind': 'ReplicaSet', 'name': 'current-rs', 'uid': 'rs-uid', 'controller': True}]},
                'spec': dict(copy.deepcopy(c.current['spec']['template']['spec']), nodeName='synthetic-node'),
                'status': {'phase': 'Running', 'containerStatuses': [{'name': 'gateway', 'ready': True}]}}
        if kind == 'replicaset':
            return {'metadata': {'uid': 'rs-uid', 'ownerReferences': [
                {'kind': 'Deployment', 'uid': c.current['metadata']['uid'], 'controller': True}]}}
        raise AssertionError('unexpected resource')
    def pod_index(self, d): return [{'name': 'gateway-%03d' % i, 'uid': 'gateway-%03d-uid' % i} for i in range(70)]
    def resource(self, kind, name):
        if kind == 'configmap': return copy.deepcopy(self.cluster.cms[name])
        if kind == 'service': return {'metadata': {'uid': name}, 'spec': {'selector': {'app': name}}}
        raise AssertionError('unexpected resource')
    def read(self, args, **kwargs):
        self.host.allowed_command(self.core, self.lib, args)
        tail = args[len(self.lib.BASE):]; c = self.cluster
        self.observations.append({'step': kwargs.get('step'), 'status': 'PASS'})
        if tail[0] == 'exec':
            request = json.loads(kwargs['data'])
            response = {'status': 'PASS', 'provider_requests': 0, 'credential_source': 'GLM_5_3_FLASH_API_KEY',
                        'default_model': 'deepseek-v4-flash', 'config_sha256': self.core.digest(c.candidate)}
            if request['mode'] == 'prepare': response['private_candidate_b64'] = base64.b64encode(c.candidate).decode()
            else:
                c.runtime_probes += 1
                if c.reject_last_pod and c.runtime_probes == 70: raise self.core.Refused('SYNTHETIC_POD_FAILED')
            return json.dumps(response).encode()
        if tail[0] == 'create':
            cm = json.loads((Path(self.core.CONTROL) / 'candidate.private.json').read_bytes())
            name = cm['metadata']['name']
            if name in c.cms: raise self.core.Refused('ALREADY_EXISTS')
            cm['metadata']['uid'] = 'candidate-uid'; c.cms[name] = cm; c.mutations.append('create-configmap')
            if c.lose_create_reply: raise TimeoutError('synthetic lost response')
            return b'configmap/candidate'
        if tail[0] == 'patch':
            operations = json.loads((Path(self.core.CONTROL) / 'patch.private.json').read_bytes())
            for op in operations:
                if op['op'] != 'test': continue
                value = c.current
                for part in op['path'].strip('/').split('/'): value = value[part]
                if value != op['value']: raise self.core.Refused('STALE_CAS')
            value = copy.deepcopy(c.current)
            value['spec']['template']['spec']['volumes'][0]['configMap']['name'] = operations[-1]['value']
            value['metadata']['generation'] += 1; value['status']['observedGeneration'] += 1
            if '--dry-run=server' in tail: return json.dumps(value).encode()
            c.patch_resource_versions.append(c.current['metadata']['resourceVersion'])
            c.current = value; c.mutations.append('patch-gateway')
            if c.lose_patch_reply: raise TimeoutError('synthetic lost response')
            return b'deployment.apps/gateway'
        if tail[:2] == ['get', 'endpointslices']:
            return json.dumps({'items': [{'endpoints': [{'conditions': {'ready': True}, 'targetRef': {
                'kind': 'Pod', 'uid': 'gateway-%03d-uid' % i}} for i in range(70)]}]}).encode()
        raise AssertionError('unexpected command')


class SplitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.hosts = {mode: load(mode) for mode in ('prepare', 'rollout')}
        self.contexts = {}
        for mode, h in self.hosts.items():
            h.PREPARE_DIR = self.tmp.name + '/prepare'; h.ROLLOUT_DIR = self.tmp.name + '/rollout'
            h.LEGACY_DIR = self.tmp.name + '/legacy'
            core, lib, api = h.initialize()
            if os.name == 'nt':
                # POSIX ownership and directory fsync are exercised by the same suite on Linux.
                h.private_read = lambda path, cap=2097152: path.read_bytes()
                def save(result, module=core):
                    if result['once_claimed']:
                        (Path(module.CONTROL) / 'REPORT.json').write_bytes(module.packed(result))
                core.save = save
            if not hasattr(self, 'cluster'): self.cluster = Cluster(lib.IMAGE)
            core.BASELINES = {name: core.binding(self.cluster.original) for name in core.BASELINES}
            core.BASE_CONFIG_SHA = core.digest(b'models: []\n')
            core.read_receipt = lambda reader: {'sha256': core.RECEIPT_SHA}
            self.contexts[mode] = core, lib, FakeReader(self.cluster, h, core, lib)
    def result(self, mode):
        return {'schema': 'portal-v41-glm-' + ('preparation' if mode == 'prepare' else 'rollout') + '.v1',
                'bundle_id': self.hosts[mode].BUNDLE_ID, 'once_claimed': False, 'status': 'INCOMPLETE',
                'patch_request_count': 0, 'patch_requested': False, 'patch_may_have_applied': False,
                'gateway_rollout_started': False, 'v41_enabled': False}
    def run_phase(self, mode):
        core, lib, reader = self.contexts[mode]; result = self.result(mode)
        with contextlib.redirect_stdout(io.StringIO()):
            getattr(self.hosts[mode], mode)(reader, core, lib, result)
        return result
    def test_preparation_creates_only_unused_config_and_keeps_gateway_unchanged(self):
        result = self.run_phase('prepare')
        self.assertEqual(self.cluster.mutations, ['create-configmap'])
        self.assertEqual(self.cluster.current, self.cluster.original)
        self.assertEqual(result['status'], 'V41_CONFIG_PREPARED_NOT_ACTIVATED')
        self.assertFalse(result['v41_enabled']); self.assertFalse(result['gateway_rollout_started'])
        self.assertEqual(result['patch_request_count'], 0); self.assertEqual(self.cluster.runtime_probes, 0)
        self.assertNotIn('private_candidate_b64', json.dumps(result))
    def test_preparation_executable_has_no_gateway_mutation_implementation(self):
        h = self.hosts['prepare']; core = self.contexts['prepare'][0]
        for name in ('patch_once', 'plan', 'observe_rollout', 'execute'): self.assertFalse(hasattr(core, name))
        self.assertFalse(hasattr(h, 'rollout')); self.assertFalse(hasattr(h, 'load_preparation'))
        self.assertEqual(h.BUNDLE_ID, self.hosts['rollout'].BUNDLE_ID)
        self.assertEqual(h.CHILD, self.hosts['rollout'].CHILD)
    def test_preparation_command_guard_rejects_workload_and_secret_operations(self):
        h = self.hosts['prepare']; core, lib, _ = self.contexts['prepare']
        for tail in (['patch', 'deployment', 'gateway'], ['rollout', 'restart', 'deployment/gateway'],
                     ['delete', 'pod', 'gateway'], ['apply', '-f', 'file'], ['get', 'secret', 'key'],
                     ['exec', 'gateway', '--', 'touch', '/file']):
            with self.subTest(tail=tail), self.assertRaises(core.Refused): h.allowed_command(core, lib, lib.BASE + tail)
    def test_two_phases_with_new_resource_version_and_all_seventy_pods(self):
        self.run_phase('prepare'); self.cluster.current['metadata']['resourceVersion'] = '222-after-wait'
        result = self.run_phase('rollout')
        self.assertEqual(self.cluster.mutations, ['create-configmap', 'patch-gateway'])
        self.assertEqual(self.cluster.patch_resource_versions, ['222-after-wait'])
        self.assertEqual(result['accepted_pod_count'], 70); self.assertTrue(result['runtime_accepted'])
        self.assertTrue(result['v41_enabled']); self.assertEqual(self.cluster.runtime_probes, 70)
    def test_rollout_resolves_lost_patch_reply_without_repost(self):
        self.run_phase('prepare'); self.cluster.lose_patch_reply = True
        result = self.run_phase('rollout')
        self.assertTrue(result['runtime_accepted']); self.assertFalse(result['patch_command_acknowledged'])
        self.assertEqual(self.cluster.mutations.count('patch-gateway'), 1)
    def test_rollout_refuses_intervening_spec_change_before_claim(self):
        self.run_phase('prepare'); self.cluster.current['spec']['replicas'] = 69
        with self.assertRaisesRegex(self.contexts['rollout'][0].Refused, 'BASELINE_CHANGED'): self.run_phase('rollout')
        self.assertFalse(Path(self.hosts['rollout'].ROLLOUT_DIR).exists())
        self.assertEqual(self.cluster.mutations, ['create-configmap'])
    def test_rollout_refuses_recreated_candidate_uid(self):
        stage = self.run_phase('prepare'); self.cluster.cms[stage['candidate_configmap']]['metadata']['uid'] = 'replacement'
        with self.assertRaisesRegex(self.contexts['rollout'][0].Refused, 'CONFIGMAP_CHANGED'): self.run_phase('rollout')
        self.assertEqual(self.cluster.mutations, ['create-configmap'])
    def test_rollout_refuses_changed_candidate_content(self):
        stage = self.run_phase('prepare'); self.cluster.cms[stage['candidate_configmap']]['data']['config.yaml'] = 'changed'
        with self.assertRaisesRegex(self.contexts['rollout'][0].Refused, 'CONFIGMAP_CHANGED'): self.run_phase('rollout')
    def test_changed_preparation_file_rejected(self):
        self.run_phase('prepare'); path = Path(self.hosts['prepare'].PREPARE_DIR) / 'candidate.private.json'
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(self.contexts['rollout'][0].Refused, 'DIGEST_CHANGED'): self.run_phase('rollout')
    def test_mismatched_packaging_rejected(self):
        self.run_phase('prepare'); self.hosts['rollout'].BUNDLE_ID = 'different-delivery'
        with self.assertRaisesRegex(self.contexts['rollout'][0].Refused, 'TOOL_CHANGED'): self.run_phase('rollout')
    def test_lost_create_reply_keeps_incomplete_stage_and_does_not_roll(self):
        self.cluster.lose_create_reply = True
        with self.assertRaises(TimeoutError): self.run_phase('prepare')
        core, _, _ = self.contexts['rollout']
        with self.assertRaisesRegex(core.Refused, 'NOT_ACCEPTED'): self.run_phase('rollout')
        self.assertEqual(self.cluster.current, self.cluster.original)
    def test_unhealthy_last_pod_cannot_be_accepted(self):
        self.run_phase('prepare'); self.cluster.reject_last_pod = True
        with self.assertRaisesRegex(self.contexts['rollout'][0].Refused, 'SYNTHETIC_POD_FAILED'): self.run_phase('rollout')
        core = self.contexts['rollout'][0]
        report = json.loads((Path(core.CONTROL) / 'REPORT.json').read_bytes())
        self.assertFalse(report.get('runtime_accepted')); self.assertEqual(len(report['accepted_pods']), 69)
    def test_missing_preparation_cannot_roll(self):
        with self.assertRaises(FileNotFoundError): self.run_phase('rollout')
        self.assertEqual(self.cluster.mutations, [])
    def test_entries_wrong_host_and_cannot_switch_mode(self):
        for mode, h in self.hosts.items():
            flag = '--prepare-v41-config-only-once' if mode == 'prepare' else '--rollout-prepared-v41-once'
            other = '--rollout-prepared-v41-once' if mode == 'prepare' else '--prepare-v41-config-only-once'
            with patch.object(h.socket, 'gethostname', return_value='workstation'), patch.object(sys, 'argv', ['tool', flag]), contextlib.redirect_stdout(io.StringIO()) as capture:
                self.assertEqual(h.main(), 1)
            self.assertEqual(json.loads(capture.getvalue())['error_code'], 'WRONG_HOST_OR_UID')
            with patch.object(sys, 'argv', ['tool', other]), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit): h.main()
    @unittest.skipIf(os.name == 'nt', 'POSIX ownership and nofollow test runs in Linux')
    def test_preparation_receipt_symlink_and_permissions_refused(self):
        self.run_phase('prepare'); h = self.hosts['rollout']
        path = Path(h.PREPARE_DIR) / 'REPORT.json'; raw = path.read_bytes()
        path.chmod(0o644)
        with self.assertRaisesRegex(RuntimeError, 'UNTRUSTED_PREPARATION_FILE'): h.private_read(path)
        path.chmod(0o600); backup = path.with_suffix('.original'); path.rename(backup); path.symlink_to(backup)
        with self.assertRaises(OSError): h.private_read(path)


if __name__ == '__main__': unittest.main()
