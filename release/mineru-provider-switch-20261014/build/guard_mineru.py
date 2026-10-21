"""Strict local files, read-only Kubernetes baseline and bounded disk budget. Py3.7+."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from isolated_buildkit import opt_path, run
from source_contract import BASE_METADATA, SourceCheck, validate_plan

GIB = 1024**3


class CheckFailure(RuntimeError):
    pass


def require(value, label):
    if not value:
        raise CheckFailure(label)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_file(path, limit, writable=False):
    path = Path(path)
    for parent in path.parents:
        s = parent.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
                'unsafe_file_ancestor')
    s = path.lstat()
    require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1 and
            (writable or not s.st_mode & 0o022) and 0 < s.st_size <= limit, 'unsafe_file:' + path.name)
    return s


def verify_package(root):
    safe_file(root / 'PACKAGE.SHA256', 65536)
    names = set()
    for line in (root / 'PACKAGE.SHA256').read_text(encoding='ascii').splitlines():
        m = re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9][A-Za-z0-9_.-]*)', line)
        require(m is not None, 'invalid_package_manifest')
        checksum, name = m.groups()
        require(name not in names and name != 'PACKAGE.SHA256', 'duplicate_package_member')
        safe_file(root / name, 8*1024**2)
        require(sha((root / name).read_bytes()) == checksum, 'package_hash_mismatch:' + name)
        names.add(name)
    require(names and {p.name for p in root.iterdir()} == names | {'PACKAGE.SHA256'}, 'unexpected_package_contents')
    plan = json.loads((root / 'BUILD-PLAN.json').read_bytes())
    require(plan['scope'] == 'MINERU_ASYNC_QUERY_COMPATIBILITY_ONLY', 'unexpected_scope')
    require(sha((root / plan['source']['payload']).read_bytes()) == plan['source']['after_sha256'], 'payload_hash_changed')
    try:
        validate_plan(plan['source'])
    except SourceCheck as exc:
        raise CheckFailure(str(exc)) from None
    return plan


def archive_input(plan):
    base = plan['base']
    archive, report = Path(base['archive']), Path(base['report'])
    opt_path(archive.parent)
    require(safe_file(archive, 3*GIB).st_size == base['bytes'], 'archive_size_changed')
    safe_file(report, 1024**2)
    raw = report.read_bytes()
    require(sha(raw) == base['report_sha256'], 'base_report_hash_changed')
    receipt = json.loads(raw)
    require(receipt.get('base_export') == 'PASS' and receipt.get('base_layer_verification') == 'PASS'
            and receipt.get('uncompressed_diffids') == 'PASS', 'base_export_not_verified')
    for field in ('archive_sha256', 'manifest_digest', 'config_digest', 'bytes', 'uncompressed_layer_bytes'):
        require(receipt.get(field) == base[field], 'base_receipt_mismatch:' + field)
    return archive


def seal_new_export(path, output, limit):
    require(path.parent == output and path.name in ('mineru.candidate.oci.tar.partial',
            'mineru-build-metadata.json'), 'not_owned_new_export')
    s = output.lstat()
    require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and stat.S_IMODE(s.st_mode) == 0o700,
            'unsafe_output_directory')
    before = safe_file(path, limit, writable=True)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    try:
        current = os.fstat(fd)
        identity = lambda x: (x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns)
        require(identity(current) == identity(before), 'new_export_raced')
        os.fchmod(fd, 0o600)
        after = path.lstat()
        require(identity(after) == identity(current) and stat.S_IMODE(after.st_mode) == 0o600,
                'new_export_raced_after_seal')
    finally:
        os.close(fd)


def available_memory():
    rows = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    return int(rows['MemAvailable'].split()[0]) * 1024


class SpaceGuard:
    """Reserve a full 45 GiB peak budget at start, then charge its consumption.

    Kernel limits also bound CPU/RAM/IO. No import or containerd snapshot budget
    is inferred from this build-only check.
    """
    def __init__(self, path):
        self.path = path
        self.start_free = None

    def check(self):
        require(os.stat(str(self.path)).st_dev == os.stat('/opt').st_dev != os.stat('/').st_dev,
                'build_must_use_separate_opt_filesystem')
        rows = {}
        for name, path in (('build', self.path), ('root', Path('/'))):
            v = os.statvfs(str(path))
            free, capacity = v.f_bavail*v.f_frsize, v.f_blocks*v.f_frsize
            reserve = max(20*GIB, capacity//5) + (2*GIB if name == 'root' else 0)
            require(v.f_favail > 100000 and not v.f_flag & os.ST_RDONLY, 'filesystem_unusable:' + name)
            if name == 'build':
                if self.start_free is None:
                    require(free >= reserve + 45*GIB, 'insufficient_initial_build_space')
                    self.start_free = free
                used = max(0, self.start_free-free)
                require(used < 45*GIB and free >= reserve + max(0, 45*GIB-used), 'build_space_budget_breached')
            else:
                require(free >= reserve, 'root_disk_reserve_breached')
            rows[name] = {'free_GiB': round(free/GIB, 2), 'reserve_GiB': round(reserve/GIB, 2)}
        return dict(disk_check='PASS', peak_build_budget_GiB=45, filesystems=rows, import_space='NOT_ASSESSED')


def kube(*args):
    try:
        return json.loads(run(['kubectl', '--request-timeout=20s', '-n', 'deer-flow'] + list(args), 35))
    except (RuntimeError, ValueError) as exc:
        if args[0] == 'exec':
            raise CheckFailure('kubernetes_source_read_failed:' + args[1]) from None
        raise CheckFailure('kubernetes_get_failed:' + args[1]) from None


def get(resource, name=None, selector=None):
    args = ['get', resource] + ([name] if name else [])
    if selector:
        args += ['--selector', selector]
    return kube(*(args + ['--show-managed-fields=false', '-o', 'json']))


def fingerprint(dep):
    return dict(uid=dep['metadata']['uid'], spec_sha256=sha(json.dumps(dep['spec'], sort_keys=True,
                       separators=(',', ':')).encode()))


def owned(obj, kind, uid):
    return any(o.get('kind') == kind and o.get('uid') == uid and o.get('controller') is True
               for o in obj.get('metadata', {}).get('ownerReferences', []))


def selector(dep):
    value = dep['spec']['selector']
    require(not value.get('matchExpressions') and value.get('matchLabels'), 'unsupported_selector_shape')
    return ','.join(k+'='+v for k, v in sorted(value['matchLabels'].items()))


def source_container(spec, name, plan):
    rows = [c for c in spec.get('containers', []) if c.get('name') == name]
    require(len(rows) == 1 and rows[0]['image'] == plan['base']['image'], 'unexpected_adapter_image')
    target = plan['source']['path']
    for mount in rows[0].get('volumeMounts', []):
        p = mount['mountPath'].rstrip('/')
        require(p.startswith('/') and '..' not in Path(p).parts, 'invalid_mount_path')
        require(target != p and not target.startswith(p + '/'), 'adapter_source_masked_by_mount')


SOURCE_PROBE = (Path(__file__).with_name('source_contract.py').read_text(encoding='utf-8')
                + '\nimport json\nprint(json.dumps(probe_base(), sort_keys=True))\n')


def live_state(plan, source=False):
    conditions = {r['type']: r['status'] for r in get('node', plan['build_node'])['status']['conditions']}
    require(all(conditions.get(k) == v for k, v in (('Ready', 'True'), ('DiskPressure', 'False'),
            ('MemoryPressure', 'False'), ('PIDPressure', 'False'))), 'build_node_unhealthy')
    result = {}
    for name, expected in sorted(plan['deployments'].items()):
        dep = get('deployment', name)
        fp = fingerprint(dep)
        require(fp == {k: expected[k] for k in ('uid', 'spec_sha256')}, 'production_baseline_changed:' + name)
        require(dep.get('status', {}).get('readyReplicas', 0) >= expected['minimum_ready'], 'production_readiness_below_baseline:' + name)
        result[name] = fp
        if 'container' not in expected:
            continue
        source_container(dep['spec']['template']['spec'], expected['container'], plan)
        if not source:
            continue
        select = selector(dep)
        rs = get('replicasets', selector=select)['items']
        owners = {r['metadata']['uid'] for r in rs if owned(r, 'Deployment', expected['uid'])}
        pods = get('pods', selector=select)['items']
        checked = 0
        for pod in pods:
            if not any(owned(pod, 'ReplicaSet', uid) for uid in owners):
                continue
            if pod['metadata'].get('deletionTimestamp') or pod.get('status', {}).get('phase') != 'Running':
                continue
            statuses = [c for c in pod['status'].get('containerStatuses', []) if c['name'] == expected['container'] and c.get('ready')]
            if not statuses:
                continue
            source_container(pod['spec'], expected['container'], plan)
            require(len(statuses) == 1 and statuses[0].get('imageID', '').split('://')[-1] in
                    (plan['base']['config_digest'], plan['base']['manifest_digest']), 'live_image_identity_changed')
            receipt = kube('exec', pod['metadata']['name'], '-c', expected['container'], '--',
                           '/app/.venv/bin/python', '-I', '-S', '-B', '-c', SOURCE_PROBE)
            if receipt.get('source_probe') == 'FAIL':
                label = receipt.get('failed_check', '')
                require(False, 'live_source_probe:' + (label if re.fullmatch(r'[a-z_]{1,80}', label)
                                                      else 'invalid_error_receipt'))
            require(receipt == {'source_probe': 'PASS', 'source_sha256': plan['source']['before_sha256'],
                                'metadata': BASE_METADATA}, 'live_source_changed')
            checked += 1
        require(checked >= expected['minimum_ready'], 'too_few_source_audits:' + name)
        require(fingerprint(get('deployment', name)) == fp, 'deployment_changed_during_source_audit')
        print('mineru_source_audit=PASS deployment=%s ready_pods=%s legacy_mode=0666 hash_locked=true' % (name, checked), flush=True)
    return result
