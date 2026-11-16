"""Current release and historical projection must both reject unsealed drift."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy/enterprise'))
import post51_energy_source_20261116 as source


@pytest.fixture(scope='module')
def data():
    raw = (ROOT / source.MANIFEST).read_bytes()
    seal = json.loads(raw)
    names = set(seal['files']) | {r['snapshot'] for r in seal['parent_files'].values() if r}
    values = {n: (ROOT / n).read_bytes() for n in names}
    values[source.MANIFEST] = raw
    return values, seal


def test_exact_released_sources_and_acceptance_limits(data):
    values, seal = data
    actual = source.qualify(values.__getitem__, sorted(seal['energy_files']), full=True)
    assert len(actual['runtime_files']) == 16 and len(actual['energy_files']) == 108
    assert not actual['fleet_runtime_acceptance_complete']
    assert not actual['personal_notification_candidate_deployed']
    assert 'backend/app/gateway/upload_document_memory_guard.py' in actual['runtime_files']


@pytest.mark.parametrize('name', [
    source.MANIFEST,
    source.ENERGY + 'scripts/daily.py',
    source.ENERGY + 'scripts/send_webhook.py',
    source.ENERGY + 'scripts/report_renderer.py',
    'backend/app/scheduler/work_notification_delivery.py',
    'backend/app/gateway/upload_document_memory_guard.py',
    source.ARCHIVE + 'deployed-overlay/backend/app/gateway/upload_document_memory_guard.py',
    source.ARCHIVE + 'parent/' + source.ENERGY + 'MANIFEST.json',
    'release/energy-publication-fix-20261116-r1/OPERATOR-INSTALL-REPORT.json',
    'release/energy-publication-fix-20261116-r1/OPERATOR-WEBHOOK-SUCCESS-20261116.json',
    'release/scheduler-concurrency-20261116-r2/OPERATOR-INSTALL-REPORT.json',
    source.ARCHIVE + 'OPERATOR-ROLLOUT-AND-OOM.json',
])
def test_changed_bytes_rejected(data, name):
    values, seal = data
    with pytest.raises(ValueError):
        source.qualify(lambda n: values[n] + b'changed' if n == name else values[n], sorted(seal['energy_files']), full=True)


def test_unknown_public_file_rejected(data):
    values, seal = data
    with pytest.raises(ValueError, match='ENERGY_FILE_SET_CHANGED'):
        source.qualify(values.__getitem__, sorted(seal['energy_files']) + [source.ENERGY + 'extra.py'])


def test_missing_manifest_cannot_revert_to_old_version(data):
    values, seal = data
    with pytest.raises(ValueError, match='POST51_MANIFEST_REQUIRED'):
        source.qualify(lambda n: (_ for _ in ()).throw(KeyError(n)) if n == source.MANIFEST else values[n], sorted(seal['energy_files']))


def test_parent_projection_uses_exact_old_files(data):
    values, seal = data
    read, names, _ = source.historical_view(values.__getitem__, sorted(seal['energy_files']))
    assert len(names) == 107 and source.ENERGY + 'scripts/send_webhook.py' not in names
    assert json.loads(read(source.ENERGY + 'MANIFEST.json'))['version'] == '2.1.0-portable'
    with pytest.raises(KeyError):
        read(source.ENERGY + 'scripts/send_webhook.py')
    with pytest.raises(KeyError):
        read(source.MANIFEST)


def test_scope_cannot_promote_unaccepted_fleet_or_personal_fix(data, monkeypatch):
    values, original = data
    for name in ['fleet_runtime_acceptance_complete', 'personal_notification_candidate_deployed']:
        seal = dict(original, **{name: True})
        raw = json.dumps(seal, sort_keys=True).encode()
        monkeypatch.setattr(source, 'MANIFEST_SHA256', hashlib.sha256(raw).hexdigest())
        with pytest.raises(ValueError, match='POST51_SCOPE_CHANGED'):
            source.qualify(lambda n: raw if n == source.MANIFEST else values[n])


def test_normalization_accepts_only_declared_lint_edits():
    path = 'backend/app/gateway/upload_document_memory_guard.py'
    assert source.normalized_ast(b'import os\nimport asyncio\nx = asyncio.TimeoutError\n', path) == source.normalized_ast(b'import asyncio\nimport os\nx = TimeoutError\n', path)
    assert source.normalized_ast(b'x = 2\n', path) != source.normalized_ast(b'x = 32\n', path)
    assert source.normalized_ast(b'x = asyncio.TimeoutError\n', 'other.py') != source.normalized_ast(b'x = TimeoutError\n', 'other.py')


def test_historical_source_set_excludes_only_real_additions():
    import post44_source_20261113 as historical
    _, result = historical.historical_view(lambda n: (ROOT / n).read_bytes())
    assert result['added_gateway_runtime'] == [
        'backend/app/gateway/upload_document_memory_guard.py',
        'backend/packages/harness/deerflow/utils/conversation_url.py',
    ]


def test_backend_only_git_archive_includes_qualified_parent_closure(data):
    values, seal = data
    names = set(seal['runtime_files']) | {source.MANIFEST}
    names.update(r['snapshot'] for r in seal['parent_files'].values() if r)
    limited = {n: values[n] for n in names}
    assert source.qualify(limited.__getitem__)['gateway_generation'] == 419
    limited.pop('backend/app/scheduler/work_notification_delivery.py')
    with pytest.raises(ValueError, match='POST51_FILE_MISSING'):
        source.qualify(limited.__getitem__)


@pytest.mark.parametrize('reader', [
    'verify-dashboard-navigation-source-20261103.py',
    'verify-nov01-released-source-20261102.py',
    'verify-nov02-released-source-20261103.py',
    'verify-release-source-20261023.py',
])
@pytest.mark.parametrize('has_successor', [False, True])
def test_git_archive_selects_successor_evidence_for_the_chosen_ref(reader, has_successor, monkeypatch):
    spec = importlib.util.spec_from_file_location('archive_closure_' + reader.replace('-', '_'), ROOT / 'deploy/enterprise' / reader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    captured = []

    class ArchiveSelected(Exception):
        pass

    def git(argv, **kwargs):
        assert argv[0] == 'git'
        if argv[1] == 'ls-tree':
            return b'' if argv[-1] == source.MANIFEST and not has_successor else b'present\n'
        if argv[1] == 'show':
            return (ROOT / argv[2].split(':', 1)[1]).read_bytes()
        if argv[1] == 'archive':
            captured.extend(argv)
            raise ArchiveSelected()
        raise AssertionError('Unexpected operation in an offline archive selector')

    monkeypatch.setattr(module.subprocess, 'check_output', git)
    with pytest.raises(ArchiveSelected):
        module.verify('chosen-ref')
    assert (source.ARCHIVE in captured) is has_successor
