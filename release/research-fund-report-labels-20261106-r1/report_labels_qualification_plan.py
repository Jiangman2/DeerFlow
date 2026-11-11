"""Describe and bind synthetic Round3 qualification without running or publishing it.

No plan or qualifier is generated at import. The final candidate is caller-approved.
"""
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST_FILES = (
    'tests/skills/test_research_fund_report_labels.py',
    'tests/skills/test_research_fund_project_type_text.py',
    'tests/skills/test_research_fund_settlement.py',
    'tests/skills/test_research_fund_black_text.py',
    'tests/skills/test_research_fund_zero_budget.py',
    'tests/skills/test_research_fund_annual_budget.py',
    'tests/skills/test_research_fund_feedback.py',
    'tests/skills/test_research_fund_nov05_import.py',
    'tests/skills/test_research_fund_nov05_model.py',
    'tests/skills/test_research_fund_nov05_roundtrip.py',
    'tests/skills/test_research_fund_round3_acceptance.py',
    'tests/skills/test_research_fund_round3_reasons.py',
    'tests/skills/test_research_fund_round3_computed.py',
    'tests/skills/test_research_fund_round3_render.py',
    'tests/skills/test_research_fund_round3_integration.py',
)


def require(ok, code):
    if not ok:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def snapshot_tests(repo_root=ROOT):
    rows = []
    for name in TEST_FILES:
        path = Path(repo_root) / name
        require(path.is_file() and not path.is_symlink(), 'QUALIFICATION_SOURCE_REQUIRED')
        before = path.stat()
        require(before.st_size <= 2097152, 'QUALIFICATION_SOURCE_SIZE')
        raw = path.read_bytes()
        after = path.stat()
        require((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
                and len(raw) == before.st_size, 'QUALIFICATION_SOURCE_CHANGED_DURING_READ')
        rows.append({'path': name, 'bytes': len(raw), 'sha256': sha(raw)})
    return rows


def plan(candidate_tree_sha256, repo_root=ROOT):
    require(isinstance(candidate_tree_sha256, str) and len(candidate_tree_sha256) == 64
            and all(c in '0123456789abcdef' for c in candidate_tree_sha256),
            'FINAL_CANDIDATE_APPROVAL_REQUIRED')
    return {'schema': 'research-fund-report-labels-qualification-plan.v1',
            'status': 'PLAN_ONLY_NO_EXECUTION', 'candidate_tree_sha256': candidate_tree_sha256,
            'test_source_files': snapshot_tests(repo_root), 'executed': False,
            'production_bundle_sealed': False}


def literal(source, name):
    tree = ast.parse(source)
    found = [node for node in tree.body if isinstance(node, ast.Assign)
             and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)]
    require(len(found) == 1, 'QUALIFIER_BINDING_REQUIRED_' + name)
    return ast.literal_eval(found[0].value)


def validate_receipt(expected_plan, receipt, qualifier_source, approved_tree, repo_root=ROOT):
    require(expected_plan == plan(approved_tree, repo_root), 'QUALIFICATION_PLAN_OR_TEST_SOURCE_CHANGED')
    require(receipt.get('schema') == 'research-fund-report-labels-synthetic-qualification.v1'
            and receipt.get('status') == 'PASS', 'QUALIFICATION_PASS_REQUIRED')
    require(receipt.get('skill_tree_sha256') == approved_tree
            and receipt.get('test_source_files') == expected_plan['test_source_files'],
            'QUALIFICATION_CANDIDATE_OR_TEST_BINDING')
    require(isinstance(qualifier_source, str) and receipt.get('qualifier_sha256') == sha(qualifier_source.encode()),
            'QUALIFIER_SOURCE_CHANGED')
    require(type(receipt.get('passed')) is int and receipt['passed'] > 30
            and receipt.get('failed') == 0 and receipt.get('skipped') == 0,
            'QUALIFICATION_CHECKS_INCOMPLETE')
    require(receipt.get('production_actions') == 0 and receipt.get('user_files_read') is False
            and receipt.get('network_access') is False and receipt.get('provider_requests') == 0
            and receipt.get('database_access') is False, 'QUALIFICATION_SCOPE_CHANGED')
    require(literal(qualifier_source, 'EXPECTED_CHECKS') == receipt['passed']
            and literal(qualifier_source, 'CANDIDATE_TREE_SHA256') == approved_tree
            and literal(qualifier_source, 'TEST_SOURCE_MANIFEST_SHA256') == sha(canonical(expected_plan['test_source_files'])),
            'QUALIFIER_LITERAL_BINDING_CHANGED')
    require(receipt.get('assertions_enabled') is True and receipt.get('pytest_cases_sha256') == literal(qualifier_source, 'EXPECTED_CASES_SHA256')
            and receipt.get('collection') == literal(qualifier_source, 'EXPECTED_COLLECTION'), 'QUALIFICATION_PYTEST_PARITY_CHANGED')
    return receipt
