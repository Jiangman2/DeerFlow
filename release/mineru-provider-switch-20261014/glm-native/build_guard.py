"""Build-only root-private inputs and read-only production binding. Python3.7+."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import audit_build_inputs as audit

GIB = 1024**3


class CheckFailure(RuntimeError):
    pass


def require(value, message):
    if not value:
        raise CheckFailure(message)


def checksum(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024**2), b''):
            h.update(block)
    return h.hexdigest()


def safe_file(path, maximum):
    for parent in path.parents:
        s = parent.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022, 'unsafe_parent')
    s = path.lstat()
    require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1 and
            not s.st_mode & 0o022 and 0 < s.st_size <= maximum, 'unsafe_file:'+path.name)
    return s


def package(root, payloads=False):
    safe_file(root/'PACKAGE.SHA256', 65536)
    names = set()
    for row in (root/'PACKAGE.SHA256').read_text(encoding='ascii').splitlines():
        m = re.fullmatch(r'([a-f0-9]{64})  ([a-zA-Z0-9][a-zA-Z0-9_.-]+)', row)
        require(m is not None, 'invalid_manifest')
        digest, name = m.groups()
        require(name not in names and name != 'PACKAGE.SHA256', 'duplicate_manifest_member')
        names.add(name)
        safe_file(root/name, 4*GIB if name.endswith('.tar') else 8*1024**2)
        if payloads or not name.endswith('.tar'):
            require(checksum(root/name) == digest, 'package_digest:'+name)
    require({p.name for p in root.iterdir()} == names | {'PACKAGE.SHA256'}, 'unexpected_package_files')
    return json.loads((root/'BUILD-PLAN.json').read_bytes())


def runtime_archive(plan):
    archive = Path('/opt/weknora-glm-base-input-20261016-r1/weknora.base.oci.tar')
    receipt = archive.with_name('TRANSFER-PASS.json')
    require(safe_file(archive, 2*GIB).st_size == plan['runtime_base']['archive_bytes'], 'runtime_archive_size')
    safe_file(receipt, 1024**2)
    require(checksum(receipt) == plan['runtime_base']['transfer_report_sha256'], 'runtime_transfer_receipt')
    return archive


def live(plan, binary=False):
    state = audit.baseline()
    require(state == plan['production_baseline'], 'recovered56_identity_changed')
    node = json.loads(audit.kube(['get', 'node', 'datax-01', '-o', 'json']))
    conditions = {c['type']: c['status'] for c in node['status']['conditions']}
    require(all(conditions.get(k) == v for k, v in [('Ready','True'), ('DiskPressure','False'),
            ('MemoryPressure','False'), ('PIDPressure','False')]), 'build_node_unhealthy')
    if binary:
        result = json.loads(audit.kube(['exec', audit.POD, '-c', 'app', '--', 'gosu', '1000:1000',
            'python3', '-I', '-B', '-c', audit.REMOTE_BINARY], timeout=50, limit=4096))
        require(result.get('sha256') == audit.BASE_BINARY and result.get('process_stable'), 'live_binary_changed')
    return state


def available_memory():
    rows = dict(x.split(':', 1) for x in Path('/proc/meminfo').read_text().splitlines())
    return int(rows['MemAvailable'].split()[0])*1024


class SpaceGuard:
    def __init__(self, path):
        self.path, self.start_free = path, None

    def check(self):
        require(self.path.stat().st_dev == Path('/opt').stat().st_dev != Path('/').stat().st_dev,
                'separate_opt_filesystem_required')
        rows = {}
        for name, path in [('build', self.path), ('root', Path('/'))]:
            v = os.statvfs(str(path))
            free, capacity = v.f_bavail*v.f_frsize, v.f_blocks*v.f_frsize
            reserve = max(20*GIB, capacity//5) + (2*GIB if name == 'root' else 0)
            require(v.f_favail > 100000 and not v.f_flag & os.ST_RDONLY, 'filesystem_unusable:'+name)
            if name == 'build':
                if self.start_free is None:
                    require(free >= reserve + 60*GIB, 'initial_build_space')
                    self.start_free = free
                used = max(0, self.start_free-free)
                require(used < 60*GIB and free >= reserve + max(0, 60*GIB-used), 'build_disk_budget')
            else:
                require(free >= reserve, 'root_disk_reserve')
            rows[name] = {'free_GiB': round(free/GIB,2), 'reserve_GiB': round(reserve/GIB,2)}
        return {'peak_budget_GiB':60, 'filesystems':rows, 'import_space':'NOT_ASSESSED'}
