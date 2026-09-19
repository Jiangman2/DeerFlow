"""Isolated DocReader entrypoint for DeerFlow Word attachment fallback."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: docreader_worker.py MODE INPUT OUTPUT", file=sys.stderr)
        return 64

    mode = sys.argv[1]
    source = Path(sys.argv[2])
    output = Path(sys.argv[3])
    if mode not in {"first-success", "native-docx", "streaming-docx"}:
        print("docreader_worker_error=invalid_mode", file=sys.stderr)
        return 64
    if not source.is_file() or source.is_symlink():
        print("docreader_worker_error=invalid_input", file=sys.stderr)
        return 65

    try:
        from deerflow_mineru_adapter import (
            _parse_docx_streaming_in_process,
            _parse_word_with_builtin_in_process,
        )

        content = source.read_bytes()
        if mode == "streaming-docx":
            markdown, images = _parse_docx_streaming_in_process(content)
        else:
            markdown, images = _parse_word_with_builtin_in_process(
                content,
                source.name,
                source.suffix.lower(),
                bypass_markitdown=mode == "native-docx",
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
