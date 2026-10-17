"""Package exact locally qualified r3 bytes for one manual native session.

Local artifact checks only: this module never imports or invokes the controller.
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
R2 = PREP / 'native-runtime-qualification-r2'
ATTEMPT = PREP / 'native-runtime-attempt-r2'
GUARD = PREP / 'native-runtime-link-guard-r1'
MERGE = Path('C:/Users/15285/Documents/deer-flow/portal-ppt-merge-20261017')
DEST = Path('C:/Users/15285/Downloads/portal-ppt-native-qualification-20261017-r3')
NAME = 'qualify-portal-ppt-native-20261017-r3.py'
COMMAND = 'sudo python3 -I -B -u /home/cnooc/' + NAME + ' --qualify-native-once'
PRELOAD_SHA = '99135299a8615bcadc90af60ab6e3332665e9714826b847d9a1f5e48c426688a'
PRIOR_SCRIPT_SHA = '8a0f48570039680348d9a8ecb97b07eca875975a8a36f7778c8a5442d0c72273'
PRIOR_REPORT_SHA = 'cc10c4d5996ed6d7fa16e9fa70399d0efaded3530e1833b8df1369102eaec5e2'


def ref(path):
    raw = path.read_bytes()
    return dict(path=str(path).replace('\\', '/'), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def read(path):
    return json.loads(path.read_bytes())


def check_pin(path, size, sha):
    actual = ref(path)
    assert actual['bytes'] == size and actual['sha256'] == sha, str(path)
    return actual


def check_refs(value, base):
    if isinstance(value, dict):
        if {'path', 'bytes', 'sha256'} <= set(value):
            path = Path(value['path'])
            if not path.is_absolute():
                path = base / path
            check_pin(path, value['bytes'], value['sha256'])
        for child in value.values():
            check_refs(child, base)
    elif isinstance(value, list):
        for child in value:
            check_refs(child, base)


def write_new(path, raw):
    with path.open('xb') as stream:
        stream.write(raw)


def write_json(path, value):
    write_new(path, (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + '\n').encode())


def controller_payload(path):
    tree = ast.parse(path.read_bytes())
    values = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
              if isinstance(node, ast.Assign) and len(node.targets) == 1
              and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Constant)}
    packed = base64.b64decode(values['PAYLOAD_Z64'], validate=True)
    decoder = zlib.decompressobj()
    raw = decoder.decompress(packed, 2097153)
    assert len(raw) <= 2097152 and decoder.eof and not decoder.unconsumed_tail and not decoder.unused_data
    assert hashlib.sha256(raw).hexdigest() == values['PAYLOAD_SHA']
    return values, json.loads(raw)


def reviewed(path, base, qualification=False):
    value = read(path)
    assert value['status'].startswith('PASS') if qualification else value['status'] == 'PASS'
    assert value.get('blockers', []) == []
    check_refs(value, base)
    return value


def main(expected):
    assert re.fullmatch('[a-f0-9]{64}', expected), 'Exact final script SHA256 required'
    candidate = HERE / 'run_native.py'
    pin = ref(candidate)
    assert pin['sha256'] == expected
    qualification_path = HERE / 'QUALIFICATION.json'
    review_path = HERE / 'INDEPENDENT-REVIEW.json'
    qualification = reviewed(qualification_path, HERE, qualification=True)
    review = reviewed(review_path, HERE)
    assert qualification['script']['sha256'] == expected and review['final_script_sha256'] == expected
    guard_qualification = GUARD / 'QUALIFICATION.json'
    guard_review = GUARD / 'INDEPENDENT-REVIEW.json'
    reviewed(guard_qualification, GUARD, qualification=True)
    reviewed(guard_review, GUARD)
    kernel_path = GUARD / 'KERNEL-QUALIFICATION.json'
    assert read(kernel_path)['status'].startswith('PASS')

    # Preserve and bind the completed preload and both failed native attempts.
    prior_script = check_pin(R2 / 'run_native.py', 143240, PRIOR_SCRIPT_SHA)
    prior_report = check_pin(ATTEMPT / 'REPORT.json', 2873, PRIOR_REPORT_SHA)
    cause_path = ATTEMPT / 'REVIEW.json'
    cause = read(cause_path)
    assert cause['status'] == 'EXACT_REPORT_ACCEPTED_OBSERVED_NETWORK_ISOLATION_NOT_ENFORCED'
    assert cause['report_sha256'] == PRIOR_REPORT_SHA and cause['connect_blocked_samples'] == 0
    assert cause['post_withdrawal_both_directions'] == 'SUCCESS_IN_ALL_THREE_ROUNDS'
    preload_path = PREP / 'native-preload-complete-evidence-r1/REPORT.json'
    check_pin(preload_path, 24125, PRELOAD_SHA)
    assert read(PREP / 'native-preload-complete-evidence-r1/REVIEW.json')['status'] == 'EXACT_REPORT_ACCEPTED_ALL_SIX_PRELOAD_PASS'
    db_review_path = PREP / 'native-runtime-qualification-r1/ROOT-DB-STAGE-REVIEW.json'
    hf_review_path = PREP / 'native-runtime-host-frontend-r1/INDEPENDENT-REVIEW.json'
    reviewed(db_review_path, db_review_path.parent)
    reviewed(hf_review_path, hf_review_path.parent)

    constants, payload = controller_payload(candidate)
    old_constants, old_payload = controller_payload(R2 / 'run_native.py')
    assert set(payload) == set(old_payload) | {'link_guard_stage.py'}
    assert all(payload[key] == value for key, value in old_payload.items()), 'Frozen existing payload changed'
    assert constants['PRELOAD_SHA'] == PRELOAD_SHA and constants['PRIOR_REPORT_SHA'] == PRIOR_REPORT_SHA
    assert constants['PRIOR_OWNER'] == read(ATTEMPT / 'REPORT.json')['owner']
    assert constants['NODE'] == 'datax-02' and constants['NODE_UID'] == '3148d4c1-6a97-42ed-a78c-950dcea5cbec'
    assert constants['MECHANISM'] == 'LINK_DOWN_LOOPBACK_ONLY'
    assert constants['ONCE'] == '/var/lib/portal-ppt-native-qualification-r3.STARTED.json'
    assert constants['LOCK'] == '/var/lib/portal-ppt-native-qualification-r3.lock'
    assert constants['ONCE'] != old_constants['ONCE'] and constants['LOCK'] != old_constants['LOCK']
    sources = {
        'network_probe.py': MERGE / 'native-network-proof-r1/network_probe.py',
        'TEMPLATE.json': MERGE / 'native-network-proof-r1/TEMPLATE.json',
        'host_frontend_stage.py': PREP / 'native-runtime-host-frontend-r1/host_frontend_stage.py',
        'db_stage.py': PREP / 'native-runtime-qualification-r1/db_stage.py',
        'link_guard_stage.py': GUARD / 'link_guard.py',
    }
    for name, path in sources.items():
        assert payload[name].encode() == path.read_bytes(), name
    assert payload['preload_report'] == dict(bytes=24125, sha256=PRELOAD_SHA)
    preload = read(preload_path)
    for name, file in [('host_runtime_inputs', 'HOST-RUNTIME-INPUTS.json'), ('postgres_cache', 'POSTGRES-CACHE.json')]:
        assert payload[name] == read(preload_path.parent / file) == preload[name]
    compile(candidate.read_bytes(), str(candidate), 'exec')

    DEST.mkdir(exist_ok=False)
    write_new(DEST / NAME, candidate.read_bytes())
    assert ref(DEST / NAME)['sha256'] == expected
    runbook = f'''原生 ARM64 隔离验收 r3 — 在 datax-01 执行

手动复制 {NAME} 到 datax-01 的 /home/cnooc/。
只需复制这个小脚本；复用 datax-02 已预载的六个镜像及 PostgreSQL 缓存。
保留 r1/r2 的报告、私人日志、once 标记和命名空间；不要重跑旧脚本。

执行一次：
{COMMAND}

r2 的实际三轮双向探针表明临时 Pod 隔离未生效。r3 不改变生产网络组件。
r3 在本次拥有的业务测试 Pod 首个普通 init 容器中，用固定 Gateway 镜像及仅 NET_ADMIN 能力关闭该 Pod 的非环回接口，保留并核对环回接口。
后续业务容器保留原用户，能力集全部为零；启动前核对只读回执、实际网络命名空间、接口和权限。
网络证明阶段仅固定的自有 subject 测试容器持有 NET_ADMIN，以便在同一 Pod 中完成关闭前后的真实双向请求和健康检查；该容器结束后按记录的 UID 清理。
结果明确标记 LINK_DOWN_LOOPBACK_ONLY，不表示生产 NetworkPolicy 已执行。
若准入拒绝该受限能力，脚本会停止，不修改 PodSecurity 或集群网络设置。

通过网络验证后，依次执行 Host、前端、PG 安全路径/用户探针，以及五步数据库升级、重启和兼容回滚验收。
所有测试由默认调度器定向到固定 UID 的 datax-02，不抢占其他 Pod，不拉取镜像。
测试使用临时数据库和内存卷，不连接生产数据库、不调用模型、不切换生产服务。
会创建和按 UID 清理本次测试资源，包括值为0、非全局默认、Never抢占策略的专属 PriorityClass。
结束后保留本次测试命名空间和 deny-all 策略；集群实际策略执行能力仍未证明。
等待阶段每约15秒显示状态，网络测试逐轮显示进度；不要在另一个窗口重复执行。

结束后贴回完整终端输出，保留 download_report 指向的 REPORT.json，手动复制回本机供核验。
失败或中断时保留现场、私人日志及 once 标记，先不要重跑。
本地真实 Linux 网络测试及控制流模拟通过，不等于服务器原生验收通过；脚本始终不会标记可上线。

脚本大小：{pin['bytes']} 字节
脚本 SHA256：{expected}
'''
    write_new(DEST / 'RUNBOOK.txt', runbook.encode())
    delivery = dict(
        schema='portal-native-qualification-r3-delivery.v1', status='REVIEWED_READY_FOR_MANUAL_NATIVE_QUALIFICATION',
        execution_host='datax-01', target_node='datax-02', target_node_uid=constants['NODE_UID'],
        server_path='/home/cnooc/' + NAME, command=COMMAND,
        script=pin, delivered_script=ref(DEST / NAME), runbook=ref(DEST / 'RUNBOOK.txt'),
        qualification=ref(qualification_path), independent_review=ref(review_path),
        link_guard_qualification=ref(guard_qualification), link_guard_independent_review=ref(guard_review),
        local_linux_kernel_qualification=ref(kernel_path),
        database_independent_root_review=ref(db_review_path), host_frontend_independent_review=ref(hf_review_path),
        prior_script=prior_script, prior_failed_report=prior_report, prior_cause_review=ref(cause_path),
        predecessor_files_preserved=True, accepted_preload_report=ref(preload_path),
        embedded_sources={name: ref(path) for name, path in sources.items()},
        existing_embedded_payload_values_unchanged_from_r2=True, only_added_payload_source='link_guard_stage.py',
        isolation_mechanism='LINK_DOWN_LOOPBACK_ONLY', network_policy_enforcement_proven=False,
        net_admin_scope=['EXACT_OWNED_PROOF_SUBJECT', 'FIRST_ORDINARY_INIT_OF_OWNED_BUSINESS_TEST_POD'],
        business_original_uid_preserved=True, business_capabilities_zero=True,
        production_cni_change=False, podsecurity_configuration_change=False,
        single_file=True, extra_image_transfer_required=False, image_reimport=False, automatic_retry=False,
        owned_cluster_priorityclass_mutation_on_execution=True,
        priorityclass_scope=dict(value=0, globalDefault=False, preemptionPolicy='Never', cleanup='OWNED_EXACT_UID_ONLY'),
        isolated_kubernetes_fixture_mutation_on_execution=True, isolated_database_fixture_mutation_on_execution=True,
        production_workload_mutation=False, production_database_access=False, model_access=False,
        existing_images_deleted=False, server_executed=False, native_runtime_passed=False, ready_for_rollout=False)
    write_json(HERE / 'DELIVERY.json', delivery)
    write_json(HERE / 'ROOT-DELIVERY-REVIEW.json', dict(
        schema='portal-native-qualification-r3-root-delivery-review.v1', status='PASS', blockers=[],
        delivery=ref(HERE / 'DELIVERY.json'), delivered_script=ref(DEST / NAME),
        qualification=ref(qualification_path), independent_review=ref(review_path),
        link_guard_qualification=ref(guard_qualification), link_guard_independent_review=ref(guard_review),
        local_linux_kernel_qualification=ref(kernel_path), database_independent_root_review=ref(db_review_path),
        checks=[
            'Exact failed r2 report (2873 bytes) and original r1/r2 controllers remain preserved',
            'Distinct r3 once/lock and exact prior failure binding; no repeat of previous failed sessions',
            'Final qualification and independent reviews bind the exact supplied candidate and helper bytes',
            'All old network, Host/Frontend and DB helper/projection payload values remain exact',
            'New helper is independently reviewed and qualified using a local actual Linux network fixture',
            'NET_ADMIN limited to exact trusted proof subject and first ordinary init of owned business test Pods',
            'Actual live Pod UID checked before privileged proof guard; business preexec verifies receipt/netns/links/capabilities',
            'LINK_DOWN_LOOPBACK_ONLY explicit; NetworkPolicy enforcement is not claimed; CNI/PSA not changed',
            'Actual cached image, scheduler, node/Pod/container identity, health barriers and three denial rounds retained',
            'Unknown creates never adopted or retried; owned UID cleanup and bounded private evidence retained',
            'Delivered bytes match reviewed candidate; no server native success or rollout readiness claimed'],
        root_repeated_tests=False, server_execution=False, native_runtime_passed=False, ready_for_rollout=False))
    print(json.dumps(dict(status='NATIVE_QUALIFICATION_R3_DELIVERED_LOCALLY', script=ref(DEST / NAME), command=COMMAND), sort_keys=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--expected-script-sha', required=True)
    main(parser.parse_args().expected_script_sha)
