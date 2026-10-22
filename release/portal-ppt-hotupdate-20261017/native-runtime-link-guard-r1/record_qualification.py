"""Pin existing focused and real-kernel evidence; performs no fixture execution."""
import ast
import base64
import hashlib
import json
from pathlib import Path
import zlib

HERE=Path(__file__).resolve().parent
def pin(name):
    path=HERE/name;raw=path.read_bytes()
    return dict(path=str(path),bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())

source=(HERE/'link_guard.py').read_bytes()
template=(HERE/'link_guard_template.py').read_text(encoding='utf-8')
for name,prefix in [('guard_kernel.py','GUARD'),('preexec.sh','WRAPPER')]:
    raw=(HERE/name).read_bytes()
    template=template.replace('__'+prefix+'_Z64__',base64.b64encode(zlib.compress(raw,9)).decode())
    template=template.replace('__'+prefix+('_CODE_SHA__' if prefix=='GUARD' else '_SHA__'),hashlib.sha256(raw).hexdigest())
assert source==template.encode()
for name in ('link_guard.py','guard_kernel.py'):
    ast.parse((HERE/name).read_bytes(),feature_version=(3,7))
focused=json.loads((HERE/'FOCUSED-QUALIFICATION.json').read_bytes())
kernel=json.loads((HERE/'KERNEL-QUALIFICATION.json').read_bytes())
assert focused['status']==kernel['status']=='PASS' and focused['returncode']==0
assert focused['cleanup_exact_owned_id'] is True
assert all(item['status'].startswith('EXACT_OWNED_') and item['status'].endswith('_REMOVED') for item in kernel['cleanup'])
for evidence in (focused,kernel):
    for name,record in evidence['files'].items():
        actual=pin(name)
        assert actual['bytes']==record['bytes'] and actual['sha256']==record['sha256'],name
assert all(value['connected'] for value in kernel['baseline'].values())
assert kernel['after_guard']['subject_loopback']['connected'] and kernel['after_guard']['peer_loopback']['connected']
assert not kernel['after_guard']['subject_to_peer']['connected'] and not kernel['after_guard']['peer_to_subject']['connected']
assert kernel['wrapper_original_argv_and_exit']['exit_code']==73
assert kernel['wrong_pod_uid_rejected']['exit_code']==91 and kernel['nonzero_capability_sets_rejected']['exit_code']==91
assert kernel['wrong_live_proof_pod_uid_rejected'] and kernel['loopback_health_after_business']
assert kernel['malicious_path_cannot_replace_tools_and_original_environment_restored']['exit_code']==0
value=dict(schema='portal-owned-pod-link-guard-qualification.v1',status='PASS_LOCAL_KERNEL_AND_FOCUSED_FIXTURES',
           helper=pin('link_guard.py'),
           files={name:pin(name) for name in ['link_guard_template.py','guard_kernel.py','preexec.sh','build_guard.py','test_guard.py',
                                            'run_focused_tests.py','run_kernel_fixture.py','record_qualification.py',
                                            'FOCUSED-QUALIFICATION.json','FOCUSED-TEST.stderr.txt','FOCUSED-TEST.stdout.txt',
                                            'KERNEL-QUALIFICATION.json','KERNEL-GUARD-RECEIPT.json']},
           preserved_attempts={name:pin(name) for name in ['KERNEL-ATTEMPT-1.json','KERNEL-ATTEMPT-2.json','KERNEL-PASS-BEFORE-PATH.json','KERNEL-GUARD-BEFORE-PATH.json']},
           fixture_setup_correction='Docker API serializes added capability as CAP_NET_ADMIN; earlier setup assertion stopped before kernel execution, all owned resources removed',
           python_source_syntax_floor='3.7',template_binding_exact=True,guard_and_wrapper_embedded_byte_exact=True,
           focused_tests=dict(count=8,elapsed_seconds=0.021,tool_chunk='2ac94e',returncode=0),
           real_kernel=dict(image=kernel['image'],scope=kernel['mechanics_scope'],
                            internal_owned_bridge=True,host_network=False,exact_owned_cleanup=True,
                            before_cross_both_success=True,after_cross_both_blocked=True,loopback_continuously_reachable=True,
                            business_capabilities_zero_cannot_restore_link=True,readonly_receipt_write_rejected=True,
                            original_argv_and_exit_preserved=True,live_proof_pod_uid_check_rejected_mismatch=True,
                            malicious_path_tool_replacement_prevented_original_path_locale_restored=True),
           runtime_scope=dict(ordinary_init_net_admin_only=True,business_original_uids_preserved=True,
                              business_caps_effective_permitted_bounding_ambient_inheritable_zero_required=True,
                              no_new_privileges_required=True,loopback_must_already_be_up=True,
                              proof_subject_keeps_net_admin_until_owned_pod_deletion=True,
                              global_cni_or_cluster_network_changes=False),
           server_execution=False,production_mutation=False,model_access=False,candidate_images_executed=False,
           native_arm64_qualified=False,ready_for_rollout=False)
raw=(json.dumps(value,sort_keys=True,indent=2)+'\n').encode()
(HERE/'QUALIFICATION.json').write_bytes(raw)
print(json.dumps(dict(status=value['status'],bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),helper=value['helper']),sort_keys=True))
