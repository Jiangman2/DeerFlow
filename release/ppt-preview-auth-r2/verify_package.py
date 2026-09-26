"""Verify the immutable R2 frontend-only correction package."""

import hashlib
import re
import stat
import sys
from pathlib import Path


FILES = {"Dockerfile.frontend", "apply_frontend.mjs", "build-frontend-datax01.sh", "README.md", "verify_package.py"}


def main() -> None:
    root = Path(sys.argv[1]).resolve(strict=True)
    manifest = root / "PACKAGE.SHA256"
    if not manifest.is_file() or manifest.is_symlink():
        raise ValueError("Missing checksum manifest")
    names = set()
    for row in manifest.read_text(encoding="ascii").splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})  ([A-Za-z0-9_.-]+)", row)
        if not match:
            raise ValueError("Invalid checksum manifest row")
        expected, name = match.groups()
        if name in names or name not in FILES:
            raise ValueError("Unexpected package file")
        path = root / name
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 200000:
            raise ValueError("Invalid package file")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Checksum mismatch: " + name)
        names.add(name)
    if names != FILES or {p.name for p in root.iterdir()} != FILES | {"PACKAGE.SHA256"}:
        raise ValueError("Unexpected package contents")
    print("ppt_preview_auth_r2_package=PASS frontend_only=yes r1_candidates=reused")


if __name__ == "__main__":
    main()
