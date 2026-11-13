"""Verify the finite post-44 delta before projecting immutable historical bytes.

Offline source provenance only. No installer, application, network or credential
is loaded. The current portable Skill and deployed overlays have separate seals.
"""
import ast
import hashlib
import json
from pathlib import PurePosixPath

ARCHIVE = 'release/post44-source-sync-20261113/'
MANIFEST = ARCHIVE + 'SOURCE.json'
MANIFEST_SHA256 = '5eebacbe4d40a36f26c0a77e8b8502fbcd3d1a1ef052b9f19ebda0ea332563e7'
ENERGY = 'skills/public/energy-market-daily/'
BASE_COMMIT = 'f882aa44b96a8aa7e35008a683677c7e55d2ddf8'
GATEWAY_PATHS = frozenset({
    'backend/app/channels/manager.py',
    'backend/app/integrations/work/notifications.py',
    'backend/app/scheduler/work_notification_delivery.py',
    'backend/app/scheduler/work_notifications.py',
    'backend/packages/harness/deerflow/tools/builtins/present_file_tool.py',
    'backend/packages/harness/deerflow/utils/conversation_url.py',
})
ARCHIVE_INPUTS = (
    ARCHIVE, 'portable-skills/',
    'release/energy-daily-portable-20261111/',
    'release/energy-daily-browser-fix-20261112-r1/',
    'release/energy-daily-public-runtime-20261112-r1/',
    'release/energy-daily-delivery-skill-20261112-r1/',
    'release/energy-daily-recovery-gate-20261112-r1/',
    'release/energy-daily-recovery-gate-20261112-r2/',
    'release/energy-delivery-budget-inspection-20261112-r1/',
    'release/sandbox-capacity-20261112-r1/',
    'release/sandbox-reclaim-plan-20261112-r1/',
    'release/sandbox-retention-20261112-r1/',
    'release/scheduler-baseline-inspection-20261113-r1/',
    'release/scheduler-budget-20261112-r1/',
    'release/scheduler-budget-20261112-r2/',
    'release/scheduler-budget-20261112-r3/',
    'release/scheduler-budget-20261113-r4/',
    'release/scheduler-budget-20261113-r5/',
)


def require(ok, code):
    if not ok:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def check(read, path, row):
    pure = PurePosixPath(path)
    require(not pure.is_absolute() and '..' not in pure.parts and '\\' not in path
            and ':' not in path and pure.as_posix() == path, 'POST44_UNSAFE_PATH')
    try:
        raw = read(path)
    except (KeyError, FileNotFoundError) as exc:
        raise ValueError('POST44_FILE_MISSING: ' + path) from exc
    require(len(raw) == row['bytes'] and sha(raw) == row['sha256'], 'POST44_FILE_CHANGED: ' + path)
    return raw


def qualify(read, energy_paths=None, gateway_only=False):
    try:
        raw = read(MANIFEST)
    except (KeyError, FileNotFoundError):
        try:
            read(ENERGY + 'scripts/daily.py')
        except (KeyError, FileNotFoundError):
            return None
        raise ValueError('POST44_MANIFEST_REQUIRED')
    require(sha(raw) == MANIFEST_SHA256, 'POST44_MANIFEST_CHANGED')
    seal = json.loads(raw)
    require(seal['schema'] == 'post44-source-reconciliation.v1'
            and seal['base_commit'] == BASE_COMMIT
            and seal['production_commands_executed'] is False
            and seal['credentials_included'] is False
            and seal['scheduler_budget_deployed'] is False
            and seal['scheduler_budget_candidate'] == 10000
            and seal['business_acceptance_pending'] is True,
            'POST44_SCOPE_CHANGED')
    require(set(seal['gateway_sources']) == GATEWAY_PATHS, 'POST44_GATEWAY_SET_CHANGED')
    if gateway_only:
        # Backend historical readers qualify exactly the six runtime changes;
        # the dedicated full verifier separately checks all Skill/tool archives.
        for path, row in seal['gateway_sources'].items():
            ast.parse(check(read, path, row), filename=path)
            parent = seal['parent_files'][path]
            if parent is not None:
                require(parent['snapshot'] == ARCHIVE + 'parent/' + path, 'POST44_PARENT_PATH')
                check(read, parent['snapshot'], parent)
        return seal
    require(len(seal['energy_files']) == 107, 'POST44_ENERGY_COUNT_CHANGED')
    if energy_paths is not None:
        require(set(energy_paths) == set(seal['energy_files']), 'ENERGY_FILE_SET_CHANGED: POST44')
    for path, row in {**seal['support_files'], **seal['energy_files'], **seal['gateway_sources']}.items():
        current = check(read, path, row)
        if path in GATEWAY_PATHS:
            ast.parse(current, filename=path)
    portable = json.loads(read(ENERGY + 'MANIFEST.json'))
    require(portable['version'] == '2.1.0-portable', 'POST44_PORTABLE_VERSION_CHANGED')
    require({ENERGY + path: row for path, row in portable['files'].items()}
            == {path: row for path, row in seal['energy_files'].items() if path != ENERGY + 'MANIFEST.json'},
            'POST44_PORTABLE_INVENTORY_CHANGED')
    for path, row in seal['parent_files'].items():
        require(path in GATEWAY_PATHS or path.startswith(ENERGY) or path.startswith('tests/'), 'POST44_PARENT_SCOPE')
        if row is not None:
            require(row['snapshot'] == ARCHIVE + 'parent/' + path, 'POST44_PARENT_PATH')
            check(read, row['snapshot'], row)
    require(set(seal['original_energy_files']) <= set(seal['parent_files']), 'POST44_ORIGINAL_PARENT_SET')
    return seal


def project(read, seal):
    def historical(path):
        if path in seal['parent_files']:
            row = seal['parent_files'][path]
            if row is None:
                raise KeyError(path)
            return read(row['snapshot'])
        # A new portable file must not leak into a historical original package.
        if path.startswith(ENERGY) and path not in seal['original_energy_files']:
            raise KeyError(path)
        return read(path)
    return historical


def historical_view(read):
    seal = qualify(read, gateway_only=True)
    if seal is None:
        return read, None
    return project(read, seal), {
        'status': 'POST44_CURRENT_SOURCE_AND_PRESERVED_PARENT_VERIFIED',
        'added_gateway_runtime': sorted(path for path in GATEWAY_PATHS if seal['parent_files'][path] is None),
        'energy_version': '2.1.0-portable',
        'scheduler_budget_candidate': 10000,
        'scheduler_budget_deployed': False,
        'production_acceptance_claimed': False,
    }


def energy_historical_view(read, energy_paths):
    seal = qualify(read, energy_paths)
    if seal is None:
        return read, energy_paths, None
    return project(read, seal), sorted(seal['original_energy_files']), {
        'portable_skill_version': '2.1.0-portable',
        'portable_source_files': 107,
        'historical_original_preserved_outside_public': True,
        'scheduler_budget_deployed': False,
    }
