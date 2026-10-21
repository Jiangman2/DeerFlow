"""Record focused successor qualification without repeating original business tests."""
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
PREP=HERE.parent
def pin(path):
    raw=path.read_bytes()
    return dict(path=str(path).replace('\\','/'),bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
build=json.loads((HERE/'BUILD.json').read_bytes())
base=json.loads((PREP/'native-runtime-qualification-r1/BUILD.json').read_bytes())
execution=json.loads((HERE/'LOCAL-FINAL-EXECUTION.json').read_bytes())
assert execution['status']=='PASS' and execution['returncode']==0
assert execution['script']==dict(bytes=build['script']['bytes'],sha256=build['script']['sha256'])
assert pin(HERE/'run_native.py')['sha256']==build['script']['sha256']
assert build['payload_sha256']==base['payload_sha256']
assert pin(PREP/'native-runtime-qualification-r1/run_native.py')['sha256']=='58e32027eaf300df8330432c50dc9cace545ef5e4ab7073bd1743f3367e31b7d'
assert pin(PREP/'native-runtime-attempt-r1/REPORT.json')['sha256']=='fdd49101a4e8892b92945a30b7334f3f5b7d2436e13f590720f4a69f7f3b2a2c'
assert pin(PREP/'native-runtime-attempt-r1/CREATE-ERROR.txt')['sha256']=='dfdbf9ce2e5c8104d66ac46a0a92b9abe4e67a4eb381d3c7324d0d8591c5b9ad'
files={name:pin(HERE/name) for name in ['run_native.py','controller_template.py','build_controller.py','test_priority.py','run_local_tests.py',
    'LOCAL-FINAL-EXECUTION.json','LOCAL-FINAL.stdout.txt','LOCAL-FINAL.stderr.txt','BUILD.json','PRIOR-RECEIPT-FIXTURE.json']}
dependencies={
    'r1_controller':pin(PREP/'native-runtime-qualification-r1/run_native.py'),
    'r1_qualification':pin(PREP/'native-runtime-qualification-r1/QUALIFICATION.json'),
    'r1_independent_review':pin(PREP/'native-runtime-qualification-r1/INDEPENDENT-REVIEW.json'),
    'r1_failure':pin(PREP/'native-runtime-attempt-r1/REPORT.json'),
    'r1_create_error':pin(PREP/'native-runtime-attempt-r1/CREATE-ERROR.txt'),
    'host_frontend_helper':pin(PREP/'native-runtime-host-frontend-r1/host_frontend_stage.py'),
    'db_helper':pin(PREP/'native-runtime-qualification-r1/db_stage.py'),
}
value=dict(schema='portal-native-controller-priority-qualification.v1',status='PASS_LOCAL_PRIORITY_FIXTURES_REMOTE_EXECUTION_PENDING',
    revision=2,script=pin(HERE/'run_native.py'),files=files,dependencies=dependencies,
    fixture_count=12,execution=execution,embedded_payload_unchanged_from_r1=True,
    scope='Focused local priority-admission and cluster UID lifecycle mocks only. No Kubernetes server/CNI/model/database execution and no repeat of the qualified business probe suite.',
    qualified_behaviors=[
        'Exact prior failed 1771-byte report and 270-byte create-error read/hash preconditions',
        'New r2 once marker, lock and evidence directory; r1 bytes and marker untouched',
        'Unique owner-labeled cluster PriorityClass value=0/globalDefault=false/preemptionPolicy=Never',
        'Missing serialized globalDefault accepted only as false; changed value, policy, ownership or UID blocks subsequent Pods/runtime',
        'Every owned test Pod gains the same exact priorityClassName while retaining Never and existing scheduler/affinity/resource gates',
        'Single overall 30-second default-ServiceAccount metadata and exact-first-Pod server-dry-run readiness budget',
        'Only the fixed own-class informer-not-found Forbidden permits another dry-run; all other failure types stop',
        'Dry-run UID never adopted into actual object ledger or used for deletion; actual create remains once',
        'Unknown actual PriorityClass create receives one metadata readback and is neither adopted nor retried/deleted',
        'PriorityClass UID-precondition DELETE uses scheduling.k8s.io/v1 cluster-scoped route',
        'Class deletion follows all known Pod removals; unknown Pod create or Pod-cleanup failure retains class for review',
        'Frozen Host, Frontend, DB and network adapter payload unchanged; no image transfer or reimport',
    ],
    remote_kubernetes_execution=False,remote_cni_enforcement_proven=False,native_runtime_passed=False,
    production_mutation=False,production_database_access=False,model_access=False,ready_for_rollout=False)
(HERE/'QUALIFICATION.json').write_text(json.dumps(value,sort_keys=True,indent=2)+'\n',encoding='utf-8',newline='\n')
print(json.dumps(pin(HERE/'QUALIFICATION.json'),sort_keys=True))
