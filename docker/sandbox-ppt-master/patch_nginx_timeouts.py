#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path


PROXY_PASS = re.compile(
    r"^(?P<indent>[ \t]*)proxy_pass[ \t]+"
    r"http://127\.0\.0\.1:(?:\$\{AUTH_BACKEND_PORT\}|[0-9]{1,5});"
    r"[ \t]*(?:#.*)?(?:\r?\n)?$"
)
TIMEOUT = re.compile(
    r"^(?P<indent>[ \t]*)proxy_(?P<name>connect|read|send)_timeout"
    r"[ \t]+[^;]+;[ \t]*(?:#.*)?(?:\r?\n)?$"
)
DESIRED = (
    "proxy_connect_timeout 5s;",
    "proxy_read_timeout 86400s;",
    "proxy_send_timeout 86400s;",
)


def _indent_width(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _location_bounds(lines: list[str], target: int) -> tuple[int, int]:
    target_indent = _indent_width(lines[target])
    start = -1
    for index in range(target - 1, -1, -1):
        stripped = lines[index].strip()
        if (
            stripped
            and not stripped.startswith("#")
            and "{" in stripped
            and _indent_width(lines[index]) < target_indent
        ):
            start = index
            break
    if start < 0:
        raise ValueError(f"proxy_pass_without_parent_block line={target + 1}")

    end = -1
    parent_indent = _indent_width(lines[start])
    for index in range(target + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("}") and _indent_width(lines[index]) <= parent_indent:
            end = index
            break
    if end < 0:
        raise ValueError(f"proxy_pass_without_closing_block line={target + 1}")
    return start, end


def patch_text(source: str) -> tuple[str, str]:
    lines = source.splitlines(keepends=True)
    targets = [index for index, line in enumerate(lines) if PROXY_PASS.fullmatch(line)]
    if len(targets) != 2:
        raise ValueError(f"expected_two_loopback_proxy_routes found={len(targets)}")

    blocks: dict[tuple[int, int], int] = {}
    for target in targets:
        bounds = _location_bounds(lines, target)
        if bounds in blocks:
            raise ValueError("multiple_loopback_proxy_routes_in_one_block")
        blocks[bounds] = target

    original = source
    for (start, end), target in sorted(blocks.items(), reverse=True):
        target_indent = PROXY_PASS.fullmatch(lines[target]).group("indent")
        newline = "\r\n" if lines[target].endswith("\r\n") else "\n"
        timeout_indexes = []
        for index in range(start + 1, end):
            match = TIMEOUT.fullmatch(lines[index])
            if match and match.group("indent") == target_indent:
                timeout_indexes.append(index)

        block = [
            line
            for index, line in enumerate(lines[start : end + 1], start=start)
            if index not in timeout_indexes
        ]
        local_target = target - start - sum(index < target for index in timeout_indexes)
        desired_lines = [target_indent + directive + newline for directive in DESIRED]
        block[local_target + 1 : local_target + 1] = desired_lines
        lines[start : end + 1] = block

    patched = "".join(lines)
    for directive in DESIRED:
        count = sum(line.strip() == directive for line in lines)
        if count != 2:
            raise ValueError(f"timeout_directive_count directive={directive!r} found={count}")
    mode = "already-compatible" if patched == original else "patched"
    return patched, mode


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    config = args.config
    source = config.read_text(encoding="utf-8")
    try:
        patched, mode = patch_text(source)
    except ValueError as exc:
        raise SystemExit("sandbox_nginx_command_timeout_patch=FAIL detail=" + str(exc)) from exc
    if patched != source:
        config.write_text(patched, encoding="utf-8")
    print(f"sandbox_nginx_command_timeout_patch=PASS routes=2 mode={mode}")


if __name__ == "__main__":
    main()
