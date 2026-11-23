#!/usr/bin/env python3
"""Hash-bound single Markdown transport module update. No image build, SQL write or model task."""
import argparse
from readiness_wait import wait_ready
import getpass
from config_reconcile import prepare_config, add_config_patch
from dataqa_config import add_dataqa_patch, create_key_secret, secret_name_for, target_proof, validate_planned
import concurrent.futures
import copy
import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent
IMAGE = 'deerflow.local/deer-flow-gateway@sha256:65e386e1534361f7af290cfe325462556c8f9d9b868abc049a6776aa290bca99'
MODULE_ROOT = '/app/backend/app/gateway/procurement_review/'
KUBE = ['sudo', '-n', '-H', '-u', 'cnooc', 'kubectl', '-n', 'deer-flow']
PREFIX = 'df-procurement-dataqa-r4-'
NAMES = ('markdown_report.py',)
BASELINES = {'markdown_report.py': ['bf2ff3f2a9a331b3685f0d4ad3bd983531c859f37ce99c21758a1b887b4a5658']}



def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def kube(record, label, args, data=None):
    result = subprocess.run(KUBE + args, input=data, capture_output=True, timeout=90)
    (record / (label + '.PRIVATE.err')).write_bytes(result.stderr)
    (record / (label + '.PRIVATE.out')).write_bytes(result.stdout)
    require(result.returncode == 0, 'COMMAND_FAILED:' + label)
    return json.loads(result.stdout) if result.stdout.strip() else None


def spec_hash(deployment):
    return sha(json.dumps(deployment['spec'], sort_keys=True, separators=(',', ':')).encode())


def gateway(deployment):
    matches = [(i, c) for i, c in enumerate(deployment['spec']['template']['spec']['containers']) if c['name'] == 'gateway']
    require(len(matches) == 1 and matches[0][1]['image'] == IMAGE, 'GATEWAY_IMAGE_OR_CONTAINER_CHANGED')
    return matches[0]


def stable(deployment):
    count = deployment['spec'].get('replicas', 1)
    status = deployment.get('status', {})
    return count > 0 and not deployment['spec'].get('paused') and status.get('observedGeneration', 0) >= deployment['metadata']['generation'] and all(status.get(k, 0) == count for k in ('updatedReplicas', 'readyReplicas', 'availableReplicas', 'replicas'))


def live_pods(record, deployment, label):
    sets = kube(record, label + '-rs', ['get', 'replicasets', '-l', 'app=gateway', '-o', 'json'])['items']
    owners = {r['metadata']['uid'] for r in sets if any(o.get('uid') == deployment['metadata']['uid'] for o in r['metadata'].get('ownerReferences', []))}
    pods = kube(record, label, ['get', 'pods', '-l', 'app=gateway', '-o', 'json'])['items']
    live = [p for p in pods if not p['metadata'].get('deletionTimestamp') and any(o.get('uid') in owners for o in p['metadata'].get('ownerReferences', []))]
    require(len(live) == deployment['spec'].get('replicas', 1), 'POD_COUNT_CHANGED')
    for p in live:
        require(any(c['type'] == 'Ready' and c['status'] == 'True' for c in p.get('status', {}).get('conditions', [])), 'POD_NOT_READY')
        require(next(c['image'] for c in p['spec']['containers'] if c['name'] == 'gateway') == IMAGE, 'POD_IMAGE_CHANGED')
    return live


def runtime(record, pod, data, label='runtime'):
    return kube(record, label + '-' + pod, ['exec', '-i', pod, '-c', 'gateway', '--', '/app/backend/.venv/bin/python', '-c', (ROOT / 'probe.py').read_text(encoding='utf-8')], json.dumps(data, ensure_ascii=False).encode())


def all_runtime(record, pods, expected, other_paths, http=False):
    names = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(runtime, record, p['metadata']['name'], {'other_paths': other_paths, 'http': http}): p['metadata']['name'] for p in pods}
        for future in concurrent.futures.as_completed(futures):
            name = futures[future]
            proof = future.result()
            require(all(proof.get(k) == v for k, v in expected.items()), 'RUNTIME_MISMATCH:' + name)
            names.append(name)
            if len(names) % 10 == 0 or len(names) == len(pods):
                print(json.dumps({'stage': 'RUNTIME_VERIFIED', 'pods': len(names), 'total': len(pods)}), flush=True)
    return names


def patch_for(deployment, code_name):
    index, container = gateway(deployment)
    spec = deployment['spec']['template']['spec']
    mounts = copy.deepcopy(container.get('volumeMounts', []))
    targets = {MODULE_ROOT + n for n in NAMES}
    for target in targets:
        require(sum(m['mountPath'] == target for m in mounts) <= 1, 'DUPLICATE_TARGET_MOUNT')
    for m in mounts:
        path = PurePosixPath(m['mountPath'])
        require(m['mountPath'] in targets or not any(path == PurePosixPath(t) or path in PurePosixPath(t).parents for t in targets), 'PARENT_CODE_MOUNT_REQUIRES_RECONCILIATION')
    mounts = [m for m in mounts if m['mountPath'] not in targets]
    mounts += [{'name': code_name, 'mountPath': MODULE_ROOT + n, 'subPath': n, 'readOnly': True} for n in NAMES]
    volumes = copy.deepcopy(spec.get('volumes', []))
    require(not any(v['name'] == code_name for v in volumes), 'TARGET_VOLUME_ALREADY_PRESENT_USE_STATUS')
    volumes.append({'name': code_name, 'configMap': {'name': code_name, 'defaultMode': 292}})
    base = '/spec/template/spec'
    return [
        {'op': 'test', 'path': '/metadata/uid', 'value': deployment['metadata']['uid']},
        {'op': 'test', 'path': '/metadata/resourceVersion', 'value': deployment['metadata']['resourceVersion']},
        {'op': 'add', 'path': base + '/volumes', 'value': volumes},
        {'op': 'add', 'path': base + '/containers/' + str(index) + '/volumeMounts', 'value': mounts},
    ]


def ensure_object(record, obj):
    name = obj['metadata']['name']
    existing = kube(record, 'get-' + name, ['get', 'configmap', name, '--ignore-not-found', '-o', 'json'])
    if existing is None:
        kube(record, 'create-' + name, ['create', '-f', '-', '-o', 'json'], json.dumps(obj, ensure_ascii=False).encode())
    else:
        require(existing.get('immutable') is True and existing.get('data') == obj['data'] and not existing.get('binaryData'), 'CONFIGMAP_CONFLICT')


def verify(record, state, wait):
    end = time.monotonic() + wait
    while True:
        deployment = kube(record, 'deployment-status', ['get', 'deployment', 'gateway', '-o', 'json'])
        require(deployment['metadata']['uid'] == state['uid'] and spec_hash(deployment) == state['expected_spec_sha256'], 'DEPLOYMENT_CHANGED_OR_SUBMISSION_NOT_OBSERVED')
        if 'generation' not in state:
            state['generation'] = deployment['metadata']['generation']
            save(record / 'state.json', state)
        require(deployment['metadata']['generation'] == state['generation'], 'DEPLOYMENT_GENERATION_CHANGED')
        counts = deployment.get('status', {})
        print(json.dumps({'stage': 'GATEWAY_ROLLING', 'generation': state['generation'], 'updated': counts.get('updatedReplicas', 0), 'ready': counts.get('readyReplicas', 0), 'total': counts.get('replicas', 0)}), flush=True)
        if stable(deployment):
            break
        require(time.monotonic() < end, 'ROLLOUT_IN_PROGRESS_USE_STATUS_RECORD')
        time.sleep(10)
    pods = live_pods(record, deployment, 'pods-final')
    names = all_runtime(record, pods, state['runtime_expected'], state['other_paths'], http=True)
    final = kube(record, 'deployment-final', ['get', 'deployment', 'gateway', '-o', 'json'])
    require(final['metadata']['generation'] == state['generation'] and spec_hash(final) == state['expected_spec_sha256'], 'DEPLOYMENT_CHANGED_DURING_VERIFY')
    proof = runtime(record, pods[0]['metadata']['name'], {'database': True, 'other_paths': state['other_paths']}, label='database-final')
    require(proof['schema_versions'] == ['0023_procurement_review_jobs'], 'SCHEMA_VERSION_CHANGED')
    result = {'record': str(record), 'passed': True, 'result': 'PROCUREMENT_MD_AND_DATAQA_CONFIG_RUNTIME_VERIFIED', 'patch_attempted': True, 'deployment_modified': True, 'verified_pods': len(names), 'job_timeout_seconds': 3600, 'bash_warn': 80, 'bash_limit': 100, 'image_changed': False, 'database_migrated': False, 'frontend_modified': False, 'model_called': False, 'callback_called': False, 'old_jobs_resumed': False, 'live_business_acceptance': False, 'ingress_stream_tested': False, 'remaining': ['EXISTING_FAILED_JOB_NOT_RECOVERED', 'NEW_DATAQA_LIVE_SSE_ACCEPTANCE']}
    save(record / 'RESULT.json', result)
    print(json.dumps(result), flush=True)


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--execute', action='store_true')
    group.add_argument('--status-record', type=Path)
    parser.add_argument('--wait-seconds', type=int, default=7200)
    parser.add_argument('--ready-wait-seconds', type=int, default=300)
    args = parser.parse_args()
    require(0 <= args.wait_seconds <= 14400, 'INVALID_WAIT_SECONDS')
    require(0 <= args.ready_wait_seconds <= 1800, 'INVALID_READY_WAIT_SECONDS')
    os.umask(0o077)
    record = args.status_record.resolve(strict=True) if args.status_record else Path(tempfile.mkdtemp(prefix=PREFIX, dir='/opt'))
    require(record.parent == Path('/opt') and record.name.startswith(PREFIX), 'INVALID_RECORD')
    state = json.loads((record / 'state.json').read_text()) if args.status_record else {'patch_attempted': False}
    print(json.dumps({'record': str(record)}), flush=True)
    try:
        manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
        for name, expected in manifest['files'].items():
            path = ROOT / name
            require(path.resolve().is_relative_to(ROOT) and sha(path.read_bytes()) == expected, 'PACKAGE_HASH_MISMATCH:' + name)
        if args.status_record:
            require(state.get('patch_attempted'), 'NO_SUBMISSION_TO_OBSERVE')
            verify(record, state, args.wait_seconds)
            return
        sources = {n: (ROOT / 'payload' / n).read_text(encoding='utf-8') for n in NAMES}
        target = {n: sha(sources[n].encode()) for n in NAMES}
        deployment, pods = wait_ready(record, args.ready_wait_seconds, kube, stable, live_pods, spec_hash)
        _, container = gateway(deployment)
        require(deployment['spec'].get('strategy', {}).get('type', 'RollingUpdate') == 'RollingUpdate', 'ROLLING_UPDATE_REQUIRED')
        other_paths = sorted(m['mountPath'] for m in container.get('volumeMounts', []) if m['mountPath'].endswith('.py') and m['mountPath'] not in {MODULE_ROOT + n for n in NAMES})
        config_object, config_proof = prepare_config(ROOT, record, deployment, pods[0]['metadata']['name'], kube)
        proof = runtime(record, pods[0]['metadata']['name'], {'candidate': sources, 'database': True, 'other_paths': other_paths, 'http': True}, label='preflight')
        require(proof['config_yaml_sha256'] == config_proof['before_sha256'], 'MOUNTED_CONFIG_BYTES_MISMATCH')
        require(proof.get('candidate_synthetic_passed') and proof['active_jobs'] == 0 and proof['active_review_runs'] == 0, 'PROCUREMENT_TASKS_ACTIVE_OR_CANDIDATE_CHECK_FAILED')
        require(proof['schema_versions'] == ['0023_procurement_review_jobs'], 'SCHEMA_BASELINE_CHANGED')
        require(all(proof['source_sha256'][n] in BASELINES[n] for n in NAMES), 'SOURCE_BASELINE_CHANGED_DO_NOT_FORCE')
        expected = {k: v for k, v in proof.items() if k not in {'schema_versions', 'active_jobs', 'active_review_runs', 'candidate_synthetic_passed'}}
        all_runtime(record, pods, expected, other_paths, http=True)
        name = 'procurement-md-delivery-' + sha(json.dumps(target, sort_keys=True).encode())[:12]
        patch = patch_for(deployment, name)
        secret_name = secret_name_for(record)
        patch = add_dataqa_patch(deployment, patch, secret_name)
        patch = add_config_patch(deployment, patch, config_object['metadata']['name'])
        save(record / 'preflight.json', proof)
        save(record / 'planned-patch.PRIVATE.json', patch)
        if not args.execute:
            print(json.dumps({'record': str(record), 'passed': True, 'result': 'PREFLIGHT_ONLY_NOT_APPLIED', 'modules': 1}), flush=True)
            return
        # Recheck active work and current fingerprints immediately before the
        # single atomic Deployment patch. Caller must pause new submissions.
        latest = runtime(record, pods[0]['metadata']['name'], {'database': True, 'other_paths': other_paths, 'http': True}, label='prepatch')
        require(latest['active_jobs'] == 0 and latest['active_review_runs'] == 0 and all(latest.get(k) == v for k, v in expected.items()), 'PREPATCH_STATE_CHANGED')
        key = getpass.getpass('请输入新 Data QA 接口密钥（不回显）: ').strip()
        require(bool(key) and not any(c.isspace() for c in key), 'INVALID_KEY')
        key_sha = sha(key.encode())
        create_key_secret(KUBE, secret_name, key, record=record)
        del key
        state['dataqa_secret_created'] = secret_name
        save(record / 'state.json', state)
        ensure_object(record, config_object)
        state['config_clone_prepared'] = config_object['metadata']['name']
        save(record / 'state.json', state)
        ensure_object(record, {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': name, 'namespace': 'deer-flow'}, 'immutable': True, 'data': sources})
        state['configmap_prepared'] = name
        save(record / 'state.json', state)
        patch_text = json.dumps(patch)
        planned = kube(record, 'dry-run', ['patch', 'deployment', 'gateway', '--type=json', '-p', patch_text, '--dry-run=server', '-o', 'json'])
        _, planned_container = gateway(planned)
        validate_planned(container, planned_container, secret_name)
        runtime_expected = dict(expected, source_sha256=target, config_yaml_sha256=config_proof['after_sha256'], data_qa=target_proof(expected['data_qa'], key_sha))
        state.update(patch_attempted=True, uid=deployment['metadata']['uid'], expected_spec_sha256=spec_hash(planned), runtime_expected=runtime_expected, other_paths=other_paths)
        save(record / 'state.json', state)
        changed = kube(record, 'patch', ['patch', 'deployment', 'gateway', '--type=json', '-p', patch_text, '-o', 'json'])
        state['generation'] = changed['metadata']['generation']
        save(record / 'state.json', state)
        print(json.dumps({'stage': 'ROLLOUT_SUBMITTED', 'record': str(record), 'generation': state['generation'], 'protocol': 'POST_SSE_MARKDOWN', 'report_content_validation': False, 'dataqa_app_id': 'app-jeg6ud59', 'new_job_callback': False}), flush=True)
        verify(record, state, args.wait_seconds)
    except Exception as exc:
        (record / 'failure.PRIVATE.txt').write_text(str(exc), encoding='utf-8')
        result = {'record': str(record), 'passed': False, 'patch_attempted': state.get('patch_attempted', False), 'configmap_prepared': state.get('configmap_prepared'), 'error_class': type(exc).__name__, 'error': str(exc), 'note': 'No automatic rollback or repeated patch. After submission use --status-record on this record.'}
        save(record / 'RESULT.json', result)
        print(json.dumps(result), flush=True)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
