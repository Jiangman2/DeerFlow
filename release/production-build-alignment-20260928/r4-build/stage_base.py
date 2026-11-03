"""Checksum and fully verify a base under bounded build resources, Python 3.7+."""

import argparse
import hashlib
import json
from pathlib import Path

from prepare_oci import extract
from verify_oci_base import document, regular, verify


def checksum(archive, expected):
    before = regular(archive, 65 * 1024**3)
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024**2), b""):
            digest.update(chunk)
    after = regular(archive, 65 * 1024**3)
    if digest.hexdigest() != expected or (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError("Base archive checksum mismatch or concurrent change")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--identity", type=Path, required=True)
    args = parser.parse_args()
    expected = document(args.identity)
    checksum(args.archive, expected["archive_sha256"])
    print(json.dumps(verify(extract(args.archive, args.output), expected), sort_keys=True))
