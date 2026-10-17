"""Publish the reviewed local single-file handoff; does not contact servers."""
import datetime
import hashlib
import json
from pathlib import Path
import shutil

HERE=Path(__file__).resolve().parent
PREP=HERE.parent
DOWNLOAD=Path('C:/Users/15285/Downloads/portal-ppt-native-preload-20261017-r3')
NAME='preload-portal-ppt-native-20261017-r3.py'

def ref(path):
    raw=path.read_bytes()
    return dict(path=str(path),bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())

def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')

def main():
    script=HERE/'preload_native_r3.py'
    qualification=HERE/'QUALIFICATION.json'
    independent=HERE/'INDEPENDENT-REVIEW.json'
    assert qualification.is_file() and independent.is_file(), 'final qualification/review required'
    # Root invokes this only after inspecting both exact-script-bound receipts.
    raw=script.read_bytes();compile(raw,str(script),'exec')
    receipt=json.loads(qualification.read_bytes())
    assert receipt['status']=='PASS_OFFLINE_PREPARATION_ONLY'
    assert receipt['files']['preload_native_r3.py']['sha256']==hashlib.sha256(raw).hexdigest()
    assert receipt['files']['preload_native_r3.py']['bytes']==len(raw)
    assert receipt['files']['budget_runtime.py']['sha256']==ref(HERE/'budget_runtime.py')['sha256']
    independent_receipt=json.loads(independent.read_bytes())
    assert independent_receipt['status']=='PASS_OFFLINE_PREPARATION_FOR_FIXED_SINGLE_CONTINUATION'
    assert not independent_receipt['blocking_findings']
    assert independent_receipt['files']['preload_native_r3.py']['sha256']==hashlib.sha256(raw).hexdigest()
    assert b'@@ALLOCATION_MODULE' not in raw and len(raw)<256*1024
    DOWNLOAD.mkdir(exist_ok=True)
    destination=DOWNLOAD/NAME
    if destination.exists():assert destination.read_bytes()==raw, 'different delivered file already exists'
    else:shutil.copyfile(script,destination)
    assert destination.read_bytes()==raw
    runbook=DOWNLOAD/'RUNBOOK.txt'
    if runbook.exists():assert runbook.read_bytes()==(HERE/'RUNBOOK.txt').read_bytes()
    else:shutil.copyfile(HERE/'RUNBOOK.txt',runbook)
    review=dict(schema='portal-ppt-native-r3-root-delivery-review.v1',
        status='REVIEWED_FOR_ONE_OPERATOR_PRELOAD_ATTEMPT',updated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        script=ref(script),delivered_script=ref(destination),qualification=ref(qualification),independent_review=ref(independent),
        budget_module=ref(HERE/'budget_runtime.py'),budget_tests=ref(HERE/'test_budget.py'),budget_tests_passed=8,
        report=ref(PREP/'native-cache-evidence-r1/REPORT.json'),full_report_review=ref(PREP/'native-cache-evidence-r1/FULL-REPORT-REVIEW.json'),
        two_phase_official_source_review=ref(PREP/'native-cache-evidence-r1/TWO-PHASE-IMPORT-REVIEW.json'),
        allocation_scan=ref(PREP/'native-allocation-review-r1/ALLOCATION.json'),
        hardlink_review=ref(PREP/'native-allocation-review-r1/HARDLINK-REVIEW.json'),
        physical_missing_chain_allowance_bytes=3496771584,extra_metadata_allowance_bytes=67108864,
        reserve_rule='max(20GiB, ceil(filesystem_capacity/5)) plus 2GiB headroom',
        storage_estimate_is_reservation=False,strict_filesystem_upper_bound=False,
        real_server_plan_rechecked_at_execution=True,live_storage_and_cache_checks=True,
        source_seals_reused_without_recopied_archives=True,target_archive_storage='/opt',
        no_automatic_retry=True,old_failures_preserved=True,
        user_old_image_archive_authorization='Conditional fallback after identifying unused non-rollback images and verifying archives; no deletion performed.',
        production_workload_mutation=False,production_database_mutation=False,old_image_deletion=False,
        native_runtime_passed=False,ready_for_rollout=False,
        pending=['Operator runs r3 once and returns report','Native ARM64 isolated runtime/database compatibility fixtures',
                 'Enterprise end-to-end acceptance and backup/restore/release procedure'])
    write(HERE/'ROOT-DELIVERY-REVIEW.json',review)
    delivery=dict(schema='portal-ppt-native-two-phase-preload-delivery.v3',status='READY_FOR_SINGLE_FILE_MANUAL_TRANSFER',
        local_file=str(destination),bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),
        remote_file='/home/cnooc/'+NAME,command='sudo python3 -I -B -u /home/cnooc/'+NAME+' --distribute',
        root_review_sha256=ref(HERE/'ROOT-DELIVERY-REVIEW.json')['sha256'],upload_files=1,
        archives_reupload_required=False,offline_unpack_repeat_required=False,server_execution_pending=True,
        image_deletion=False,ready_for_rollout=False)
    write(HERE/'DELIVERY.json',delivery)
    print(json.dumps(delivery,ensure_ascii=False))

if __name__=='__main__':main()
