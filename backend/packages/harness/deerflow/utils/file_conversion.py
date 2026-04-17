"""File conversion utilities.

Converts document files (PDF, PPT, Excel, Word) to Markdown using markitdown.
Adds an automatic pipeline for legacy .doc:
- Prefer LibreOffice (soffice --headless) to convert .doc -> .docx, then run markitdown.
- Fallback to antiword to extract plaintext, then wrap as Markdown.
If both are unavailable, returns None (caller should avoid binary text fallback).
No FastAPI or HTTP dependencies — pure utility functions.
"""

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# File extensions that should be converted to markdown
CONVERTIBLE_EXTENSIONS = {
    ".pdf",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".doc",
    ".docx",
}


async def convert_file_to_markdown(file_path: Path) -> Path | None:
    """Convert a file to markdown using markitdown.

    Args:
        file_path: Path to the file to convert.

    Returns:
        Path to the markdown file if conversion was successful, None otherwise.
    """
    # Special handling for legacy .doc files
    if file_path.suffix.lower() == ".doc":
        md_path = _convert_doc_with_pipeline(file_path)
        if md_path is not None:
            return md_path
        # If pipeline fails, do not attempt markitdown on raw .doc
        logger.warning("DOC pipeline failed; skipping binary text fallback for %s", file_path.name)
        return None

    try:
        from markitdown import MarkItDown

        md = MarkItDown()
        result = md.convert(str(file_path))

        md_path = file_path.with_suffix(".md")
        md_path.write_text(result.text_content, encoding="utf-8")

        logger.info("Converted %s to markdown: %s", file_path.name, md_path.name)
        return md_path
    except Exception as e:
        logger.error("Failed to convert %s to markdown: %s", file_path.name, e)
        return None


def _convert_doc_with_pipeline(file_path: Path) -> Path | None:
    """Try converting legacy .doc using external tools.

    Order:
    1) LibreOffice (soffice --headless) -> .docx -> markitdown
    2) antiword -> .txt -> wrap to .md
    """
    # Attempt LibreOffice conversion to .docx
    soffice = _which_soffice()
    if soffice:
        with tempfile.TemporaryDirectory() as td:
            outdir = Path(td)
            profile_dir = outdir / "lo_profile"
            profile_dir.mkdir(parents=True, exist_ok=True)
            try:
                # soffice --headless --convert-to docx --outdir <outdir> <file>
                completed = subprocess.run(
                    [
                        soffice,
                        "--headless",
                        "--nologo",
                        "--norestore",
                        "--nolockcheck",
                        "--nodefault",
                        "--nofirststartwizard",
                        f"-env:UserInstallation={profile_dir.as_uri()}",
                        "--convert-to",
                        "docx",
                        "--outdir",
                        str(outdir),
                        str(file_path),
                    ],
                    check=True,
                    capture_output=True,
                    timeout=180,
                )
                converted = outdir / (file_path.stem + ".docx")
                if converted.exists():
                    # Run markitdown on the docx copy
                    from markitdown import MarkItDown

                    md = MarkItDown()
                    result = md.convert(str(converted))
                    md_path = file_path.with_suffix(".md")
                    md_path.write_text(result.text_content, encoding="utf-8")
                    logger.info("Converted .doc via LibreOffice -> .docx -> .md: %s", md_path.name)
                    return md_path
                logger.warning(
                    "LibreOffice finished but did not produce .docx for %s. stdout=%s stderr=%s",
                    file_path.name,
                    completed.stdout.decode("utf-8", errors="replace"),
                    completed.stderr.decode("utf-8", errors="replace"),
                )
            except Exception as e:
                logger.warning("LibreOffice conversion failed for %s: %s", file_path.name, e)

    # Fallback: antiword to plaintext
    antiword = shutil.which("antiword")
    if antiword:
        try:
            completed = subprocess.run(
                [antiword, str(file_path)],
                check=True,
                capture_output=True,
                timeout=60,
            )
            txt = completed.stdout.decode("utf-8", errors="replace")
            md_path = file_path.with_suffix(".md")
            md_path.write_text(txt, encoding="utf-8")
            logger.info("Converted .doc via antiword -> .md: %s", md_path.name)
            return md_path
        except Exception as e:
            logger.warning("antiword conversion failed for %s: %s", file_path.name, e)

    return None


def _which_soffice() -> str | None:
    """Find LibreOffice executable across common OS paths and PATH."""
    candidates = [
        shutil.which("soffice"),
        shutil.which("libreoffice"),
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/usr/bin/soffice",
        "/usr/lib/libreoffice/program/soffice",
        "/snap/bin/libreoffice",
        "/snap/bin/soffice",
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return None
