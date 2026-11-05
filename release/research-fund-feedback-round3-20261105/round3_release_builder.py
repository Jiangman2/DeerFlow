"""Explicit, offline release binding. Import is inert; no CLI or production calls.

Call prepare_bundle only after an unmodified fresh Round3 r3 accepted baseline is available.
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
BEFORE_TREE = '6c393b11a0023a50de29a1958088322324f759e85c6f3080816aed023c61e951'


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


def validate_baseline(baseline, before):
    require(baseline.get('schema') == 'portal-research-fund-skill-preflight.v3' and baseline.get('tool_revision') == '20261105-r3', 'FRESH_ROUND3_R3_REQUIRED')
    require(baseline.get('status') == 'READONLY_FINANCE_SKILL_BASELINE_ACCEPTED_NO_CHANGE' and baseline.get('exitcode') == 0 and baseline.get('stable_readback') is True, 'UNREVIEWED_BASELINE_REQUIRES_SEPARATE_REVIEW_RECORD')
    require(baseline.get('runtime_loading_review_required') is False and not baseline.get('review_reasons'), 'BASELINE_REVIEW_PENDING')
    require(baseline['deployments']['gateway']['generation'] >= 407, 'OLD_GATEWAY_BASELINE_REFUSED')
    require(set(baseline['deployments']) == {'gateway', 'ppt-master-runner', 'provisioner'}, 'DEPLOYMENT_SCOPE')
    for key in ('files_modified', 'workload_modified', 'database_access', 'application_started', 'skill_update_requested'):
        require(baseline.get(key) is False, 'BASELINE_NOT_READONLY')
    require(before['tree_sha256'] == BEFORE_TREE and baseline.get('expected_skill_tree_sha256') == BEFORE_TREE, 'RELEASED_NOV05_SKILL_REQUIRED')
    samples = baseline.get('samples', [])
    require(0 < len(samples) <= 16 and len({row['node'] for row in samples}) == len(samples), 'GATEWAY_SAMPLE_COVERAGE')
    for row in samples:
        require(row['skill']['matches_expected'] is True and row['skill']['observed_tree_sha256'] == before['tree_sha256'] and row['skill']['observed_count'] == before['file_count'] and not row['custom_shadow_present'], 'RELEASED_SKILL_CHANGED')
        require(row['runtime_sources'] == samples[0]['runtime_sources'] and not row['runtime_changes'], 'UNREVIEWED_RUNTIME_SOURCE_CHANGE')
    require(baseline['gateway_scope']['nodes'] == len(samples) and baseline['gateway_scope']['sampling'] == 'one_ready_owned_pod_per_node', 'GATEWAY_SCOPE_MISMATCH')
    image = baseline['provisioner_sandbox_configuration'].get('image')
    require(isinstance(image, str) and image and baseline['provisioner_sandbox_configuration']['configured_image_explicit'], 'EXPLICIT_SANDBOX_IMAGE_REQUIRED')
    require(any(row['matches_configured_image'] and row['image'] == image and row['ready_pods'] > 0 for row in baseline['sandbox_inventory']['images']), 'CACHED_SANDBOX_IMAGE_REQUIRED')
    require(before['file_count'] == 30 and before['bytes'] == 788816, 'DEPLOYED_SKILL_SIZE_CHANGED')
    frontend = baseline.get('frontend_observation') or {}
    require(frontend.get('stable_readback') is True and frontend.get('included_in_skill_update') is False, 'FRONTEND_OBSERVATION_REQUIRED')
    require(frontend.get('deployment', {}).get('name') == 'frontend' and frontend.get('images'), 'FRONTEND_IDENTITY_REQUIRED')
    return baseline


def prepare_bundle(candidate_root, before_manifest, frozen_candidate_manifest, baseline_bytes, inspector_source, qualifier_source, qualifier_count, *, approved_candidate_tree_sha256, qualification_plan, qualification_receipt):
    """Return reviewable bundle/host source in memory. Never execute or write them."""
    require(isinstance(approved_candidate_tree_sha256, str) and len(approved_candidate_tree_sha256) == 64 and all(c in '0123456789abcdef' for c in approved_candidate_tree_sha256), 'FINAL_CANDIDATE_APPROVAL_REQUIRED')
    before = validate_manifest(before_manifest); after = validate_manifest(frozen_candidate_manifest)
    require(after['tree_sha256'] == approved_candidate_tree_sha256 and after['file_count'] == 30, 'FINAL_CANDIDATE_APPROVAL_CHANGED')
    require(after['tree_sha256'] != before['tree_sha256'], 'NO_NEW_SKILL_CHANGE')
    import importlib.util
    spec = importlib.util.spec_from_file_location('round3_qualification_plan', HERE/'round3_qualification_plan.py')
    qualification = importlib.util.module_from_spec(spec); spec.loader.exec_module(qualification)
    qualification.validate_receipt(qualification_plan, qualification_receipt, qualifier_source, approved_candidate_tree_sha256)
    require(qualification_receipt['passed'] == qualifier_count, 'QUALIFICATION_COUNT_CHANGED')
    baseline = validate_baseline(json.loads(baseline_bytes), before)
    require(literal_binding(inspector_source, 'EXPECTED_SKILL') == before, 'INSPECTOR_OLD_PACKAGE_BINDING')
    require(type(qualifier_count) is int and qualifier_count > 30 and literal_binding(qualifier_source, 'EXPECTED_CHECKS') == qualifier_count, 'QUALIFIER_COUNT_BINDING')
    child = literal_binding(inspector_source, 'CHILD')
    archive = archive_for(candidate_root, after)
    remote = (HERE/'round3_remote_template.py').read_text(encoding='utf-8')
    remote = replace_binding(remote, 'QUALIFIER_SOURCE', qualifier_source)
    remote = replace_binding(remote, 'QUALIFIER_EXPECTED', qualifier_count)
    skill_md = read_regular(Path(candidate_root)/'SKILL.md')
    front = skill_md.split(b'---', 2)
    require(skill_md.startswith(b'---') and len(front) == 3, 'FRONTMATTER_REQUIRED')
    image = baseline['provisioner_sandbox_configuration']['image']
    bundle = {'schema': 'research-fund-round3-sealed-bundle.v1', 'before': before, 'after': after, 'deployments': baseline['deployments'], 'pvc': baseline['skills_pvc'], 'pv': baseline['skills_pv'], 'skills_mounts': baseline['skills_mounts'], 'gateway_nodes': sorted(row['node'] for row in baseline['samples']), 'runtime_sources': baseline['samples'][0]['runtime_sources'], 'sandbox_image': image, 'sandbox_image_ids': sorted({row['image_id'] for row in baseline['sandbox_inventory']['images'] if row['image'] == image and row['matches_configured_image']}), 'frontmatter_sha256': sha(front[1]), 'baseline_receipt_sha256': sha(baseline_bytes), 'baseline_status': baseline['status'], 'baseline_tool_revision': baseline['tool_revision'], 'frontend_observation': baseline['frontend_observation'], 'approved_candidate_tree_sha256': approved_candidate_tree_sha256, 'qualification_plan_sha256': sha(canonical(qualification_plan)), 'qualification_receipt_sha256': sha(canonical(qualification_receipt)), 'archive_b64': base64.b64encode(archive).decode(), 'archive_sha256': sha(archive), 'inspector_source': inspector_source, 'readonly_child': child, 'remote_source': remote}
    for key in ('inspector_source', 'readonly_child', 'remote_source'):
        bundle[key+'_sha256'] = sha(bundle[key].encode())
    updater = replace_binding((HERE/'round3_updater_template.py').read_text(encoding='utf-8'), 'BUNDLE', bundle)
    updater = updater.replace('Unbound Round3 Skill updater template. Await fresh r3 receipt and approved candidate.', 'Bound Round3 one-Skill updater. Fresh r3 receipt and approved candidate verified offline.', 1)
    return {'bundle': bundle, 'updater_source': updater, 'remote_source': remote, 'candidate_manifest': after, 'production_actions': 0, 'files_written': 0}
