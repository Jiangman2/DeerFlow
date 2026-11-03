#!/usr/bin/env python3
"""Restore retained Agent config files byte-for-byte from a guarded ZIP."""

from __future__ import print_function

import argparse
import hashlib
import os
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


MAX_MEMBERS = 10000
MAX_MEMBER_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024


def _allowed(parts):
    return (
        len(parts) == 5
        and parts[0] == "users"
        and parts[2] == "agents"
        and parts[4] == "config.yaml"
        and bool(parts[1])
        and bool(parts[3])
    ) or (
        len(parts) == 3
        and parts[0] in {"team_agents", "agents"}
        and parts[2] == "config.yaml"
        and bool(parts[1])
    )


def validate_members(infos):
    infos = list(infos)
    if len(infos) > MAX_MEMBERS:
        raise ValueError("file-config backup contains too many members")
    seen = set()
    total = 0
    validated = []
    for info in infos:
        name = info.filename
        path = PurePosixPath(name)
        parts = path.parts
        mode = (info.external_attr >> 16) & 0o177777
        if (
            info.is_dir()
            or not name
            or "\\" in name
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in parts)
            or not _allowed(parts)
            or stat.S_ISLNK(mode)
        ):
            raise ValueError("unsafe file-config backup member: " + name)
        if name in seen:
            raise ValueError("duplicate file-config backup member: " + name)
        if info.file_size < 0 or info.file_size > MAX_MEMBER_BYTES:
            raise ValueError("oversized file-config backup member: " + name)
        total += info.file_size
        if total > MAX_TOTAL_BYTES:
            raise ValueError("file-config backup exceeds total size limit")
        seen.add(name)
        validated.append((info, parts))
    return validated


def restore_archive(archive_path, root_path):
    root = Path(root_path).resolve()
    if not root.is_dir():
        raise ValueError("file-config restore root is not a directory: " + str(root))
    restored = 0
    with zipfile.ZipFile(str(archive_path), "r") as archive:
        validated = validate_members(archive.infolist())
        corrupt = archive.testzip()
        if corrupt is not None:
            raise ValueError("corrupt file-config backup member: " + corrupt)
        for info, parts in validated:
            target = root.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            parent = target.parent.resolve()
            if os.path.commonpath([str(root), str(parent)]) != str(root):
                raise ValueError("file-config restore escaped root: " + info.filename)
            data = archive.read(info)
            if len(data) != info.file_size:
                raise ValueError("short file-config backup member: " + info.filename)
            previous = target.stat() if target.exists() else None
            mode = ((info.external_attr >> 16) & 0o777) or (
                stat.S_IMODE(previous.st_mode) if previous is not None else 0o600
            )
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".config.yaml.rollback.", dir=str(target.parent)
            )
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary_name, mode)
                if (
                    previous is not None
                    and hasattr(os, "chown")
                    and hasattr(os, "geteuid")
                    and os.geteuid() == 0
                ):
                    os.chown(temporary_name, previous.st_uid, previous.st_gid)
                os.replace(temporary_name, str(target))
                temporary_name = None
            finally:
                if temporary_name is not None:
                    try:
                        os.unlink(temporary_name)
                    except FileNotFoundError:
                        pass
            observed = target.read_bytes()
            if hashlib.sha256(observed).digest() != hashlib.sha256(data).digest():
                raise RuntimeError("file-config byte verification failed: " + info.filename)
            restored += 1
    return restored


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    restored = restore_archive(args.archive, args.root)
    print("file_agent_config_rows_restored=%d" % restored)
    print("file_agent_config_exact_byte_restore=PASS")


if __name__ == "__main__":
    main()
