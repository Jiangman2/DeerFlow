"""Freeze the reviewed HTML-only change and seal its Skill-only installer."""
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('html_warning_seal', HERE / 'seal.py')
seal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seal)


def freeze():
    files = {path.relative_to(seal.CANDIDATE).as_posix(): path.read_bytes()
             for path in seal.CANDIDATE.rglob('*') if path.is_file()
             and path.relative_to(seal.CANDIDATE).as_posix() not in seal.EXCLUDED_LOCAL_FILES}
    assert len(files) == 106
    assert not any('__pycache__' in name or name.endswith('.pyc') for name in files)
    fixtures = {name: (seal.REPO / 'portable-skills/tests' / name).read_bytes() for name in seal.FIXTURE_NAMES}
    for name in seal.PRIOR_SOURCE_NAMES:
        fixtures['published-2.1.2/scripts/' + name] = (seal.REPO / 'portable-skills/candidates/energy-market-daily-2.1.2/scripts' / name).read_bytes()
    identity = lambda raw: {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    review = {'schema': 'energy-programmatic-reviewed-source.v1', 'version': seal.VERSION,
              'source_freeze_authorized': True, 'production_executed': False,
              'offline_suites': seal.OFFLINE_SUITES, 'offline_test_count': seal.OFFLINE_TEST_COUNT,
              'files': {name: identity(raw) for name, raw in files.items()},
              'fixtures': {name: identity(raw) for name, raw in fixtures.items()}}
    preservation = {'schema': 'energy-programmatic-core-source-preservation.v1',
                    'comparison_basis': 'PUBLISHED_2.1.3_ADAPTATION_NOT_ORIGINAL_AUTHOR_FULL_WORKFLOW',
                    'prior_version': '2.1.3-portable', 'candidate_version': seal.VERSION,
                    'all_five_files_byte_identical': True, 'files': {}}
    for name in seal.PRIOR_SOURCE_NAMES:
        if name == 'daily.py':
            continue
        prior = (seal.REPO / 'portable-skills/candidates/energy-market-daily-2.1.3/scripts' / name).read_bytes()
        current = files['scripts/' + name]
        assert prior == current
        preservation['files'][name] = {'bytes': len(prior), 'prior_sha256': seal.sha(prior), 'candidate_sha256': seal.sha(current)}
    for name, value in [('REVIEWED-SOURCE.json', review), ('CORE-SOURCE-PRESERVATION.json', preservation)]:
        target = HERE / name
        raw = (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()
        if target.exists():
            assert target.read_bytes() == raw, 'EXISTING_FREEZE_DIFFERS'
        else:
            target.write_bytes(raw)
    output, metadata = seal.build(seal.REPO / 'release/task-conversation-context-postcheck-20261114-r2/OPERATOR-RUNTIME-ACCEPTANCE.json')
    seal.write_output(output, seal.RELEASE)
    print(json.dumps({'version': seal.VERSION, 'release': str(seal.RELEASE), 'files': metadata['files']}, sort_keys=True))


if __name__ == '__main__':
    freeze()
