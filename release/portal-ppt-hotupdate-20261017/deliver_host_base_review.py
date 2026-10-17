"""Record completed review and deliver the exact fixed diagnostic, without running it."""
import datetime
import hashlib
import json
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HERE = ROOT / 'host-base-verify-r1'
DEST = Path('C:/Users/15285/Downloads/portal-ppt-host-base-20261017-r1')
LOCKS = {
    'verify_host_base.py': '57fc8798ae3fac30dbf73862d66ad670319a9eea3d85436d4e68330ac57b5eee',
    'EXPECTATIONS.json': '968f8bff22c6072b8c1075386198e6af5f23fff2c8e1e89651720084ee3ee199',
    'frozen_gateway_replay.py': 'ee7500ca36844d96308a83caef0663766569c42aa935bee2fd2c34f653b4751a',
    'frozen_export_verifier.py': '23f61ad3164f37856c3c320c34a4dd8a0c81020e2bc9d14a783736f7a8a0be0f',
    'wrapper_bootstrap.py': 'd3241115745c1aba0d40fcc54e2989170b14fb107060a2e43218a0eeb2948f5c',
    'generate_wrapper.py': '0b299e09d9abee98acdedc996cfbf3cd4237d75fcbd4b529759374aa709257ff',
    'WRAPPER-QUALIFICATION.json': '2d75c8dbd3945e28d33e371d832509d9401cb72eeaa25d669d4e81bce57b6638',
    'run-portal-ppt-host-base-20261017-r1.sh': 'fb304d18f45409caac4ddff39150bd89a144b538c8e79329d242f7eb72d4dd41',
}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    records = []
    for name, expected in LOCKS.items():
        raw = (HERE / name).read_bytes()
        assert digest(raw) == expected, name
        records.append({'path': str(HERE / name), 'sha256': expected, 'bytes': len(raw)})
    qualification = json.loads((HERE / 'WRAPPER-QUALIFICATION.json').read_text())
    for row in qualification['files']:
        raw = (HERE / row['path']).read_bytes()
        assert len(raw) == row['bytes'] and digest(raw) == row['sha256'], row['path']
    assert qualification['linux']['passed'] == 7 and qualification['linux']['exit_code'] == 0
    assert qualification['bash_n']['exit_code'] == 0
    rendered = runpy.run_path(str(HERE / 'generate_wrapper.py'))['render']()
    name = 'run-portal-ppt-host-base-20261017-r1.sh'
    assert rendered == (HERE / name).read_bytes()
    DEST.mkdir(exist_ok=False)
    with (DEST / name).open('xb') as target:
        target.write(rendered)
    assert digest((DEST / name).read_bytes()) == LOCKS[name]
    (DEST / 'SHA256SUMS.txt').write_text(LOCKS[name] + '  ' + name + '\n', encoding='ascii')
    (DEST / 'README.txt').write_text(
        '只需把本目录的 .sh 文件上传到 datax-01 的 /home/cnooc/。\n'
        '随后在 datax-01 执行一整行：\n'
        'sudo bash /home/cnooc/run-portal-ppt-host-base-20261017-r1.sh\n\n'
        '它只读取固定的 Agent Host 镜像包并生成脱敏报告；不操作 Kubernetes，不启动镜像。\n'
        '核验较大的现有镜像包可能需要数分钟，上限约 31 分钟。\n'
        '结束后保留终端输出，并将 download_report 指向的 REPORT.json 取回交给本任务。\n'
        'collector_exit_code=2 与 DIAGNOSTIC_COMPLETE_REVIEW_REQUIRED 同时出现是待审阅状态。\n'
        '若出现错误，保留现场，不要自动重跑。\n', encoding='utf-8')
    receipt = {
        'schema': 'portal-ppt-host-base-independent-review-delivery.v1',
        'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'status': 'FIXED_READ_ONLY_DIAGNOSTIC_REVIEWED_AND_DELIVERED',
        'files': records,
        'wrapper_reproduces_exactly': True,
        'reviewed': [
            'fixed datax root and archive identity, no image execution or extraction',
            'full bounded OCI closure and diffID replay; 13 fixed source checks',
            'single historical unsafe name is diagnostic only, disjoint under both spellings',
            'known unsafe header can never grant ready-for-build or rollout',
            'one bounded child process group; no retry; minimal environment',
            'root-private no-follow report and user handoff after final writes',
            'embedded four files and qualification evidence hashes reconciled',
        ],
        'operator_script': str(DEST / name),
        'operator_command': 'sudo bash /home/cnooc/run-portal-ppt-host-base-20261017-r1.sh',
        'server_executed': False,
        'production_mutation': False,
        'ready_for_build': False,
        'ready_for_rollout': False,
    }
    with (HERE / 'INDEPENDENT-REVIEW-DELIVERY.json').open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(receipt, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': receipt['status'], 'script': str(DEST / name), 'sha256': LOCKS[name]}))


if __name__ == '__main__':
    main()
