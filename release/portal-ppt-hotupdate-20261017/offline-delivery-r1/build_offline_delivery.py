"""Package the five existing local OCI archives without any network operation."""
import hashlib
import json
import os
from pathlib import Path
import tarfile
import time

ROOT = Path(__file__).resolve().parent
INPUT = Path('C:/Users/15285/Downloads/portal-ppt-native-preload-20261017-r1')
OUTPUT = Path('C:/Users/15285/Downloads/portal-ppt-native-offline-20261017-r1')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class CheckedReader:
    def __init__(self, stream, expected):
        self.stream, self.expected = stream, expected
        self.digest, self.count = hashlib.sha256(), 0
    def read(self, size):
        raw = self.stream.read(size)
        self.digest.update(raw)
        self.count += len(raw)
        return raw


class CheckedWriter:
    def __init__(self, stream):
        self.stream, self.digest, self.count = stream, hashlib.sha256(), 0
    def write(self, raw):
        count = self.stream.write(raw)
        assert count == len(raw), 'partial_output_write'
        self.digest.update(raw)
        self.count += count
        return count


def write_new(path, data):
    with path.open('xb') as stream:
        stream.write(data)


def main():
    manifest_raw = (INPUT / 'UPLOAD-MANIFEST.json').read_bytes()
    delivery = json.loads((ROOT.parent / 'native-preload-r1/UPLOAD-DELIVERY.json').read_bytes())
    expected_manifest = next(r for r in delivery['files'] if r['name'] == 'UPLOAD-MANIFEST.json')
    assert sha(manifest_raw) == expected_manifest['sha256']
    manifest = json.loads(manifest_raw)
    assert len(manifest['tools']) == 3 and len(manifest['archives']) == 5
    assert manifest['archive_bytes'] == 3818267648
    rows = manifest['tools'] + manifest['archives']
    expected = [{k:r[k] for k in ('name','bytes','sha256')} for r in rows]
    assert len({r['name'] for r in expected}) == 8
    OUTPUT.mkdir(exist_ok=False)
    output_tar = OUTPUT / 'portal-ppt-native-offline-20261017-r1.tar'
    started = time.monotonic()
    with output_tar.open('xb') as output:
        writer = CheckedWriter(output)
        with tarfile.open(fileobj=writer, mode='w|', format=tarfile.USTAR_FORMAT) as tar:
            for row in rows:
                source = INPUT / row['name'] if row['kind'] == 'tool' else Path(row['path'])
                assert source.is_file() and not source.is_symlink()
                print(json.dumps({'packing':row['name'], 'bytes':row['bytes']}), flush=True)
                with source.open('rb') as stream:
                    before = os.fstat(stream.fileno())
                    assert before.st_size == row['bytes']
                    item = tarfile.TarInfo(row['name'])
                    item.size, item.mode, item.mtime = row['bytes'], 0o600, 1792195200
                    item.uid = item.gid = 0
                    item.uname = item.gname = ''
                    checked = CheckedReader(stream, row)
                    tar.addfile(item, checked)
                    after = os.fstat(stream.fileno())
                    assert checked.count == row['bytes'] and checked.digest.hexdigest() == row['sha256'], row['name']
                    assert (before.st_size,before.st_mtime_ns,before.st_ctime_ns)==(after.st_size,after.st_mtime_ns,after.st_ctime_ns), row['name']
        output.flush()
        os.fsync(output.fileno())
        assert output.tell() == writer.count
    template = (ROOT / 'installer_template.py').read_text(encoding='utf-8')
    replacements = {'__EXPECTED_FILES__':repr(expected),'__BUNDLE_BYTES__':str(writer.count),'__BUNDLE_SHA256__':writer.digest.hexdigest()}
    for key, value in replacements.items():
        assert template.count(key) == 1
        template = template.replace(key,value)
    compile(template, 'offline-installer', 'exec')
    script = ('#!/usr/bin/env bash\nset -eu\nif [ "$#" -ne 0 ]; then echo "No arguments are supported." >&2; exit 2; fi\n'
              "python3 -I -B -u - <<'PORTAL_OFFLINE_PY'\n" + template + '\nPORTAL_OFFLINE_PY\n'
              + manifest['server_command'] + '\n').encode('utf-8')
    script_name='run-portal-ppt-native-offline-20261017-r1.sh'
    write_new(OUTPUT / script_name, script)
    write_new(OUTPUT / 'README.txt', (
        '离线转运包：通过你现有的文件传输渠道，把以下两个文件原名传到 datax-01 的 /home/cnooc/：\n'
        'portal-ppt-native-offline-20261017-r1.tar\n'+script_name+'\n\n'
        '以 cnooc 登录 datax-01，执行（不要在 bash 前加 sudo）：\n'
        'bash /home/cnooc/'+script_name+'\n\n'
        '脚本先校验整个离线包和全部八个文件，解到新目录，然后调用已审阅的预载程序；按提示输入 sudo/SSH 密码。\n'
        'Host 大包复用 datax-01 上的现存文件；服务器间 datax-01 → datax-02 仍使用内网 SSH。\n'
        '结束后贴回完整终端输出。失败保留现场，不自动重跑；不切换生产服务。\n'
    ).encode('utf-8'))
    report={'schema':'portal-ppt-native-offline-delivery.v1','status':'PACKAGED_REVIEW_PENDING',
            'directory':str(OUTPUT),'bundle':{'name':output_tar.name,'bytes':writer.count,'sha256':writer.digest.hexdigest()},
            'script':{'name':script_name,'bytes':len(script),'sha256':sha(script)},'members':expected,
            'elapsed_seconds':round(time.monotonic()-started,3),'source_hashes_verified_while_packaging':True,
            'host_archive_included':False,'network_access':False,'ready_for_rollout':False,
            'server_command':'bash /home/cnooc/'+script_name}
    write_new(ROOT / 'DELIVERY.json', (json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
    print(json.dumps(report,ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
