#!/usr/bin/env python3
"""Render Markdown to a CJK-safe PDF in the immutable Sandbox runtime."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname


_MAX_INPUT_BYTES = 20 * 1024 * 1024


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _local_only_fetcher(root: Path):
    from weasyprint import default_url_fetcher

    def fetch(url: str):
        parsed = urlparse(url)
        if parsed.scheme == "data":
            return default_url_fetcher(url)
        if parsed.scheme != "file":
            raise ValueError(f"External resource URLs are not allowed in PDF rendering: {parsed.scheme or 'unknown'}")
        candidate = Path(url2pathname(unquote(parsed.path))).resolve()
        if not _is_within(candidate, root):
            raise ValueError("PDF resource path escapes the Markdown directory")
        return default_url_fetcher(candidate.as_uri())

    return fetch


def render(source: Path, destination: Path, *, title: str | None = None) -> None:
    import markdown
    from weasyprint import CSS, HTML

    source = source.resolve(strict=True)
    if not source.is_file():
        raise ValueError("Markdown input must be a regular file")
    if source.stat().st_size > _MAX_INPUT_BYTES:
        raise ValueError("Markdown input exceeds 20 MiB")

    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    body = source.read_text(encoding="utf-8")
    rendered = markdown.markdown(
        body,
        extensions=("extra", "sane_lists", "tables"),
        output_format="html5",
    )
    safe_title = (title or source.stem).replace("<", "&lt;").replace(">", "&gt;")
    document = (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        f"<title>{safe_title}</title></head><body>{rendered}</body></html>"
    )
    stylesheet = CSS(
        string="""
        @page { size: A4; margin: 18mm 17mm; }
        body {
          font-family: "Noto Sans SC", "Noto Sans CJK SC", "WenQuanYi Zen Hei", sans-serif;
          font-size: 10.5pt; line-height: 1.58; color: #172033;
        }
        h1, h2, h3 { break-after: avoid; color: #0d355f; }
        h1 { font-size: 20pt; } h2 { font-size: 15pt; } h3 { font-size: 12pt; }
        pre, code { font-family: "Noto Sans Mono CJK SC", "DejaVu Sans Mono", monospace; }
        pre { white-space: pre-wrap; background: #f4f6f8; padding: 8pt; }
        table { border-collapse: collapse; width: 100%; }
        th, td { border: 0.5pt solid #9aa7b4; padding: 4pt; vertical-align: top; }
        img { max-width: 100%; height: auto; }
        """
    )

    fd, raw_temp = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".partial", dir=destination.parent)
    os.close(fd)
    temporary = Path(raw_temp)
    try:
        HTML(
            string=document,
            base_url=source.parent.as_uri() + "/",
            url_fetcher=_local_only_fetcher(source.parent),
        ).write_pdf(str(temporary), stylesheets=[stylesheet])
        if not temporary.read_bytes().startswith(b"%PDF-"):
            raise RuntimeError("PDF renderer did not produce a valid PDF header")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="UTF-8 Markdown input")
    parser.add_argument("output", type=Path, help="PDF output")
    parser.add_argument("--title", default=None)
    args = parser.parse_args()
    render(args.input, args.output, title=args.title)
    print(f"deerflow_markdown_pdf=PASS output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
