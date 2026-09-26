"""Verify immutable preview hotfix files; Python 3.7+ compatible."""

import hashlib
import json
import re
import stat
import sys
from pathlib import Path


FILES = {
    "BASELINE.json",
    "Dockerfile.gateway",
    "Dockerfile.runner",
    "Dockerfile.frontend",
    "apply_delta.py",
    "apply_frontend.mjs",
    "prepare_base.py",
    "build-datax01.sh",
    "README.md",
    "verify_package.py",
}


def main() -> None:
    root = Path(sys.argv[1]).resolve(strict=True)
    manifest = root / "PACKAGE.SHA256"
    if not manifest.is_file() or manifest.is_symlink():
        raise ValueError("Missing checksum manifest")
    rows = manifest.read_text(encoding="ascii").splitlines()
    names = set()
    for row in rows:
        match = re.fullmatch(r"([a-f0-9]{64})  ([A-Za-z0-9_.-]+)", row)
        if not match:
            raise ValueError("Invalid checksum manifest row")
        expected, name = match.groups()
        if name in names or name not in FILES:
            raise ValueError("Unexpected or duplicate file: " + name)
        target = root / name
        info = target.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 200000:
            raise ValueError("Invalid package file: " + name)
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise ValueError("Checksum mismatch: " + name)
        names.add(name)
    if names != FILES or {p.name for p in root.iterdir()} != FILES | {"PACKAGE.SHA256"}:
        raise ValueError("Package file set differs from manifest")
    baseline = json.loads((root / "BASELINE.json").read_text(encoding="utf-8"))
    if set(baseline) != {"gateway", "runner", "frontend"}:
        raise ValueError("Invalid component set")
    expected_sources = {
        "gateway": "25db8c3d4b98686aea770e45f22a750354d4af2b70d32f1501b08f05c113b12f",
        "runner": "8c05d60d67b259e3286fcea33e558d4325c401f66c25c90aa7d847eb782d40e0",
        "frontend": "a11c882b53936ee34489a5ffeda5db3daec1df3b6c9be95381d775e0aa2c3164",
    }
    for component, expected in expected_sources.items():
        if baseline[component]["source_sha256"] != expected:
            raise ValueError("Source baseline drift: " + component)
    print("ppt_preview_auth_r1_package=PASS components=gateway,runner,frontend")


if __name__ == "__main__":
    main()
