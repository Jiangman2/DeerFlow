"""Bounded, metadata-only disk cleanup inventory for datax-01; deletes nothing."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import time

REPORT = Path('/var/lib/portal-runner-oa-schema-compatibility-report-mmfccu1f/REPORT.json')
REPORT_BYTES = 17517
REPORT_SHA = 'f80540b32a3f59549d31d6d96abbe8774297476425c72c38fc83205cbcc59210'
OPERATION = '10ffc378f8de4920afcc9de26968fc71'
SAFE_PATH = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
FIELDS = ('device', 'paths', 'available_bytes', 'capacity_bytes', 'available_inodes',
          'total_inodes', 'inherited_required_bytes', 'inherited_required_inodes',
          'control_write_allowance_bytes', 'required_bytes', 'sixteen_percent_ceil_bytes',
          'extra_headroom_bytes', 'predicted_gc_usage_percent', 'pass')


def report_observation():
    result = {'phase': 'report_read', 'verified': False}
    try:
        with REPORT.open('rb') as stream:
            raw = stream.read(2 * 1024 * 1024 + 1)
        result.update(observed_bytes=len(raw), observed_sha256=hashlib.sha256(raw).hexdigest(),
                      expected_bytes=REPORT_BYTES, expected_sha256=REPORT_SHA)
        if len(raw) != REPORT_BYTES or result['observed_sha256'] != REPORT_SHA:
            return dict(result, error_code='REPORT_MISMATCH')
        result['phase'] = 'report_state'
        value = json.loads(raw)
        result['operation_matches'] = value.get('operation') == OPERATION
        result['import_not_requested'] = value.get('image_import_requested') is False
        if not result['operation_matches'] or not result['import_not_requested']:
            return dict(result, error_code='REPORT_STATE_CHANGED')
        result['phase'] = 'storage_projection'
        rows = []
        for check in value.get('storage_checks', [])[-6:]:
            groups = []
            for device in check.get('devices', [])[:16]:
                row = {k: device[k] for k in FIELDS if k in device}
                if all(type(device.get(k)) is int for k in ('required_bytes', 'available_bytes')):
                    row['shortfall_bytes'] = max(0, device['required_bytes'] - device['available_bytes'])
                groups.append(row)
            rows.append({'phase': check.get('phase'), 'pass': check.get('pass'), 'devices': groups})
        result.update(verified=True, phase='complete', storage_checks=rows)
    except Exception as exc:
        result.update(error_class=type(exc).__name__, error_code='REPORT_INSPECTION_FAILED')
    return result


def filesystem(path):
    try:
        s = os.statvfs(str(path))
        return {'path': str(path), 'device': path.stat().st_dev,
                'capacity_bytes': s.f_blocks * s.f_frsize,
                'available_bytes': s.f_bavail * s.f_frsize,
                'available_inodes': s.f_favail, 'total_inodes': s.f_files}
    except OSError as exc:
        return {'path': str(path), 'error_class': type(exc).__name__, 'errno': exc.errno}


def sizes(path):
    tool = shutil.which('du', path=SAFE_PATH)
    if not tool:
        return {'path': str(path), 'error_code': 'DU_UNAVAILABLE'}
    args = [tool, '-x', '-B1', '--max-depth=1', '--', str(path)]
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        if len(p.stdout) > 262144:
            return {'path': str(path), 'error_code': 'OUTPUT_LIMIT'}
        rows = []
        for line in p.stdout.decode('utf-8', 'replace').splitlines():
            parts = line.split('\t', 1)
            if len(parts) == 2 and parts[0].isdigit():
                rows.append({'bytes': int(parts[0]), 'path': parts[1]})
        return {'path': str(path), 'returncode': p.returncode,
                'rows': sorted(rows, key=lambda r: r['bytes'], reverse=True)[:32],
                'stderr_bytes': len(p.stderr)}
    except subprocess.TimeoutExpired:
        return {'path': str(path), 'error_code': 'DU_TIMEOUT'}


def large_files(root, seconds=10, limit=20000):
    rows, scanned, pending, errors = [], 0, [root], 0
    start = time.monotonic()
    try:
        device = root.stat().st_dev
        while pending and scanned < limit and time.monotonic() - start < seconds:
            current = pending.pop()
            try:
                with os.scandir(str(current)) as entries:
                    for entry in entries:
                        scanned += 1
                        if scanned >= limit or time.monotonic() - start >= seconds:
                            break
                        try:
                            info = entry.stat(follow_symlinks=False)
                        except OSError:
                            errors += 1
                            continue
                        if info.st_dev != device:
                            continue
                        if stat.S_ISDIR(info.st_mode):
                            pending.append(Path(entry.path))
                        elif stat.S_ISREG(info.st_mode) and info.st_size >= 32 * 1024 * 1024:
                            rows.append({'path': entry.path, 'bytes': info.st_size,
                                         'allocated_bytes': info.st_blocks * 512,
                                         'device': info.st_dev, 'inode': info.st_ino,
                                         'links': info.st_nlink, 'uid': info.st_uid,
                                         'mtime_ns': info.st_mtime_ns})
                            rows = sorted(rows, key=lambda r: r['allocated_bytes'], reverse=True)[:40]
            except OSError:
                errors += 1
        return {'root': str(root), 'files': rows, 'scanned_entries': scanned,
                'bounded_scan_incomplete': bool(pending) or scanned >= limit or time.monotonic() - start >= seconds,
                'errors': errors}
    except OSError as exc:
        return {'root': str(root), 'error_class': type(exc).__name__}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspect-readonly', action='store_true', required=True)
    parser.parse_args()
    host = os.uname().nodename.split('.')[0]
    if host != 'datax-01':
        print(json.dumps({'status': 'INCOMPLETE', 'phase': 'host', 'error_code': 'HOST_CHANGED', 'modified': False}))
        return 1
    result = {'schema': 'portal-datax01-cleanup-inventory.v2', 'hostname': host,
              'report': report_observation(), 'filesystems': [], 'directory_sizes': [], 'large_files': [],
              'files_modified': False, 'containerd_commands': 0, 'cluster_queries': 0,
              'workload_modified': False, 'cleanup_performed': False}
    result['filesystems'] = [filesystem(Path(p)) for p in ('/', '/var/lib', '/home/cnooc', '/mnt/data')]
    for path in ('/var/lib', '/var/log', '/home/cnooc', '/tmp', '/var/tmp'):
        result['directory_sizes'].append(sizes(Path(path)))
        print(json.dumps({'phase': 'READONLY_DIRECTORY_SIZES', 'checked': len(result['directory_sizes']), 'total': 5}), flush=True)
    for path in ('/home/cnooc', '/tmp', '/var/tmp', '/var/log'):
        result['large_files'].append(large_files(Path(path)))
    result['status'] = 'READONLY_CLEANUP_INVENTORY_COLLECTED'
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
