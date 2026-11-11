"""Explicit offline binding only; no cluster access and no import-time writes."""
import argparse
import json
from pathlib import Path

import report_labels_release_builder as builder

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
NAME = 'update-portal-research-fund-report-labels-20261106-r1.py'


def prepare(baseline_path, qa_path, approved_tree, runtime_review_path=None):
    before = json.loads(builder.read_regular(HERE.parent/'research-fund-project-type-20261105/candidate-manifest.json'))
    after = json.loads(builder.read_regular(HERE/'candidate-manifest.json'))
    baseline_raw = builder.read_regular(baseline_path)
    inspector = builder.read_regular(ROOT/'deploy/enterprise/inspect-portal-research-fund-report-labels-20261106-r1.py').decode()
    qualifier = builder.read_regular(qa_path/'report_labels_qualifier.py').decode()
    plan = json.loads(builder.read_regular(HERE/'qualification-plan.json'))
    qualified = json.loads(builder.read_regular(qa_path/'qualification-run.json'))
    builder.require(qualified.get('schema') == 'research-fund-report-labels-synthetic-qualification-run.v1', 'REAL_QUALIFICATION_RUN_REQUIRED')
    qualified = dict(qualified, schema='research-fund-report-labels-synthetic-qualification.v1', qualifier_sha256=builder.sha(qualifier.encode()))
    review = json.loads(builder.read_regular(runtime_review_path)) if runtime_review_path is not None else None
    result = builder.prepare_bundle(ROOT/'skills/public/research-fund-settlement', before, after, baseline_raw,
                                    inspector, qualifier, plan, qualified,
                                    approved_candidate_tree_sha256=approved_tree, runtime_review=review)
    return dict(result, qualified=qualified, qualifier_source=qualifier)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--qa', type=Path, required=True)
    parser.add_argument('--approved-tree', required=True)
    parser.add_argument('--runtime-review', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.baseline, args.qa, args.approved_tree, args.runtime_review)
    # Packaging is explicit and offline. A new directory prevents historical overwrite.
    args.output_dir.mkdir(mode=0o700)
    outputs = {NAME: result['updater_source'].encode(),
               'report_labels_qualifier.py': result['qualifier_source'].encode(),
               'qualification-receipt.json': builder.canonical(result['qualified']) + b'\n'}
    for name, data in outputs.items():
        with (args.output_dir/name).open('xb') as handle:
            handle.write(data)
    record = {'schema': 'research-fund-report-labels-package.v1', 'status': 'PACKAGED_NOT_DEPLOYED',
              'production_actions': 0, 'before_tree': builder.BEFORE_TREE, 'after_tree': args.approved_tree,
              'changed_skill_files': list(builder.ALLOWED_CHANGED), 'added_skill_files': list(builder.ALLOWED_ADDED),
              'baseline_sha256': result['bundle']['baseline_receipt_sha256'],
              'baseline_status': result['bundle']['baseline_status'], 'prior_release': builder.PRIOR_RELEASE,
              'files': [{'path': name, 'bytes': len(data), 'sha256': builder.sha(data)} for name, data in outputs.items()]}
    with (args.output_dir/'PACKAGE.json').open('xb') as handle:
        handle.write(builder.canonical(record) + b'\n')
    print(json.dumps(record, sort_keys=True))


if __name__ == '__main__':
    main()
