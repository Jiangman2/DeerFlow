"""Isolated local AnyDoc entrypoint for DeerFlow conversation attachments."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: anydoc_worker.py INPUT OUTPUT", file=sys.stderr)
        return 64

    source = Path(sys.argv[1])
    output = Path(sys.argv[2])
    if not source.is_file() or source.is_symlink():
        print("anydoc_worker_error=invalid_input", file=sys.stderr)
        return 65

    try:
        import anydoc

        markdown = anydoc.to_markdown(str(source))
        if not isinstance(markdown, str):
            raise TypeError("AnyDoc returned a non-string result")
        temporary = output.with_suffix(".tmp")
        temporary.write_text(markdown, encoding="utf-8")
        os.replace(temporary, output)
    except Exception as exc:
        print(f"anydoc_worker_error={type(exc).__name__}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
