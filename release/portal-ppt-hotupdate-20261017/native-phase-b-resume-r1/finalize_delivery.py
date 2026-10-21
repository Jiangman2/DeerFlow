"""Bind reviewed local artifacts and create the manual-copy Phase B delivery."""
import ast
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
PREP = HERE.parent
DEST = Path('C:/Users/15285/Downloads/portal-ppt-phase-b-resume-20261017-r1')
NAME = 'resume-portal-ppt-phase-b-20261017-r1.py'
SCRIPT_SHA = '6b20039722ebd7b17a03439deb717c8f587efc56667e72ba6eeb88816cb0513b'
INVENTORY_SHA = 'ccfb0e3efe75efab543da7a6596fd8137662a30f4a5abbf6f85660f7cac0105a'
COMMAND = 'sudo python3 -I -B -u /home/cnooc/' + NAME + ' --resume-phase-b'


def ref(path):
    raw = path.read_bytes()
    return dict(path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_new(path, raw):
    with path.open('xb') as stream:
        stream.write(raw)


def write_json(path, data):
    write_new(path, (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + '\n').encode())


def main():
    candidate = HERE / 'resume_phase_b_bound.py'
    assert ref(candidate)['sha256'] == SCRIPT_SHA and ref(candidate)['bytes'] == 21443
    inventory = PREP / 'native-image-inventory-evidence-r1/REPORT.json'
    assert ref(inventory)['sha256'] == INVENTORY_SHA and ref(inventory)['bytes'] == 89968
    independent = read(HERE / 'INDEPENDENT-REVIEW.json')
    assert independent['status'] == 'PASS' and not independent['blockers']
    for name, expected in independent['files'].items():
        found = ref(HERE / name)
        assert found['sha256'] == expected['sha256'] and found['bytes'] == expected['bytes'], name
    values = {}
    tree = ast.parse(candidate.read_bytes())
    for item in tree.body:
        if isinstance(item, ast.Assign) and len(item.targets) == 1 and isinstance(item.targets[0], ast.Name):
            try:
                values[item.targets[0].id] = ast.literal_eval(item.value)
            except (ValueError, TypeError):
                pass
    assert values['CHECKPOINT_SHA256'] == read(PREP / 'native-image-inventory-evidence-r1/CHECKPOINT-PINS.json')
    assert len(values['CHECKPOINT_SHA256']) == 24
    assert values['RELEASE_STATE'] == 'REVIEWED_FOR_MANUAL_EXECUTION'
    assert values['INVENTORY_SHA256'] == INVENTORY_SHA
    assert values['INVENTORY_COLLECTED_AT_UTC'] == '2026-10-17T12:48:42Z'
    compile(candidate.read_bytes(), str(candidate), 'exec')
    DEST.mkdir(exist_ok=False)
    delivered = DEST / NAME
    write_new(delivered, candidate.read_bytes())
    assert ref(delivered)['sha256'] == SCRIPT_SHA
    runbook = f'''仅续接 Phase B 解压 — datax-02

手动复制本目录的 {NAME} 到 datax-02 的 /home/cnooc/。
无需复制六个大镜像；无需运行 Windows 上传脚本。

在 datax-02 执行一次：
{COMMAND}

本脚本绑定 operation abf96dde50174b1eb7caf70a4e6711b1 的24份原阶段回执。
Phase A 已完成。本次只使用现存六个小元数据 OCI 和 containerd 内容继续 Phase B 解压。
它在执行前和每个镜像解压前重新核对内容、镜像引用、缓存和原空间预算，解压期间继续监测余量。
20:48:42 库存快照比此前 Phase B 预算高118,912,263字节；这不是空间预留，也不替代执行时检查。
本脚本不传大镜像、不归档/删除镜像，不切换 Kubernetes 工作负载，不访问数据库或模型。
完成后带回完整终端摘要及 download_report 指向的 REPORT.json。
若失败或中断，保留现场并带回结果；不要重跑本脚本或旧 r3/local-resume。
镜像预载完成后仍须原生运行验收；ready_for_rollout 保持 false。

脚本 SHA256：{SCRIPT_SHA}
'''
    write_new(DEST / 'RUNBOOK.txt', runbook.encode('utf-8'))
    delivery = dict(
        schema='portal-native-phase-b-resume-delivery.v1',
        status='BOUND_REVIEWED_READY_FOR_MANUAL_PHASE_B_EXECUTION',
        execution_host='datax-02', execution_ip='192.168.111.37',
        server_path='/home/cnooc/' + NAME, command=COMMAND,
        operation=values['OP'], script=ref(candidate), delivered_script=ref(delivered),
        runbook=ref(DEST / 'RUNBOOK.txt'), inventory=ref(inventory),
        accepted_inventory_review=ref(PREP / 'native-image-inventory-evidence-r1/REVIEW.json'),
        independent_review=ref(HERE / 'INDEPENDENT-REVIEW.json'),
        original_qualification=ref(HERE / 'QUALIFICATION.json'),
        bound_delta_qualification=ref(HERE / 'BOUND-DELTA-QUALIFICATION.json'),
        bound_checkpoint_count=24, metadata_archive_total_bytes=716800,
        storage_policy_unchanged=True, fresh_execution_checks_required=True,
        historical_margin_over_previous_budget_bytes=118912263,
        actual_server_budget_pass_claimed=False, server_executed=False,
        automatic_retry=False, phase_a_repeated=False, new_ssh_or_scp=False,
        archives_retransfer_required=False, image_archival=False, image_deletion=False,
        image_import_scope='PHASE_B_ONLY_ORIGINAL_METADATA_UNPACK_FROM_EXISTING_CONTENT',
        workload_mutation=False, database_access=False, model_access=False,
        native_runtime_passed=False, ready_for_rollout=False)
    write_json(HERE / 'DELIVERY.json', delivery)
    root_review = dict(
        schema='portal-native-phase-b-root-delivery-review.v1', status='PASS',
        delivery=ref(HERE / 'DELIVERY.json'), delivered_script=ref(delivered),
        inventory=ref(inventory), independent_review=ref(HERE / 'INDEPENDENT-REVIEW.json'),
        original_linux_fixtures=10, bound_delta_linux_fixtures=2,
        root_repeated_tests=False, original_script_preserved=True,
        root_checks=['Exact reviewed candidate bytes copied without rewriting',
                     'All 24 actual inventory checkpoint pins independently compared',
                     'Independent review and all referenced artifact hashes reconciled',
                     'Phase-B-only flow, same-lock exclusive continuation, unchanged live space gates and failure reporting inspected',
                     'Manual-copy command targets datax-02; no SSH upload dependency'],
        old_image_cleanup_performed=False, server_execution=False,
        fresh_server_budget_pass=False, native_runtime_passed=False, ready_for_rollout=False)
    write_json(HERE / 'ROOT-DELIVERY-REVIEW.json', root_review)
    print(json.dumps(dict(status='DELIVERED_LOCALLY_SERVER_EXECUTION_PENDING',
                         script=ref(delivered), command=COMMAND, ready_for_rollout=False), sort_keys=True))


if __name__ == '__main__':
    main()
