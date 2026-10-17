"""Record locally observed controller qualification and exact dependency chain."""
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
PREP=HERE.parent
def pin(path):
    raw=path.read_bytes()
    return dict(path=str(path).replace('\\','/'),bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
build=json.loads((HERE/'BUILD.json').read_bytes())
execution=json.loads((HERE/'LOCAL-FINAL-EXECUTION.json').read_bytes())
assert execution['status']=='PASS' and execution['returncode']==0
delta=json.loads((HERE/'LOCAL-DELTA-EXECUTION.json').read_bytes()) if (HERE/'LOCAL-DELTA-EXECUTION.json').exists() else None
if delta:
    assert delta['status']=='PASS' and delta['returncode']==0 and delta['base_script']==execution['script']
    assert delta['script']==dict(bytes=build['script']['bytes'],sha256=build['script']['sha256'])
else:
    assert execution['script']==dict(bytes=build['script']['bytes'],sha256=build['script']['sha256'])
assert pin(HERE/'run_native.py')['sha256']==build['script']['sha256']
files={name:pin(HERE/name) for name in ['run_native.py','controller_template.py','build_controller.py','test_controller.py','run_local_tests.py',
    'LOCAL-FINAL-EXECUTION.json','LOCAL-FINAL.stdout.txt','LOCAL-FINAL.stderr.txt','BUILD.json','db_stage.py']}
if delta:
    files['LOCAL-DELTA-EXECUTION.json']=pin(HERE/'LOCAL-DELTA-EXECUTION.json')
    files['BASE21-QUALIFICATION.json']=pin(HERE/'BASE21-QUALIFICATION.json')
dependencies={
    'host_frontend_helper':pin(PREP/'native-runtime-host-frontend-r1/host_frontend_stage.py'),
    'host_frontend_qualification':pin(PREP/'native-runtime-host-frontend-r1/QUALIFICATION.json'),
    'host_frontend_review':pin(PREP/'native-runtime-host-frontend-r1/INDEPENDENT-REVIEW.json'),
}
value=dict(schema='portal-native-controller-qualification.v1',status='PASS_LOCAL_FIXTURES_REMOTE_EXECUTION_PENDING',
    script=pin(HERE/'run_native.py'),files=files,dependencies=dependencies,
    fixture_count=21+(delta['fixture_count'] if delta else 0),base_full_execution=execution,final_delta_execution=delta,
    scope='Controller local mocks plus real isolated child subprocess exit/interrupt/timeout/output/descendant fixtures. Existing network verifier reused, no real remote Kubernetes/CNI/runtime/database/model execution.',
    qualified_behaviors=[
        'Fresh fixed node UID, Linux/ARM64, Ready, no pressures, no taints and scheduler binding',
        'Create-only unique namespace plus exact known UID lifecycle; unknown create outcomes retained, no adoption/retry/deletion',
        'Strict admitted Pod security/image/command/env/volume/resource intent with narrowly allowed built-in defaults',
        'Namespace and object UID or owner replacement prevents read/exec/deletion',
        'UID-precondition cleanup of exact created UID even when admission rejected its spec',
        'Existing network checker receives real orchestration-shaped baseline and three final bidirectional denial rounds',
        'Failed baseline, unproven connected protocol, one-direction denial and additive policies never qualify',
        'Live listener and actual Pod/container/image/node identities bound across policy withdrawal',
        'Host then frontend then PostgreSQL safe-profile and five native DB probes execute serially in same owned namespace',
        'Frontend projected init plus main verified, main terminal receipt selected correctly',
        'Actual container image IDs, restartCount zero, terminal exits, PG sidecar ordered shutdown gates',
        '120-second scheduling bound distinct from started init-chain total deadline',
        'Safe production identity/image/resource projection server-side, no Env or full Deployment reads',
        'Bounded private command stderr and stage logs preserve actual nonzero exit, timeout, interruption and owned descendant cleanup',
        'Failure stops next stage; owned diagnostics recorded before cleanup; retained deny-all verified as final success gate',
    ],
    historical_test_fixture_correction='One orchestration stub used a guessed Pod name for host role; changed fixture-only classification to actual terminal container key. Final full 21-fixture run on exact delivered candidate passed.',
    remote_kubernetes_execution=False,remote_cni_enforcement_proven=False,native_runtime_passed=False,
    production_mutation=False,production_database_access=False,model_access=False,ready_for_rollout=False)
(HERE/'QUALIFICATION.json').write_text(json.dumps(value,sort_keys=True,indent=2)+'\n',encoding='utf-8',newline='\n')
print(json.dumps(pin(HERE/'QUALIFICATION.json'),sort_keys=True))
