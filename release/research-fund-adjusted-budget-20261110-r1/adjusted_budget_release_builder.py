"""Explicit, offline release binding. Import is inert; no CLI or production calls.

Call prepare_bundle only after an unmodified fresh adjusted-budget observation and any explicit runtime review is available.
REVIEW_REQUIRED receipts must be reviewed separately; never rewrite their status.
This helper returns values in memory and does not seal or distribute a release.
"""
import ast
import base64
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tarfile

HERE = Path(__file__).resolve().parent
NAME = 'research-fund-settlement'
BEFORE_TREE = '0b417f6540cbab2f35ce9bbde28efe0d9d214273bf4fbf9baf47734f9ea01c85'


def require(ok, code):
    if not ok:
        raise ValueError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def literal_binding(source, name):
    tree = ast.parse(source)
    nodes = [n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
    require(len(nodes) == 1, 'LITERAL_BINDING_REQUIRED_' + name)
    return ast.literal_eval(nodes[0].value)


def replace_binding(source, name, value):
    tree = ast.parse(source)
    nodes = [n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
    require(len(nodes) == 1, 'BINDING_REQUIRED_' + name)
    node = nodes[0]; lines = source.splitlines(keepends=True)
    lines[node.lineno-1:node.end_lineno] = [name + ' = ' + repr(value) + '\n']
    result = ''.join(lines); ast.parse(result, feature_version=(3, 7))
    return result


def read_regular(path, maximum=2097152):
    path = Path(path)
    require(not path.is_symlink(), 'SYMLINK_REFUSED')
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and 0 <= before.st_size <= maximum, 'REGULAR_FILE_BOUND')
    data = path.read_bytes(); after = path.lstat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) and len(data) == before.st_size, 'FILE_CHANGED_DURING_READ')
    return data


def validate_manifest(value):
    rows = value['files']; require(isinstance(rows, list) and 0 < len(rows) <= 64, 'FILE_COUNT_BOUND')
    require(rows == sorted(rows, key=lambda row: row['path']) and len({r['path'] for r in rows}) == len(rows), 'MANIFEST_ORDER_OR_DUPLICATE')
    total = 0
    for row in rows:
        path = PurePosixPath(row['path'])
        require(path.parts and not path.is_absolute() and '..' not in path.parts and '\\' not in row['path'] and ':' not in row['path'] and path.as_posix() == row['path'], 'MANIFEST_PATH')
        require(type(row['bytes']) is int and 0 <= row['bytes'] <= 2097152, 'MEMBER_BYTES')
        require(isinstance(row['sha256'], str) and len(row['sha256']) == 64 and all(c in '0123456789abcdef' for c in row['sha256']), 'MEMBER_DIGEST')
        total += row['bytes']
    require(total <= 8388608 and total == value['bytes'] and len(rows) == value['file_count'] and sha(canonical(rows)) == value['tree_sha256'], 'MANIFEST_INTEGRITY')
    return value


def manifest(root):
    root = Path(root)
    require(root.is_dir() and not root.is_symlink(), 'SKILL_DIRECTORY_REQUIRED')
    rows = []
    for parent, dirs, files in os.walk(str(root), followlinks=False):
        for name in list(dirs):
            require(not (Path(parent)/name).is_symlink(), 'DIRECTORY_SYMLINK_REFUSED')
            if name == '__pycache__': dirs.remove(name)
        for name in files:
            path = Path(parent)/name
            require(not path.is_symlink(), 'FILE_SYMLINK_REFUSED')
            if name.endswith(('.pyc', '.pyo')): continue
            data = read_regular(path)
            rows.append({'path': path.relative_to(root).as_posix(), 'bytes': len(data), 'sha256': sha(data)})
    rows.sort(key=lambda row: row['path'])
    return validate_manifest({'schema': 'research-fund-skill-source.v1', 'files': rows, 'bytes': sum(row['bytes'] for row in rows), 'file_count': len(rows), 'tree_sha256': sha(canonical(rows))})


def archive_for(root, expected):
    require(manifest(root) == expected, 'CANDIDATE_CHANGED_BEFORE_PACK')
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w', format=tarfile.USTAR_FORMAT) as target:
        for row in expected['files']:
            data = read_regular(Path(root)/row['path'])
            require(len(data) == row['bytes'] and sha(data) == row['sha256'], 'CANDIDATE_CHANGED_DURING_PACK')
            entry = tarfile.TarInfo(NAME+'/'+row['path']); entry.size = len(data); entry.mode = 0o644; entry.mtime = 0
            target.addfile(entry, io.BytesIO(data))
    require(manifest(root) == expected, 'CANDIDATE_CHANGED_AFTER_PACK')
    compressed = gzip.compress(output.getvalue(), mtime=0)
    require(len(compressed) <= 1048576, 'ARCHIVE_COMPRESSED_BOUND')
    return compressed


EXPECTED_NODES = ('ecs-zdz-pro-0018', 'ecs-zdz-pro-0019', 'ecs-zdz-pro-0020', 'ecs-zdz-pro-0021', 'ecs-zdz-pro-0022')
PRIOR_RELEASE = {'operation': '73efed02b7ce41b2bdc097a57fef48aa', 'path': '/var/lib/portal-research-fund-skill-update-expense-purposes-20261110-r1/REPORT.json', 'bytes': 7342, 'sha256': '964c0b00763789f6366673b73561e1a14d5ce32c5476f8a3c147cec9ed2fdb10'}
ALLOWED_CHANGED = ('SKILL.md', 'references/group-company.md', 'scripts/group_template.py')
ALLOWED_ADDED = ()


def validate_scope(before, after):
    validate_manifest(before); validate_manifest(after)
    require(before['tree_sha256'] == BEFORE_TREE and before['file_count'] == 31 and before['bytes'] == 809428, 'DEPLOYED_EXPENSE_PURPOSES_TREE_REQUIRED')
    old = {row['path']: row for row in before['files']}
    new = {row['path']: row for row in after['files']}
    require(not set(old) - set(new), 'SKILL_FILE_REMOVAL_REFUSED')
    require(set(new) - set(old) == set(ALLOWED_ADDED), 'SKILL_ADDED_FILE_SCOPE')
    require({name for name in old if old[name] != new[name]} == set(ALLOWED_CHANGED), 'SKILL_CHANGED_FILE_SCOPE')
    require(after['file_count'] == 31 and after['tree_sha256'] != before['tree_sha256'], 'CANDIDATE_FILE_COUNT_OR_TREE')


def validate_baseline(baseline_bytes, before, runtime_review=None):
    """Validate original observation bytes; review never rewrites that receipt."""
    baseline = json.loads(baseline_bytes)
    require(baseline.get('schema') == 'portal-research-fund-adjusted-budget-preflight.v1'
            and baseline.get('tool_revision') == '20261110-r1', 'FRESH_ADJUSTED_BUDGET_BASELINE_REQUIRED')
    require(baseline.get('stable_readback') is True and baseline.get('ready_for_update') is False, 'STABLE_READONLY_BASELINE_REQUIRED')
    require(baseline.get('prior_release') == dict(PRIOR_RELEASE, validated=True), 'PRIOR_RELEASE_RECEIPT_BINDING_CHANGED')
    require(before['tree_sha256'] == BEFORE_TREE and baseline.get('expected_skill_tree_sha256') == BEFORE_TREE, 'RELEASED_PROJECT_TYPE_SKILL_REQUIRED')
    for key in ('files_modified', 'workload_modified', 'database_access', 'application_started', 'skill_update_requested'):
        require(baseline.get(key) is False, 'BASELINE_NOT_READONLY')
    require(set(baseline['deployments']) == {'gateway', 'ppt-master-runner', 'provisioner'}, 'DEPLOYMENT_SCOPE')
    for side in ('convergence_before', 'convergence_after'):
        for role in ('gateway', 'provisioner', 'frontend'):
            row = baseline.get(side, {}).get(role, {})
            require(row.get('converged') is True and row.get('paused') is False
                    and row.get('desired', 0) > 0 and row.get('generation') == row.get('observed_generation')
                    and row.get('total') == row['desired'] == row.get('updated') == row.get('ready') == row.get('available')
                    and row.get('terminating') == 0, 'WRITABLE_BASELINE_NOT_CONVERGED_' + role)
    for side in ('pod_selection_diagnostics', 'final_pod_selection_diagnostics'):
        for role in ('gateway', 'provisioner'):
            row = baseline.get(side, {}).get(role, {})
            require(row.get('complete_current_replica_set') is True and row.get('desired', 0) > 0
                    and row.get('current_owned_ready_count') == row['desired'], 'CURRENT_REPLICA_COVERAGE_REQUIRED_' + role)
    coverage = baseline.get('gateway_node_coverage', {})
    require(coverage == {'expected': list(EXPECTED_NODES), 'missing': [], 'additional': [], 'complete': True}, 'GATEWAY_NODE_COVERAGE_REQUIRED')
    samples = baseline.get('samples', [])
    require(len(samples) == 5 and sorted(row['node'] for row in samples) == list(EXPECTED_NODES), 'GATEWAY_SAMPLE_COVERAGE')
    for row in samples:
        skill = row['skill']
        require(skill['matches_expected'] is True and skill['observed_tree_sha256'] == before['tree_sha256']
                and skill['observed_count'] == before['file_count'] and row['custom_shadow_present'] is False, 'RELEASED_SKILL_CHANGED')
        require(row['runtime_sources'] == samples[0]['runtime_sources'], 'MIXED_RUNTIME_SOURCES')
    changes = any(row['runtime_changes'] for row in samples)
    reasons = baseline.get('review_reasons')
    if changes:
        require(baseline.get('status') == 'READONLY_ADJUSTED_BUDGET_BASELINE_REVIEW_REQUIRED' and baseline.get('exitcode') == 2
                and baseline.get('review_required') is True and baseline.get('runtime_loading_review_required') is True
                and reasons == ['RUNTIME_SKILL_LOADING_SOURCES_CHANGED'], 'NON_RUNTIME_REVIEW_REQUIRES_NEW_OBSERVATION')
        require(isinstance(runtime_review, dict)
                and runtime_review.get('schema') == 'research-fund-runtime-source-review.v1'
                and runtime_review.get('status') == 'REVIEWED_COMPATIBLE_FOR_SHARED_SKILL_UPDATE'
                and runtime_review.get('baseline_sha256') == sha(baseline_bytes)
                and runtime_review.get('runtime_sources_sha256') == sha(canonical(samples[0]['runtime_sources']))
                and runtime_review.get('reviewed_reasons') == reasons
                and runtime_review.get('compatibility_evidence_sha256') is not None
                and isinstance(runtime_review.get('compatibility_evidence_sha256'), str)
                and len(runtime_review['compatibility_evidence_sha256']) == 64
                and all(c in '0123456789abcdef' for c in runtime_review['compatibility_evidence_sha256']), 'EXPLICIT_RUNTIME_COMPATIBILITY_REVIEW_REQUIRED')
    else:
        require(baseline.get('status') == 'READONLY_ADJUSTED_BUDGET_BASELINE_ACCEPTED_NO_CHANGE' and baseline.get('exitcode') == 0
                and baseline.get('review_required') is False and baseline.get('runtime_loading_review_required') is False
                and reasons == [], 'BASELINE_REVIEW_PENDING')
        require(runtime_review is None, 'UNEXPECTED_RUNTIME_REVIEW')
    require(baseline['gateway_scope']['nodes'] == 5 and baseline['gateway_scope']['sampling'] == 'one_ready_owned_pod_per_node', 'GATEWAY_SCOPE_MISMATCH')
    image = baseline['provisioner_sandbox_configuration'].get('image')
    require(isinstance(image, str) and image and baseline['provisioner_sandbox_configuration']['configured_image_explicit'], 'EXPLICIT_SANDBOX_IMAGE_REQUIRED')
    require(any(row['matches_configured_image'] and row['image'] == image and row['ready_pods'] > 0 for row in baseline['sandbox_inventory']['images']), 'CACHED_SANDBOX_IMAGE_REQUIRED')
    require(any(row['mount']['kind'] == 'shared_public' and row['mount']['read_only'] is True
                and row['image'] == image and row['known_sample_files_match'] is True for row in baseline['sandbox_samples']), 'SHARED_SANDBOX_SAMPLE_REQUIRED')
    frontend = baseline.get('frontend_observation') or {}
    require(frontend.get('stable_readback') is True and frontend.get('included_in_skill_update') is False
            and frontend.get('deployment', {}).get('name') == 'frontend' and frontend.get('images'), 'FRONTEND_OBSERVATION_REQUIRED')
    return baseline


def prepare_bundle(candidate_root, before_manifest, frozen_candidate_manifest, baseline_bytes, inspector_source,
                   qualifier_source, qualification_plan, qualification_receipt, *, approved_candidate_tree_sha256,
                   runtime_review=None):
    """Offline only: return a reviewed candidate in memory; no writes or production calls."""
    before = validate_manifest(before_manifest); after = validate_manifest(frozen_candidate_manifest)
    validate_scope(before, after)
    require(after['tree_sha256'] == approved_candidate_tree_sha256, 'FINAL_CANDIDATE_APPROVAL_CHANGED')
    baseline = validate_baseline(baseline_bytes, before, runtime_review)
    require(literal_binding(inspector_source, 'EXPECTED_SKILL') == before, 'INSPECTOR_OLD_PACKAGE_BINDING')
    require(literal_binding(inspector_source, 'PRIOR_RELEASE') == PRIOR_RELEASE, 'INSPECTOR_PRIOR_RECEIPT_BINDING')
    import importlib.util
    spec = importlib.util.spec_from_file_location('adjusted_budget_qualification_plan', HERE/'adjusted_budget_qualification_plan.py')
    qualification = importlib.util.module_from_spec(spec); spec.loader.exec_module(qualification)
    qualification.validate_receipt(qualification_plan, qualification_receipt, qualifier_source, approved_candidate_tree_sha256)
    child = literal_binding(inspector_source, 'CHILD')
    archive = archive_for(candidate_root, after)
    remote = (HERE/'adjusted_budget_remote.py').read_text(encoding='utf8')
    remote = replace_binding(remote, 'QUALIFIER_SOURCE', qualifier_source)
    remote = replace_binding(remote, 'QUALIFIER_EXPECTED', qualification_receipt['passed'])
    skill_md = read_regular(Path(candidate_root)/'SKILL.md'); front = skill_md.split(b'---', 2)
    require(skill_md.startswith(b'---') and len(front) == 3, 'FRONTMATTER_REQUIRED')
    image = baseline['provisioner_sandbox_configuration']['image']
    bundle = {'schema': 'research-fund-adjusted-budget-sealed-bundle.v1', 'before': before, 'after': after,
              'deployments': baseline['deployments'], 'pvc': baseline['skills_pvc'], 'pv': baseline['skills_pv'],
              'skills_mounts': baseline['skills_mounts'], 'gateway_nodes': list(EXPECTED_NODES),
              'runtime_sources': baseline['samples'][0]['runtime_sources'], 'sandbox_image': image,
              'sandbox_image_ids': sorted({r['image_id'] for r in baseline['sandbox_inventory']['images'] if r['image'] == image and r['matches_configured_image']}),
              'frontmatter_sha256': sha(front[1]), 'baseline_receipt_sha256': sha(baseline_bytes),
              'baseline_status': baseline['status'], 'baseline_tool_revision': baseline['tool_revision'],
              'runtime_review': runtime_review, 'prior_release': PRIOR_RELEASE,
              'frontend_observation': baseline['frontend_observation'], 'approved_candidate_tree_sha256': approved_candidate_tree_sha256,
              'qualification_plan_sha256': sha(canonical(qualification_plan)), 'qualification_receipt_sha256': sha(canonical(qualification_receipt)),
              'archive_b64': base64.b64encode(archive).decode(), 'archive_sha256': sha(archive),
              'inspector_source': inspector_source, 'readonly_child': child, 'remote_source': remote}
    for key in ('inspector_source', 'readonly_child', 'remote_source'):
        bundle[key+'_sha256'] = sha(bundle[key].encode())
    updater = replace_binding((HERE/'adjusted_budget_updater_template.py').read_text(encoding='utf8'), 'BUNDLE', bundle)
    return {'bundle': bundle, 'updater_source': updater, 'remote_source': remote,
            'candidate_manifest': after, 'production_actions': 0, 'files_written': 0}


