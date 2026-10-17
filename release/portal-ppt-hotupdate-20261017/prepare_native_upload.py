"""Build the operator upload folder from independently reviewed small-tool pins."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MERGE = ROOT.parent.parent.parent / 'portal-ppt-merge-20261017'
INVENTORY_SHA = 'e6662fbebf2210d73de5f64498fe7548a718570ef303181016802ea2c118dbef'
OUTPUT = Path('C:/Users/15285/Downloads/portal-ppt-native-preload-20261017-r1')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, raw):
    with path.open('xb') as stream:
        stream.write(raw)


def main():
    raw = (MERGE / 'CANDIDATE-IMAGE-INVENTORY.json').read_bytes()
    assert digest(raw) == INVENTORY_SHA
    inventory = json.loads(raw)
    local = [r for r in inventory['images'] if r['archive']['host'] == 'local']
    assert len(local) == 5
    archives = []
    for image in local:
        row = image['archive']
        path = Path(row['path'])
        assert path.is_file() and not path.is_symlink() and path.stat().st_size == row['bytes']
        archives.append({'kind': 'archive', 'role': image['role'], 'name': path.name,
                         'path': str(path), 'bytes': row['bytes'], 'sha256': row['sha256']})
    # The root review must enumerate exact small files and provide the fixed command.
    # Never ship files discovered by a wildcard or derived from attached report prose.
    review_path = ROOT / 'native-preload-r1/ROOT-DELIVERY-BINDING.json'
    review = json.loads(review_path.read_bytes())
    assert review['status'] == 'REVIEWED_FOR_OPERATOR_PRELOAD_ONLY'
    assert review['inventory_sha256'] == INVENTORY_SHA
    assert review['remote_upload_directory'] == '/home/cnooc/portal-ppt-native-preload-20261017-r1'
    tool_rows = []
    tools = []
    for row in review['files']:
        path = ROOT / 'native-preload-r1' / row['name']
        assert path.parent == ROOT / 'native-preload-r1'
        content = path.read_bytes()
        assert len(content) == row['bytes'] and digest(content) == row['sha256']
        tool_rows.append(dict(row, kind='tool'))
        tools.append((path.name, content))
    assert len({r['name'] for r in tool_rows + archives}) == len(tool_rows) + len(archives)
    assert sum(r['bytes'] for r in archives) == 3818267648
    manifest = {'schema': 'portal-ppt-native-upload.v1',
                'destination': 'cnooc@192.168.111.2',
                'directory': review['remote_upload_directory'],
                'inventory_sha256': INVENTORY_SHA,
                'archives': archives, 'archive_bytes': 3818267648,
                'tools': tool_rows, 'server_command': review['server_command'],
                'server_host_archive_uploaded': False, 'automatic_execution': False}
    manifest_raw = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode()
    template = (ROOT / 'native-upload-template.ps1').read_text(encoding='utf-8')
    assert template.count('__UPLOAD_MANIFEST_SHA256__') == 1
    script = template.replace('__UPLOAD_MANIFEST_SHA256__', digest(manifest_raw)).encode('utf-8')
    OUTPUT.mkdir(exist_ok=False)
    for name, content in tools:
        save(OUTPUT / name, content)
    save(OUTPUT / 'UPLOAD-MANIFEST.json', manifest_raw)
    save(OUTPUT / 'Upload-To-Datax01.ps1', script)
    save(OUTPUT / 'README.txt', (
        '本机五个固定 OCI 包上传到 datax-01，合计 3,818,267,648 字节。\n'
        '不会复制或下载服务器上的 Host 大包；不会在上传时执行服务器预载。\n'
        '在 Windows PowerShell 执行：\n'
        f'powershell -NoProfile -ExecutionPolicy Bypass -File "{OUTPUT / "Upload-To-Datax01.ps1"}"\n'
        '按原方式输入 SSH 密码。上传先逐包校验，再检查目标磁盘和新建私人目录，最后一次 scp 传完。\n'
        '若失败，保留输出及部分文件，不自动重跑。\n'
        '上传成功后登录 datax-01，执行终端打印的固定预载命令。\n'
        '该命令仅核对并预载镜像，不切换 Deployment，不修改生产数据库。\n'
    ).encode('utf-8'))
    record = {'schema': 'portal-ppt-native-upload.local-delivery.v1',
              'path': str(OUTPUT), 'files': [], 'large_archives_copied': False,
              'archive_hashes_recomputed_here': False,
              'archive_hashes_checked_before_upload_and_before_import': True,
              'production_accessed': False, 'ready_for_rollout': False}
    for name in [r['name'] for r in tool_rows] + ['UPLOAD-MANIFEST.json', 'Upload-To-Datax01.ps1', 'README.txt']:
        content = (OUTPUT / name).read_bytes()
        record['files'].append({'name': name, 'bytes': len(content), 'sha256': digest(content)})
    dest = ROOT / 'native-preload-r1/UPLOAD-DELIVERY.json'
    save(dest, (json.dumps(record, ensure_ascii=False, indent=2) + '\n').encode())
    print(json.dumps({'directory': str(OUTPUT), 'receipt': str(dest),
                      'upload_script_sha256': digest(script)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
