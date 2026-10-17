"""Bind focused r3 orchestration qualification to exact reviewed guard/helper bytes."""
import ast
import base64
import hashlib
import json
from pathlib import Path
import zlib

HERE=Path(__file__).resolve().parent
PREP=HERE.parent


def pin(path):
    raw=path.read_bytes()
    return dict(path=str(path).replace('\\','/'),bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())


def embedded(path):
    values={}
    for node in ast.parse(path.read_bytes()).body:
        if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name) and node.targets[0].id in ('PAYLOAD_SHA','PAYLOAD_Z64'):
            values[node.targets[0].id]=ast.literal_eval(node.value)
    raw=zlib.decompress(base64.b64decode(values['PAYLOAD_Z64'],validate=True))
    assert hashlib.sha256(raw).hexdigest()==values['PAYLOAD_SHA']
    return json.loads(raw)


build=json.loads((HERE/'BUILD.json').read_bytes())
execution=json.loads((HERE/'LOCAL-FINAL-EXECUTION.json').read_bytes())
assert execution['status']=='PASS' and execution['returncode']==0
assert execution['script']==dict(bytes=build['script']['bytes'],sha256=build['script']['sha256'])
assert execution['test']=={k:v for k,v in pin(HERE/'test_link_controller.py').items() if k!='path'}
assert pin(HERE/'run_native.py')['sha256']==build['script']['sha256']
old=embedded(PREP/'native-runtime-qualification-r2/run_native.py')
current=embedded(HERE/'run_native.py')
assert set(current)==set(old)|{'link_guard_stage.py'}
assert all(current[name]==value for name,value in old.items())
assert pin(PREP/'native-runtime-qualification-r2/run_native.py')['sha256']=='8a0f48570039680348d9a8ecb97b07eca875975a8a36f7778c8a5442d0c72273'
assert pin(PREP/'native-runtime-attempt-r2/REPORT.json')['sha256']=='cc10c4d5996ed6d7fa16e9fa70399d0efaded3530e1833b8df1369102eaec5e2'
assert current['link_guard_stage.py'].encode()==(PREP/'native-runtime-link-guard-r1/link_guard.py').read_bytes()
files={name:pin(HERE/name) for name in ['run_native.py','controller_template.py','build_controller.py','test_link_controller.py',
    'run_local_tests.py','record_qualification.py','LOCAL-FINAL-EXECUTION.json','LOCAL-FINAL.stdout.txt','LOCAL-FINAL.stderr.txt',
    'BUILD.json','PRIOR-RECEIPT-FIXTURE.json','R2-NETWORK-PROJECTION.json']}
dependencies={
    'r2_controller':pin(PREP/'native-runtime-qualification-r2/run_native.py'),
    'r2_qualification':pin(PREP/'native-runtime-qualification-r2/QUALIFICATION.json'),
    'r2_independent_review':pin(PREP/'native-runtime-qualification-r2/INDEPENDENT-REVIEW.json'),
    'r2_failure':pin(PREP/'native-runtime-attempt-r2/REPORT.json'),
    'r2_actual_network_projection':pin(PREP/'native-runtime-attempt-r2/NETWORK-PROJECTION.json'),
    'host_frontend_helper':pin(PREP/'native-runtime-host-frontend-r1/host_frontend_stage.py'),
    'db_helper':pin(PREP/'native-runtime-qualification-r1/db_stage.py'),
    'link_guard_helper':pin(PREP/'native-runtime-link-guard-r1/link_guard.py'),
    'link_guard_qualification':pin(PREP/'native-runtime-link-guard-r1/QUALIFICATION.json'),
    'link_guard_kernel_qualification':pin(PREP/'native-runtime-link-guard-r1/KERNEL-QUALIFICATION.json'),
    'link_guard_independent_review':pin(PREP/'native-runtime-link-guard-r1/INDEPENDENT-REVIEW.json'),
}
value=dict(schema='portal-native-controller-link-guard-qualification.v1',status='PASS_LOCAL_LINK_GUARD_ORCHESTRATION_REMOTE_EXECUTION_PENDING',
    revision=3,script=pin(HERE/'run_native.py'),files=files,dependencies=dependencies,fixture_count=13,execution=execution,
    existing_frozen_payload_values_unchanged_from_r2=True,only_new_embedded_source='link_guard_stage.py',
    isolation_mechanism='LINK_DOWN_LOOPBACK_ONLY',network_policy_enforcement_proven=False,
    scope='Focused local orchestration mocks only. Guard primitive has separately pinned local kernel qualification. No Kubernetes server, production model/database execution, image reimport or repeat of the qualified business probe suite.',
    qualified_behaviors=[
        'Exact prior r2 2873-byte failure report read/hash precondition and independent new r3 once/lock/evidence paths',
        'Original r2 PriorityClass UID, Never preemption, default scheduler, affinity, resource, admission and UID cleanup protections retained',
        'Only exact proof subject and first ordinary business guard init receive UID0/dropALL/addNET_ADMIN/readonly root/NNP/RuntimeDefault',
        'All original business containers preserve their UIDs and frozen argv inside receipt-verifying capless preexec wrappers',
        'Guard init precedes every original init including restartable PostgreSQL sidecar and has real image/CID/zero-restart/exit0/log-receipt gates',
        'Same frozen listener/packet verifier with baseline both directions, subject-only link closure, global two-health-before/two-cross/two-health-after barriers and three final bidirectional denials',
        'New proof schema explicitly reports LINK_DOWN_LOOPBACK_ONLY and never claims NetworkPolicy enforcement',
        'Every complete or interrupted round writes bounded private partial evidence and visible per-round progress',
        'One mixed TYPE/NAME CLI GET returns exact namespace/Pod pair; duplicate/missing/unexpected/partial/bad JSON and all original UID/spec/IP drift gates stop before exec',
        'Observed r2 command timing projection models reduced CLI startup costs only; actual frozen 60-second convergence and 240-second whole proof gates still decide success',
        'Guard failure, invalid terminal receipt, one-way denial or still-reachable cross cannot pass or start later business stages',
        'Frozen Host/Frontend/DB/network adapter source bytes and six cached image identities remain unchanged',
    ],
    remote_kubernetes_execution=False,remote_cni_enforcement_proven=False,native_runtime_passed=False,
    production_mutation=False,production_database_access=False,model_access=False,ready_for_rollout=False)
(HERE/'QUALIFICATION.json').write_text(json.dumps(value,sort_keys=True,indent=2)+'\n',encoding='utf-8',newline='\n')
print(json.dumps(pin(HERE/'QUALIFICATION.json'),sort_keys=True))
