"""Bounded, complete Gateway inventory for an isolated Embedding acceptance.

No sampling: every Service endpoint has its runtime configuration read once.
Subsequent reads reuse it only for identical Pod/container/config identities.
"""
from concurrent.futures import ThreadPoolExecutor
import copy
import ipaddress
import json
import re
import time
import types

MAX_ENDPOINTS = 128  # Tool workload bound, NOT a production capacity limit.
PARALLEL_READS = 4
SCAN_SECONDS = 240


class GatewayBaseline:
    def __init__(self, acceptance):
        self.a = acceptance
        self.cached_inventory = None
        self.cached_result = None

    def rollout(self, deployment):
        a = self.a
        status = deployment.get('status', {})
        desired = deployment.get('spec', {}).get('replicas', 1)
        a.need(type(desired) is int and 1 <= desired <= MAX_ENDPOINTS,
               'gateway_inventory_exceeds_tool_bound')
        checks = dict(generation_observed=status.get('observedGeneration', 0) >= deployment['metadata']['generation'])
        checks.update({key: status.get(key, 0) == desired for key in
                       ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas')})
        checks['not_terminating'] = not status.get('terminatingReplicas', 0)
        checks['not_unavailable'] = not status.get('unavailableReplicas', 0)
        if not all(checks.values()):
            a.emit({'gateway_review': 'NOT_CONVERGED', 'desired': desired, 'checks': checks,
                    'production_writes': 0, 'provider_requests': 0})
        a.need(all(checks.values()), 'gateway_rollout_not_converged')
        return desired

    def listing(self, kind, selector):
        raw = self.a.native.call(['get', kind, '-n', 'deer-flow', '-l', selector,
            '--show-managed-fields=false', '-o', 'json'], limit=16*1024**2)[1]
        value = json.loads(raw)
        self.a.need(isinstance(value.get('items'), list) and not value.get('metadata', {}).get('continue'),
                    'gateway_incomplete_inventory')
        return value['items']

    def inventory(self):
        a = self.a
        d = a.native.get('deployment', 'gateway', 'deer-flow')
        desired = self.rollout(d)
        svc = a.native.get('service', 'gateway', 'deer-flow')
        spec = svc['spec']; service_ip = spec.get('clusterIP', '')
        a.need(ipaddress.ip_address(service_ip).version == 4, 'gateway_ip_family')
        a.need(spec.get('type', 'ClusterIP') == 'ClusterIP' and not spec.get('publishNotReadyAddresses', False)
               and any(x.get('port') == 8001 and x.get('protocol', 'TCP') == 'TCP' for x in spec.get('ports', [])),
               'gateway_service_shape')
        labels = spec.get('selector', {})
        a.need(isinstance(labels, dict) and 0 < len(labels) <= 16 and all(
            re.fullmatch(r'[A-Za-z0-9_.\-/]{1,253}', k) and
            re.fullmatch(r'[A-Za-z0-9_.\-]{0,63}', v) for k, v in labels.items()), 'gateway_selector_shape')
        selector = ','.join(k+'='+v for k, v in sorted(labels.items()))
        all_pods = self.listing('pods', selector)
        all_rs = self.listing('replicasets', selector)
        rs = {x['metadata']['uid']: x for x in all_rs if any(
            r.get('controller') is True and r.get('kind') == 'Deployment' and r.get('name') == 'gateway'
            and r.get('uid') == d['metadata']['uid'] for r in x['metadata'].get('ownerReferences', []))}
        by_name = {p['metadata']['name']: p for p in all_pods}
        endpoints = a.native.get('endpoints', 'gateway', 'deer-flow')
        a.need(endpoints.get('metadata', {}).get('annotations', {}).get('endpoints.kubernetes.io/over-capacity')
               != 'truncated', 'gateway_endpoints_truncated')
        pods = []
        for subset in endpoints.get('subsets', []):
            a.need(not subset.get('notReadyAddresses') and any(
                p.get('port') == 8001 and p.get('protocol', 'TCP') == 'TCP' for p in subset.get('ports', [])),
                'gateway_endpoints_not_ready')
            for address in subset.get('addresses', []):
                ref = address.get('targetRef', {})
                a.need(ref.get('kind') == 'Pod' and ref.get('namespace', 'deer-flow') == 'deer-flow',
                       'gateway_endpoint_not_owned_pod')
                p = by_name.get(ref.get('name'))
                a.need(p is not None and p['metadata']['uid'] == ref.get('uid'), 'gateway_pod_uid_changed')
                meta = p['metadata']; ps = p.get('status', {})
                owner = [r for r in meta.get('ownerReferences', []) if r.get('controller') is True]
                a.need(len(owner) == 1 and owner[0].get('kind') == 'ReplicaSet' and owner[0].get('uid') in rs,
                       'gateway_endpoint_foreign_owner')
                parent = rs[owner[0]['uid']]
                a.need(owner[0].get('name') == parent['metadata']['name'] and not parent['metadata'].get('deletionTimestamp'),
                       'gateway_replicaset_changed')
                a.need(not meta.get('deletionTimestamp') and ps.get('phase') == 'Running' and
                       any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in ps.get('conditions', [])),
                       'gateway_pod_not_ready')
                a.need(ps.get('podIP') == address['ip'] and ipaddress.ip_address(address['ip']).version == 4,
                       'gateway_pod_ip_changed')
                c = [v for v in ps.get('containerStatuses', []) if v.get('name') == 'gateway']
                a.need(len(c) == 1 and c[0].get('ready') is True and c[0].get('state', {}).get('running') and
                       c[0].get('imageID') and c[0].get('containerID'), 'gateway_container_not_ready')
                pods.append(dict(name=ref['name'], uid=ref['uid'], ip=address['ip'], image_id=c[0]['imageID'],
                    container_id=c[0]['containerID'], restarts=c[0].get('restartCount', 0),
                    started_at=c[0]['state']['running'].get('startedAt'),
                    spec_sha256=a.spec_hash(p['spec']), owner_uid=owner[0]['uid']))
        a.need(len(pods) == desired and len({p['uid'] for p in pods}) == desired and
               len({p['ip'] for p in pods}) == desired, 'gateway_endpoint_coverage')
        a.need(len({p['image_id'] for p in pods}) == 1 and len({p['owner_uid'] for p in pods}) == 1,
               'gateway_mixed_revision_requires_review')
        return dict(deployment_uid=d['metadata']['uid'], generation=d['metadata']['generation'],
            desired=desired, spec_sha256=a.spec_hash(d['spec']), service_uid=svc['metadata']['uid'],
            service_spec_sha256=a.spec_hash(spec), service_ip=service_ip,
            pods=sorted(pods, key=lambda p: p['uid']))

    def read_contract(self, pod):
        a = self.a
        # Reuse the frozen bounded subprocess runner, but isolate its mutable LAST
        # dictionary per thread so a successful neighbour cannot mask a failure.
        call = types.FunctionType(a.native.call.__code__, dict(a.native.call.__globals__, LAST={}),
                                  a.native.call.__name__, a.native.call.__defaults__)
        try:
            raw = call(['exec', '-n', 'deer-flow', pod['name'], '-c', 'gateway', '--',
                'python3', '-I', '-B', '-c', a.GATEWAY_CONFIG], limit=4096, timeout=25)[1]
            cfg = json.loads(raw)
            a.need(set(cfg) == {'model', 'dimension', 'upstream', 'retries', 'mrl',
                               'relay_key_present', 'upstream_key_present'} and
                   cfg['model'] == 'Qwen3-Embedding-8B' and cfg['dimension'] == '1024' and
                   cfg['mrl'].lower() == 'true' and cfg['relay_key_present'] is True and
                   cfg['upstream_key_present'] is True and re.fullmatch('[0-3]', cfg['retries']) and
                   cfg['upstream'] == 'http://model.ai.cnooc/member1/qwen3-embedding-8b/v1/embeddings',
                   'gateway_embedding_contract_requires_review')
            return cfg
        except Exception as exc:
            # No environment contents, stderr, credentials or response body.
            a.emit({'gateway_config_read': 'FAIL', 'pod': pod['name'], 'error_type': type(exc).__name__,
                    'failed_check': str(exc) if isinstance(exc, a.native.AcceptanceFailure) else 'SAFE_DIAGNOSTIC_REQUIRED',
                    'raw_output_printed': False})
            raise

    def __call__(self):
        a = self.a
        before = self.inventory()
        if self.cached_inventory is not None:
            a.need(before == self.cached_inventory, 'gateway_identity_changed_after_complete_review')
            a.emit({'gateway_review': 'PASS', 'endpoints': len(before['pods']),
                    'runtime_contracts': 'REUSED_ONLY_FOR_IDENTICAL_CONTAINER_AND_SPEC',
                    'pod_exec': False, 'provider_requests': 0, 'production_writes': 0})
            return copy.deepcopy(self.cached_result)
        started = time.monotonic(); contracts = []
        a.emit({'gateway_review': 'READ_ALL_RUNTIME_CONTRACTS', 'endpoints': len(before['pods']),
                'maximum_parallel_readonly_exec': PARALLEL_READS, 'sampling': False, 'provider_requests': 0})
        with ThreadPoolExecutor(max_workers=PARALLEL_READS) as executor:
            for offset in range(0, len(before['pods']), PARALLEL_READS):
                a.need(time.monotonic()-started < SCAN_SECONDS, 'gateway_contract_scan_deadline')
                batch = before['pods'][offset:offset+PARALLEL_READS]
                futures = [executor.submit(self.read_contract, p) for p in batch]
                contracts.extend(f.result() for f in futures)
                a.emit({'gateway_contracts_verified': len(contracts), 'of': len(before['pods']), 'provider_requests': 0})
        a.need(self.inventory() == before, 'gateway_changed_during_contract_scan')
        result = copy.deepcopy(before)
        for p, cfg in zip(result['pods'], contracts): p['contract'] = cfg
        self.cached_inventory = before
        self.cached_result = result
        return copy.deepcopy(result)
