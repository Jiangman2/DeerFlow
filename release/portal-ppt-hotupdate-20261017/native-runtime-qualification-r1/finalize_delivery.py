"""Copy the qualified, independently reviewed native controller for manual use."""
import argparse
import ast
import base64
import hashlib
import json
from pathlib import Path
import zlib

HERE = Path(__file__).resolve().parent
PREP = HERE.parent
MERGE = Path('C:/Users/15285/Documents/deer-flow/portal-ppt-merge-20261017')
DEST = Path('C:/Users/15285/Downloads/portal-ppt-native-qualification-20261017-r1')
NAME = 'qualify-portal-ppt-native-20261017-r1.py'
COMMAND = 'sudo python3 -I -B -u /home/cnooc/' + NAME + ' --qualify-native-once'
PRELOAD_SHA = '99135299a8615bcadc90af60ab6e3332665e9714826b847d9a1f5e48c426688a'


def ref(path):
    raw = path.read_bytes()
    return dict(path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def read(path):
    return json.loads(path.read_bytes())


def check_refs(value):
    if isinstance(value, dict):
        if {'path', 'bytes', 'sha256'} <= set(value):
            actual = ref(Path(value['path']))
            assert actual['bytes'] == value['bytes'] and actual['sha256'] == value['sha256'], value['path']
        for child in value.values():
            check_refs(child)
    elif isinstance(value, list):
        for child in value:
            check_refs(child)


def write_new(path, raw):
    with path.open('xb') as stream:
        stream.write(raw)


def write_json(path, value):
    write_new(path, (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + '\n').encode())


def main(expected):
    candidate = HERE / 'run_native.py'
    pin = ref(candidate)
    assert pin['sha256'] == expected
    qualification_path = HERE / 'QUALIFICATION.json'
    review_path = HERE / 'INDEPENDENT-REVIEW.json'
    qualification, review = read(qualification_path), read(review_path)
    assert qualification['status'].startswith('PASS')
    assert review['status'] == 'PASS' and review.get('blockers') == []
    assert qualification['script']['sha256'] == expected
    assert expected.encode() in review_path.read_bytes()
    check_refs(qualification)
    check_refs(review)
    db_review_path = HERE / 'ROOT-DB-STAGE-REVIEW.json'
    db_review = read(db_review_path)
    assert db_review['status'] == 'PASS' and db_review['blockers'] == []
    check_refs(db_review)
    hf_review_path = PREP / 'native-runtime-host-frontend-r1/INDEPENDENT-REVIEW.json'
    hf_review = read(hf_review_path)
    assert hf_review['status'] == 'PASS' and hf_review.get('blockers') == []
    check_refs(hf_review)
    preload_path = PREP / 'native-preload-complete-evidence-r1/REPORT.json'
    assert ref(preload_path)['sha256'] == PRELOAD_SHA and ref(preload_path)['bytes'] == 24125
    assert read(PREP / 'native-preload-complete-evidence-r1/REVIEW.json')['status'] == 'EXACT_REPORT_ACCEPTED_ALL_SIX_PRELOAD_PASS'

    # Decode constants without importing the Linux controller or running any probe.
    tree = ast.parse(candidate.read_bytes())
    constants = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
                 if isinstance(node, ast.Assign) and len(node.targets) == 1
                 and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Constant)}
    packed = base64.b64decode(constants['PAYLOAD_Z64'], validate=True)
    raw = zlib.decompress(packed)
    assert len(raw) <= 2097152 and hashlib.sha256(raw).hexdigest() == constants['PAYLOAD_SHA']
    payload = json.loads(raw)
    sources = {
        'network_probe.py': MERGE / 'native-network-proof-r1/network_probe.py',
        'TEMPLATE.json': MERGE / 'native-network-proof-r1/TEMPLATE.json',
        'host_frontend_stage.py': PREP / 'native-runtime-host-frontend-r1/host_frontend_stage.py',
        'db_stage.py': HERE / 'db_stage.py',
    }
    for name, path in sources.items():
        assert payload[name].encode() == path.read_bytes(), name
    assert payload['preload_report'] == dict(bytes=24125, sha256=PRELOAD_SHA)
    for name, file in [('host_runtime_inputs', 'HOST-RUNTIME-INPUTS.json'), ('postgres_cache', 'POSTGRES-CACHE.json')]:
        assert payload[name] == read(PREP / 'native-preload-complete-evidence-r1' / file)
    compile(candidate.read_bytes(), str(candidate), 'exec')

    DEST.mkdir(exist_ok=False)
    write_new(DEST / NAME, candidate.read_bytes())
    assert ref(DEST / NAME)['sha256'] == expected
    runbook = f'''原生 ARM64 隔离验收 — 在 datax-01 执行

手动复制 {NAME} 到 datax-01 的 /home/cnooc/。
本次只复制这个小脚本；复用 datax-02 已预载的六个镜像及 PostgreSQL 缓存。

执行一次：
{COMMAND}

执行顺序：新建专属测试命名空间；验证双向网络隔离；Host 运行探针；前端探针；PostgreSQL 安全路径/用户探针；五步数据库升级、重启和兼容回滚验收。
所有测试 Pod 均由默认调度器定向到固定 UID 的 datax-02，不抢占其他 Pod，不拉取镜像。
测试使用独立的临时数据库和内存卷，不连接生产数据库、不调用模型、不切换生产服务。
会在自己的测试命名空间创建和删除临时资源；结束后保留该命名空间及拒绝全部通信的策略。
运行过程中通常每 15 秒显示当前等待阶段；不要在另一个窗口重复执行。

结束后贴回完整终端输出，保留 download_report 对应 REPORT.json，手动复制回本机供核验。
若失败或中断，保留全部回执、私人诊断日志和 once 标记，先不要重跑。
本地模拟测试通过不等于服务器原生验收通过；本脚本始终不会标记可上线。

脚本大小：{pin['bytes']} 字节
脚本 SHA256：{expected}
'''
    write_new(DEST / 'RUNBOOK.txt', runbook.encode())
    delivery = dict(
        schema='portal-native-qualification-delivery.v1', status='REVIEWED_READY_FOR_MANUAL_NATIVE_QUALIFICATION',
        execution_host='datax-01', target_node='datax-02', target_node_uid=constants['NODE_UID'],
        server_path='/home/cnooc/' + NAME, command=COMMAND,
        script=pin, delivered_script=ref(DEST / NAME), runbook=ref(DEST / 'RUNBOOK.txt'),
        qualification=ref(qualification_path), independent_review=ref(review_path),
        database_independent_root_review=ref(db_review_path), host_frontend_independent_review=ref(hf_review_path),
        accepted_preload_report=ref(preload_path), embedded_sources={name: ref(path) for name, path in sources.items()},
        single_file=True, extra_image_transfer_required=False, image_reimport=False, automatic_retry=False,
        isolated_kubernetes_fixture_mutation_on_execution=True, isolated_database_fixture_mutation_on_execution=True,
        production_workload_mutation=False, production_database_access=False, model_access=False,
        existing_images_deleted=False, server_executed=False, native_runtime_passed=False, ready_for_rollout=False)
    write_json(HERE / 'DELIVERY.json', delivery)
    write_json(HERE / 'ROOT-DELIVERY-REVIEW.json', dict(
        schema='portal-native-qualification-root-delivery-review.v1', status='PASS', blockers=[],
        delivery=ref(HERE / 'DELIVERY.json'), delivered_script=ref(DEST / NAME),
        qualification=ref(qualification_path), independent_review=ref(review_path),
        database_independent_root_review=ref(db_review_path),
        checks=[
            'Exact 24125-byte final report accepted; all six content/unpack/CRI checks passed',
            'Embedded network source/template, Host/Frontend helper and DB helper byte-exact against reviewed files',
            'Final safe Host and PostgreSQL cached identity projections match accepted evidence',
            'Local orchestration fixtures and exact final delta qualified; independent review has no blockers',
            'Actual node, Pod UID, container ID, image digest, restart and exit checks remain execution prerequisites',
            'Network baseline and bidirectional denial required before serial candidate stages',
            'Unknown create results are not adopted or retried; cleanup deletes only recorded owned UIDs',
            'Failure logs and real child exits retained privately; final deny-all and production shape checks required',
            'Delivered bytes match reviewed candidate; no server execution or rollout claimed'],
        root_repeated_tests=False, server_execution=False, native_runtime_passed=False, ready_for_rollout=False))
    print(json.dumps(dict(status='NATIVE_QUALIFICATION_DELIVERED_LOCALLY', script=ref(DEST / NAME), command=COMMAND), sort_keys=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--expected-script-sha', required=True)
    main(parser.parse_args().expected_script_sha)
