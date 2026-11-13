"""Reject current/archived drift without any application or production access."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy/enterprise'))
import post44_source_20261113 as source


@pytest.fixture(scope='module')
def snapshot():
    raw = (ROOT / source.MANIFEST).read_bytes()
    seal = json.loads(raw)
    paths = set(seal['support_files']) | set(seal['energy_files']) | set(seal['gateway_sources'])
    paths |= {row['snapshot'] for row in seal['parent_files'].values() if row is not None}
    data = {path: (ROOT / path).read_bytes() for path in paths}
    data[source.MANIFEST] = raw
    return data, sorted(seal['energy_files'])


def test_current_and_original_are_separately_verified(snapshot):
    data, paths = snapshot
    seal = source.qualify(data.__getitem__, paths)
    historical, old_paths, result = source.energy_historical_view(data.__getitem__, paths)
    assert len(paths) == 107 and len(old_paths) == 162
    assert result['historical_original_preserved_outside_public']
    assert not result['scheduler_budget_deployed']
    assert b'entry.py' in historical(source.ENERGY + 'SKILL.md')
    assert b'daily.py' in data[source.ENERGY + 'SKILL.md']
    with pytest.raises(KeyError):
        historical(source.ENERGY + 'scripts/daily.py')
    assert len(seal['gateway_sources']) == 6


@pytest.mark.parametrize('path', [
    source.MANIFEST,
    source.ENERGY + 'scripts/daily.py',
    source.ENERGY + 'scripts/delivery_text.py',
    source.ENERGY + 'scripts/market.py',
    source.ARCHIVE + 'parent/' + source.ENERGY + 'scripts/entry.py',
    source.ARCHIVE + 'parent/' + source.ENERGY + 'assets/energy-daily-v1.0.original.zip',
    'backend/app/channels/manager.py',
    'backend/packages/harness/deerflow/utils/conversation_url.py',
    'release/energy-daily-delivery-skill-20261112-r1/FIELD-OBSERVATION-20261112.json',
    'release/scheduler-budget-20261113-r5/activate-scheduler-budget-20261113-r5.py',
])
def test_tampered_bytes_rejected(snapshot, path):
    data, paths = snapshot
    with pytest.raises(ValueError):
        source.qualify(lambda key: data[key] + b'changed' if key == path else data[key], paths)


def test_missing_manifest_cannot_hide_current_marker(snapshot):
    data, paths = snapshot
    changed = dict(data)
    changed.pop(source.MANIFEST)
    with pytest.raises(ValueError, match='POST44_MANIFEST_REQUIRED'):
        source.qualify(changed.__getitem__, paths)


def test_unknown_skill_file_cannot_enter_historical_projection(snapshot):
    data, paths = snapshot
    for altered in (paths[:-1], paths + [source.ENERGY + 'extra.py']):
        with pytest.raises(ValueError, match='ENERGY_FILE_SET_CHANGED'):
            source.qualify(data.__getitem__, altered)


def test_missing_support_file_fails_closed(snapshot):
    data, paths = snapshot
    changed = dict(data)
    changed.pop('release/scheduler-budget-20261113-r5/activate-scheduler-budget-20261113-r5.py')
    with pytest.raises(ValueError, match='POST44_FILE_MISSING'):
        source.qualify(changed.__getitem__, paths)


@pytest.mark.parametrize('field,value', [('scheduler_budget_deployed', True),
                                       ('credentials_included', True),
                                       ('production_commands_executed', True),
                                       ('scheduler_budget_candidate', 1000)])
def test_scope_overclaim_rejected_even_with_new_manifest_hash(snapshot, monkeypatch, field, value):
    data, paths = snapshot
    seal = json.loads(data[source.MANIFEST])
    seal[field] = value
    raw = json.dumps(seal).encode()
    monkeypatch.setattr(source, 'MANIFEST_SHA256', hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError, match='POST44_SCOPE_CHANGED'):
        source.qualify(lambda key: raw if key == source.MANIFEST else data[key], paths)


def test_gateway_projection_preserves_latest_enterprise_parent(snapshot):
    data, _ = snapshot
    read, result = source.historical_view(data.__getitem__)
    expected = data[source.ARCHIVE + 'parent/backend/app/channels/manager.py']
    assert read('backend/app/channels/manager.py') == expected
    assert result['added_gateway_runtime'] == ['backend/packages/harness/deerflow/utils/conversation_url.py']
    with pytest.raises(KeyError):
        read('backend/packages/harness/deerflow/utils/conversation_url.py')
