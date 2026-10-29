"""Verify the deployed 389/231 baseline and immutable historical component evidence.

Offline only. --ref verifies committed Git objects, not uncommitted working files.
Historical release inventories are never rewritten to accept newer runtime code.
"""
import argparse
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
LOCK = 'deploy/enterprise/production-source-lock-20261029.json'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def verify(ref=None):
    cache = {}

    def prefetch(paths):
        if not ref:
            return
        missing = sorted(set(paths) - cache.keys())
        if not missing:
            return
        raw = subprocess.check_output(['git', 'cat-file', '--batch'], cwd=ROOT,
                                      input=''.join(ref + ':' + p + '\n' for p in missing).encode())
        stream = io.BytesIO(raw)
        for path in missing:
            header = stream.readline().decode().split()
            assert len(header) == 3 and header[1] == 'blob', (path, 'MISSING_GIT_BLOB')
            cache[path] = stream.read(int(header[2]))
            assert stream.read(1) == b'\n', 'INVALID_GIT_BATCH'

    def read(path):
        if ref:
            prefetch([path])
            return cache[path]
        return (ROOT / path).read_bytes()

    lock = json.loads(read(LOCK))
    spec = importlib.util.spec_from_file_location('historical_source_verifier', ROOT / 'deploy/enterprise/verify-release-source-20261023.py')
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    result = old.verify(ref, LOCK)
    prefetch(lock['evidence_files'])
    evidence = {name: read(name) for name in lock['evidence_files']}
    for name, expected in lock['evidence_files'].items():
        assert sha(evidence[name]) == expected, (name, 'EVIDENCE_CHANGED')
    prefix = 'release/combined-features-20261028-r1/'
    seal = json.loads(evidence[prefix + 'SOURCE.json'])
    for component in ('gateway', 'frontend'):
        assert lock['source_sets'][component] == {row['path']: row['sha256'] for row in seal['source_inventories'][component]}, 'SEALED_SOURCE_SET_CHANGED'
    assert not seal['work_update_included'] and not seal['migration_required'], 'UNEXPECTED_RELEASE_SCOPE'
    gw = json.loads(evidence[prefix + 'GATEWAY-BUILD.json'])
    fe = json.loads(evidence[prefix + 'FRONTEND-PACKAGE.json'])
    assert gw['qualification']['source_seal_sha256'] == sha(evidence[prefix + 'SOURCE.json']), 'GATEWAY_SOURCE_BINDING'
    assert fe['source_change_sha256'] == sha(evidence[prefix + 'SOURCE.json']), 'FRONTEND_SOURCE_BINDING'
    assert lock['images']['gateway'] == 'deerflow.local/deer-flow-gateway@' + gw['profile']['roles'][0]['manifest_digest'], 'GATEWAY_IMAGE_BINDING'
    assert lock['images']['frontend'] == 'deerflow.local/deer-flow-frontend@' + fe['manifest'], 'FRONTEND_IMAGE_BINDING'
    receipt = json.loads(evidence[prefix + 'RUNTIME-ACCEPTED-FIELD-20261029-r1.json'])
    assert receipt['sealed_build_source_receipt']['sha256'] == sha(evidence[prefix + 'SOURCE.json']), 'FIELD_SOURCE_BINDING'
    field = receipt['operator_summary']
    assert field['runtime_accepted'] and field['candidate_images_deployed'], 'RUNTIME_NOT_ACCEPTED'
    assert field['after'] == lock['accepted_deployments'], 'DEPLOYMENT_BINDING'
    assert field['accepted_pods'] == {'gateway': 70, 'frontend': 2}, 'REPLICA_ACCEPTANCE'
    assert not field['v41_enabled'] and not field['work_update_included'] and field['default_model_preserved'], 'FEATURE_SCOPE_CHANGED'
    assert not field['schema_migration_requested'] and field['other_deployments_preserved'], 'OTHER_WORKLOADS_CHANGED'
    assert field['report_sha256'] == lock['server_report_sha256'], 'SERVER_RECEIPT_CHANGED'
    # The old UI base has unproven provenance. Revalidate its exact archived overlay
    # and October 26 observation separately; never label that observation as fresh.
    historical = json.loads(evidence[lock['historical_components_lock']])
    patch = historical['runtime_patch']
    paths = [*patch['files'], *patch['qualified_source_files'], historical['current_observation']['path']]
    if ref:
        raw = subprocess.check_output(['git', 'archive', ref, *paths], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
            archive = {m.name: tar.extractfile(m).read() for m in tar if m.isfile()}
        patch_read = archive.__getitem__
    else:
        patch_read = read
    result['results'].append(old.verify_runtime_patch(patch_read, historical))
    assert lock['source_sets']['ppt-master-runner'] == historical['source_sets']['ppt-master-runner'], 'RUNNER_SNAPSHOT_CHANGED'
    assert lock['images']['ppt-master-runner'] == historical['images']['ppt-master-runner'], 'RUNNER_IMAGE_CHANGED'
    # Disallow an unsealed new frontend runtime file, as well as changed files.
    expected = {n for n in lock['source_sets']['frontend'] if n.startswith(('frontend/src/', 'frontend/public/'))}
    if ref:
        names = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', ref, 'frontend/src', 'frontend/public'], cwd=ROOT).decode().splitlines()
        actual = set(names)
    else:
        actual = {p.relative_to(ROOT).as_posix() for base in ('frontend/src', 'frontend/public') for p in (ROOT / base).rglob('*') if p.is_file()}
    assert actual == expected, ('FRONTEND_RUNTIME_SET_CHANGED', sorted(actual ^ expected))
    result.update(status='PRODUCTION_389_231_SOURCE_VERIFIED', historical_component_observation='2026-10-26; preserved by combined rollout receipt',
                  runtime_accepted=True, browser_and_business_acceptance_pending=True,
                  v41_enabled=False, work_update_included=False,
                  evidence_files_verified=len(evidence), production_modified=False)
    if result.get('candidate'):
        result.update(status='CANDIDATE_ON_PRODUCTION_BASELINE_VERIFIED', runtime_accepted=False,
                      production_baseline_runtime_accepted=True, work_update_included=True,
                      candidate_deployed=False)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref')
    print(json.dumps(verify(parser.parse_args().ref), ensure_ascii=False))
