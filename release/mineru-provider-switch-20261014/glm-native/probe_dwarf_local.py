"""Local disposable ARM64 container qualification; no network or production."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import repair_dwarf_build as repair


def main():
    start = time.monotonic()
    context = Path(sys.argv[1])
    assert repair.base.digest(context) == '0fe0c953f10926b0587feea3a47e260947e5691a836990ae4a2535c7f0b80838'
    assert subprocess.check_output(['go', 'env', 'GOVERSION']).strip() == b'go1.26.8'
    assert subprocess.check_output(['uname', '-m']).strip() == b'aarch64'
    root = Path('/qualification')
    root.mkdir()
    Path('/out/evidence').mkdir(parents=True)
    seen = set()
    with tarfile.open(str(context), 'r|') as archive:
        for member in archive:
            name = member.name
            if name not in ('source/go.mod', 'source/go.sum') and not name.startswith('offline/go-proxy/'):
                continue
            assert member.isfile() and not member.linkname and not member.issparse()
            assert not name.startswith('/') and '\\' not in name and ':' not in name
            assert all(p not in ('', '.', '..') for p in name.split('/')) and name not in seen
            assert 0 <= member.size < 1024**3
            seen.add(name)
            target = root/name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, target.open('xb') as out:
                import shutil
                shutil.copyfileobj(source, out, 1024**2)
    print(json.dumps({'compiler_probe':'START', 'toolchain':'go1.26.8', 'platform':'linux/arm64',
                      'network':'none', 'proxy_files':len(seen)-2}), flush=True)
    script = root/'preflight.sh'
    script.write_text('set -Eeuo pipefail\n'+repair.COMPILER_PREFLIGHT)
    env = dict(os.environ, GOPROXY='file:///qualification/offline/go-proxy', GOSUMDB='off',
               GOTOOLCHAIN='local', GOWORK='off', GOFLAGS='-mod=readonly', GOMAXPROCS='2', CGO_ENABLED='1')
    subprocess.run(['bash', str(script)], cwd=str(root/'source'), env=env, check=True, timeout=1500)
    result = {'local_compiler_probe':'PASS', 'scope':'ARM64_CONTAINER_EMULATION_NOT_SERVER_FULL_BUILD',
              'toolchain':'go1.26.8', 'elapsed_seconds':round(time.monotonic()-start, 2),
              'compiler':json.loads(Path('/out/evidence/compiler-config.json').read_bytes()),
              'dwarf':json.loads(Path('/out/evidence/dwarf-format.json').read_bytes()),
              'exact_previously_failing_dependency':'PASS', 'provider_requests':0, 'production_writes':0}
    Path('/out/RESULT.json').write_text(json.dumps(result, sort_keys=True, indent=2)+'\n')
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
