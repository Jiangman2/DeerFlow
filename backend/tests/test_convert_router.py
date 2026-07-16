import asyncio
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.gateway.routers import convert


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_convert_markdown_to_pdf_returns_download_response() -> None:
    body = convert.MarkdownToPdfRequest(markdown="# Report", filename="../report:final")

    with patch.object(convert, "markdown_to_pdf_bytes", return_value=b"%PDF fake") as mock_convert:
        response = _run(convert.convert_markdown_to_pdf(body))

    mock_convert.assert_called_once_with("# Report", title="reportfinal")
    assert response.media_type == "application/pdf"
    assert bytes(response.body) == b"%PDF fake"
    assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''reportfinal.pdf"


def test_convert_markdown_to_pdf_rejects_large_markdown() -> None:
    body = convert.MarkdownToPdfRequest(markdown="x" * (convert._MAX_MARKDOWN_CHARS + 1), filename="report")

    with pytest.raises(HTTPException) as exc_info:
        _run(convert.convert_markdown_to_pdf(body))

    assert exc_info.value.status_code == 413
    assert exc_info.value.detail == "Markdown too large"


def test_convert_markdown_to_pdf_defaults_blank_filename() -> None:
    body = convert.MarkdownToPdfRequest(markdown="# Report", filename="../:")

    with patch.object(convert, "markdown_to_pdf_bytes", return_value=b"%PDF fake") as mock_convert:
        response = _run(convert.convert_markdown_to_pdf(body))

    mock_convert.assert_called_once_with("# Report", title="report")
    assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''report.pdf"


def test_convert_router_mounts_markdown_pdf_endpoint() -> None:
    app = FastAPI()
    app.include_router(convert.router)

    with patch.object(convert, "markdown_to_pdf_bytes", return_value=b"%PDF fake"):
        with TestClient(app) as client:
            response = client.post(
                "/api/convert/markdown/pdf",
                json={"markdown": "# Report", "filename": "quarterly report"},
            )

    assert response.status_code == 200
    assert response.content == b"%PDF fake"
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''quarterly%20report.pdf"
