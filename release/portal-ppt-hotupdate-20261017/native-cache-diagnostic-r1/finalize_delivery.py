"""Record the reviewed read-only diagnostic and copy one small operator file."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOWNLOAD = Path('C:/Users/15285/Downloads/portal-ppt-native-cache-20261017-r1')
NAME = 'collect-portal-ppt-native-cache-20261017-r1.py'


def ref(path):
    raw = path.read_bytes()
    return dict(path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def write(path, value):
    raw = (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()
    if path.exists():
        assert path.read_bytes() == raw, str(path)
    else:
        with path.open('xb') as stream:
            stream.write(raw)


build = json.loads((HERE / 'BUILD.json').read_bytes())
script = ref(HERE / 'collect_cache.py')
assert script['sha256'] == build['script_sha256']
assert 'Ran 5 tests' in (HERE / 'SOURCE-SCAN-LINUX.log').read_text(encoding='utf-8-sig')
parser = json.loads((HERE / 'PARSER-TESTS-VERIFICATION.json').read_bytes())
review = dict(
    schema='portal-ppt-native-cache-diagnostic-delivery-review.v1',
    status='PASS_FOR_FIXED_READ_ONLY_CACHE_DIAGNOSTIC',
    script=script, catalog=ref(HERE / 'CATALOG.json'), catalog_review=ref(HERE / 'CATALOG-REVIEW.json'),
    parser_test_receipt=ref(HERE / 'PARSER-TESTS-VERIFICATION.json'),
    parser_tests_passed=13, source_scan_tests_passed=5,
    source_scan_tests=ref(HERE / 'test_source_scan.py'), source_scan_log=ref(HERE / 'SOURCE-SCAN-LINUX.log'),
    build=ref(HERE / 'BUILD.json'),
    independent_review_findings_resolved=[
        'Require stable filesystem devices/capacities and a separate opt device before estimating imagefs budget',
        'Install TERM/HUP handlers so existing helper finally blocks clean up owned process groups and terminal state',
        'Account maximum actual outer blob as temporary ingest scratch, even when cached',
        'Only intersect content and complete committed snapshot ancestry from both observations',
        'Preserve verified aggregate bounds for Frontend layers without inventing per-layer exact expansion',
        'Use integer chain references; verify full wire-plan roundtrip and bounded SSH argv'],
    local_verification_scope='Small local fixtures and metadata only; no candidate runtime execution or production access',
    source_seals_reused_without_copy_or_full_rehash=True,
    remote_apis=['ctr content ls with exact digest filters', 'ctr snapshots overlayfs ls then relevant projection',
                 'crictl imagefsinfo', 'findmnt/statvfs', 'read exact prior failure and frozen helper/profile'],
    writes='Fresh private diagnostic evidence and separate cnooc-owned report handoff only',
    archive_transfer=False, image_import=False, image_deletion=False,
    production_workload_mutation=False, database_access=False, model_access=False,
    cache_observation_is_not_reservation=True, automatic_retry=False,
    ready_for_import=False, ready_for_rollout=False,
    references=[
        'https://raw.githubusercontent.com/containerd/containerd/v2.3.3/cmd/ctr/commands/content/content.go',
        'https://raw.githubusercontent.com/containerd/containerd/v2.3.3/cmd/ctr/commands/snapshots/snapshots.go',
        'https://raw.githubusercontent.com/containerd/containerd/v2.3.3/core/images/archive/importer.go',
        'https://raw.githubusercontent.com/containerd/containerd/v2.3.3/docs/garbage-collection.md'])
write(HERE / 'ROOT-DELIVERY-REVIEW.json', review)
DOWNLOAD.mkdir(exist_ok=True)
destination = DOWNLOAD / NAME
raw = (HERE / 'collect_cache.py').read_bytes()
if destination.exists():
    assert destination.read_bytes() == raw
else:
    with destination.open('xb') as stream:
        stream.write(raw)
delivery = dict(schema='portal-ppt-native-cache-diagnostic-delivery.v1',
                status='READY_FOR_MANUAL_SINGLE_FILE_COPY', local_file=str(destination),
                remote_file='/home/cnooc/' + NAME,
                command='sudo python3 -I -B -u /home/cnooc/' + NAME + ' --collect',
                bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                review=ref(HERE / 'ROOT-DELIVERY-REVIEW.json'),
                archives_reupload_required=False, server_result_pending=True,
                automatic_retry=False, ready_for_rollout=False)
write(HERE / 'DELIVERY.json', delivery)
print(json.dumps(delivery))
