"""Verify committed/source trees against the accepted image-source inventory.

No imports from the application, cluster calls, model calls or database access.
Use --ref HEAD after committing to check Git objects rather than working files.
"""
import argparse
import ast
import hashlib
import importlib.util
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile

# This verifier is also loaded by file path from backend tests. Resolve its
# sibling explicitly so those callers need no process-wide sys.path mutation.
_spec = importlib.util.spec_from_file_location("released_source_20261102", Path(__file__).with_name("released_source_20261102.py"))
_nov02 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_nov02)
NOV02_ARCHIVE, NOV02_DELTA, nov02_overrides = _nov02.ARCHIVE, _nov02.DELTA, _nov02.overrides

ROOT = Path(__file__).resolve().parents[2]
LOCK = 'deploy/enterprise/production-source-lock-20261029.json'
RUNNER = 'release/production-source-20261022/ppt-master-runner/'
FORMAT_BASELINE = 'release/production-source-20261023/gateway-format-baseline/'
TEST_BASELINE = 'release/production-source-20261026/frontend-test-baseline/'
CANDIDATE = 'deploy/enterprise/work-source-candidate-20261026.json'
INCIDENT_DELTA = 'deploy/enterprise/incident-source-candidate-20261031.json'
RELEASED_DELTA = 'deploy/enterprise/released-source-delta-20261101.json'
WORK_ROUTING_DELTA = 'deploy/enterprise/work-routing-source-candidate-20261102.json'
WORK_TASK_AGENT_DELTA = 'deploy/enterprise/work-task-agent-source-candidate-20261102.json'
WORK_TASK_AGENT_FIX_DELTA = 'deploy/enterprise/work-task-agent-fix-source-candidate-20261103.json'
WORK_TASK_AGENT_MODEL_FIX_DELTA = 'deploy/enterprise/work-task-agent-model-fix-source-candidate-20261103.json'
WORK_ROUTING_BASE_COMMIT = '0dc61aa7435ce57546e93eae81fb3d124a4ef6a7'
WORK_RELEASE_ALIGNMENT = 'deploy/enterprise/work-release-alignment-source-candidate-20261104.json'
WORK_RELEASE_ALIGNMENT_ARCHIVE = 'release/work-release-alignment-20261104/'
WORK_RELEASE_BASE_COMMIT = 'ef4ce7a177a92750ceb780eb8725814328bade3d'
WORK_RELEASE_ALIGNMENT_PATHS = frozenset({
    'backend/app/gateway/services.py',
    'backend/packages/harness/deerflow/agents/lead_agent/agent.py',
})
WORK_PARENT_MANIFESTS = (LOCK, CANDIDATE, INCIDENT_DELTA, WORK_ROUTING_DELTA, WORK_TASK_AGENT_DELTA, WORK_TASK_AGENT_FIX_DELTA, WORK_TASK_AGENT_MODEL_FIX_DELTA)
WORK_AUTHORITY_SANITIZER_DELTA = 'deploy/enterprise/work-authority-sanitizer-source-candidate-20261104.json'
WORK_AUTHORITY_SANITIZER_BASE_COMMIT = 'a651e31ee39cb88d123989b083d0f490bd96162f'
WORK_AUTHORITY_SANITIZER_PATH = 'backend/packages/harness/deerflow/agents/middlewares/input_sanitization_middleware.py'
WORK_AUTHORITY_SANITIZER_SHA256 = '6db8263377b00c07151cb8f8da84b220fdc034aa0e828c0438c3de3daf8b58e3'


def candidate_overrides(lock, manifest, lock_digest):
    """Validate an explicit undeployed delta without rewriting accepted evidence."""
    assert manifest['schema'] == 'production-source-candidate.v1'
    assert manifest['status'] == 'NOT_DEPLOYED'
    assert manifest['base_lock_sha256'] == lock_digest, 'CANDIDATE_BASE_LOCK_MISMATCH'
    assert len(manifest['base_commit']) == 40 and all(c in '0123456789abcdef' for c in manifest['base_commit'])
    baseline = lock['source_sets']['gateway']
    normalizations = lock.get('format_normalizations', {})
    result = {}
    for name, change in manifest['changes'].items():
        assert name.startswith(('backend/app/', 'backend/packages/harness/')) and name.endswith('.py')
        assert '\\' not in name and PurePosixPath(name).as_posix() == name and '..' not in PurePosixPath(name).parts
        previous = normalizations.get(name, {}).get('formatted_sha256', baseline.get(name))
        assert change['base_sha256'] == previous, (name, 'CANDIDATE_PARENT_MISMATCH')
        digest = change['candidate_sha256']
        assert len(digest) == 64 and all(c in '0123456789abcdef' for c in digest)
        result[name] = digest
    return result


def incident_overrides(lock, prior, manifest, lock_digest, prior_digest):
    """Layer an explicit, undeployed repair over the preserved candidate seal."""
    assert manifest['schema'] == 'incident-source-candidate.v1'
    assert manifest['status'] == 'NOT_DEPLOYED'
    assert manifest['base_lock_sha256'] == lock_digest, 'INCIDENT_BASE_LOCK_MISMATCH'
    assert manifest['parent_candidate_sha256'] == prior_digest, 'INCIDENT_PARENT_CHANGED'
    assert len(manifest['base_commit']) == 40 and all(c in '0123456789abcdef' for c in manifest['base_commit'])
    assert set(manifest['changes']) == {'gateway', 'frontend'}, 'INCIDENT_COMPONENT_SCOPE'
    result = {'gateway': {}, 'frontend': {}}
    for component, changes in manifest['changes'].items():
        for name, change in changes.items():
            if component == 'gateway':
                assert name.startswith(('backend/app/', 'backend/packages/harness/')) and name.endswith('.py')
                expected = prior.get(name, lock.get('format_normalizations', {}).get(name, {}).get('formatted_sha256', lock['source_sets'][component].get(name)))
            else:
                assert (name.startswith(('frontend/src/', 'frontend/tests/unit/', 'frontend/tests/e2e/')) and name.endswith(('.ts', '.tsx'))) or name in ('frontend/README.md', 'frontend/AGENTS.md')
                expected = lock.get('line_ending_normalizations', {}).get(name, {}).get('lf_sha256', lock.get('qualification_test_overrides', {}).get(name, {}).get('current_sha256', lock['source_sets'][component].get(name)))
            assert '\\' not in name and PurePosixPath(name).as_posix() == name and '..' not in PurePosixPath(name).parts
            assert change['base_sha256'] == expected, (name, 'INCIDENT_PARENT_MISMATCH')
            digest = change['candidate_sha256']
            assert len(digest) == 64 and all(c in '0123456789abcdef' for c in digest)
            result[component][name] = digest
    return result



def released_overrides(lock, prior, incident, manifest, read):
    """Validate the separately sealed November 1 release without rewriting history."""
    assert manifest['schema'] == 'released-source-delta.v1'
    assert manifest['work_datalake_deferred'] is True and manifest['database_migration'] is False
    assert set(manifest['parent_sha256']) == {Path(LOCK).name, Path(CANDIDATE).name, Path(INCIDENT_DELTA).name}
    for name, digest in manifest['parent_sha256'].items():
        assert hashlib.sha256(read('deploy/enterprise/' + name).replace(b'\r\n', b'\n')).hexdigest() == digest, 'RELEASE_PARENT_CHANGED'
    source_path = 'release/nov01-released-fixes-source-20261102/SOURCE.json'
    assert manifest['source_manifest'] == source_path
    assert hashlib.sha256(read(source_path).replace(b'\r\n', b'\n')).hexdigest() == manifest['source_manifest_sha256'], 'RELEASE_SOURCE_SEAL_CHANGED'
    sealed = json.loads(read(source_path))['sources']
    runtime = {name: row for name, row in sealed.items() if name.startswith(('backend/', 'frontend/src/'))}
    formatting = json.loads(read(source_path)).get('ci_formatting', {})
    runtime.update(formatting)
    declared = {name: row for changes in manifest['changes'].values() for name, row in changes.items() if not name.startswith('frontend/tests/')}
    assert set(declared) == set(runtime), 'RELEASE_DELTA_SCOPE_CHANGED'
    for name, row in declared.items():
        assert row['released_sha256'] == runtime[name]['source_sha256'] and row['base_sha256'] == runtime[name]['base_sha256'], 'RELEASE_DELTA_SOURCE_CHANGED'
    assert set(manifest['changes']) == {'gateway', 'frontend'}
    result = {'gateway': {}, 'frontend': {}}
    for component, changes in manifest['changes'].items():
        for name, change in changes.items():
            assert '\\' not in name and '..' not in PurePosixPath(name).parts
            if component == 'gateway':
                assert name.startswith(('backend/app/', 'backend/packages/harness/')) and name.endswith('.py')
                previous = prior.get(name, lock.get('format_normalizations', {}).get(name, {}).get('formatted_sha256', lock['source_sets'][component].get(name)))
            else:
                assert name.startswith(('frontend/src/', 'frontend/tests/')) and name.endswith(('.ts', '.tsx'))
                if name.startswith('frontend/tests/'):
                    assert change.get('kind') == 'regression-test' and name in lock['source_sets']['frontend'], 'RELEASE_TEST_SCOPE_CHANGED'
                previous = lock.get('line_ending_normalizations', {}).get(name, {}).get('lf_sha256', lock.get('qualification_test_overrides', {}).get(name, {}).get('current_sha256', lock['source_sets'][component].get(name)))
            previous = incident[component].get(name, previous)
            assert change['base_sha256'] == previous, (name, 'RELEASE_PARENT_SOURCE_CHANGED')
            digest = change['released_sha256']
            assert len(digest) == 64 and all(c in '0123456789abcdef' for c in digest)
            result[component][name] = digest
    return result


def work_routing_overrides(lock, prior, manifest, lock_digest, prior_digest):
    """Seal the routing candidate without replacing either historical delta."""
    required = {'schema', 'status', 'base_commit', 'base_lock_sha256', 'parent_candidate_sha256', 'changes'}
    assert isinstance(manifest, dict) and required <= set(manifest) <= required | {'scope'}, 'WORK_ROUTING_SCHEMA'
    assert manifest['schema'] == 'work-routing-source-candidate.v1', 'WORK_ROUTING_SCHEMA'
    assert manifest['status'] == 'NOT_DEPLOYED', 'WORK_ROUTING_NOT_DEPLOYED_REQUIRED'
    assert manifest['base_commit'] == WORK_ROUTING_BASE_COMMIT, 'WORK_ROUTING_BASE_COMMIT_MISMATCH'
    assert manifest['base_lock_sha256'] == lock_digest, 'WORK_ROUTING_BASE_LOCK_MISMATCH'
    assert manifest['parent_candidate_sha256'] == prior_digest, 'WORK_ROUTING_PARENT_CHANGED'
    assert 'scope' not in manifest or isinstance(manifest['scope'], str), 'WORK_ROUTING_SCHEMA'
    changes = manifest['changes']
    assert isinstance(changes, dict) and set(changes) == {'gateway', 'frontend'}, 'WORK_ROUTING_COMPONENT_SCOPE'
    assert isinstance(changes['gateway'], dict) and isinstance(changes['frontend'], dict) and not changes['frontend'], 'WORK_ROUTING_COMPONENT_SCOPE'
    result = {}
    for name, change in changes['gateway'].items():
        assert isinstance(name, str) and name.startswith(('backend/app/', 'backend/packages/harness/')) and name.endswith('.py'), 'WORK_ROUTING_PATH_SCOPE'
        assert '\\' not in name and PurePosixPath(name).as_posix() == name and '..' not in PurePosixPath(name).parts, 'WORK_ROUTING_PATH_SCOPE'
        assert isinstance(change, dict) and set(change) == {'base_sha256', 'candidate_sha256'}, 'WORK_ROUTING_CHANGE_SCHEMA'
        expected = prior.get(name, lock.get('format_normalizations', {}).get(name, {}).get('formatted_sha256', lock['source_sets']['gateway'].get(name)))
        assert change['base_sha256'] == expected, (name, 'WORK_ROUTING_PARENT_MISMATCH')
        digest = change['candidate_sha256']
        assert isinstance(digest, str) and len(digest) == 64 and all(c in '0123456789abcdef' for c in digest), 'WORK_ROUTING_HASH_FORMAT'
        result[name] = digest
    return result


def work_task_agent_overrides(lock, prior, manifest, lock_digest, prior_digest):
    """Append finite-task execution evidence; keep the prior routing seal intact."""
    required = {'schema', 'status', 'base_commit', 'base_lock_sha256', 'parent_candidate_sha256', 'changes'}
    assert isinstance(manifest, dict) and required <= set(manifest) <= required | {'scope'}, 'WORK_TASK_AGENT_SCHEMA'
    assert manifest['schema'] == 'work-task-agent-source-candidate.v1', 'WORK_TASK_AGENT_SCHEMA'
    assert manifest['status'] == 'NOT_DEPLOYED', 'WORK_TASK_AGENT_NOT_DEPLOYED_REQUIRED'
    assert manifest['base_commit'] == WORK_ROUTING_BASE_COMMIT, 'WORK_TASK_AGENT_BASE_COMMIT_MISMATCH'
    assert manifest['base_lock_sha256'] == lock_digest, 'WORK_TASK_AGENT_BASE_LOCK_MISMATCH'
    assert manifest['parent_candidate_sha256'] == prior_digest, 'WORK_TASK_AGENT_PARENT_CHANGED'
    assert 'scope' not in manifest or isinstance(manifest['scope'], str), 'WORK_TASK_AGENT_SCHEMA'
    changes = manifest['changes']
    assert isinstance(changes, dict) and set(changes) == {'gateway', 'frontend'}, 'WORK_TASK_AGENT_COMPONENT_SCOPE'
    assert isinstance(changes['gateway'], dict) and isinstance(changes['frontend'], dict) and not changes['frontend'], 'WORK_TASK_AGENT_COMPONENT_SCOPE'
    result = {}
    for name, change in changes['gateway'].items():
        assert isinstance(name, str) and name.startswith(('backend/app/', 'backend/packages/harness/')) and name.endswith('.py'), 'WORK_TASK_AGENT_PATH_SCOPE'
        assert '\\' not in name and PurePosixPath(name).as_posix() == name and '..' not in PurePosixPath(name).parts, 'WORK_TASK_AGENT_PATH_SCOPE'
        assert isinstance(change, dict) and set(change) == {'base_sha256', 'candidate_sha256'}, 'WORK_TASK_AGENT_CHANGE_SCHEMA'
        expected = prior.get(name, lock.get('format_normalizations', {}).get(name, {}).get('formatted_sha256', lock['source_sets']['gateway'].get(name)))
        assert change['base_sha256'] == expected, (name, 'WORK_TASK_AGENT_PARENT_MISMATCH')
        digest = change['candidate_sha256']
        assert isinstance(digest, str) and len(digest) == 64 and all(c in '0123456789abcdef' for c in digest), 'WORK_TASK_AGENT_HASH_FORMAT'
        result[name] = digest
    return result


def work_task_agent_fix_overrides(lock, prior, manifest, lock_digest, prior_digest):
    """Seal the bounded R3 repair over R2 without revising any prior evidence."""
    required = {'schema', 'status', 'base_commit', 'base_lock_sha256', 'parent_candidate_sha256', 'changes'}
    assert isinstance(manifest, dict) and required <= set(manifest) <= required | {'scope'}, 'WORK_TASK_AGENT_FIX_SCHEMA'
    assert manifest['schema'] == 'work-task-agent-fix-source-candidate.v1', 'WORK_TASK_AGENT_FIX_SCHEMA'
    assert manifest['status'] == 'NOT_DEPLOYED', 'WORK_TASK_AGENT_FIX_NOT_DEPLOYED_REQUIRED'
    assert manifest['base_commit'] == WORK_ROUTING_BASE_COMMIT, 'WORK_TASK_AGENT_FIX_BASE_COMMIT_MISMATCH'
    assert manifest['base_lock_sha256'] == lock_digest, 'WORK_TASK_AGENT_FIX_BASE_LOCK_MISMATCH'
    assert manifest['parent_candidate_sha256'] == prior_digest, 'WORK_TASK_AGENT_FIX_PARENT_CHANGED'
    assert 'scope' not in manifest or isinstance(manifest['scope'], str), 'WORK_TASK_AGENT_FIX_SCHEMA'
    changes = manifest['changes']
    assert isinstance(changes, dict) and set(changes) == {'gateway', 'frontend'}, 'WORK_TASK_AGENT_FIX_COMPONENT_SCOPE'
    assert isinstance(changes['gateway'], dict) and isinstance(changes['frontend'], dict) and not changes['frontend'], 'WORK_TASK_AGENT_FIX_COMPONENT_SCOPE'
    result = {}
    for name, change in changes['gateway'].items():
        assert isinstance(name, str) and name.startswith(('backend/app/', 'backend/packages/harness/')) and name.endswith('.py'), 'WORK_TASK_AGENT_FIX_PATH_SCOPE'
        assert '\\' not in name and PurePosixPath(name).as_posix() == name and '..' not in PurePosixPath(name).parts, 'WORK_TASK_AGENT_FIX_PATH_SCOPE'
        assert isinstance(change, dict) and set(change) == {'base_sha256', 'candidate_sha256'}, 'WORK_TASK_AGENT_FIX_CHANGE_SCHEMA'
        expected = prior.get(name, lock.get('format_normalizations', {}).get(name, {}).get('formatted_sha256', lock['source_sets']['gateway'].get(name)))
        assert change['base_sha256'] == expected, (name, 'WORK_TASK_AGENT_FIX_PARENT_MISMATCH')
        digest = change['candidate_sha256']
        assert isinstance(digest, str) and len(digest) == 64 and all(c in '0123456789abcdef' for c in digest), 'WORK_TASK_AGENT_FIX_HASH_FORMAT'
        result[name] = digest
    return result


def work_task_agent_model_fix_overrides(lock, prior, manifest, lock_digest, prior_digest):
    """Permit only the reviewed planner file over the preserved R3 repair seal."""
    required = {'schema', 'status', 'base_commit', 'base_lock_sha256', 'parent_candidate_sha256', 'changes'}
    assert isinstance(manifest, dict) and required <= set(manifest) <= required | {'scope'}, 'WORK_TASK_AGENT_MODEL_FIX_SCHEMA'
    assert manifest['schema'] == 'work-task-agent-model-fix-source-candidate.v1', 'WORK_TASK_AGENT_MODEL_FIX_SCHEMA'
    assert manifest['status'] == 'NOT_DEPLOYED', 'WORK_TASK_AGENT_MODEL_FIX_NOT_DEPLOYED_REQUIRED'
    assert manifest['base_commit'] == WORK_ROUTING_BASE_COMMIT, 'WORK_TASK_AGENT_MODEL_FIX_BASE_COMMIT_MISMATCH'
    assert manifest['base_lock_sha256'] == lock_digest, 'WORK_TASK_AGENT_MODEL_FIX_BASE_LOCK_MISMATCH'
    assert manifest['parent_candidate_sha256'] == prior_digest, 'WORK_TASK_AGENT_MODEL_FIX_PARENT_CHANGED'
    assert 'scope' not in manifest or isinstance(manifest['scope'], str), 'WORK_TASK_AGENT_MODEL_FIX_SCHEMA'
    changes = manifest['changes']
    assert isinstance(changes, dict) and set(changes) == {'gateway', 'frontend'}, 'WORK_TASK_AGENT_MODEL_FIX_COMPONENT_SCOPE'
    assert isinstance(changes['gateway'], dict) and isinstance(changes['frontend'], dict) and not changes['frontend'], 'WORK_TASK_AGENT_MODEL_FIX_COMPONENT_SCOPE'
    name = 'backend/app/integrations/work/agent.py'
    assert set(changes['gateway']) == {name}, 'WORK_TASK_AGENT_MODEL_FIX_PATH_SCOPE'
    change = changes['gateway'][name]
    assert isinstance(change, dict) and set(change) == {'base_sha256', 'candidate_sha256'}, 'WORK_TASK_AGENT_MODEL_FIX_CHANGE_SCHEMA'
    expected = prior.get(name, lock.get('format_normalizations', {}).get(name, {}).get('formatted_sha256', lock['source_sets']['gateway'].get(name)))
    assert change['base_sha256'] == expected, (name, 'WORK_TASK_AGENT_MODEL_FIX_PARENT_MISMATCH')
    digest = change['candidate_sha256']
    assert isinstance(digest, str) and len(digest) == 64 and all(c in '0123456789abcdef' for c in digest), 'WORK_TASK_AGENT_MODEL_FIX_HASH_FORMAT'
    return {name: digest}


def qualify_work_release_alignment(read):
    """Share the strict merge qualification with the historical audit readers."""
    return _nov02.followup.alignment.qualify(read)


def work_release_alignment_overrides(lock, production, work, manifest):
    """Bind both independently verified lineages to the exact merged source."""
    result = {}
    for path, row in manifest['changes'].items():
        production_parent = production.get(path, lock.get('format_normalizations', {}).get(path, {}).get('formatted_sha256', lock['source_sets']['gateway'].get(path)))
        assert row['base_sha256'] == production_parent, (path, 'WORK_RELEASE_ALIGNMENT_PRODUCTION_PARENT')
        assert row['work_candidate_sha256'] == work.get(path), (path, 'WORK_RELEASE_ALIGNMENT_WORK_PARENT')
        result[path] = row['candidate_sha256']
    return result


def work_authority_sanitizer_overrides(lock, prior, manifest, lock_digest, parent_digest):
    """Qualify exactly the Gateway authority-tag guard, leaving Runner unchanged."""
    required = {'schema', 'status', 'base_commit', 'base_lock_sha256', 'parent_candidate_sha256', 'changes', 'scope'}
    assert isinstance(manifest, dict) and set(manifest) == required, 'WORK_AUTHORITY_SANITIZER_SCHEMA'
    assert manifest['schema'] == 'work-authority-sanitizer-source-candidate.v1', 'WORK_AUTHORITY_SANITIZER_SCHEMA'
    assert manifest['status'] == 'NOT_DEPLOYED', 'WORK_AUTHORITY_SANITIZER_NOT_DEPLOYED_REQUIRED'
    assert manifest['base_commit'] == WORK_AUTHORITY_SANITIZER_BASE_COMMIT, 'WORK_AUTHORITY_SANITIZER_BASE_COMMIT'
    assert manifest['base_lock_sha256'] == lock_digest, 'WORK_AUTHORITY_SANITIZER_BASE_LOCK_CHANGED'
    assert manifest['parent_candidate_sha256'] == parent_digest, 'WORK_AUTHORITY_SANITIZER_PARENT_CHANGED'
    assert isinstance(manifest['scope'], str), 'WORK_AUTHORITY_SANITIZER_SCHEMA'
    changes = manifest['changes']
    assert isinstance(changes, dict) and set(changes) == {'gateway', 'frontend'}, 'WORK_AUTHORITY_SANITIZER_COMPONENT_SCOPE'
    assert isinstance(changes['frontend'], dict) and not changes['frontend'], 'WORK_AUTHORITY_SANITIZER_COMPONENT_SCOPE'
    assert isinstance(changes['gateway'], dict) and set(changes['gateway']) == {WORK_AUTHORITY_SANITIZER_PATH}, 'WORK_AUTHORITY_SANITIZER_PATH_SCOPE'
    row = changes['gateway'][WORK_AUTHORITY_SANITIZER_PATH]
    assert isinstance(row, dict) and set(row) == {'base_sha256', 'candidate_sha256'}, 'WORK_AUTHORITY_SANITIZER_CHANGE_SCHEMA'
    expected = prior.get(WORK_AUTHORITY_SANITIZER_PATH, lock.get('format_normalizations', {}).get(WORK_AUTHORITY_SANITIZER_PATH, {}).get('formatted_sha256', lock['source_sets']['gateway'].get(WORK_AUTHORITY_SANITIZER_PATH)))
    assert row['base_sha256'] == expected and expected is not None, 'WORK_AUTHORITY_SANITIZER_BASE_SOURCE_CHANGED'
    digest = row['candidate_sha256']
    assert isinstance(digest, str) and len(digest) == 64 and all(c in '0123456789abcdef' for c in digest), 'WORK_AUTHORITY_SANITIZER_HASH_FORMAT'
    return {WORK_AUTHORITY_SANITIZER_PATH: digest}


def syntax_fingerprint(raw, *, import_order=False):
    """Ignore layout; optionally sort only consecutive module-level imports."""
    tree = ast.parse(raw)
    if import_order:
        start = 0
        while start < len(tree.body):
            if not isinstance(tree.body[start], (ast.Import, ast.ImportFrom)):
                start += 1
                continue
            end = start + 1
            while end < len(tree.body) and isinstance(tree.body[end], (ast.Import, ast.ImportFrom)):
                end += 1
            tree.body[start:end] = sorted(tree.body[start:end], key=ast.dump)
            start = end
    return ast.dump(tree, include_attributes=False)


def qualification_source(read, lock, name):
    """Preserve sealed test inputs while auditing later CI-only corrections."""
    raw = read(name)
    override = lock.get('qualification_test_overrides', {}).get(name)
    if override is None:
        return raw
    allowed_test = (name.startswith('frontend/tests/e2e/') and name.endswith('.spec.ts')) or (
        name.startswith('frontend/tests/unit/') and name.endswith(('.test.ts', '.test.tsx')))
    assert allowed_test, 'TEST_OVERRIDE_OUTSIDE_TESTS'
    test_prefix = lock.get('qualification_test_baseline_prefix', TEST_BASELINE)
    assert test_prefix in (TEST_BASELINE, 'release/combined-features-20261028-r1/qualification/source/'), 'TEST_BASELINE_PREFIX'
    assert override['baseline_path'] == test_prefix + name, 'TEST_BASELINE_PATH_MISMATCH'
    baseline = read(override['baseline_path'])
    if hashlib.sha256(baseline).hexdigest() != lock['source_sets']['frontend'][name]:
        baseline = baseline.replace(b'\r\n', b'\n')
    assert hashlib.sha256(baseline).hexdigest() == lock['source_sets']['frontend'][name], 'SEALED_TEST_BASELINE_CHANGED'
    assert hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest() == override['current_sha256'], 'QUALIFICATION_TEST_CHANGED'
    return baseline


def verify_runtime_patch(read, lock):
    """Bind archived runtime bytes to the independently collected ConfigMap hash."""
    patch = lock.get('runtime_patch')
    if not patch:
        return None
    for path, expected in patch['files'].items():
        assert hashlib.sha256(read(path).replace(b'\r\n', b'\n')).hexdigest() == expected, (path, 'RUNTIME_ARCHIVE_CHANGED')
    data = {name: read(patch['archive_prefix'] + name).decode('utf-8').replace('\r\n', '\n')
            for name in patch['payload_files']}
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
    assert digest(data) == patch['payload_sha256'], 'RUNTIME_PAYLOAD_CHANGED'
    assert digest({'data': data, 'binaryData': {}}) == patch['configmap']['content_sha256'], 'RUNTIME_CONFIGMAP_MISMATCH'
    for path, expected in patch['qualified_source_files'].items():
        assert hashlib.sha256(read(path).replace(b'\r\n', b'\n')).hexdigest() == expected, (path, 'MEMBER_UI_SOURCE_CHANGED')
    record = lock['current_observation']
    raw = read(record['path']).replace(b'\r\n', b'\n')
    assert hashlib.sha256(raw).hexdigest() == record['sha256'], 'CURRENT_OBSERVATION_CHANGED'
    observation = json.loads(raw)['observation']
    assert observation['status'] == 'CURRENT_METADATA_COLLECTED' and observation['workload_specs_stable'], 'UNSTABLE_OBSERVATION'
    observed_cm = next(c for c in observation['configmaps'] if c['identity']['name'] == patch['configmap']['identity']['name'])
    assert observed_cm == patch['configmap'], 'OBSERVED_CONFIGMAP_CHANGED'
    workloads = {w['identity']['name']: w for w in observation['workloads']}
    for name, expected in record['bindings'].items():
        actual = workloads[name]
        assert all(actual['identity'][key] == expected[key] for key in ('uid', 'generation')), (name, 'WORKLOAD_IDENTITY_MISMATCH')
        assert actual['spec_sha256'] == expected['spec_sha256'], (name, 'WORKLOAD_SPEC_MISMATCH')
        images = [c['image'] for c in actual['containers']]
        assert images == expected['images'], (name, 'WORKLOAD_IMAGE_MISMATCH')
        if name in lock['images']:
            assert lock['images'][name] in images, (name, 'RELEASE_IMAGE_MISMATCH')
    return {'component': 'weknora-member-ui-overlay', 'files': len(patch['files']),
            'configmap_hash': 'PASS', 'source_files': len(patch['qualified_source_files']),
            'observation_bindings': 'PASS', 'full_base_image_source_mapping': 'NOT_PROVEN'}


def verify(ref=None, lock_path=LOCK):
    archived = None
    if ref:
        selected_lock = json.loads(subprocess.check_output(['git', 'show', ref + ':' + lock_path], cwd=ROOT))
        extra = []
        if selected_lock.get('runtime_patch'):
            extra = [selected_lock['runtime_patch']['archive_prefix'],
                     *selected_lock['runtime_patch']['qualified_source_files'], selected_lock['current_observation']['path']]
        extra += [row['baseline_path'] for row in selected_lock.get('qualification_test_overrides', {}).values()]
        extra += [row['baseline_path'] for row in selected_lock.get('line_ending_normalizations', {}).values()]
        for manifest_path in (CANDIDATE, INCIDENT_DELTA, RELEASED_DELTA, "release/nov01-released-fixes-source-20261102/SOURCE.json", WORK_ROUTING_DELTA, WORK_TASK_AGENT_DELTA, WORK_TASK_AGENT_FIX_DELTA, WORK_TASK_AGENT_MODEL_FIX_DELTA, WORK_AUTHORITY_SANITIZER_DELTA):
            if subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', ref, manifest_path], cwd=ROOT).strip():
                extra.append(manifest_path)
        if subprocess.check_output(['git', 'ls-tree', '--name-only', ref, WORK_RELEASE_ALIGNMENT], cwd=ROOT).strip():
            extra.append(WORK_RELEASE_ALIGNMENT)
        if subprocess.check_output(['git', 'ls-tree', '--name-only', ref, WORK_RELEASE_ALIGNMENT_ARCHIVE], cwd=ROOT).strip():
            extra.append(WORK_RELEASE_ALIGNMENT_ARCHIVE)
        if subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', ref, NOV02_DELTA], cwd=ROOT).strip():
            extra.extend((NOV02_DELTA, NOV02_ARCHIVE, "backend/tests"))
        if subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', ref, _nov02.CI_MANIFEST], cwd=ROOT).strip():
            extra.append(_nov02.CI_ARCHIVE)
        if subprocess.check_output(['git', 'ls-tree', '--name-only', ref, _nov02.followup.MANIFEST], cwd=ROOT).strip():
            extra.append(_nov02.followup.ARCHIVE)
        if subprocess.check_output(['git', 'ls-tree', '--name-only', ref, _nov02.followup.navigation.MANIFEST], cwd=ROOT).strip():
            extra.append(_nov02.followup.navigation.ARCHIVE)
        raw = subprocess.check_output(['git', 'archive', '--format=tar', ref,
            'backend/app', 'backend/packages/harness', 'frontend', RUNNER, FORMAT_BASELINE, lock_path, *extra], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
            archived = {m.name: tar.extractfile(m).read() for m in tar if m.isfile()}
    current_read = (lambda name: archived[name]) if archived is not None else (lambda name: (ROOT / name).read_bytes())
    alignment_manifest = qualify_work_release_alignment(current_read)
    # Existing qualifiers must verify the actual latest production source, not
    # old R4 bytes. Only these prequalified overlaps project the sealed ef4
    # parents; current merged bytes are checked again in the final inventory.
    read = _nov02.followup.alignment.historical_read(current_read)
    navigation = _nov02.followup.navigation.qualify(read)
    followup = _nov02.followup.qualify(read)
    read = _nov02.followup.historical_read(read)
    lock_raw = read(lock_path)
    lock = json.loads(lock_raw)
    has_candidate = CANDIDATE in archived if archived is not None else (ROOT / CANDIDATE).is_file()
    manifest = json.loads(read(CANDIDATE)) if has_candidate else None
    overrides = candidate_overrides(lock, manifest, hashlib.sha256(lock_raw.replace(b'\r\n', b'\n')).hexdigest()) if manifest else {}
    has_incident = INCIDENT_DELTA in archived if archived is not None else (ROOT / INCIDENT_DELTA).is_file()
    delta = json.loads(read(INCIDENT_DELTA)) if has_incident else None
    incident = incident_overrides(lock, overrides, delta, hashlib.sha256(lock_raw.replace(b'\r\n', b'\n')).hexdigest(), hashlib.sha256(read(CANDIDATE).replace(b'\r\n', b'\n')).hexdigest()) if delta else {'gateway': {}, 'frontend': {}}
    has_release = RELEASED_DELTA in archived if archived is not None else (ROOT / RELEASED_DELTA).is_file()
    release_manifest = json.loads(read(RELEASED_DELTA)) if has_release else None
    released = released_overrides(lock, overrides, incident, release_manifest, read) if release_manifest else {'gateway': {}, 'frontend': {}}
    has_nov02 = NOV02_DELTA in archived if archived is not None else (ROOT / NOV02_DELTA).is_file()
    nov02 = nov02_overrides(lock, overrides, incident, released, read) if has_nov02 else {'gateway': {}, 'frontend': {}}
    has_routing = WORK_ROUTING_DELTA in archived if archived is not None else (ROOT / WORK_ROUTING_DELTA).is_file()
    routing_manifest = json.loads(read(WORK_ROUTING_DELTA)) if has_routing else None
    if has_routing:
        assert manifest is not None and delta is not None, 'WORK_ROUTING_PARENT_REQUIRED'
        routing = work_routing_overrides(lock, {**overrides, **incident['gateway']}, routing_manifest,
            hashlib.sha256(lock_raw.replace(b'\r\n', b'\n')).hexdigest(), hashlib.sha256(read(INCIDENT_DELTA).replace(b'\r\n', b'\n')).hexdigest())
    else:
        routing = {}
    has_task_agent = WORK_TASK_AGENT_DELTA in archived if archived is not None else (ROOT / WORK_TASK_AGENT_DELTA).is_file()
    task_agent_manifest = json.loads(read(WORK_TASK_AGENT_DELTA)) if has_task_agent else None
    if has_task_agent:
        assert routing_manifest is not None, 'WORK_TASK_AGENT_PARENT_REQUIRED'
        task_agent = work_task_agent_overrides(lock, {**overrides, **incident['gateway'], **routing}, task_agent_manifest,
            hashlib.sha256(lock_raw.replace(b'\r\n', b'\n')).hexdigest(), hashlib.sha256(read(WORK_ROUTING_DELTA).replace(b'\r\n', b'\n')).hexdigest())
    else:
        task_agent = {}
    has_task_agent_fix = WORK_TASK_AGENT_FIX_DELTA in archived if archived is not None else (ROOT / WORK_TASK_AGENT_FIX_DELTA).is_file()
    task_agent_fix_manifest = json.loads(read(WORK_TASK_AGENT_FIX_DELTA)) if has_task_agent_fix else None
    if has_task_agent_fix:
        assert task_agent_manifest is not None, 'WORK_TASK_AGENT_FIX_PARENT_REQUIRED'
        task_agent_fix = work_task_agent_fix_overrides(lock, {**overrides, **incident['gateway'], **routing, **task_agent}, task_agent_fix_manifest,
            hashlib.sha256(lock_raw.replace(b'\r\n', b'\n')).hexdigest(), hashlib.sha256(read(WORK_TASK_AGENT_DELTA).replace(b'\r\n', b'\n')).hexdigest())
    else:
        task_agent_fix = {}
    has_model_fix = WORK_TASK_AGENT_MODEL_FIX_DELTA in archived if archived is not None else (ROOT / WORK_TASK_AGENT_MODEL_FIX_DELTA).is_file()
    model_fix_manifest = json.loads(read(WORK_TASK_AGENT_MODEL_FIX_DELTA)) if has_model_fix else None
    if has_model_fix:
        assert task_agent_fix_manifest is not None, 'WORK_TASK_AGENT_MODEL_FIX_PARENT_REQUIRED'
        model_fix = work_task_agent_model_fix_overrides(lock, {**overrides, **incident['gateway'], **routing, **task_agent, **task_agent_fix}, model_fix_manifest,
            hashlib.sha256(lock_raw.replace(b'\r\n', b'\n')).hexdigest(), hashlib.sha256(read(WORK_TASK_AGENT_FIX_DELTA).replace(b'\r\n', b'\n')).hexdigest())
    else:
        model_fix = {}
    work = {**overrides, **incident['gateway'], **routing, **task_agent, **task_agent_fix, **model_fix}
    production = {**overrides, **incident['gateway'], **released['gateway'], **nov02['gateway']}
    aligned = work_release_alignment_overrides(lock, production, work, alignment_manifest) if alignment_manifest else {}
    has_sanitizer = WORK_AUTHORITY_SANITIZER_DELTA in archived if archived is not None else (ROOT / WORK_AUTHORITY_SANITIZER_DELTA).is_file()
    sanitizer_manifest = None
    sanitizer = {}
    if has_sanitizer:
        assert alignment_manifest is not None, 'WORK_AUTHORITY_SANITIZER_PARENT_REQUIRED'
        raw = read(WORK_AUTHORITY_SANITIZER_DELTA).replace(b'\r\n', b'\n')
        assert hashlib.sha256(raw).hexdigest() == WORK_AUTHORITY_SANITIZER_SHA256, 'WORK_AUTHORITY_SANITIZER_SEAL_CHANGED'
        sanitizer_manifest = json.loads(raw)
        sanitizer = work_authority_sanitizer_overrides(lock, {**production, **routing, **task_agent, **task_agent_fix, **model_fix, **aligned}, sanitizer_manifest,
            hashlib.sha256(lock_raw.replace(b'\r\n', b'\n')).hexdigest(), hashlib.sha256(current_read(WORK_RELEASE_ALIGNMENT).replace(b'\r\n', b'\n')).hexdigest())
    normalizations = lock.get('format_normalizations', {})
    assert set(lock.get('qualification_test_overrides', {})) <= set(lock['source_sets']['frontend']), 'UNKNOWN_QUALIFICATION_TEST'
    results = []
    for component, expected in lock['source_sets'].items():
        component_overrides = {**(overrides if component == 'gateway' else {}), **incident.get(component, {}), **released.get(component, {}), **nov02.get(component, {}), **(routing if component == 'gateway' else {}), **(task_agent if component == 'gateway' else {}), **(task_agent_fix if component == 'gateway' else {}), **(model_fix if component == 'gateway' else {}), **(aligned if component == 'gateway' else {}), **(sanitizer if component == 'gateway' else {})}
        expected = {**expected, **component_overrides}
        prefix = RUNNER if component == 'ppt-master-runner' else ''
        if component != 'frontend':
            roots = [prefix + 'backend/app/', prefix + 'backend/packages/harness/']
            if archived is not None:
                actual = {(n[len(prefix):] if n.startswith(prefix) else n) for n in archived
                          if n.endswith('.py') and any(n.startswith(r) for r in roots)}
            else:
                actual = {p.relative_to(ROOT / prefix).as_posix() for r in roots
                          for p in (ROOT / r).rglob('*.py') if '__pycache__' not in p.parts}
            if component == 'gateway' and followup:
                actual -= {path for path, row in followup['changes'].items()
                           if row['base_sha256'] is None and row['kind'] == 'runtime-source'
                           and path.startswith(('backend/app/', 'backend/packages/harness/'))}
            if actual != set(expected):
                raise AssertionError((component, 'SOURCE_SET_MISMATCH', sorted(actual ^ set(expected))))
        for name, digest in expected.items():
            if name in component_overrides:
                raw = (current_read(name) if name in aligned or name in sanitizer else read(name)).replace(b'\r\n', b'\n')
                assert hashlib.sha256(raw).hexdigest() == digest, (name, 'CANDIDATE_HASH_MISMATCH')
                if name.endswith('.py'):
                    ast.parse(raw, filename=name)
                continue
            raw = qualification_source(read, lock, name) if component == 'frontend' else read(prefix + name)
            newline = lock.get('line_ending_normalizations', {}).get(name) if component == 'frontend' else None
            if newline:
                # Preserve the original CRLF build input while verifying Git's LF
                # representation byte-for-byte after only newline conversion.
                original = read(newline['baseline_path'])
                original.decode('utf-8')
                assert hashlib.sha256(original).hexdigest() == digest, (name, 'NEWLINE_BASELINE_CHANGED')
                assert raw.replace(b'\r\n', b'\n') == original.replace(b'\r\n', b'\n'), (name, 'NEWLINE_CONTENT_CHANGED')
                assert hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest() == newline['lf_sha256'], (name, 'NEWLINE_HASH_CHANGED')
                raw = original
            normalization = normalizations.get(name) if component == 'gateway' else None
            if normalization:
                original = read(FORMAT_BASELINE + name).replace(b'\r\n', b'\n')
                raw = raw.replace(b'\r\n', b'\n')
                assert hashlib.sha256(original).hexdigest() == digest, (name, 'PRODUCTION_BASELINE_MISMATCH')
                assert hashlib.sha256(raw).hexdigest() == normalization['formatted_sha256'], (name, 'FORMATTED_HASH_MISMATCH')
                import_order = normalization['kind'] == 'layout_and_import_order'
                assert syntax_fingerprint(original, import_order=import_order) == syntax_fingerprint(raw, import_order=import_order), (name, 'LOGIC_CHANGED')
                continue
            if hashlib.sha256(raw).hexdigest() != digest:
                # Git enforces LF for text; binary assets are always compared raw.
                try:
                    raw.decode('utf-8')
                except UnicodeDecodeError:
                    raise AssertionError((component, name, 'BINARY_HASH_MISMATCH')) from None
                raw = raw.replace(b'\r\n', b'\n')
                if hashlib.sha256(raw).hexdigest() != digest:
                    raise AssertionError((component, name, 'SOURCE_HASH_MISMATCH'))
            if name.endswith('.py'):
                ast.parse(raw, filename=name)
        results.append({'component': component, 'files': len(expected), 'hashes': 'PASS',
                        'format_normalized': len(normalizations) if component == 'gateway' else 0})
    patch_result = verify_runtime_patch(read, lock)
    if patch_result:
        results.append(patch_result)
    return {'status': 'CANDIDATE_SOURCE_VERIFIED' if manifest else 'RELEASE_SOURCE_VERIFIED', 'git_ref': ref, 'lock_file': lock_path, 'results': results,
            'enterprise_source_followup': _nov02.followup.summary(followup),
            'dashboard_navigation_candidate': _nov02.followup.navigation.summary(navigation),
            'post_release_test_only_corrections': len(lock.get('qualification_test_overrides', {})),
            'scope': lock['scope'], 'production_modified': False,
            'released_delta': {'source_manifest': release_manifest['source_manifest'], 'frontend_runtime_paths': sorted(n for n in released['frontend'] if n.startswith('frontend/src/')), 'changed_sources': sum(map(len, released.values()))} if release_manifest else None,
            'released_delta_nov02': {'source_manifest': NOV02_ARCHIVE + 'SOURCE.json', 'frontend_runtime_paths': sorted(n for n in nov02['frontend'] if n.startswith('frontend/src/')), 'changed_sources': sum(map(len, nov02.values()))} if has_nov02 else None,
            'candidate': {'status': 'NOT_DEPLOYED', 'base_commit': manifest['base_commit'], 'changed_sources': len(overrides), 'incident_delta': {'base_commit': delta['base_commit'], 'changed_sources': sum(len(v) for v in incident.values()), 'frontend_runtime_paths': sorted(n for n in incident['frontend'] if n.startswith('frontend/src/'))} if delta else None,
                          **({'work_routing_delta': {'status': 'NOT_DEPLOYED', 'base_commit': routing_manifest['base_commit'], 'changed_sources': len(routing)}} if routing_manifest is not None else {}),
                          **({'work_task_agent_delta': {'status': 'NOT_DEPLOYED', 'base_commit': task_agent_manifest['base_commit'], 'changed_sources': len(task_agent)}} if task_agent_manifest is not None else {}),
                          **({'work_task_agent_fix_delta': {'status': 'NOT_DEPLOYED', 'base_commit': task_agent_fix_manifest['base_commit'], 'changed_sources': len(task_agent_fix)}} if task_agent_fix_manifest is not None else {}),
                          **({'work_task_agent_model_fix_delta': {'status': 'NOT_DEPLOYED', 'base_commit': model_fix_manifest['base_commit'], 'changed_sources': len(model_fix)}} if model_fix_manifest is not None else {}),
                          **({'work_release_alignment': {'status': 'NOT_DEPLOYED', 'base_commit': alignment_manifest['base_commit'], 'changed_sources': len(aligned)}} if alignment_manifest is not None else {}),
                          **({'work_authority_sanitizer_delta': {'status': 'NOT_DEPLOYED', 'base_commit': sanitizer_manifest['base_commit'], 'changed_sources': len(sanitizer)}} if sanitizer_manifest is not None else {})} if manifest else None}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', help='Verify a committed Git tree, e.g. HEAD')
    parser.add_argument('--lock', default=LOCK, help='Repository-relative source lock; use the matching historical lock with an older ref')
    args = parser.parse_args()
    print(json.dumps(verify(args.ref, args.lock), ensure_ascii=False))
