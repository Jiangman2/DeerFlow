#!/usr/bin/env python3
"""Read-only trace of the exact failed image-preload attempt. Python3.7+."""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys

TOOL = Path('/var/lib/weknora-enrichment-preload-tool-xIpS67Mi/preload.py')
TOOL_SHA = 'db944072aa40e78118e70c0af34bc5f7ccc6a4059f9a8cc39b58a65c9c4f1e93'


def emit(value): print(json.dumps(value, sort_keys=True), flush=True)


def require(value, check):
    if not value: raise RuntimeError(check)


def load():
    for parent in TOOL.parents:
        s = parent.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022, 'unsafe_tool_parent')
    fd = os.open(str(TOOL), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        s = os.fstat(f.fileno())
        require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
                and not s.st_mode & 0o022 and 0 < s.st_size < 2*1024**2, 'unsafe_tool_file')
        raw = f.read(2*1024**2)
    require(hashlib.sha256(raw).hexdigest() == TOOL_SHA, 'delivered_tool_changed')
    # The frozen single-file bootstrap creates modules but never calls main here.
    exec(compile(raw, str(TOOL), 'exec'), {'__name__': 'audited_frozen_preload', '__file__': str(TOOL)})
    return sys.modules['preload_enrichment_images']


def operation(p, argv):
    names = {i['image']: i['role'] for i in p.ITEMS}
    if argv[:len(p.io.CTR)] == p.io.CTR:
        tail = argv[len(p.io.CTR):]
        if tail == ['images', 'ls']: return {'operation': 'LIST_LOCAL_IMAGE_METADATA'}
        for image, role in names.items():
            if tail == ['images', 'ls', '--quiet', 'name=='+image]:
                return {'operation': 'FIND_EXACT_REFERENCE', 'role': role}
            if tail == ['images', 'check', '--snapshotter', 'overlayfs', '--quiet', 'name=='+image]:
                return {'operation': 'CHECK_CONTENT_AND_UNPACK', 'role': role}
        if len(tail) == 3 and tail[:2] == ['content', 'get'] and re.fullmatch(r'sha256:[a-f0-9]{64}', tail[2]):
            return {'operation': 'READ_HASHED_IMAGE_METADATA', 'digest': tail[2]}
    for prefix in (p.io.CRI, p.cli.CRI):
        if argv[:len(prefix)] == prefix and len(argv) == len(prefix)+2:
            if argv[-2] == 'inspecti' and argv[-1] in names:
                return {'operation': 'READ_CRI_IMAGE', 'role': names[argv[-1]]}
    raise RuntimeError('non_readonly_command_rejected')


def install_trace(p):
    original = p.cli.command
    observations = []
    def read(argv, **options):
        detail = operation(p, argv)
        require(not options.get('monitor'), 'mutation_monitor_rejected')
        emit(dict(detail, check_state='RUNNING'))
        p.cli.LAST.clear()
        try:
            raw = original(argv, **options)
            result = dict(detail, check_state='PASS', **p.cli.LAST)
            observations.append(result); emit(result)
            return raw
        except BaseException as exc:
            result = dict(detail, check_state='FAIL', error_type=type(exc).__name__,
                          failed_check=p.cli.safe_detail(exc), **p.cli.LAST)
            observations.append(result); emit(result)
            raise
    p.cli.command = read
    # Correct the diagnostic gap without editing or executing the write path.
    # Inventory originally used pipe_command, which discarded stderr and LAST.
    p.inventory.capture = lambda args: read(args, timeout=30, limit=8*1024**2)
    return observations


def main():
    require(sys.platform == 'linux' and os.geteuid() == 0
            and socket.gethostname().split('.')[0] == 'datax-01', 'root_datax01_required')
    p = load(); observations = install_trace(p)
    p.status()
    failed = [x for x in observations if x['check_state'] == 'FAIL']
    emit(dict(preload_diagnostic='COMPLETE', failed_commands=failed,
              historical_stderr='NOT_RETAINED_BY_OLD_INVENTORY_HELPER', historical_cause='NOT_INFERRED',
              files_modified=False, images_imported=False, image_labels_changed=False,
              provider_requests=0, production_writes=0, credentials_requested=False))


if __name__ == '__main__':
    try: main()
    except Exception as exc:
        emit(dict(preload_diagnostic='INCOMPLETE', error_type=type(exc).__name__, raw_error_printed=False,
                  files_modified=False, images_imported=False, production_writes=0))
        sys.exit(1)
