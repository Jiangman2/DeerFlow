"""Create the reviewed manual-copy remaining-four image preload delivery."""
import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEST = Path('C:/Users/15285/Downloads/portal-ppt-remaining-preload-20261017-r1')
NAME = 'resume-portal-ppt-remaining-20261017-r1.py'
COMMAND = 'sudo python3 -I -B -u /home/cnooc/' + NAME + ' --resume-remaining-once'


def ref(path):
    raw = path.read_bytes()
    return dict(path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def write_new(path, raw):
    with path.open('xb') as stream:
        stream.write(raw)


def write_json(path, value):
    write_new(path, (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n').encode())


def main(expected):
    candidate = HERE / 'resume_remaining.py'
    assert ref(candidate)['sha256'] == expected
    qualification_path, review_path = HERE / 'QUALIFICATION.json', HERE / 'INDEPENDENT-REVIEW.json'
    qualification_raw, review_raw = qualification_path.read_bytes(), review_path.read_bytes()
    qualification, review = json.loads(qualification_raw), json.loads(review_raw)
    assert qualification['status'].startswith('PASS') and review['status'] == 'PASS'
    assert not review.get('blockers') and expected.encode() in qualification_raw and expected.encode() in review_raw
    for document in (qualification, review):
        for name, pin in document.get('files', {}).items():
            actual = ref(HERE / name)
            assert actual['sha256'] == pin['sha256'] and actual['bytes'] == pin['bytes'], name
    success = HERE.parent / 'native-gateway-recovery-evidence-r1/REPORT.json'
    assert ref(success)['sha256'] == 'c0732346dcb41b9a979984b1036bf67c05ce314c030551246f5c7ec3f20ab662'
    assert (HERE.parent / 'native-gateway-recovery-evidence-r1/REVIEW.json').is_file()
    compile(candidate.read_bytes(), str(candidate), 'exec')
    DEST.mkdir(exist_ok=False)
    write_new(DEST / NAME, candidate.read_bytes())
    assert ref(DEST / NAME)['sha256'] == expected
    runbook = f'''余下四镜像预载 — datax-02

手动复制 {NAME} 到 datax-02 的 /home/cnooc/。
现有镜像包继续复用，不需要重传。

在 datax-02 执行一次：
{COMMAND}

处理顺序：Gateway兼容回滚、Runner正向、Runner兼容回滚、PPT Agent Host正向。
Frontend和Gateway正向已完成，本次只读复核。
每项沿用原内容、引用、快照、空间预算与运行中检查；保存真实退出码和有界私人日志。
任一失败立即停止，保留此前完成项及旧证据。不要重跑本脚本或历史导入脚本，不要删除once标记。
完成后贴回此次完整终端输出，并保留download_report指向的新REPORT.json。
这一步仍是预载，生产服务切换及原生运行验收尚未完成。

脚本SHA256：{expected}
'''
    write_new(DEST / 'RUNBOOK.txt', runbook.encode())
    delivery = dict(
        schema='portal-native-remaining-four-preload-delivery.v1',
        status='REVIEWED_READY_FOR_MANUAL_REMAINING_FOUR_PRELOAD',
        execution_host='datax-02', execution_ip='192.168.111.37',
        server_path='/home/cnooc/' + NAME, command=COMMAND,
        script=ref(candidate), delivered_script=ref(DEST / NAME), runbook=ref(DEST / 'RUNBOOK.txt'),
        qualification=ref(qualification_path), independent_review=ref(review_path),
        accepted_gateway_success_report=ref(success),
        import_role_scope=['gateway-rollback-compatible', 'ppt-master-runner-forward',
                           'ppt-master-runner-rollback-compatible', 'ppt-master-agent-host-forward'],
        maximum_import_subprocesses=4, frontend_reimported=False, forward_gateway_reimported=False,
        stop_on_first_failure=True, phase_a_repeated=False, original_storage_policy_unchanged=True,
        fresh_gate_pass_not_claimed=True, raw_output_root_private=True,
        automatic_retry=False, old_markers_removed=False, existing_images_deleted=False,
        workload_mutation=False, database_access=False, model_access=False,
        ssh_scp=False, server_executed=False, ready_for_rollout=False)
    write_json(HERE / 'DELIVERY.json', delivery)
    write_json(HERE / 'ROOT-DELIVERY-REVIEW.json', dict(
        schema='portal-native-remaining-four-root-delivery-review.v1', status='PASS',
        delivery=ref(HERE / 'DELIVERY.json'), delivered_script=ref(DEST / NAME),
        independent_review=ref(review_path), qualification=ref(qualification_path),
        root_checks=[
            'Exact accepted Gateway success boundary and prior failure evidence retained',
            'Final qualified and independently reviewed script copied without rewriting',
            'Only four remaining roles may import, at most once each; frontend and forward Gateway read-only',
            'Original private frozen helpers, held metadata descriptors, common lock and fresh live gates retained',
            'Bounded root-private logs and real child returncode retained for each requested role',
            'First failure stops subsequent imports; success requires complete image verification',
            'No old marker removal, cleanup, production workload, database or model action'],
        server_execution=False, ready_for_rollout=False))
    print(json.dumps(dict(status='REMAINING_FOUR_DELIVERED_LOCALLY',
                         script=ref(DEST / NAME), command=COMMAND), sort_keys=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--expected-script-sha', required=True)
    main(parser.parse_args().expected_script_sha)
