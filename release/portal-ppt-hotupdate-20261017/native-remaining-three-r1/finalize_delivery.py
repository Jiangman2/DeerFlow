"""Package the reviewed remaining-three continuation for manual copying."""
import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEST = Path('C:/Users/15285/Downloads/portal-ppt-remaining-three-20261017-r1')
NAME = 'resume-portal-ppt-remaining-three-20261017-r1.py'
COMMAND = 'sudo python3 -I -B -u /home/cnooc/' + NAME + ' --resume-three-once'


def ref(path):
    raw = path.read_bytes()
    return dict(path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def write_new(path, raw):
    with path.open('xb') as stream: stream.write(raw)


def write_json(path, value):
    write_new(path, (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + '\n').encode())


def main(expected):
    candidate = HERE / 'resume_three.py'
    assert ref(candidate)['sha256'] == expected
    qualification_path, review_path = HERE / 'QUALIFICATION.json', HERE / 'INDEPENDENT-REVIEW.json'
    qualification_raw, review_raw = qualification_path.read_bytes(), review_path.read_bytes()
    qualification, review = json.loads(qualification_raw), json.loads(review_raw)
    assert qualification['status'].startswith('PASS') and review['status'] == 'PASS' and not review.get('blockers')
    assert expected.encode() in qualification_raw and expected.encode() in review_raw
    for document in (qualification, review):
        for name, pin in document.get('files', {}).items():
            actual = ref(HERE / name)
            assert actual['sha256'] == pin['sha256'] and actual['bytes'] == pin['bytes'], name
    partial = HERE.parent / 'native-remaining-preload-evidence-r1/REPORT.json'
    assert ref(partial)['sha256'] == '77823e4308eac6d38f5576f463342591750833b3555681472d9804eae7e1a417'
    compile(candidate.read_bytes(), str(candidate), 'exec')
    DEST.mkdir(exist_ok=False)
    write_new(DEST / NAME, candidate.read_bytes())
    assert ref(DEST / NAME)['sha256'] == expected
    runbook = f'''余下三个镜像预载 — datax-02

手动复制 {NAME} 到 datax-02 的 /home/cnooc/。
复用服务器已有镜像包，无需重传。

执行一次：
{COMMAND}

处理顺序：Runner正向、Runner兼容回滚、PPT Agent Host正向。
Frontend、Gateway正向和Gateway兼容回滚已完成，本次只读复核。
新脚本修正快照KEY/PARENT栏判断：普通Active容器引用已提交父层不会误报；实际目标解压冲突、异常快照链和原空间门槛仍会阻止执行。
每项保存真实退出码和有界私人日志，任一失败立即停止。
执行结束后贴回此次完整终端输出，保留新download_report对应REPORT.json。
失败或中断先不要重跑，保留全部现场、旧回执及once标记。
预载完成后还须原生运行验收；本脚本不切换生产服务。

脚本SHA256：{expected}
'''
    write_new(DEST / 'RUNBOOK.txt', runbook.encode())
    delivery = dict(
        schema='portal-native-remaining-three-delivery.v1', status='REVIEWED_READY_FOR_MANUAL_REMAINING_THREE_PRELOAD',
        execution_host='datax-02', execution_ip='192.168.111.37', server_path='/home/cnooc/' + NAME,
        command=COMMAND, script=ref(candidate), delivered_script=ref(DEST / NAME), runbook=ref(DEST / 'RUNBOOK.txt'),
        qualification=ref(qualification_path), independent_review=ref(review_path),
        accepted_partial_report=ref(partial),
        snapshot_cause_review=ref(HERE.parent / 'native-remaining-preload-evidence-r1/SNAPSHOT-CAUSE-REVIEW.json'),
        import_role_scope=['ppt-master-runner-forward', 'ppt-master-runner-rollback-compatible', 'ppt-master-agent-host-forward'],
        maximum_import_subprocesses=3, frontend_reimported=False, forward_gateway_reimported=False,
        compatible_rollback_gateway_reimported=False, phase_a_repeated=False,
        corrected_snapshot_field_distinction=True, actual_extraction_conflicts_still_block=True,
        original_storage_policy_unchanged=True, stop_on_first_failure=True,
        raw_output_root_private=True, automatic_retry=False, old_markers_removed=False,
        existing_images_deleted=False, workload_mutation=False, database_access=False,
        model_access=False, ssh_scp=False, server_executed=False, ready_for_rollout=False)
    write_json(HERE / 'DELIVERY.json', delivery)
    write_json(HERE / 'ROOT-DELIVERY-REVIEW.json', dict(
        schema='portal-native-remaining-three-root-delivery-review.v1', status='PASS',
        delivery=ref(HERE / 'DELIVERY.json'), delivered_script=ref(DEST / NAME),
        qualification=ref(qualification_path), independent_review=ref(review_path),
        root_checks=[
            'Exact partial report accepted; rollback Gateway complete, Runner import never started',
            'Actual parent-only Active rows match fixed Runner candidate chains 342 and 365',
            'Corrected gate separates KEY and PARENT and retains actual extraction and chain-closure checks',
            'Only three remaining roles import, each at most once; three complete images checked read-only',
            'Original identity, anchored metadata, common lock, private logs and live space checks retained',
            'First failure stops later imports; final success requires all six image checks',
            'Final qualified and independently reviewed bytes copied exactly; old evidence and markers retained'],
        root_repeated_tests=False, server_execution=False, ready_for_rollout=False))
    print(json.dumps(dict(status='REMAINING_THREE_DELIVERED_LOCALLY', script=ref(DEST / NAME), command=COMMAND), sort_keys=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--expected-script-sha', required=True)
    main(parser.parse_args().expected_script_sha)
