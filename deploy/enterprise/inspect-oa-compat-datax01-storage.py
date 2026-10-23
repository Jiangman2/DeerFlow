"""Read the exact failed preload report and filesystem metadata, without commands."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat

REPORT = Path('/var/lib/portal-runner-oa-schema-compatibility-report-mmfccu1f/REPORT.json')
REPORT_BYTES = 17517
REPORT_SHA = 'f80540b32a3f59549d31d6d96abbe8774297476425c72c38fc83205cbcc59210'
OPERATION = '10ffc378f8de4920afcc9de26968fc71'
FIELDS = ('device', 'paths', 'available_bytes', 'capacity_bytes', 'available_inodes',
          'total_inodes', 'inherited_required_bytes', 'inherited_required_inodes',
          'control_write_allowance_bytes', 'sixteen_percent_ceil_bytes',
          'extra_headroom_bytes', 'required_bytes', 'predicted_available_after_all_inherited_costs',
          'predicted_gc_usage_percent', 'predicted_available_inodes_after_cost', 'pass')


def read_report():
    with REPORT.open('rb') as stream:
        raw = stream.read(REPORT_BYTES + 1)
    assert len(raw) == REPORT_BYTES and hashlib.sha256(raw).hexdigest() == REPORT_SHA, 'REPORT_MISMATCH'
    value = json.loads(raw)
    assert value['operation'] == OPERATION and value.get('image_import_requested') is False, 'REPORT_STATE_CHANGED'
    return value


def summarize(value):
    rows = []
    for check in value.get('storage_checks', [])[-6:]:
        groups = []
        for device in check.get('devices', []):
            row = {k: device[k] for k in FIELDS if k in device}
            if all(type(device.get(k)) is int for k in ('available_bytes', 'required_bytes')):
                row['shortfall_bytes'] = max(0, device['required_bytes'] - device['available_bytes'])
            groups.append(row)
        rows.append({'phase': check.get('phase'), 'pass': check.get('pass'), 'devices': groups})
    return rows


def filesystem(path):
    row = {'path': str(path)}
    try:
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or path.resolve() != path:
            return dict(row, status='NOT_DIRECT_DIRECTORY')
        space = os.statvfs(str(path))
        row.update(device=info.st_dev, available_bytes=space.f_bavail * space.f_frsize,
                   capacity_bytes=space.f_blocks * space.f_frsize,
                   available_inodes=space.f_favail, total_inodes=space.f_files)
    except OSError as exc:
        row.update(error_class=type(exc).__name__, errno=exc.errno)
    return row


def archives():
    result = []
    entries = list(Path('/home/cnooc').iterdir())
    assert len(entries) <= 4096, 'HOME_ENTRY_LIMIT'
    for path in entries:
        if not path.name.endswith('.oci.tar'):
            continue
        info = path.lstat()
        if stat.S_ISREG(info.st_mode):
            result.append({'path': str(path), 'bytes': info.st_size, 'device': info.st_dev,
                           'inode': info.st_ino, 'links': info.st_nlink, 'mtime_ns': info.st_mtime_ns})
    assert len(result) <= 64, 'ARCHIVE_ENTRY_LIMIT'
    return sorted(result, key=lambda row: row['path'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspect-readonly', action='store_true', required=True)
    parser.parse_args()
    assert os.uname().nodename.split('.')[0] == 'datax-01', 'HOST_CHANGED'
    value = read_report()
    rows = summarize(value)
    paths = {'/var/lib', '/home/cnooc', '/mnt/data'}
    for row in rows:
        for group in row['devices']:
            for path in group.get('paths', []):
                assert isinstance(path, str) and path.startswith('/') and '..' not in path.split('/'), 'PATH_SHAPE'
                paths.add(path)
    assert len(paths) <= 16, 'PATH_COUNT'
    print(json.dumps({'schema': 'portal-oa-compat-datax01-storage.v1', 'status': 'READONLY_STORAGE_OBSERVED',
                      'saved_operation': OPERATION, 'saved_error_code': value.get('error_code'),
                      'saved_storage_checks': rows, 'filesystems': [filesystem(Path(p)) for p in sorted(paths)],
                      'archive_metadata': archives(), 'image_import_requested': False, 'containerd_commands': 0,
                      'cluster_queries': 0, 'files_modified': False, 'file_contents_read': 'PINNED_REPORT_ONLY',
                      'workload_modified': False}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status': 'INCOMPLETE', 'error_class': type(exc).__name__, 'modified': False}))
        raise SystemExit(1)
