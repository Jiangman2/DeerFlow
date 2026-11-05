#!/usr/bin/env python3
"""Normalize one CRLF-translated upstream source file back to its Git blob bytes."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path


RELATIVE_PATH = Path("frontend/src/components/SandboxConfigEditorDrawer.vue")
TRANSLATED_SHA256 = "218690afdcac008fbf562f64254f0df1fb7afb9ed761ec4e4ab4cfea7fc53e73"
OFFICIAL_SHA256 = "be538fc14bfcbead8e74f8543610fb37f88c87d61057f33220f6c77725519994"
EXPECTED_NEWLINES = 2490


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True)
    args = parser.parse_args()

    root = Path(args.source_root).resolve()
    path = (root / RELATIVE_PATH).resolve()
    if root not in path.parents:
        raise AssertionError("normalization target escaped the source root")

    data = path.read_bytes()
    before = digest(data)
    if before == TRANSLATED_SHA256:
        if data.count(b"\r\n") != EXPECTED_NEWLINES:
            raise AssertionError("unexpected CRLF count in translated upstream component")
        if data.count(b"\r") != EXPECTED_NEWLINES:
            raise AssertionError("translated upstream component contains a lone CR")
        normalized = data.replace(b"\r\n", b"\n")
        if digest(normalized) != OFFICIAL_SHA256:
            raise AssertionError("LF-normalized component does not match the official Git blob")
        temporary = path.with_name(f".{path.name}.deerflow-normalized.tmp")
        temporary.write_bytes(normalized)
        os.replace(temporary, path)
    elif before != OFFICIAL_SHA256:
        raise AssertionError(f"unexpected upstream component SHA-256: {before}")

    final = path.read_bytes()
    if digest(final) != OFFICIAL_SHA256:
        raise AssertionError("normalized component SHA-256 mismatch")
    if final.count(b"\r") != 0 or final.count(b"\n") != EXPECTED_NEWLINES:
        raise AssertionError("normalized component newline contract mismatch")
    print(
        "weknora_v08_ui_line_endings=PASS "
        f"path={RELATIVE_PATH.as_posix()} official_sha256={OFFICIAL_SHA256}"
    )


if __name__ == "__main__":
    main()
