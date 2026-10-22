"""Freeze the small read-only operator delivery; no remote operation."""
import ast
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
PREP=HERE.parent
DEST=Path('C:/Users/15285/Downloads/portal-ppt-image-inventory-20261017-r1')
NAME='collect-portal-ppt-image-inventory-20261017-r1.py'
SCRIPT_SHA='aa5bc686e796bf48abc593d6282e61bdce263e02cb3c59d799236867a476f7e1'
def sha(raw): return hashlib.sha256(raw).hexdigest()
def ref(path):
    raw=path.read_bytes()
    return dict(path=str(path),bytes=len(raw),sha256=sha(raw))
def write(path,value): path.write_bytes((json.dumps(value,sort_keys=True,indent=2,ensure_ascii=True)+'\n').encode())

raw=(HERE/'collect_inventory.py').read_bytes()
assert len(raw)==21785 and sha(raw)==SCRIPT_SHA
ast.parse(raw,feature_version=(3,7))
q=json.loads((HERE/'QUALIFICATION.json').read_bytes())
assert q['status']=='PASS_OFFLINE_PREPARATION_ONLY' and q['tests']['unique_tests_passed']==8
assert q['tests']['failed']==q['tests']['skipped']==0
for row in q['files'].values(): assert ref(Path(row['path']))==row
review=json.loads((HERE/'INDEPENDENT-REVIEW.json').read_bytes())
assert review['status']=='PASS' and SCRIPT_SHA in json.dumps(review) and not review.get('blockers')
cluster=json.loads((HERE/'CLUSTER-PROTECTED-IMAGES.json').read_bytes())
assert cluster['kubectl_exit_code']==0 and len(set(cluster['images']))==cluster['image_count']==103
DEST.mkdir(exist_ok=True)
for name,data in ((NAME,raw),('RUNBOOK.txt',(HERE/'RUNBOOK.txt').read_bytes())):
    path=DEST/name
    if path.exists(): assert path.read_bytes()==data
    else:
        with path.open('xb') as stream:stream.write(data)
command='sudo python3 -I -B -u /home/cnooc/'+NAME+' --collect'
delivery=dict(schema='portal-native-image-inventory-delivery.v1',status='READY_FOR_MANUAL_READ_ONLY_COLLECTION',
    script=ref(HERE/'collect_inventory.py'),delivered_script=ref(DEST/NAME),runbook=ref(DEST/'RUNBOOK.txt'),
    qualification=ref(HERE/'QUALIFICATION.json'),independent_review=ref(HERE/'INDEPENDENT-REVIEW.json'),
    cluster_protection_observation=ref(HERE/'CLUSTER-PROTECTED-IMAGES.json'),
    execution_host='datax-02',execution_ip='192.168.111.37',server_path='/home/cnooc/'+NAME,command=command,
    image_export=False,image_import=False,image_delete=False,workload_mutation=False,
    database_access=False,model_access=False,new_ssh_or_scp=False,
    source_archives_read=False,server_executed=False,ready_for_deletion=False,ready_for_rollout=False)
write(HERE/'DELIVERY.json',delivery)
root_review=dict(schema='portal-native-image-inventory-root-review.v1',status='REVIEWED_FOR_READ_ONLY_COLLECTION',
    script=ref(HERE/'collect_inventory.py'),delivered_script=ref(DEST/NAME),
    qualification=ref(HERE/'QUALIFICATION.json'),independent_review=ref(HERE/'INDEPENDENT-REVIEW.json'),
    prior_report=ref(PREP/'native-preload-attempt-r3-local-resume/REPORT.json'),
    delivery=ref(HERE/'DELIVERY.json'),
    reviewed=['Four fixed metadata-only commands; stdout allowlists omit environment, labels, arguments and raw stderr',
              'Exact frozen dependencies and host public-key identity; existing original checkpoints remain read-only',
              'All 24 expected phase-A receipts and no phase-B required for expected-checkpoint state flag',
              'Fixed PLAN-WIRE hash and sealed directory device/inode projection collected for phase-B continuation',
              '30-second command limits, 180-second total budget and bounded reports; original subprocess cleanup reused',
              'Existing guarded report handoff reused; no archiving, import, prune or deletion command exists',
              '8 unique offline fixtures passed across 7-case initial suite and one added size-bound case',
              '103 operator-returned cluster current/revision image refs retained separately as a protection union'],
    limitations=['Inventory is not unused-image or deletion eligibility proof',
                 'Image sizes are not reclaimable disk bytes; selected alias/shared content references need review',
                 'Runtime inventories are observations, not an atomic reservation'],
    production_mutation=False,ready_for_deletion=False,ready_for_rollout=False)
write(HERE/'ROOT-DELIVERY-REVIEW.json',root_review)
print(json.dumps(dict(delivered_script=ref(DEST/NAME),command=command,ready_for_deletion=False),sort_keys=True))
