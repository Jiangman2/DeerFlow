"""Offline provenance checks; no production commands or provider requests."""
import copy
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'deploy/enterprise'))
sys.path.insert(0, str(ROOT/'scripts'))
import released_sync_source_20261110 as source
import review_energy_market_original as review
import review_changed_public_skills as runner


def snapshot():
    paths = [p.relative_to(ROOT).as_posix() for p in (ROOT/source.ENERGY).rglob('*')
             if p.is_file() and '__pycache__' not in p.parts]
    return lambda p:(ROOT/p).read_bytes(), paths


def test_actual_release_and_original_zip_match():
    result = source.verify(*snapshot())
    assert result['energy_original_files'] == 158
    assert not result['energy_full_report_accepted']
    assert not result['production_commands_executed']


@pytest.mark.parametrize('path', [source.MANIFEST, source.ENERGY+'SKILL.md',
    source.ENERGY+'scripts/entry.py', source.ENERGY+'upstream/README.md',
    source.ENERGY+'assets/energy-daily-v1.0.original.zip',
    'deploy/enterprise/energy_market_collector.py',
    source.FIELD+'proxy-activation.normalized.json',
    source.HUB+'edge-bound-20261106-r2/REPORT-RECONSTRUCTED.json'])
def test_changed_bytes_are_rejected(path):
    read, paths = snapshot()
    if not (ROOT / path).is_file():
        path = source.successor.ARCHIVE + 'parent/' + path
    with pytest.raises(ValueError):
        source.verify(lambda p:read(p)+b'changed' if p==path else read(p), paths)


def test_missing_or_additional_energy_files_are_rejected():
    read, paths = snapshot()
    for changed in (paths[:-1],paths+[source.ENERGY+'injected.py']):
        with pytest.raises(ValueError, match='ENERGY_FILE_SET_CHANGED'):
            source.verify(read,changed)


def facts():
    return {'completeness':{},'findings':[dict(zip(review.FIELDS,x)) for x in review.ALLOWED]}


def test_exact_scanner_facts_only():
    original = facts()
    assert review.reviewed(original)
    for changed in (dict(original,findings=original['findings'][:-1]),
                    dict(original,findings=original['findings']+[original['findings'][0]]),
                    dict(original,completeness={'truncated':True}),
                    dict(original,completeness={'not_assessed':['entry.py']})):
        assert not review.reviewed(changed)
    for key,value in (('path','new.py'),('rule_id','new-danger'),('line',999),('severity','blocker')):
        changed = copy.deepcopy(original)
        changed['findings'][0][key] = value
        if changed != original:
            assert not review.reviewed(changed)


def test_upstream_instructions_are_reviewed_with_whole_sealed_package():
    from pathlib import PurePosixPath
    assert runner.find_public_skill_package(
        PurePosixPath(source.ENERGY+'upstream/agent/SKILL.md'), ROOT
    ) == PurePosixPath(source.ENERGY)
    assert runner.find_public_skill_package(
        PurePosixPath('skills/public/research-fund-settlement/SKILL.md'), ROOT
    ) == PurePosixPath('skills/public/research-fund-settlement')
