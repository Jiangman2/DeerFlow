"""Freeze the reviewed small manual-copy delivery; no server operation."""
import ast
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
PREP=HERE.parent
DEST=Path('C:/Users/15285/Downloads/portal-ppt-native-r3-local-resume-20261017')
NAME='resume-portal-ppt-native-20261017-r3-local.py'
SCRIPT_SHA='81c069d566023c68301498742906cfc5f78207e70a8e0d03ef0d0632ff7d2e2d'

def sha(raw): return hashlib.sha256(raw).hexdigest()
def ref(path):
    raw=path.read_bytes()
    return dict(path=str(path),bytes=len(raw),sha256=sha(raw))
def write(path,value):
    path.write_bytes((json.dumps(value,sort_keys=True,indent=2,ensure_ascii=True)+'\n').encode())

raw=(HERE/'resume_target.py').read_bytes()
assert len(raw)==12814 and sha(raw)==SCRIPT_SHA
ast.parse(raw,feature_version=(3,7))
qualification=json.loads((HERE/'QUALIFICATION.json').read_bytes())
assert qualification['files']['resume_target.py']['sha256']==SCRIPT_SHA
assert qualification['status']=='PASS_LOCAL_PREPARATION_ONLY'
assert qualification['verification']['passed']==10 and qualification['verification']['failed']==0
assert qualification['verification']['skipped']==0
review=json.loads((HERE/'INDEPENDENT-REVIEW.json').read_bytes())
assert review['status']=='PASS'
assert SCRIPT_SHA in json.dumps(review) and not review.get('blockers')
for row in list(qualification['files'].values())+list(qualification['fixed_evidence'].values()):
    assert ref(Path(row['path']))==row
assert sha((PREP/'native-preload-r3/preload_native_r3.py').read_bytes())=='052c7001dfb8215830a7cb54ef26410955b88136810d1869fe6627bd80158157'

DEST.mkdir(exist_ok=True)
for name,data in ((NAME,raw),('RUNBOOK.txt',(HERE/'RUNBOOK.txt').read_bytes())):
    path=DEST/name
    if path.exists(): assert path.read_bytes()==data
    else:
        with path.open('xb') as stream: stream.write(data)
command='sudo python3 -I -B -u /home/cnooc/'+NAME+' --resume-on-target'
delivery=dict(schema='portal-ppt-native-r3-local-resume-delivery.v1',
    status='READY_FOR_MANUAL_TARGET_LOCAL_CONTINUATION',
    script=ref(HERE/'resume_target.py'),delivered_script=ref(DEST/NAME),
    runbook=ref(DEST/'RUNBOOK.txt'),qualification=ref(HERE/'QUALIFICATION.json'),
    independent_review=ref(HERE/'INDEPENDENT-REVIEW.json'),
    binding=ref(PREP/'native-preload-attempt-r3/RESUME-BINDING.json'),
    operation='abf96dde50174b1eb7caf70a4e6711b1',execution_host='datax-02',
    execution_ip='192.168.111.37',server_path='/home/cnooc/'+NAME,command=command,
    archive_retransfer=False,new_ssh_or_scp=False,automatic_retry=False,
    existing_images_deleted=False,workload_mutation=False,database_access=False,model_access=False,
    server_executed=False,native_runtime_passed=False,ready_for_rollout=False)
write(HERE/'DELIVERY.json',delivery)
root_review=dict(schema='portal-ppt-native-r3-local-resume-root-review.v1',
    status='REVIEWED_FOR_ONE_OPERATOR_TARGET_LOCAL_CONTINUATION',
    script=ref(HERE/'resume_target.py'),delivered_script=ref(DEST/NAME),
    qualification=ref(HERE/'QUALIFICATION.json'),independent_review=ref(HERE/'INDEPENDENT-REVIEW.json'),
    source_failure=ref(PREP/'native-preload-attempt-r3/REPORT.json'),
    prepared_receipt=ref(PREP/'native-preload-attempt-r3/PREPARED.json'),
    delivery=ref(HERE/'DELIVERY.json'),
    reviewed=[
        'Exact 6274-byte operator source report reconstructed and announced SHA verified',
        'Original import request uncertainty retained until locked target state proof',
        'Frozen derived target code and old dependencies loaded by exact hashes',
        'Local public host key and architecture checked; no new SSH authentication',
        'Original common lock held through guard, unchanged import and report finalization',
        'Strict two-receipt target state and exclusive successor marker prevent duplicate imports',
        'Original six-archive full hashing, budget gates, two-phase imports and final identities retained',
        'Failures preserve old evidence and scoped process cleanup; safe report handoff reused',
        'Ten targeted Linux guard fixtures passed, without repeating existing import suites',
        'No change to production workloads, database, model calls or existing image deletion'],
    limitations=[
        'Server execution and current target state remain operator steps',
        'Fresh Kubernetes Node UID and scheduler/runtime checks remain required before native workloads',
        'Storage allowance is not a reservation; existing live checks may stop this attempt',
        'COMPLETE_PRELOAD alone is not native runtime or rollout acceptance'],
    native_runtime_passed=False,ready_for_rollout=False)
write(HERE/'ROOT-DELIVERY-REVIEW.json',root_review)
print(json.dumps(dict(delivered_script=ref(DEST/NAME),command=command,ready_for_rollout=False),sort_keys=True))
