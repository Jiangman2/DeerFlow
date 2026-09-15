"""Isolated DocReader entrypoint for DeerFlow Word attachment fallback."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: docreader_worker.py INPUT OUTPUT", file=sys.stderr)
        return 64

    source = Path(sys.argv[1])
    output = Path(sys.argv[2])
    if not source.is_file() or source.is_symlink():
        print("docreader_worker_error=invalid_input", file=sys.stderr)
        return 65

    try:
        from deerflow_mineru_adapter import _parse_word_with_builtin_in_process

        markdown, images = _parse_word_with_builtin_in_process(
            source.read_bytes(),
            source.name,
            source.suffix.lower(),
        )
        temporary = output.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {"markdown": markdown, "images": images},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        os.replace(temporary, output)
    except Exception as exc:
        print(f"docreader_worker_error={type(exc).__name__}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
