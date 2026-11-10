"""Bind the reviewed candidate to the operator's read-only preflight, offline."""
import ast
import base64
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OLD = Path('C:/Users/15285/.codex/worktrees/research-fund-report-labels-20261106/deer-flow')
APPROVED = '0b417f6540cbab2f35ce9bbde28efe0d9d214273bf4fbf9baf47734f9ea01c85'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    b = load('verified_expense_builder', HERE/'expense_purposes_release_builder.py')
    before = json.loads((ROOT/'evidence/baseline-manifest.json').read_bytes())
    after = json.loads((HERE/'candidate-manifest.json').read_bytes())
    qualifier = (HERE/'expense_purposes_qualifier.py').read_text(encoding='utf8')
    run = json.loads((ROOT/'evidence/linux-qualification-run.json').read_bytes())
    assert run['schema'] == 'research-fund-expense-purposes-synthetic-qualification-run.v1'
    assert (run['passed'], run['failed'], run['skipped']) == (320, 0, 0)
    receipt = dict(run, schema='research-fund-expense-purposes-synthetic-qualification.v1',
                   qualifier_sha256=b.sha(qualifier.encode()))
    result = b.prepare_bundle(
        ROOT/'skills/public/research-fund-settlement', before, after,
        (HERE/'operator-preflight-20261110.json').read_bytes(),
        (ROOT/'deploy/enterprise/inspect-portal-research-fund-expense-purposes-20261110-r1.py').read_text(encoding='utf8'),
        qualifier, json.loads((HERE/'qualification-plan.json').read_bytes()), receipt,
        approved_candidate_tree_sha256=APPROVED)
    assert result['production_actions'] == result['files_written'] == 0
    ast.parse(result['updater_source'], feature_version=(3, 7))
    ast.parse(result['remote_source'], feature_version=(3, 7))
    target = HERE/'bound-package'
    target.mkdir()
    def write(name, value):
        data = value.encode('utf8') if isinstance(value, str) else value
        with (target/name).open('xb') as handle:
            handle.write(data)
    write('qualification-receipt.json', b.canonical(receipt)+b'\n')
    write('sealed-bundle.json', b.canonical(result['bundle'])+b'\n')
    filename = 'update-portal-research-fund-expense-purposes-20261110-r1.py'
    write(filename, result['updater_source'])
    write('installed_remote.py', result['remote_source'])
    qa = ROOT/'evidence/local-install'
    qa.mkdir()
    inputs = {
        'installed_remote.py': result['remote_source'].encode(),
        'before-manifest.json': b.canonical(before)+b'\n',
        'after-manifest.json': b.canonical(after)+b'\n',
        'before.tar.gz': b.archive_for(OLD/'skills/public/research-fund-settlement', before),
        'candidate.tar.gz': base64.b64decode(result['bundle']['archive_b64']),
    }
    harness = (OLD/'release/research-fund-report-labels-20261106-r1/local-install-harness.py').read_text(encoding='utf8')
    harness = harness.replace('research-fund-report-labels-local-install-qualification.v1', 'research-fund-expense-purposes-local-install-qualification.v1').replace("'passed':304", "'passed':320")
    inputs['local-install-harness.py'] = harness.encode()
    for name, raw in inputs.items():
        with (qa/name).open('xb') as handle:
            handle.write(raw)
    raw = (target/filename).read_bytes()
    print(json.dumps({'status': 'BOUND_NOT_DEPLOYED', 'candidate_tree': APPROVED,
                      'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                      'baseline_sha256': result['bundle']['baseline_receipt_sha256'],
                      'production_actions': 0}, sort_keys=True))


if __name__ == '__main__':
    main()
