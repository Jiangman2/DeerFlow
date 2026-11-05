"""Bounded, link-free extraction of our locked build context; Python3.7+."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
from build_guard import require, checksum, safe_file
from prepare_oci import fresh_directory


def unpack(archive, output, expected):
    safe_file(archive, 4*1024**3)
    require(checksum(archive) == expected, 'context_archive_digest')
    fresh_directory(output)
    names, total = set(), 0
    with tarfile.open(str(archive), 'r|') as t:
        for m in t:
            p = PurePosixPath(m.name)
            require(m.isfile() and not m.issparse() and not m.linkname and not p.is_absolute()
                    and not any(x in ('', '.', '..') for x in m.name.split('/'))
                    and not any(x in m.name for x in ('\\', ':', '\n', '\r'))
                    and m.name not in names and len(names) < 30000, 'unsafe_context_member')
            names.add(m.name)
            total += m.size
            require(0 <= m.size <= 1024**3 and total <= 4*1024**3, 'context_size')
            target = output / m.name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with t.extractfile(m) as inp, target.open('xb') as out:
                shutil.copyfileobj(inp, out, 1024**2)
            target.chmod(0o600)
    return {'context_stage':'PASS', 'files':len(names), 'bytes':total}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--archive', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--sha256', required=True)
    a = p.parse_args()
    print(json.dumps(unpack(a.archive, a.output, a.sha256)), flush=True)
