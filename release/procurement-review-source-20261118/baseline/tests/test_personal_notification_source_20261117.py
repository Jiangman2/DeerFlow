"""Reject drift in the accepted notification release and its historical view."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy/enterprise'))
import personal_notification_source_20261117 as source


@pytest.fixture(scope='module')
def data():
    raw = (ROOT / source.MANIFEST).read_bytes()
    seal = json.loads(raw)
    names = set(seal['files']) | {r['snapshot'] for r in seal['parent_files'].values()}
    values = {n: (ROOT / n).read_bytes() for n in names}
    values[source.MANIFEST] = raw
    return values, seal


def test_exact_accepted_source_and_separate_business_evidence(data):
    values, expected = data
    actual = source.qualify(values.__getitem__, full=True)
    assert actual == expected and actual['gateway_generation'] == 420
    assert actual['personal_work_business_accepted'] and actual['production_actions_by_pr'] == 0
    raw = values[source.ARCHIVE + 'OPERATOR-ACTIVATION-REPORT.json']
    assert len(raw) == 3517 and hashlib.sha256(raw).hexdigest() == '3f213d29113448de68fde26944a5e4fb7ff0e9ac9ed7682e8d2f8236460c2e0b'


@pytest.mark.parametrize('path', [
    source.MANIFEST, source.RUNTIME,
    source.ARCHIVE + 'deployed-overlay/' + source.RUNTIME,
    source.ARCHIVE + 'OPERATOR-ACTIVATION-REPORT.json',
    source.ARCHIVE + 'OPERATOR-RUNTIME-ACCEPTANCE-20261117.json',
    source.ARCHIVE + 'OPERATOR-BUSINESS-ACCEPTANCE-20261117.json',
    source.ARCHIVE + 'parent/' + source.RUNTIME,
    source.RELEASE + 'activate-energy-personal-notification-20261116-r1.py',
])
def test_changed_source_receipt_or_parent_is_rejected(data, path):
    values, _ = data
    with pytest.raises(ValueError):
        source.qualify(lambda n: values[n] + b'changed' if n == path else values[n], full=True)


def test_missing_manifest_cannot_accept_new_runtime(data):
    values, _ = data
    with pytest.raises(ValueError, match='PN420_MANIFEST_REQUIRED'):
        source.qualify(lambda n: (_ for _ in ()).throw(KeyError(n)) if n == source.MANIFEST else values[n])


def test_historical_projection_keeps_original_419_bytes_and_hides_new_release(data):
    values, seal = data
    read, _ = source.historical_view(values.__getitem__)
    for path, row in seal['parent_files'].items():
        assert read(path) == values[row['snapshot']]
    with pytest.raises(KeyError):
        read(source.MANIFEST)
    with pytest.raises(KeyError):
        read(source.RELEASE + 'SOURCE.json')
    assert b'async def _ready_daily_message(' not in read(source.RUNTIME)
    assert source.qualify(read) is None


def test_scope_cannot_change_concurrency_or_operator_acceptance(data, monkeypatch):
    values, original = data
    for field, value in [('scheduler_concurrency', 64), ('scheduler_budget', 300), ('personal_work_business_accepted', False)]:
        seal = dict(original, **{field:value})
        raw = json.dumps(seal, sort_keys=True).encode()
        monkeypatch.setattr(source, 'MANIFEST_SHA256', source.sha(raw))
        with pytest.raises(ValueError, match='PN420_SCOPE_CHANGED'):
            source.qualify(lambda n: raw if n == source.MANIFEST else values[n])


def test_approved_manifest_cannot_disguise_different_deployed_semantics(data, monkeypatch):
    values, original = data
    seal = json.loads(json.dumps(original))
    changed = values[source.RUNTIME] + b'\nunsupported_runtime_change = True\n'
    seal['files'][source.RUNTIME] = {'bytes': len(changed), 'sha256': source.sha(changed)}
    raw = json.dumps(seal, sort_keys=True).encode()
    monkeypatch.setattr(source, 'MANIFEST_SHA256', source.sha(raw))
    with pytest.raises(ValueError, match='PN420_DEPLOYED_SEMANTICS_CHANGED'):
        source.qualify(lambda n: raw if n == source.MANIFEST else changed if n == source.RUNTIME else values[n])


@pytest.mark.parametrize('reader', [
    'verify-dashboard-navigation-source-20261103.py', 'verify-nov01-released-source-20261102.py',
    'verify-nov02-released-source-20261103.py', 'verify-release-source-20261023.py',
])
@pytest.mark.parametrize('has_current', [False, True])
def test_each_git_archive_reader_includes_exact_current_evidence_only_when_present(reader, has_current, monkeypatch):
    spec = importlib.util.spec_from_file_location('current_reader_' + reader.replace('-', '_'), ROOT / 'deploy/enterprise' / reader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    captured = []

    class Selected(Exception):
        pass

    def git(argv, **kwargs):
        if argv[1] == 'ls-tree':
            return b'' if argv[-1] == source.MANIFEST and not has_current else b'present\n'
        if argv[1] == 'show':
            return (ROOT / argv[2].split(':', 1)[1]).read_bytes()
        if argv[1] == 'archive':
            captured.extend(argv)
            raise Selected()
        raise AssertionError('Unexpected non-read Git command')

    monkeypatch.setattr(module.subprocess, 'check_output', git)
    with pytest.raises(Selected):
        module.verify('chosen-ref')
    assert (source.ARCHIVE in captured) is has_current
