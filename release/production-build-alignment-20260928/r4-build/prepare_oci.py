"""Stage one local OCI archive into a NEW private directory. Python 3.7+.

Never extract a filesystem layer, follow a link, overwrite a path or fetch a
blob. Run this I/O-heavy helper inside the isolated builder's resource slice.
An interrupted directory is evidence, not reusable successful input.
"""

import argparse
import hashlib
import json
import os
import re
import stat
import tarfile
from pathlib import Path

from verify_oci_base import LAYER_LIMIT, META_LIMIT, document, regular, verify

TOTAL_LIMIT = 64 * 1024**3


def member_name(member):
    name = member.name
    if name.startswith("./"):
        name = name[2:]
    if member.isdir() and name.rstrip("/") in (".", "blobs", "blobs/sha256"):
        return name.rstrip("/")
    if not member.isfile() or member.sparse is not None or member.linkname:
        raise ValueError("Links, sparse files and special archive members forbidden")
    if name not in ("index.json", "oci-layout", "manifest.json") and not re.fullmatch(
        r"blobs/sha256/[a-f0-9]{64}", name
    ):
        raise ValueError("Unexpected OCI archive member")
    limit = LAYER_LIMIT if name.startswith("blobs/") else META_LIMIT
    if not 0 <= member.size <= limit:
        raise ValueError("Oversized OCI archive member")
    return name


def fresh_directory(path):
    path = Path(path).absolute()
    for parent in path.parents:
        if not stat.S_ISDIR(parent.lstat().st_mode):
            raise ValueError("Linked or missing output ancestor")
    if os.path.lexists(str(path)):
        raise ValueError("Output already exists; never reuse a partial extraction")
    path.mkdir(mode=0o700)
    return path


def extract(archive, destination):
    archive = Path(archive).absolute()
    before = regular(archive, TOTAL_LIMIT + 64 * 1024**2)
    destination = fresh_directory(destination)
    seen, total = set(), 0
    # OCI exports are uncompressed tar; layers remain opaque compressed blobs.
    # Streaming headers avoid loading an attacker-controlled member table.
    with tarfile.open(str(archive), mode="r|") as stream:
        for member in stream:
            name = member_name(member)
            if name in seen or len(seen) >= 4096:
                raise ValueError("Duplicate or excessive OCI members")
            seen.add(name)
            if member.isdir():
                continue
            total += member.size
            if total > TOTAL_LIMIT:
                raise ValueError("OCI archive exceeds aggregate bound")
            target = destination / name
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            incoming = stream.extractfile(member)
            if incoming is None:
                raise ValueError("Missing archive member body")
            fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            digest, remaining = hashlib.sha256(), member.size
            with incoming, os.fdopen(fd, "wb") as out:
                while remaining:
                    chunk = incoming.read(min(1024**2, remaining))
                    if not chunk:
                        raise ValueError("Truncated OCI archive")
                    digest.update(chunk)
                    out.write(chunk)
                    remaining -= len(chunk)
                out.flush()
                os.fsync(out.fileno())
            if name.startswith("blobs/") and digest.hexdigest() != name.split("/")[-1]:
                raise ValueError("Archive blob checksum mismatch")
    after = regular(archive, TOTAL_LIMIT + 64 * 1024**2)
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ValueError("Archive changed during staging")
    if not {"oci-layout", "index.json"} <= seen:
        raise ValueError("Incomplete OCI archive")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--delta", type=Path, required=True)
    args = parser.parse_args()
    result = verify(extract(args.archive, args.output), document(args.delta))
    print(json.dumps(result, sort_keys=True))
