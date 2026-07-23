"""Translate WeKnora's MinerU protocol to the enterprise PDF parsing API."""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import subprocess
import tempfile
import zipfile
from email.parser import BytesParser
from email.policy import default as email_policy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger("deerflow.mineru_adapter")


def detect_input_suffix(content: bytes) -> str:
    """Identify the supported document type without trusting the filename."""
    if content.startswith(b"%PDF-"):
        return ".pdf"
    if content.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        return ".ppt"
    if content.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if "ppt/presentation.xml" in archive.namelist():
                    return ".pptx"
        except zipfile.BadZipFile:
            pass
    raise ValueError("Only PDF, PPT, and PPTX files are supported")


def convert_to_pdf(content: bytes, filename: str) -> bytes:
    """Convert a presentation to PDF; pass validated PDF input through."""
    suffix = detect_input_suffix(content)
    if suffix == ".pdf":
        return content

    timeout = int(os.getenv("LIBREOFFICE_TIMEOUT_SECONDS", "180"))
    with tempfile.TemporaryDirectory(prefix="deerflow-mineru-") as temp_dir:
        workspace = Path(temp_dir)
        source = workspace / f"input{suffix}"
        source.write_bytes(content)
        profile = (workspace / "libreoffice-profile").as_uri()
        completed = subprocess.run(
            [
                "libreoffice",
                "--headless",
                f"-env:UserInstallation={profile}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(workspace),
                str(source),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = source.with_suffix(".pdf")
        if completed.returncode != 0 or not output.is_file():
            detail = (completed.stderr or completed.stdout or "conversion failed").strip()
            raise RuntimeError(f"LibreOffice could not convert {filename}: {detail[:500]}")
        pdf = output.read_bytes()
        if not pdf.startswith(b"%PDF-"):
            raise RuntimeError(f"LibreOffice produced an invalid PDF for {filename}")
        return pdf


class EnterpriseMinerUClient:
    """Small client matching the enterprise MinerU example script."""

    def __init__(
        self,
        *,
        api_url: str,
        api_token: str,
        timeout_seconds: int = 300,
        session: Any | None = None,
    ) -> None:
        self.api_url = api_url
        self.api_token = api_token
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()

    def parse_pdf(self, pdf: bytes, filename: str) -> dict[str, Any]:
        response = self.session.post(
            self.api_url,
            headers={
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
            },
            json={
                "filename": filename,
                "pdf_base64": base64.b64encode(pdf).decode("ascii"),
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("pages"), list):
            raise RuntimeError("Enterprise MinerU returned an invalid response")
        return payload


def _block_content(block: dict[str, Any]) -> str:
    content = block.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if content is None:
        return ""
    return json.dumps(content, ensure_ascii=False, indent=2)


def mineru_pages_to_markdown(payload: dict[str, Any]) -> str:
    """Convert the enterprise page/block response into WeKnora markdown."""
    sections: list[str] = []
    for index, page in enumerate(payload.get("pages", []), start=1):
        if not isinstance(page, dict):
            continue
        page_number = page.get("page", index)
        blocks = page.get("blocks", [])
        contents = [content for block in blocks if isinstance(block, dict) and (content := _block_content(block))]
        sections.append(f"## 第 {page_number} 页\n\n" + "\n\n".join(contents))
    markdown = "\n\n".join(sections).strip()
    if not markdown:
        raise RuntimeError("Enterprise MinerU returned no parseable content")
    return markdown


def weknora_response(markdown: str) -> dict[str, Any]:
    """Wrap markdown in WeKnora's self-hosted MinerU response schema."""
    return {"results": {"document": {"md_content": markdown, "images": {}}}}


def parse_multipart_upload(content_type: str, body: bytes) -> tuple[str, bytes]:
    """Extract WeKnora's file part without relying on the deprecated cgi module."""
    if "multipart/form-data" not in content_type.lower():
        raise ValueError("Content-Type must be multipart/form-data")
    envelope = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
    message = BytesParser(policy=email_policy).parsebytes(envelope)
    if not message.is_multipart():
        raise ValueError("Invalid multipart upload")
    for part in message.iter_parts():
        field_name = part.get_param("name", header="content-disposition")
        if field_name not in {"files", "file"}:
            continue
        filename = Path(part.get_filename() or "upload").name
        content = part.get_payload(decode=True) or b""
        if not content:
            raise ValueError("Uploaded file is empty")
        return filename, content
    raise ValueError("Multipart upload does not contain a file")


class MinerUAdapterHandler(BaseHTTPRequestHandler):
    server_version = "DeerFlowMinerUAdapter/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        # WeKnora's built-in MinerU availability check calls /docs, while the
        # Kubernetes probes use /health. Both are intentionally side-effect free.
        if self.path.rstrip("/") in {"", "/health", "/docs"}:
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/file_parse":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            max_bytes = int(os.getenv("MINERU_MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > max_bytes:
                raise ValueError("Upload is empty or exceeds the configured size limit")
            filename, content = parse_multipart_upload(
                self.headers.get("Content-Type", ""),
                self.rfile.read(content_length),
            )
            pdf = convert_to_pdf(content, filename)

            api_url = os.environ["MINERU_API_URL"]
            api_token = os.environ["MINERU_API_TOKEN"]
            timeout = int(os.getenv("MINERU_TIMEOUT_SECONDS", "300"))
            parsed = EnterpriseMinerUClient(
                api_url=api_url,
                api_token=api_token,
                timeout_seconds=timeout,
            ).parse_pdf(pdf, f"{Path(filename).stem}.pdf")
            self._json(HTTPStatus.OK, weknora_response(mineru_pages_to_markdown(parsed)))
        except (KeyError, ValueError) as exc:
            logger.warning("Rejected MinerU adapter request: %s", exc)
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            logger.exception("MinerU adapter request failed")
            self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)[:500]})


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    if not os.getenv("MINERU_API_URL") or not os.getenv("MINERU_API_TOKEN"):
        raise SystemExit("MINERU_API_URL and MINERU_API_TOKEN are required")
    port = int(os.getenv("MINERU_LISTEN_PORT", "50052"))
    server = ThreadingHTTPServer(("0.0.0.0", port), MinerUAdapterHandler)
    logger.info("MinerU compatibility adapter listening on port %d", port)
    server.serve_forever()


if __name__ == "__main__":
    main()
