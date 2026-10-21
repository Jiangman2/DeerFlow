"""Create the reviewed manual-copy diagnostic, without contacting a server."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEST = Path('C:/Users/15285/Downloads/portal-ppt-phase-b-diagnostic-20261017-r1')
NAME = 'diagnose-portal-ppt-phase-b-20261017-r1.py'
EXPECTED = '4bdcf33fd8b7891b277fa472f2e9a1462d4da7b09d4b23243149325d8d63e03d'
COMMAND = 'sudo python3 -I -B -u /home/cnooc/' + NAME + ' --collect'


def ref(path):
    raw = path.read_bytes()
    return dict(path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def write_new(path, raw):
    with path.open('xb') as stream:
        stream.write(raw)


def write_json(path, value):
    write_new(path, (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + '\n').encode())


def main():
    candidate = HERE / 'collect_failure.py'
    assert ref(candidate)['sha256'] == EXPECTED and ref(candidate)['bytes'] == 20397
    qualification = json.loads((HERE / 'QUALIFICATION.json').read_bytes())
    assert ref(HERE / 'QUALIFICATION.json')['sha256'] == '688d517a3c2089fa0bb5ba901ae5b008f13e3f86eace6c0a3533cde267b89fe6'
    assert qualification['status'] == 'PASS_LOCAL_PREPARATION_ONLY' and qualification['unique_fixtures'] == 6
    for name, pin in qualification['files'].items():
        actual = ref(HERE / name)
        assert actual['sha256'] == pin['sha256'] and actual['bytes'] == pin['bytes'], name
    review_raw = (HERE / 'INDEPENDENT-REVIEW.json').read_bytes()
    review = json.loads(review_raw)
    assert review['status'] == 'PASS' and EXPECTED.encode() in review_raw
    assert not review.get('blockers')
    compile(candidate.read_bytes(), str(candidate), 'exec')
    DEST.mkdir(exist_ok=False)
    write_new(DEST / NAME, candidate.read_bytes())
    assert ref(DEST / NAME)['sha256'] == EXPECTED
    instructions = f'''Gateway 解压失败只读诊断 — 在 datax-02 执行

将 {NAME} 手动复制到 datax-02 的 /home/cnooc/。
无需再传六个大镜像，也不需要 Windows SSH 上传脚本。

执行一次：
{COMMAND}

请先把新的完整终端输出贴回聊天。保留新输出 download_report 指向的 REPORT.json，勿再次选取旧库存报告。
输出 schema 应为 portal-native-phase-b-failure-diagnostic.v1；摘要会显示日志状态、错误类别、Gateway 已解压层数和当前可用空间。

范围：核对固定12001字节失败报告和已有回执，按Gateway请求至失败的时间窗口读取containerd服务日志，最多10分钟窗口/1000条/2MiB，并只输出固定镜像关联的安全类别；读取固定Gateway快照链与当前空间。
这一步不导入、不删除、不归档镜像，不读镜像大包，不操作生产工作负载、数据库或模型。
旧脚本丢弃的CLI错误正文无法从该报告恢复；无匹配日志或日志不可用会明确标记未知。
若报错或中断，保留报告和现场，先不要重跑。不要重跑之前的Phase B续接脚本。

脚本 SHA256：{EXPECTED}
'''
    write_new(DEST / 'RUNBOOK.txt', instructions.encode())
    delivery = dict(schema='portal-native-phase-b-diagnostic-delivery.v1',
        status='REVIEWED_READY_FOR_MANUAL_READ_ONLY_DIAGNOSTIC', execution_host='datax-02',
        server_path='/home/cnooc/' + NAME, command=COMMAND,
        script=ref(candidate), delivered_script=ref(DEST / NAME), runbook=ref(DEST / 'RUNBOOK.txt'),
        qualification=ref(HERE / 'QUALIFICATION.json'), independent_review=ref(HERE / 'INDEPENDENT-REVIEW.json'),
        exact_prior_report=ref(HERE.parent / 'native-phase-b-attempt-r1/REPORT.json'),
        current_failure_review=ref(HERE.parent / 'native-phase-b-attempt-r1/REVIEW.json'),
        server_executed=False, image_import=False, image_delete=False, image_export=False,
        workload_mutation=False, database_access=False, model_access=False, ssh_scp=False,
        ready_for_rollout=False, original_cli_stderr_recoverable=False)
    write_json(HERE / 'DELIVERY.json', delivery)
    write_json(HERE / 'ROOT-DELIVERY-REVIEW.json', dict(
        schema='portal-native-phase-b-diagnostic-root-review.v1', status='PASS',
        delivery=ref(HERE / 'DELIVERY.json'), delivered_script=ref(DEST / NAME),
        independent_review=ref(HERE / 'INDEPENDENT-REVIEW.json'),
        local_fixtures=6, qualification=ref(HERE / 'QUALIFICATION.json'), root_repeated_tests=False,
        verified=['Exact failure REPORT and FAIL identities reconciled',
                  'Read-only fixed evidence, limited journal projection and Gateway snapshot scope inspected',
                  'No import or workload mutation command; frozen helper imports do not call execution entrypoints',
                  'Unmatched or unavailable logs stay unknown; old stderr is not claimed recovered',
                  'Final signal control flow stops later reads and preserves owned-process cleanup',
                  'Delivered script bytes equal the final independently reviewed and qualified candidate'],
        server_execution=False, image_import=False, ready_for_rollout=False))
    print(json.dumps(dict(status='DIAGNOSTIC_DELIVERED_LOCALLY', script=ref(DEST / NAME), command=COMMAND), sort_keys=True))


if __name__ == '__main__':
    main()
