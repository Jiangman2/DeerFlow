"""Extract an already hash-verified Rust component inside the offline builder.

The locked bookworm builder has Python/lzma but no xz executable. Never install
host packages or permit archive links/special files to work around that fact.
"""
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import sys


def unpack(archive, destination):
    name = archive.name
    if not re.fullmatch(r"(rustc|cargo|rust-std)-1\.99\.1-aarch64-unknown-linux-gnu\.tar\.xz", name):
        raise ValueError("unexpected_rust_component")
    root_name = name[:-7]
    if destination.is_symlink() or not destination.is_dir():
        raise ValueError("unsafe_destination")
    target = destination / root_name
    if target.exists() or target.is_symlink():
        raise ValueError("new_component_directory_required")
    with tarfile.open(archive, "r:xz") as stream:
        entries = stream.getmembers()
        seen, total = set(), 0
        if not 1 <= len(entries) <= 4096:
            raise ValueError("member_count")
        for entry in entries:
            p = PurePosixPath(entry.name)
            if (p.is_absolute() or "\\" in entry.name or ":" in entry.name or
                    any(x in ("", ".", "..") for x in entry.name.rstrip("/").split("/")) or
                    not p.parts or p.parts[0] != root_name or
                    not (entry.isfile() or entry.isdir()) or p.as_posix() in seen):
                raise ValueError("unsafe_archive_member")
            seen.add(p.as_posix())
            total += entry.size
            if entry.size < 0 or total > 600*1024**2:
                raise ValueError("expanded_size")
        # Entire archive validated before writing into a fresh private subtree.
        target.mkdir(mode=0o755)
        for entry in entries:
            path = destination / entry.name
            if entry.isdir():
                path.mkdir(parents=True, exist_ok=True, mode=0o755)
            else:
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                with stream.extractfile(entry) as src, path.open("xb") as out:
                    shutil.copyfileobj(src, out, 1024**2)
                path.chmod(0o755 if entry.mode & 0o111 else 0o644)
    return {"files": sum(x.isfile() for x in entries), "expanded_bytes": total}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: unpack_rust.py ARCHIVE NEW_COMPONENT_PARENT")
    unpack(Path(sys.argv[1]), Path(sys.argv[2]))
