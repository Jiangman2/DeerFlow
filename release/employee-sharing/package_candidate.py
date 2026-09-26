"""Package verified BUILD-ONLY inputs, never a production hotfix."""

import argparse
import gzip
import hashlib
import io
import os
import re
import tarfile
from pathlib import Path

from verify_candidate import verify


def package(source, archive):
    source, archive = Path(source).absolute(), Path(archive).absolute()
    verify(source)
    name = archive.name.removesuffix(".tar.gz")
    if not re.fullmatch(
        r"deerflow-employee-sharing-build-inputs-[0-9]{8}-v[0-9]+", name
    ):
        raise ValueError("Archive name must identify BUILD INPUTS, not a hotfix")
    checksum = archive.with_name(archive.name + ".sha256")
    for path in (archive, checksum):
        if path.exists() or path.is_symlink():
            raise ValueError("Archive/checksum already exists")
    for parent in archive.parents:
        if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
            raise ValueError("Linked archive output ancestor")
    payload = {path.name: path.read_bytes() for path in source.iterdir()}
    with archive.open("xb") as output:
        with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as zipped:
            with tarfile.open(
                fileobj=zipped, mode="w", format=tarfile.USTAR_FORMAT
            ) as stream:
                for relative, content in sorted(payload.items()):
                    item = tarfile.TarInfo(name + "/" + relative)
                    item.size, item.mode, item.mtime = len(content), 0o644, 0
                    item.uid = item.gid = 0
                    stream.addfile(item, io.BytesIO(content))
        output.flush()
        os.fsync(output.fileno())
    # Inspect the actual resulting archive, not merely its input list.
    with tarfile.open(archive, "r:gz") as stream:
        members = stream.getmembers()
        expected = {name + "/" + relative for relative in payload}
        if len(members) != len(expected) or {m.name for m in members} != expected:
            raise ValueError("Archive member coverage differs")
        for member in members:
            relative = member.name.split("/", 1)[1]
            if (
                not member.isfile()
                or member.mode != 0o644
                or stream.extractfile(member).read() != payload[relative]
            ):
                raise ValueError("Packaged bytes/mode differ")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    # Binary output avoids Windows CRLF translation: GNU sha256sum treats the
    # CR as part of the filename rather than as a checksum-line terminator.
    with checksum.open("xb") as output:
        output.write((digest + "  " + archive.name + "\n").encode("ascii"))
    return digest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    digest = package(args.source, args.archive)
    print("build_inputs_archive=PASS sha256=" + digest + " ready_for_rollout=no")
