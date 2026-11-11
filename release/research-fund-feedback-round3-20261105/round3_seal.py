"""Offline binding only. No production command or distribution side effect."""
import argparse
import json
from pathlib import Path
import round3_release_builder as b

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
NAME = 'update-portal-research-fund-skill-round3-20261105-r1.py'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--approved-tree', required=True)
    args = parser.parse_args()
    plan = json.loads(b.read_regular(HERE/'qualification-plan.json'))
    receipt = json.loads(b.read_regular(HERE/'qualification-run.json'))
    b.require(receipt.get('status') == 'PASS' and receipt.get('assertions_enabled') is True, 'REAL_QUALIFIER_PASS_REQUIRED')
    source = b.read_regular(HERE/'round3_qualifier.py').decode()
    b.require(receipt.get('pytest_cases_sha256') == b.literal_binding(source, 'EXPECTED_CASES_SHA256')
              and receipt.get('collection') == b.literal_binding(source, 'EXPECTED_COLLECTION'),
              'PYTEST_CASE_PARITY_REQUIRED')
    receipt.update(schema='research-fund-round3-synthetic-qualification.v1',
                   qualifier_sha256=b.sha(source.encode()),
                   execution_environment={'network': 'none', 'repository_mount': 'readonly',
                      'image': 'sha256:a721a1d378e99172e6ae569c93c52e00fb2a17d6a0151c1aaae362d73277f5bf',
                      'synthetic_tmpfs': True})
    result = b.prepare_bundle(ROOT/'skills/public/research-fund-settlement',
                json.loads(b.read_regular(HERE.parent/'research-fund-feedback-20261105/candidate-manifest.json')),
                json.loads(b.read_regular(HERE/'candidate-manifest.json')),
                b.read_regular(HERE/'operator-preflight-r3.json'),
                b.read_regular(ROOT/'deploy/enterprise/inspect-portal-research-fund-skill-20261105-r3.py').decode(),
                source, receipt['passed'], approved_candidate_tree_sha256=args.approved_tree,
                qualification_plan=plan, qualification_receipt=receipt)
    outputs = {'qualification-receipt.json': json.dumps(receipt, sort_keys=True, indent=2)+'\n',
               'round3_remote_candidate.py': result['remote_source'], NAME: result['updater_source']}
    for name, value in outputs.items():
        target = HERE/name
        b.require(not target.exists() or b.read_regular(target) == value.encode(), 'EXISTING_SEAL_DIFFERS_'+name)
    for name, value in outputs.items():
        (HERE/name).write_bytes(value.encode())
    print(json.dumps({'status':'SEALED_OFFLINE_ONLY', 'tree_sha256':args.approved_tree,
        'production_actions':0,'files':[{'path':name,'bytes':len(value.encode()),'sha256':b.sha(value.encode())}
                                      for name,value in outputs.items()]},sort_keys=True))
if __name__ == '__main__':
    main()
