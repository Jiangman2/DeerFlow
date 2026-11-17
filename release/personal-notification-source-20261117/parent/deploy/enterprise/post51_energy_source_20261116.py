"""Offline November 16 source qualification and immutable parent projection."""
import ast
import hashlib
import json
from pathlib import PurePosixPath

ARCHIVE = 'release/post51-energy-production-sync-20261116/'
MANIFEST = ARCHIVE + 'SOURCE.json'
MANIFEST_SHA256 = 'd7fac072fb71fdf76c533f5f07ce4e42f074f5eec788527e10bca13a68316623'
ENERGY = 'skills/public/energy-market-daily/'
BASE_COMMIT = '5c39443075c007d6bf82148759f0b3b797826eee'


def require(ok, code):
    if not ok:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def check(read, path, row):
    pure = PurePosixPath(path)
    require(not pure.is_absolute() and '..' not in pure.parts and '\\' not in path
            and ':' not in path and pure.as_posix() == path, 'POST51_UNSAFE_PATH')
    try:
        raw = read(path)
    except (KeyError, FileNotFoundError) as exc:
        raise ValueError('POST51_FILE_MISSING: ' + path) from exc
    require(len(raw) == row['bytes'] and sha(raw) == row['sha256'], 'POST51_FILE_CHANGED: ' + path)
    return raw


def qualify(read, energy_paths=None, full=False):
    try:
        raw = read(MANIFEST)
    except (KeyError, FileNotFoundError):
        try:
            read(ENERGY + 'scripts/send_webhook.py')
        except (KeyError, FileNotFoundError):
            return None
        raise ValueError('POST51_MANIFEST_REQUIRED')
    require(sha(raw) == MANIFEST_SHA256, 'POST51_MANIFEST_CHANGED')
    seal = json.loads(raw)
    require(seal['schema'] == 'post51-energy-production-source.v1'
            and seal['base_commit'] == BASE_COMMIT
            and seal['production_access'] is False
            and seal['credentials_included'] is False
            and seal['skill_version'] == '2.1.8-portable'
            and seal['gateway_generation'] == 419
            and seal['scheduler_max_concurrent_runs'] == 32
            and seal['scheduler_multi_instance'] is True
            and seal['fleet_runtime_acceptance_complete'] is False
            and seal['personal_notification_candidate_deployed'] is False,
            'POST51_SCOPE_CHANGED')
    paths = set(seal['runtime_files'])
    if energy_paths is not None or full:
        paths.update(seal['energy_files'])
        if energy_paths is not None:
            require(set(energy_paths) == set(seal['energy_files']), 'ENERGY_FILE_SET_CHANGED: POST51')
    # Backend-only readers qualify the runtime closure. Skill/full readers also
    # qualify changed support files before exposing their retained parents.
    if energy_paths is not None or full:
        paths.update(p for p in seal['parent_files'] if p in seal['files'] and not p.startswith(ENERGY))
    if full:
        paths.update(seal['files'])
    for path in sorted(paths):
        check(read, path, seal['files'][path])
    for path, row in seal['parent_files'].items():
        if row is not None:
            require(row['snapshot'] == ARCHIVE + 'parent/' + path, 'POST51_PARENT_PATH')
            check(read, row['snapshot'], row)
    if full:
        verify_field(read, seal)
    return seal


def verify_field(read, seal):
    """Bind exact operator evidence, keeping a failed fleet check as failed."""
    receipt = json.loads(read('release/energy-publication-fix-20261116-r1/OPERATOR-INSTALL-REPORT.json'))
    require(receipt['status'] == 'ENERGY_DELIVERY_SKILL_INSTALLED'
            and receipt['skill_updated'] is True
            and receipt['publication']['after_tree_sha256'] == seal['installed_skill_tree_sha256'],
            'POST51_SKILL_RECEIPT_CHANGED')
    release = json.loads(read('release/energy-publication-fix-20261116-r1/CODE-MANIFEST.json'))
    require({ENERGY + p: row for p, row in release['files'].items()} == seal['energy_files'],
            'POST51_SKILL_PACKAGE_MISMATCH')
    activation = json.loads(read('release/scheduler-concurrency-20261116-r2/OPERATOR-INSTALL-REPORT.json'))
    require(activation['operation'] == 'f0f944d091bc434caff845c24d19a836'
            and activation['status'] == 'GATEWAY_419_CONCURRENCY_32_ROLLOUT_REQUESTED'
            and activation['candidate_spec_sha256'] == seal['gateway_spec_sha256']
            and activation['scheduler_max_concurrent_runs_after'] == 32,
            'POST51_CONCURRENCY_RECEIPT_CHANGED')
    field = json.loads(read(ARCHIVE + 'OPERATOR-ROLLOUT-AND-OOM.json'))
    require(field['rollout']['updated'] == field['rollout']['ready'] == field['rollout']['available'] == 70
            and field['fleet_verifier_error'] == 'POD_NOT_READY_OR_RESTARTED'
            and field['oom']['reason'] == 'OOMKilled'
            and field['oom']['exit_code'] == 137
            and field['fleet_runtime_acceptance_complete'] is False,
            'POST51_ACCEPTANCE_OVERCLAIM')
    observed = json.loads(read('release/scheduler-concurrency-20261116-r2/SOURCE.json'))['expected_sources']
    require({p.removeprefix('/app/'): row for p, row in observed.items() if p.startswith('/app/')}
            == seal['runtime_files'], 'POST51_RUNTIME_SOURCE_SET_CHANGED')
    for path, row in seal['runtime_files'].items():
        deployed = check(read, ARCHIVE + 'deployed-overlay/' + path, row)
        current = check(read, path, seal['files'][path])
        require(normalized_ast(current, path) == normalized_ast(deployed, path),
                'POST51_RUNTIME_SEMANTICS_CHANGED: ' + path)


def normalized_ast(raw, path):
    """Allow formatting/import sorting and two explicit Python 3.12 lint fixes."""
    tree = ast.parse(raw)

    class Normalize(ast.NodeTransformer):
        def visit_Attribute(self, node):
            if path.endswith('/upload_document_memory_guard.py') and isinstance(node.value, ast.Name) \
                    and node.value.id == 'asyncio' and node.attr == 'TimeoutError':
                return ast.Name(id='TimeoutError', ctx=ast.Load())
            return self.generic_visit(node)

        def visit_ImportFrom(self, node):
            if path.endswith('/routers/uploads.py') and node.module == 'app.gateway.upload_document_parser':
                node.names = [n for n in node.names if n.name != 'parse_conversation_document']
            node.names.sort(key=lambda n: (n.name, n.asname or ''))
            return node if node.names else None

    tree = Normalize().visit(tree)
    result, imports = [], []
    for node in tree.body + [None]:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(node)
            continue
        if imports:
            result.extend(sorted(imports, key=ast.dump))
            imports = []
        if node is not None:
            result.append(node)
    tree.body = result
    return ast.dump(tree, include_attributes=False)


def historical_view(read, energy_paths=None):
    seal = qualify(read, energy_paths)
    if seal is None:
        return read, energy_paths, None

    def previous(path):
        if path == MANIFEST:
            raise KeyError(path)
        if path in seal['parent_files']:
            row = seal['parent_files'][path]
            if row is None:
                raise KeyError(path)
            return read(row['snapshot'])
        if path.startswith(ENERGY) and path not in seal['original_energy_files']:
            raise KeyError(path)
        return read(path)

    return previous, (sorted(seal['original_energy_files']) if energy_paths is not None else None), seal
