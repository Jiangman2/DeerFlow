"""Run ONLY in disposable offline image; harden the pinned legacy source to 0644."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

from source_contract import check_base, read_verified, validate_plan

HERE = Path(__file__).resolve().parent


def require(value, label):
    if not value:
        raise RuntimeError(label)


def check(path, expected):
    return read_verified(path, expected)


def apply(path, plan, raw):
    legacy = 'before_metadata' in plan
    s = check_base(path, plan) if legacy else check(path, plan['before_sha256'])
    require(hashlib.sha256(raw).hexdigest() == plan['after_sha256'], 'payload_hash_mismatch')
    compile(raw, str(path), 'exec')
    fd, temp = tempfile.mkstemp(prefix='.mineru-candidate-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchown(stream.fileno(), s.st_uid, s.st_gid)
            os.fchmod(stream.fileno(), 0o644 if legacy else stat.S_IMODE(s.st_mode))
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if legacy:
            check_base(path, plan)
        else:
            check(path, plan['before_sha256'])
        os.replace(temp, str(path))
        read_verified(path, plan['after_sha256'], mode=0o644 if legacy else stat.S_IMODE(s.st_mode))
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def main():
    plan = json.loads((HERE / 'BUILD-PLAN.json').read_bytes())['source']
    validate_plan(plan)
    path = Path(plan['path'])
    require(path == Path('/app/deerflow_mineru_adapter.py'), 'unexpected_source_path')
    if sys.argv[1:] == ['before']:
        check_base(path, plan)
        print('mineru_base_source=PASS legacy_mode=0666 hash_locked=true source_executed=no')
        return
    require(sys.argv[1:] == ['apply'], 'invalid_mode')
    apply(path, plan, (HERE / plan['payload']).read_bytes())
    print('mineru_candidate_source=PASS mode=0644')


if __name__ == '__main__':
    main()
