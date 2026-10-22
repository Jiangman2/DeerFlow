"""Freeze a reviewed local operator bundle; never invokes production tooling."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--collector-sha256', required=True)
    parser.add_argument('--review-sha256', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    helper = (root / 'refresh_baseline_v3.py').read_bytes()
    collector = (root / 'collector.py').read_bytes()
    review = root / 'INDEPENDENT-REVIEW.json'
    if sha(helper) != 'ac6d909f579bddef02675eb248b662eba44c5b5eba1cdd65a24bf7889100a92b':
        raise SystemExit('frozen helper changed')
    if sha(collector) != args.collector_sha256 or sha(review.read_bytes()) != args.review_sha256:
        raise SystemExit('reviewed inputs changed')
    reviewed = json.loads(review.read_bytes())
    names = {'collector.py', 'refresh_baseline_v3.py', 'run-portal-ppt-targeted-20261017-r1.sh.in',
             'test_collector.py', 'test_wrapper.py', 'README.txt'}
    if reviewed.get('status') != 'PASS' or set(reviewed['source_hashes']) != names:
        raise SystemExit('independent review is incomplete')
    for name, expected in reviewed['source_hashes'].items():
        if sha((root / name).read_bytes()) != expected:
            raise SystemExit('reviewed source changed: ' + name)
    wrapper = (root / 'run-portal-ppt-targeted-20261017-r1.sh.in').read_text(encoding='utf-8')
    for marker, value in {
        '__HELPER_SHA256__': sha(helper),
        '__COLLECTOR_SHA256__': sha(collector),
        '__BASELINE_REPORT_SHA256__': 'd7e53f8fb380984a2055c30f5e17bc685867716f847720fa480a8d31a811ba99',
    }.items():
        if wrapper.count(marker) != 1:
            raise SystemExit('unexpected wrapper placeholder')
        wrapper = wrapper.replace(marker, value)
    if '__SHA256__' in wrapper or '\r' in wrapper:
        raise SystemExit('wrapper encoding/placeholder failure')
    intro = '''2026-10-17 热更只读补证 r1

解压后将下列三个文件上传至 datax-01 的 /home/cnooc/：
  run-portal-ppt-targeted-20261017-r1.sh
  portal-ppt-targeted-20261017-r1.py
  portal-ppt-baseline-helper-20261017-r1.py

运行：
  sudo bash /home/cnooc/run-portal-ppt-targeted-20261017-r1.sh

保留原 R2 控制目录：
  /var/lib/portal-ppt-refresh-tool-TeLN6c6D/baseline/REPORT.json
入口会校验三个输入的精确 SHA256；无需改名或手工编辑脚本。

运行后回传终端 download_report 指向的 REPORT.json 和完整终端摘要。
如果失败，保留原报告与 control_directory，先审查，不要自动重复运行。
不要重新执行旧的 R2 全量采集工具，也不要在此步骤更新或重启服务。

此包只补数据库迁移元数据、Runner 3个文件摘要、Gateway 7个文件摘要、
本机镜像元数据及 Sandbox 镜像引用。四组部署/Pod身份仍会检查，但不会再次
对70个Gateway逐一执行源码采样。数据库通过独立只读事务查询，不加载应用，
不执行迁移、不初始化表、不读取业务行；密码、环境值和原始配置不写入报告。
报告先在root私有目录保存，再降至cnooc身份创建可下载副本。
未构建镜像、未发布，ready_for_build/ready_for_rollout始终为false。

详细范围及未采项目：

'''
    payload = {
        'run-portal-ppt-targeted-20261017-r1.sh': wrapper.encode('utf-8'),
        'portal-ppt-targeted-20261017-r1.py': collector,
        'portal-ppt-baseline-helper-20261017-r1.py': helper,
        'README.txt': (intro + (root / 'README.txt').read_text(encoding='utf-8')).encode('utf-8'),
    }
    payload['SHA256SUMS.txt'] = ''.join(sha(raw) + '  ' + name + '\n' for name, raw in sorted(payload.items())).encode('ascii')
    output = Path('C:/Users/15285/Downloads/portal-ppt-targeted-20261017-r1')
    output.mkdir(exist_ok=False)
    for name, raw in payload.items():
        (output / name).write_bytes(raw)
    archive = output / 'portal-ppt-targeted-20261017-r1.zip'
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, raw in payload.items():
            z.writestr(name, raw)
    with zipfile.ZipFile(archive) as z:
        if set(z.namelist()) != set(payload) or z.testzip() is not None:
            raise SystemExit('archive inventory/CRC failure')
        for name, raw in payload.items():
            if z.read(name) != raw or (output / name).read_bytes() != raw:
                raise SystemExit('archive roundtrip mismatch')
    receipt = {
        'schema': 'portal-ppt-targeted-delivery.v1',
        'archive': str(archive), 'archive_sha256': sha(archive.read_bytes()),
        'archive_bytes': archive.stat().st_size,
        'files': {name: {'sha256': sha(raw), 'bytes': len(raw)} for name, raw in payload.items()},
        'collector_review': str(review), 'collector_review_sha256': args.review_sha256,
        'verified_zip_roundtrip': True, 'utf8_lf_wrapper': True,
        'production_execution': False, 'ready_for_build': False, 'ready_for_rollout': False,
    }
    (root.parent / 'DELIVERY-targeted-r1.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
