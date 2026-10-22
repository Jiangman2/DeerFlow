"""Package the reviewed r2 successor for one manually started native session.

This finalizer is local-only. It never invokes the controller or a cluster client.
Run it only after root has reviewed the final qualification and independent review.
"""
import argparse
import ast
import base64
import hashlib
import json
from pathlib import Path
import re
import zlib

HERE = Path(__file__).resolve().parent
PREP = HERE.parent
R1 = PREP / 'native-runtime-qualification-r1'
ATTEMPT = PREP / 'native-runtime-attempt-r1'
MERGE = Path('C:/Users/15285/Documents/deer-flow/portal-ppt-merge-20261017')
DEST = Path('C:/Users/15285/Downloads/portal-ppt-native-qualification-20261017-r2')
NAME = 'qualify-portal-ppt-native-20261017-r2.py'
COMMAND = 'sudo python3 -I -B -u /home/cnooc/' + NAME + ' --qualify-native-once'
PRELOAD_SHA = '99135299a8615bcadc90af60ab6e3332665e9714826b847d9a1f5e48c426688a'
PRIOR_SCRIPT_SHA = '58e32027eaf300df8330432c50dc9cace545ef5e4ab7073bd1743f3367e31b7d'
PRIOR_REPORT_SHA = 'fdd49101a4e8892b92945a30b7334f3f5b7d2436e13f590720f4a69f7f3b2a2c'
PRIOR_ERROR_SHA = 'dfdbf9ce2e5c8104d66ac46a0a92b9abe4e67a4eb381d3c7324d0d8591c5b9ad'


def ref(path):
    raw = path.read_bytes()
    return dict(path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def read(path):
    return json.loads(path.read_bytes())


def check_pin(path, size, sha):
    value = ref(path)
    assert value['bytes'] == size and value['sha256'] == sha, str(path)
    return value


def check_refs(value):
    if isinstance(value, dict):
        if {'path', 'bytes', 'sha256'} <= set(value):
            path = Path(value['path'])
            if not path.is_absolute():
                path = HERE / path
            check_pin(path, value['bytes'], value['sha256'])
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


def controller_payload(path):
    # AST constants only: never import the Linux controller or execute probes.
    tree = ast.parse(path.read_bytes())
    constants = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
                 if isinstance(node, ast.Assign) and len(node.targets) == 1
                 and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Constant)}
    packed = base64.b64decode(constants['PAYLOAD_Z64'], validate=True)
    decoder = zlib.decompressobj()
    raw = decoder.decompress(packed, 2097153)
    assert len(raw) <= 2097152 and decoder.eof and not decoder.unconsumed_tail and not decoder.unused_data
    assert hashlib.sha256(raw).hexdigest() == constants['PAYLOAD_SHA']
    return constants, json.loads(raw)


def main(expected):
    assert re.fullmatch('[a-f0-9]{64}', expected), 'Expected final lowercase SHA256 required'
    candidate = HERE / 'run_native.py'
    pin = ref(candidate)
    assert pin['sha256'] == expected
    qualification_path = HERE / 'QUALIFICATION.json'
    review_path = HERE / 'INDEPENDENT-REVIEW.json'
    qualification, review = read(qualification_path), read(review_path)
    assert qualification['status'].startswith('PASS')
    assert review['status'] == 'PASS' and review.get('blockers', []) == []
    assert qualification['script']['sha256'] == expected
    assert expected.encode() in review_path.read_bytes()
    check_refs(qualification)
    check_refs(review)

    # Preserve and bind the failed predecessor; no r1 file is changed or copied over.
    prior_script = check_pin(R1 / 'run_native.py', 132749, PRIOR_SCRIPT_SHA)
    prior_report = check_pin(ATTEMPT / 'REPORT.json', 1771, PRIOR_REPORT_SHA)
    prior_error = check_pin(ATTEMPT / 'CREATE-ERROR.txt', 270, PRIOR_ERROR_SHA)
    cause_path = ATTEMPT / 'CAUSE-REVIEW.json'
    cause = read(cause_path)
    assert cause['status'] == 'EXACT_ERROR_VERIFIED_PRIORITY_CLASS_ADMISSION_MISMATCH'
    assert cause['report_sha256'] == PRIOR_REPORT_SHA and cause['create_error_sha256'] == PRIOR_ERROR_SHA
    db_review_path = R1 / 'ROOT-DB-STAGE-REVIEW.json'
    db_review = read(db_review_path)
    assert db_review['status'] == 'PASS' and db_review.get('blockers', []) == []
    check_refs(db_review)
    hf_review_path = PREP / 'native-runtime-host-frontend-r1/INDEPENDENT-REVIEW.json'
    hf_review = read(hf_review_path)
    assert hf_review['status'] == 'PASS' and hf_review.get('blockers', []) == []
    check_refs(hf_review)
    preload_path = PREP / 'native-preload-complete-evidence-r1/REPORT.json'
    check_pin(preload_path, 24125, PRELOAD_SHA)
    assert read(PREP / 'native-preload-complete-evidence-r1/REVIEW.json')['status'] == 'EXACT_REPORT_ACCEPTED_ALL_SIX_PRELOAD_PASS'

    constants, payload = controller_payload(candidate)
    old_constants, old_payload = controller_payload(R1 / 'run_native.py')
    assert payload == old_payload, 'Frozen embedded helpers and projections must stay exact'
    assert constants['PRELOAD_SHA'] == PRELOAD_SHA
    assert constants['PRIOR_REPORT_SHA'] == PRIOR_REPORT_SHA and constants['PRIOR_STDERR_SHA'] == PRIOR_ERROR_SHA
    assert constants['PRIOR_OWNER'] == read(ATTEMPT / 'REPORT.json')['owner']
    assert constants['NODE'] == 'datax-02' and constants['NODE_UID'] == '3148d4c1-6a97-42ed-a78c-950dcea5cbec'
    assert constants['ONCE'] == '/var/lib/portal-ppt-native-qualification-r2.STARTED.json'
    assert constants['LOCK'] == '/var/lib/portal-ppt-native-qualification-r2.lock'
    assert constants['ONCE'] != old_constants['ONCE'] and constants['LOCK'] != old_constants['LOCK']
    sources = {
        'network_probe.py': MERGE / 'native-network-proof-r1/network_probe.py',
        'TEMPLATE.json': MERGE / 'native-network-proof-r1/TEMPLATE.json',
        'host_frontend_stage.py': PREP / 'native-runtime-host-frontend-r1/host_frontend_stage.py',
        'db_stage.py': R1 / 'db_stage.py',
    }
    for name, path in sources.items():
        assert payload[name].encode() == path.read_bytes(), name
    assert payload['preload_report'] == dict(bytes=24125, sha256=PRELOAD_SHA)
    preload = read(preload_path)
    for name, file in [('host_runtime_inputs', 'HOST-RUNTIME-INPUTS.json'), ('postgres_cache', 'POSTGRES-CACHE.json')]:
        assert payload[name] == read(PREP / 'native-preload-complete-evidence-r1' / file) == preload[name]
    compile(candidate.read_bytes(), str(candidate), 'exec')

    DEST.mkdir(exist_ok=False)
    write_new(DEST / NAME, candidate.read_bytes())
    assert ref(DEST / NAME)['sha256'] == expected
    runbook = f'''原生 ARM64 隔离验收 r2 — 在 datax-01 执行

手动复制 {NAME} 到 datax-01 的 /home/cnooc/。
本次只复制这个小脚本；复用 datax-02 已预载的六个镜像及 PostgreSQL 缓存。
保留 r1 的报告、日志、once 标记和命名空间；r2 会先核对原失败报告和确切准入错误。

执行一次：
{COMMAND}

r2 修复测试 Pod 的 PriorityClass 准入问题。它会创建新的专属测试命名空间及本次专属的集群级 PriorityClass，值为 0、globalDefault=false、preemptionPolicy=Never，供本次测试 Pod 使用；结束时按本次记录的 UID 清理该 PriorityClass。
通过不保存资源的服务器端准入检查后，依次执行双向网络隔离、Host、前端、PostgreSQL 安全路径/用户探针，以及五步数据库升级、重启和兼容回滚验收。
所有测试 Pod 均由默认调度器定向到固定 UID 的 datax-02，不抢占其他 Pod，不拉取镜像。
测试使用独立的临时数据库和内存卷，不连接生产数据库、不调用模型、不切换生产服务。
会创建和删除本次拥有的测试资源；结束后保留新测试命名空间及拒绝全部通信的策略。
运行过程中通常每 15 秒显示当前等待阶段；不要在另一个窗口重复执行。

结束后贴回完整终端输出，保留 download_report 对应 REPORT.json，手动复制回本机供核验。
若失败或中断，保留全部回执、私人诊断日志和新的 once 标记，先不要重跑。
本地模拟测试通过不等于服务器原生验收通过；本脚本始终不会标记可上线。

脚本大小：{pin['bytes']} 字节
脚本 SHA256：{expected}
'''
    write_new(DEST / 'RUNBOOK.txt', runbook.encode())
    delivery = dict(
        schema='portal-native-qualification-r2-delivery.v1', status='REVIEWED_READY_FOR_MANUAL_NATIVE_QUALIFICATION',
        execution_host='datax-01', target_node='datax-02', target_node_uid=constants['NODE_UID'],
        server_path='/home/cnooc/' + NAME, command=COMMAND,
        script=pin, delivered_script=ref(DEST / NAME), runbook=ref(DEST / 'RUNBOOK.txt'),
        qualification=ref(qualification_path), independent_review=ref(review_path),
        database_independent_root_review=ref(db_review_path), host_frontend_independent_review=ref(hf_review_path),
        prior_script=prior_script, prior_failed_report=prior_report, prior_exact_create_error=prior_error,
        prior_cause_review=ref(cause_path), predecessor_files_preserved=True,
        accepted_preload_report=ref(preload_path), embedded_sources={name: ref(path) for name, path in sources.items()},
        embedded_payload_unchanged_from_r1=True,
        single_file=True, extra_image_transfer_required=False, image_reimport=False, automatic_retry=False,
        owned_cluster_priorityclass_mutation_on_execution=True,
        priorityclass_scope=dict(value=0, globalDefault=False, preemptionPolicy='Never', cleanup='OWNED_EXACT_UID_ONLY'),
        isolated_kubernetes_fixture_mutation_on_execution=True, isolated_database_fixture_mutation_on_execution=True,
        production_workload_mutation=False, production_database_access=False, model_access=False,
        existing_images_deleted=False, server_executed=False, native_runtime_passed=False, ready_for_rollout=False)
    write_json(HERE / 'DELIVERY.json', delivery)
    write_json(HERE / 'ROOT-DELIVERY-REVIEW.json', dict(
        schema='portal-native-qualification-r2-root-delivery-review.v1', status='PASS', blockers=[],
        delivery=ref(HERE / 'DELIVERY.json'), delivered_script=ref(DEST / NAME),
        qualification=ref(qualification_path), independent_review=ref(review_path),
        database_independent_root_review=ref(db_review_path),
        checks=[
            'Exact failed r1 report (1771 bytes), create error (270 bytes), and original controller preserved and pinned',
            'Successor uses distinct r2 once marker and lock and binds the accepted predecessor failure',
            'Final local qualification and independent review bind the supplied exact candidate SHA256',
            'All embedded network, Host/Frontend and DB helper bytes and safe input projections equal reviewed r1 payload',
            'Temporary uniquely owned non-default PriorityClass value0/Never and exact UID cleanup are disclosed',
            'Actual node, Pod UID, container ID, image digest, restart and exit checks remain execution prerequisites',
            'Network baseline and bidirectional denial required before serial candidate stages',
            'Unknown create results are not adopted or retried; cleanup deletes only recorded owned UIDs',
            'Private failure logs, final deny-all and production shape checks remain required',
            'Delivered bytes match reviewed successor; no server execution or rollout claimed'],
        root_repeated_tests=False, server_execution=False, native_runtime_passed=False, ready_for_rollout=False))
    print(json.dumps(dict(status='NATIVE_QUALIFICATION_R2_DELIVERED_LOCALLY', script=ref(DEST / NAME), command=COMMAND), sort_keys=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--expected-script-sha', required=True)
    main(parser.parse_args().expected_script_sha)
