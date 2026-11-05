"""Build a source-pinned, process-local OA candidate probe (no deployment)."""
import ast
import base64
import hashlib
import json
import pathlib
import subprocess
import zlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULES = {
    'app.gateway.oa_policy_flow_config': 'app/gateway/oa_policy_flow_config.py',
    'app.gateway.services': 'app/gateway/services.py',
    'app.gateway.oa_policy_flow_results': 'app/gateway/oa_policy_flow_results.py',
    'app.gateway.oa_policy_flow_service': 'app/gateway/oa_policy_flow_service.py',
}


def build():
    modules, baseline = {}, {}
    for name, path in MODULES.items():
        source = (ROOT / 'backend' / path).read_text(encoding='utf-8')
        compile(source, path, 'exec')
        modules[name] = {'source': source, 'sha256': hashlib.sha256(source.encode()).hexdigest()}
        if name.endswith('oa_policy_flow_results'):
            baseline[path] = None
        else:
            commit = '739e17f9a4d5a8d1bf505a5934b5c3883a23a24c' if name.endswith('.services') else 'd7d119af686d55759a6870b00d464992b3f2ea47'
            raw = subprocess.check_output(['git', 'show', commit + ':backend/' + path], cwd=ROOT)
            baseline[path] = hashlib.sha256(raw).hexdigest()
    bundle = {'modules': modules, 'baseline': baseline}
    checker = (ROOT / 'deploy/enterprise/oa-v2-candidate-check.py').read_text(encoding='utf-8')
    payload = json.dumps({'bundle': bundle, 'checker': checker}, separators=(',', ':')).encode()
    encoded = base64.b64encode(zlib.compress(payload, 9)).decode()
    child = ('import base64,zlib,json\np=json.loads(zlib.decompress(base64.b64decode(' + repr(encoded) + ')))\n'
             + 'exec(compile(p["checker"],"<oa-candidate-check>","exec"))\nrun(p["bundle"])\n')
    assert len(child.encode()) < 100000, 'CHILD_ARG_TOO_LARGE'
    source = (ROOT / 'deploy/enterprise/inspect-oa-policy-flow-cases.py').read_text(encoding='utf-8')
    start, end = source.index('CHILD = '), source.index('\n\n\ndef main')
    source = source[:start] + 'CHILD = ' + repr(child) + source[end:]
    source = source.replace('--inspect-readonly', '--verify-candidate-offline')
    source = source.replace("pods = query(['get', 'pods', '-l', 'app=gateway', '-o', 'json'])['items']", """assert any(c['name'] == 'gateway' and c['image'] == expected for c in dep['spec']['template']['spec']['containers']), 'DEPLOYMENT_IMAGE_CHANGED'
    status = dep['status']; desired = dep['spec']['replicas']
    assert desired == 70 and status.get('observedGeneration') == 382, 'DEPLOYMENT_NOT_OBSERVED'
    assert all(status.get(k) == desired for k in ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas')), 'ROLLOUT_IN_PROGRESS'
    assert not status.get('terminatingReplicas') and not status.get('unavailableReplicas'), 'ROLLOUT_IN_PROGRESS'
    pods = query(['get', 'pods', '-l', 'app=gateway', '-o', 'json'])['items']""")
    source = source.replace("assert after['metadata']['uid'] == uid, 'POD_CHANGED'", """assert after['metadata']['uid'] == uid, 'POD_CHANGED'
    before_container = next(c for c in pod['status']['containerStatuses'] if c['name'] == 'gateway')
    after_container = next(c for c in after['status']['containerStatuses'] if c['name'] == 'gateway')
    assert before_container.get('containerID') and before_container['containerID'] == after_container.get('containerID') and after_container.get('ready'), 'CONTAINER_CHANGED'
    current = query(['get', 'deployment', 'gateway', '-o', 'json'])
    assert current['metadata']['uid'] == dep['metadata']['uid'] and current['metadata']['generation'] == dep['metadata']['generation'] and current['spec'] == dep['spec'], 'DEPLOYMENT_CHANGED'""")
    source = source.replace("'status': 'READONLY_CASES_COLLECTED'", "'status': 'OA_V2_CANDIDATE_CHECKED_NOT_DEPLOYED' if observation.get('status') == 'CANDIDATE_OFFLINE_RUNTIME_ACCEPTED' else 'INCOMPLETE'")
    source = source.replace("'callback_requests': 0}, sort_keys=True))", "'callback_requests': 0, 'ready_for_rollout': False}, sort_keys=True))\n    if observation.get('status') != 'CANDIDATE_OFFLINE_RUNTIME_ACCEPTED':\n        raise SystemExit(1)")
    source = source.replace('portal-oa-case-diagnosis.v1', 'portal-oa-v2-runtime-candidate.v1').replace('READONLY_CASES_COLLECTED', 'OA_V2_CANDIDATE_CHECKED_NOT_DEPLOYED')
    ast.parse(source, feature_version=(3, 7))
    compile(child, '<candidate-child>', 'exec')
    return source, payload


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--payload', required=True)
    args = parser.parse_args()
    source, payload = build()
    pathlib.Path(args.output).write_text(source, encoding='utf-8')
    pathlib.Path(args.payload).write_bytes(payload)
    print(json.dumps({'candidate_payload_sha256': hashlib.sha256(payload).hexdigest(), 'script_bytes': len(source.encode()), 'production_modified': False, 'ready_for_rollout': False}))
