"""Bind original behavior-test bytes and actual pytest collection to a small runner."""
import base64, gzip
from pathlib import Path
import adjusted_budget_qualification_plan as q
import adjusted_budget_release_builder as b
HERE = Path(__file__).resolve().parent

def build(plan, collection, repo_root=q.ROOT):
    b.require(plan == q.plan(plan['candidate_tree_sha256'], repo_root), 'QUALIFIER_PLAN_CHANGED')
    b.require(collection.get('status') == 'COLLECTED' and collection.get('exitcode') == 0
              and collection.get('test_source_files') == plan['test_source_files'], 'REAL_PYTEST_COLLECTION_REQUIRED')
    counts = collection['collection']
    b.require(isinstance(counts, dict) and all(type(v) is int and v > 0 for v in counts.values()), 'COLLECTION_COUNT_REQUIRED')
    expected = sum(counts.values())
    b.require(expected == collection['collected'] and expected > 30, 'COLLECTION_TOTAL_CHANGED')
    b.require(len(collection['cases']) == expected and b.sha(b.canonical(collection['cases'])) == collection['cases_sha256'], 'COLLECTION_CASES_CHANGED')
    sources = {}
    for row in plan['test_source_files']:
        raw = b.read_regular(Path(repo_root)/row['path'])
        b.require(len(raw) == row['bytes'] and b.sha(raw) == row['sha256'], 'TEST_SOURCE_CHANGED_DURING_PACK')
        sources[row['path']] = base64.b64encode(raw).decode()
    packed = base64.b64encode(gzip.compress(b.canonical(sources), mtime=0)).decode()
    source = (HERE/'adjusted_budget_qualifier_template.py').read_text(encoding='utf-8')
    for name, value in {'EXPECTED_CHECKS': expected, 'CANDIDATE_TREE_SHA256': plan['candidate_tree_sha256'],
                        'TEST_SOURCE_MANIFEST_SHA256': b.sha(b.canonical(plan['test_source_files'])),
                        'TEST_SOURCE_FILES': plan['test_source_files'], 'TEST_ARCHIVE': packed,
                        'EXPECTED_COLLECTION': counts, 'EXPECTED_CASES_SHA256': collection['cases_sha256']}.items():
        source = b.replace_binding(source, name, value)
    b.require(len(source.encode()) < 110000, 'QUALIFIER_ARGUMENT_BOUND')
    return source
