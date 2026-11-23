"""Bounded read-only preflight wait; never lowers the release health threshold."""
import json
import time


def wait_ready(record, seconds, kube, stable, live_pods, spec_hash):
    deadline = time.monotonic() + seconds
    identity = None
    while True:
        deployment = kube(record, 'deployment-before', ['get', 'deployment', 'gateway', '-o', 'json'])
        current = (deployment['metadata']['uid'], spec_hash(deployment))
        if identity is None:
            identity = current
        if current != identity:
            raise RuntimeError('DEPLOYMENT_CHANGED_WHILE_WAITING')
        if deployment['spec'].get('paused'):
            raise RuntimeError('DEPLOYMENT_PAUSED')
        status = deployment.get('status', {})
        evidence = {'stage': 'WAITING_FOR_GATEWAY_READINESS', 'generation': deployment['metadata']['generation'],
                    'observed': status.get('observedGeneration', 0), 'desired': deployment['spec'].get('replicas', 1),
                    **{k: status.get(k, 0) for k in ('updatedReplicas', 'readyReplicas', 'availableReplicas', 'replicas')}}
        pods = None
        if stable(deployment):
            try:
                pods = live_pods(record, deployment, 'pods-before')
            except RuntimeError as exc:
                if str(exc) not in {'POD_COUNT_CHANGED', 'POD_NOT_READY'}:
                    raise
                evidence['pod_check'] = str(exc)
        ready = pods is not None
        evidence['stage'] = 'GATEWAY_PREFLIGHT_READY' if ready else evidence['stage']
        evidence['remaining_wait_seconds'] = max(0, round(deadline - time.monotonic()))
        with (record / 'readiness.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(evidence) + '\n')
        print(json.dumps(evidence), flush=True)
        if ready:
            return deployment, pods
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError('PREFLIGHT_READINESS_TIMEOUT')
        time.sleep(min(10, remaining))
