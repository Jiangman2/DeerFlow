#!/usr/bin/env python3
"""Render the narrow POSIX shared-lock descriptor fix from live Runner source."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
from pathlib import Path


OPEN_RE = re.compile(
    r"(?P<prefix>return\s+open\(\s*lock_path\s*,\s*)(?P<quote>['\"])(?P<mode>a\+?)(?P=quote)"
    r"(?P<suffix>\s*,\s*encoding\s*=\s*['\"]utf-8['\"]\s*\))"
)


def _function_source(source: str, name: str) -> tuple[ast.FunctionDef, str, int, int]:
    tree = ast.parse(source)
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    matches = [node for node in functions if node.name == name]
    if len(matches) != 1:
        raise ValueError("expected exactly one %s function" % name)
    node = matches[0]
    ordered = sorted(functions, key=lambda item: item.lineno)
    position = ordered.index(node)
    start = node.lineno - 1
    end = ordered[position + 1].lineno - 1 if position + 1 < len(ordered) else len(source.splitlines())
    lines = source.splitlines(keepends=True)
    return node, "".join(lines[start:end]), start, end


def render(source: str) -> tuple[str, dict[str, str]]:
    for required in (
        "def _lock_file_shared(",
        "fcntl.LOCK_SH",
        "def _try_lock_file_exclusive(",
        "fcntl.LOCK_EX | fcntl.LOCK_NB",
        "def acquire_execution_lease(",
    ):
        if required not in source:
            raise ValueError("live source is missing required execution-lease contract: %s" % required)

    _node, function_source, start, end = _function_source(source, "_open_lock_file")
    matches = list(OPEN_RE.finditer(function_source))
    if len(matches) != 1:
        raise ValueError("_open_lock_file must contain exactly one recognized non-truncating open call")
    mode = matches[0].group("mode")
    if mode not in {"a", "a+"}:
        raise ValueError("unsupported live lock-file mode: %s" % mode)

    if mode == "a+":
        patched = source
        patch_mode = "already-compatible"
    else:
        patched_function, count = OPEN_RE.subn(
            lambda match: match.group("prefix") + match.group("quote") + "a+" + match.group("quote") + match.group("suffix"),
            function_source,
            count=1,
        )
        if count != 1:
            raise ValueError("failed to render exact lock-file mode patch")
        lines = source.splitlines(keepends=True)
        patched = "".join(lines[:start]) + patched_function + "".join(lines[end:])
        patch_mode = "a-to-a-plus"

    compile(patched, "aio_sandbox_provider.py", "exec")
    _node, patched_function, _start, _end = _function_source(patched, "_open_lock_file")
    patched_matches = list(OPEN_RE.finditer(patched_function))
    if len(patched_matches) != 1 or patched_matches[0].group("mode") != "a+":
        raise ValueError("rendered source did not establish read-write append mode")

    before_sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
    after_sha = hashlib.sha256(patched.encode("utf-8")).hexdigest()
    return patched, {
        "patch_mode": patch_mode,
        "source_sha256": before_sha,
        "patched_sha256": after_sha,
        "lock_file_mode_before": mode,
        "lock_file_mode_after": "a+",
    }


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("usage: render_execution_lease_patch.py INPUT OUTPUT METADATA")
    source_path, output_path, metadata_path = map(Path, sys.argv[1:])
    source = source_path.read_text(encoding="utf-8")
    patched, metadata = render(source)
    output_path.write_text(patched, encoding="utf-8")
    metadata_path.write_text(json.dumps(metadata, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    for key in sorted(metadata):
        print("%s=%s" % (key, metadata[key]))


if __name__ == "__main__":
    main()
