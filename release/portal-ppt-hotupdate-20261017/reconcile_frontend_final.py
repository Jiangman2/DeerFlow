"""Reconcile completed immutable frontend receipts, without repeating execution."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MERGE = ROOT.parent.parent.parent / 'portal-ppt-merge-20261017'


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    path = MERGE / 'frontend-qualification-r3/FINAL-REPORT.json'
    assert sha(path) == 'fed9821ad99c17df02c881278cff540a97b16ff1caa72e76214021f8b3e60fea'
    report = json.loads(path.read_text())
    assert report['status'] == 'LOCAL_ARM64_EMULATED_BUILD_HTTP_AND_OCI_PASS'
    checked = []
    for row in report['evidence']:
        item = Path(row['path'])
        assert item.is_relative_to(MERGE) and item.stat().st_size == row['bytes']
        assert sha(item) == row['sha256'], item
        checked.append(row)
    oci = report['oci']
    archive = Path(oci['archive'])
    assert archive.stat().st_size == oci['bytes'] and sha(archive) == oci['sha256']
    assert report['http']['status'] == 'PASS' and len(report['http']['checks']) == 14
    assert report['version_binding']['status'] == 'PASS'
    assert report['fixture_cleanup_complete'] and report['resources']['builder_stopped_cache_retained']
    assert report['source_delta']['changed_product_files_since_r2'] == 1
    assert report['source_delta']['unchanged_files'] == 666
    assert report['image']['descriptor']['digest'] == oci['exact_qualified_identity']['manifest_digest']
    assert all(oci[key] == 'PASS' for key in ('exact_qualified_layers', 'blob_and_diffid_hashes'))
    receipt = {
        'schema': 'portal-ppt-frontend-independent-final-reconciliation.v1',
        'status': 'EXACT_BUILD_HTTP_SOURCE_AND_OCI_RECEIPTS_RECONCILED',
        'report': {'path': str(path), 'sha256': sha(path)},
        'evidence_count': len(checked),
        'archive': {'path': str(archive), 'sha256': oci['sha256'], 'bytes': oci['bytes']},
        'source_identity': {
            'base_merged_source_manifest': str(MERGE / 'MERGED-SOURCE-MANIFEST.json'),
            'successor_patch': str(MERGE / 'frontend-projected-fix-r1/projected-containment.patch'),
            'final_context_manifest': str(MERGE / 'frontend-inputs-r3/CONTEXT-MANIFEST.json'),
        },
        'source_and_runtime_tests_not_rerun': True,
        'native_arm64': False,
        'enterprise_integration_tested': False,
        'production_mutation': False,
        'ready_for_rollout': False,
    }
    with (ROOT / 'FRONTEND-FINAL-RECONCILIATION.json').open('x', encoding='utf-8', newline='\n') as target:
        json.dump(receipt, target, indent=2)
        target.write('\n')
    print(json.dumps({'status': receipt['status'], 'evidence_count': len(checked), 'archive_sha256': oci['sha256']}))


if __name__ == '__main__':
    main()
