import ast
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import accept_enrichment as a
import gateway_enrichment_baseline as g
import recover_enrichment_gateway as r

CONTRACT = dict(model='Qwen3-Embedding-8B', dimension='1024', mrl='true', retries='3',
    upstream='http://model.ai.cnooc/member1/qwen3-embedding-8b/v1/embeddings',
    relay_key_present=True, upstream_key_present=True)


def inventory(count=70):
    d = dict(metadata=dict(uid='deployment-uid', generation=373), spec=dict(replicas=count),
             status=dict(observedGeneration=373, replicas=count, updatedReplicas=count,
                         readyReplicas=count, availableReplicas=count))
    svc = dict(metadata=dict(uid='service-uid'), spec=dict(clusterIP='10.96.0.2', selector={'app': 'gateway'},
                ports=[dict(port=8001)]))
    rs = [dict(metadata=dict(uid='rs-uid', name='gateway-rs', ownerReferences=[
        dict(controller=True, kind='Deployment', name='gateway', uid='deployment-uid')]))]
    pods = []
    addresses = []
    for i in range(count):
        name = 'gateway-'+str(i); uid = 'pod-'+str(i); ip = '10.244.0.'+str(i+1)
        pods.append(dict(metadata=dict(name=name, uid=uid, ownerReferences=[dict(
            controller=True, kind='ReplicaSet', name='gateway-rs', uid='rs-uid')]),
            spec=dict(containers=[dict(name='gateway', image='candidate')]),
            status=dict(phase='Running', podIP=ip, conditions=[dict(type='Ready', status='True')],
                containerStatuses=[dict(name='gateway', ready=True, imageID='sha256:one-image',
                    containerID='containerd://'+uid, restartCount=0, state=dict(running=dict(startedAt='now')))])))
        addresses.append(dict(ip=ip, targetRef=dict(kind='Pod', name=name, uid=uid, namespace='deer-flow')))
    ep = dict(metadata={}, subsets=[dict(ports=[dict(port=8001)], addresses=addresses)])
    return d, svc, rs, pods, ep


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.data = inventory(); self.baseline = g.GatewayBaseline(a); self.stack = ExitStack()
        self.stack.enter_context(patch.object(a, 'emit'))
        self.get = self.stack.enter_context(patch.object(a.native, 'get', side_effect=self.get_object))
        self.listing = self.stack.enter_context(patch.object(self.baseline, 'listing', side_effect=self.list_objects))

    def tearDown(self): self.stack.close()

    def get_object(self, kind, name, namespace):
        return copy.deepcopy(self.data[{'deployment': 0, 'service': 1, 'endpoints': 4}[kind]])

    def list_objects(self, kind, selector):
        self.assertEqual(selector, 'app=gateway')
        return copy.deepcopy(self.data[{'replicasets': 2, 'pods': 3}[kind]])

    def fails(self, check):
        with self.assertRaisesRegex(a.native.AcceptanceFailure, check): self.baseline.inventory()

    def test_all_70_stable_pass_no_per_pod_get(self):
        result = self.baseline.inventory()
        self.assertEqual(result['desired'], 70); self.assertEqual(len(result['pods']), 70)
        self.assertEqual(self.get.call_count, 3); self.assertEqual(self.listing.call_count, 2)

    def test_only_safety_bound_not_four_replicas(self):
        for count in (1, 4, 16, 70, 128):
            self.data = inventory(count); self.assertEqual(self.baseline.inventory()['desired'], count)
        for count in (0, 129, True):
            self.data[0]['spec']['replicas'] = count; self.fails('tool_bound')

    def test_each_rollout_counter_required(self):
        for key in ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas', 'observedGeneration'):
            self.data = inventory(); self.data[0]['status'][key] -= 1
            self.fails('not_converged')

    def test_terminating_and_unavailable_block(self):
        for key in ('terminatingReplicas', 'unavailableReplicas'):
            self.data = inventory(); self.data[0]['status'][key] = 1; self.fails('not_converged')

    def test_missing_duplicate_and_nonready_endpoint(self):
        self.data[4]['subsets'][0]['addresses'].pop(); self.fails('coverage')
        self.data = inventory(); self.data[4]['subsets'][0]['addresses'][1] = self.data[4]['subsets'][0]['addresses'][0]
        self.fails('coverage')
        self.data = inventory(); self.data[4]['subsets'][0]['notReadyAddresses'] = [{}]; self.fails('not_ready')

    def test_truncated_endpoints_block(self):
        self.data[4]['metadata']['annotations'] = {'endpoints.kubernetes.io/over-capacity': 'truncated'}
        self.fails('truncated')

    def test_foreign_endpoint_and_owner_block(self):
        self.data[4]['subsets'][0]['addresses'][0]['targetRef']['uid'] = 'other'; self.fails('uid_changed')
        self.data = inventory(); self.data[2][0]['metadata']['ownerReferences'][0]['uid'] = 'other'
        self.fails('foreign_owner')

    def test_pod_readiness_deletion_and_ip(self):
        self.data[3][0]['metadata']['deletionTimestamp'] = 'now'; self.fails('pod_not_ready')
        self.data = inventory(); self.data[3][0]['status']['conditions'][0]['status'] = 'False'; self.fails('pod_not_ready')
        self.data = inventory(); self.data[3][0]['status']['podIP'] = '10.1.1.1'; self.fails('ip_changed')

    def test_mixed_images_rejected(self):
        self.data[3][0]['status']['containerStatuses'][0]['imageID'] = 'different'; self.fails('mixed_revision')

    def test_no_unready_publishing_or_selectorless_service(self):
        self.data[1]['spec']['publishNotReadyAddresses'] = True; self.fails('service_shape')
        self.data = inventory(); self.data[1]['spec']['selector'] = {}; self.fails('selector_shape')

    def test_complete_parallel_read_and_identity_cached_no_repeat_exec(self):
        active = [0, 0]; lock = threading.Lock()
        def read(p):
            with lock:
                active[0] += 1; active[1] = max(active)
            time.sleep(.002)
            with lock: active[0] -= 1
            return copy.deepcopy(CONTRACT)
        with patch.object(self.baseline, 'read_contract', side_effect=read) as read_mock:
            first = self.baseline(); second = self.baseline(); third = self.baseline()
        self.assertEqual(read_mock.call_count, 70)
        self.assertGreater(active[1], 1); self.assertLessEqual(active[1], 4)
        self.assertEqual(first, second); self.assertEqual(second, third)
        first['pods'][0]['contract']['model'] = 'poison'
        self.assertEqual(self.baseline()['pods'][0]['contract'], CONTRACT)

    def test_cache_invalid_on_restart_container_spec_or_service_drift(self):
        for mutation in ('restart', 'container', 'spec', 'service'):
            self.data = inventory(); self.baseline = g.GatewayBaseline(a)
            with patch.object(self.baseline, 'listing', side_effect=self.list_objects), patch.object(
                    self.baseline, 'read_contract', return_value=CONTRACT) as read:
                self.baseline()
                if mutation == 'restart': self.data[3][0]['status']['containerStatuses'][0]['restartCount'] += 1
                elif mutation == 'container': self.data[3][0]['status']['containerStatuses'][0]['containerID'] = 'new'
                elif mutation == 'spec': self.data[3][0]['spec']['containers'][0]['env'] = [{'name': 'changed'}]
                elif mutation == 'service': self.data[1]['spec']['sessionAffinity'] = 'ClientIP'
                with self.assertRaisesRegex(a.native.AcceptanceFailure, 'identity_changed'): self.baseline()
                self.assertEqual(read.call_count, 70)

    def test_scan_drift_or_failed_contract_never_cached(self):
        def drift(p): self.data[0]['metadata']['generation'] += 1; return CONTRACT
        with patch.object(self.baseline, 'read_contract', side_effect=drift):
            with self.assertRaises(a.native.AcceptanceFailure): self.baseline()
        self.assertIsNone(self.baseline.cached_result)
        self.data = inventory()
        with patch.object(self.baseline, 'read_contract', side_effect=a.native.AcceptanceFailure('bad_contract')):
            with self.assertRaisesRegex(a.native.AcceptanceFailure, 'bad_contract'): self.baseline()
        self.assertIsNone(self.baseline.cached_result)


class ContractTests(unittest.TestCase):
    def test_frozen_runner_globals_isolated_and_all_reads_bounded(self):
        scope = dict(json=json, LAST={'original': True}, config=copy.deepcopy(CONTRACT))
        exec('def call(args, data=None, timeout=40, limit=4194304, checked=True):\n'
             ' LAST.clear(); LAST.update(operation="exec")\n'
             ' assert timeout==25 and limit==4096 and args[0]=="exec"\n'
             ' return 0,json.dumps(config).encode()\n', scope)
        with patch.object(a.native, 'call', scope['call']):
            self.assertEqual(g.GatewayBaseline(a).read_contract({'name': 'gateway-one'}), CONTRACT)
        self.assertEqual(scope['LAST'], {'original': True})

    def test_bad_contracts_fail_closed_without_body_printed(self):
        for k, v in (('model', 'wrong'), ('dimension', '2048'), ('upstream', 'http://other/'),
                     ('retries', '4'), ('relay_key_present', False), ('upstream_key_present', False),
                     ('mrl', 'false'), ('secret', 'must-not-print')):
            cfg = copy.deepcopy(CONTRACT); cfg[k] = v
            scope = dict(json=json, LAST={}, config=cfg)
            exec('def call(*args,**kwargs): return 0,json.dumps(config).encode()', scope)
            with patch.object(a.native, 'call', scope['call']), patch.object(a, 'emit') as emit:
                with self.assertRaises(a.native.AcceptanceFailure):
                    g.GatewayBaseline(a).read_contract({'name': 'gateway-one'})
            self.assertNotIn('must-not-print', str(emit.call_args_list))

    def test_inventory_output_bound_and_pagination(self):
        b = g.GatewayBaseline(a)
        with patch.object(a.native, 'call', return_value=(0, b'{"items":[]}')) as call:
            self.assertEqual(b.listing('pods', 'app=gateway'), [])
        self.assertEqual(call.call_args.kwargs['limit'], 16*1024**2)
        with patch.object(a.native, 'call', return_value=(0, b'{"items":[],"metadata":{"continue":"x"}}')):
            with self.assertRaisesRegex(a.native.AcceptanceFailure, 'incomplete_inventory'): b.listing('pods', 'app=gateway')


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/root'); self.root = Path(self.temp.name)
        self.old = self.root/'old'; self.old.mkdir(mode=0o700)
        self.row = dict(native_enrichment_acceptance='UNCONFIRMED', failed_check='gateway_rollout_not_stable',
            failed_stage='FRESH_RESOURCE_AND_COMPLETED_GATEWAY_ROLLOUT_REVIEW', document_submission_attempted=False,
            production_writes=0, production_workloads_mutated=False, cleanup='NOT_REQUESTED')
        self.raw = json.dumps(self.row).encode(); (self.old/'RESULT.json').write_bytes(self.raw)
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(r, 'OLD_STATE', self.old))
        self.stack.enter_context(patch.object(r, 'OLD_RESULT_SHA', hashlib.sha256(self.raw).hexdigest()))
        self.stack.enter_context(patch.object(r, 'STATE', self.root/'recovery'))
        self.stack.enter_context(patch.object(r, 'NEW_STATE', self.root/'acceptance'))
        self.stack.enter_context(patch.object(a, 'emit'))
        self.stack.enter_context(patch.object(a, 'STATE'))
        self.stack.enter_context(patch.object(a, 'gateway_baseline'))

    def tearDown(self): self.stack.close(); self.temp.cleanup()

    def test_exact_boundary_and_original_bytes_preserved(self):
        self.assertEqual(r.bind_previous(a)['previous_provider_submissions'], 'ZERO_VERIFIED_PRE_FIXTURE_BOUNDARY')
        with patch.object(a, 'run', return_value=0) as run:
            self.assertEqual(r.recover(a), 0)
        run.assert_called_once(); self.assertEqual((self.old/'RESULT.json').read_bytes(), self.raw)
        self.assertEqual(a.STATE, r.NEW_STATE); self.assertIsInstance(a.gateway_baseline, g.GatewayBaseline)

    def test_existing_marker_refuses_recovery(self):
        for name in ('STARTED.json', 'PROVIDER-REQUESTED.json', 'PREPARED.json'):
            p = self.old/name; p.write_bytes(b'{}')
            with patch.object(a, 'run') as run, self.assertRaisesRegex(r.RecoveryFailure, 'boundary_requires_review'):
                r.recover(a)
            run.assert_not_called(); p.unlink()

    def test_changed_receipt_never_recovered(self):
        (self.old/'RESULT.json').write_bytes(self.raw+b' ')
        with patch.object(a, 'run') as run, self.assertRaises(a.audit.AuditFailure): r.recover(a)
        run.assert_not_called()

    def test_failure_and_second_attempt_never_resubmit(self):
        with patch.object(a, 'run', return_value=1) as run:
            self.assertEqual(r.recover(a), 1)
            with self.assertRaisesRegex(r.RecoveryFailure, 'USE_STATUS'): r.recover(a)
        self.assertEqual(run.call_count, 1)

    def test_new_acceptance_state_already_exists_blocks(self):
        r.NEW_STATE.mkdir(mode=0o700)
        with patch.object(a, 'run') as run, self.assertRaisesRegex(r.RecoveryFailure, 'USE_STATUS'): r.recover(a)
        run.assert_not_called()

    def test_status_does_not_exec_or_restart(self):
        with patch.object(a, 'status') as status, patch.object(a.native, 'call') as call:
            r.status(a)
        call.assert_not_called(); status.assert_called_once(); self.assertFalse(r.STATE.exists())

    def test_missing_or_unsafe_original_script_rejected(self):
        p = self.root/'original.py'; p.write_text('raise RuntimeError("must not execute")')
        with patch.object(r, 'OLD_TOOL', p), self.assertRaisesRegex(r.RecoveryFailure, 'hash_changed'): r.load_original()
        p.chmod(0o666)
        with patch.object(r, 'OLD_TOOL', p), self.assertRaisesRegex(r.RecoveryFailure, 'unsafe_original_tool'): r.load_original()

    def test_python37_grammar(self):
        for name in ('gateway_enrichment_baseline.py', 'recover_enrichment_gateway.py'):
            ast.parse(Path(name).read_text(), feature_version=(3, 7))


if __name__ == '__main__': unittest.main()
