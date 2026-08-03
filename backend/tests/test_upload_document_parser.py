import asyncio

import pytest

from app.gateway.upload_document_parser import (
    UploadDocumentParseError,
    conversation_document_parser_endpoint,
    extract_parser_markdown,
    parse_conversation_document,
)


def test_conversation_document_parser_endpoint_reuses_weknora_adapter(
    monkeypatch,
) -> None:
    monkeypatch.delenv("DEERFLOW_UPLOAD_PDF_PARSER_ENDPOINT", raising=False)
    monkeypatch.delenv("DEERFLOW_UPLOAD_PDF_PARSER_ENABLED", raising=False)
    monkeypatch.delenv("DEERFLOW_UPLOAD_DOCUMENT_PARSER_ENDPOINT", raising=False)
    monkeypatch.delenv("DEERFLOW_UPLOAD_DOCUMENT_PARSER_ENABLED", raising=False)
    monkeypatch.setenv(
        "WEKNORA_MINERU_ENDPOINT",
        "http://deer-flow-mineru-adapter:50052/",
    )

    assert conversation_document_parser_endpoint() == "http://deer-flow-mineru-adapter:50052/file_parse"


def test_conversation_document_parser_endpoint_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setenv(
        "WEKNORA_MINERU_ENDPOINT",
        "http://deer-flow-mineru-adapter:50052",
    )
    monkeypatch.setenv("DEERFLOW_UPLOAD_PDF_PARSER_ENABLED", "false")

    assert conversation_document_parser_endpoint() is None


def test_conversation_document_parser_endpoint_prefers_generic_configuration(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "DEERFLOW_UPLOAD_DOCUMENT_PARSER_ENDPOINT",
        "http://document-parser:50052",
    )
    monkeypatch.setenv(
        "DEERFLOW_UPLOAD_PDF_PARSER_ENDPOINT",
        "http://legacy-parser:50052",
    )
    monkeypatch.setenv("DEERFLOW_UPLOAD_DOCUMENT_PARSER_ENABLED", "true")
    monkeypatch.setenv("DEERFLOW_UPLOAD_PDF_PARSER_ENABLED", "false")

    assert conversation_document_parser_endpoint() == "http://document-parser:50052/file_parse"


def test_extract_parser_markdown_keeps_text_and_replaces_broken_images() -> None:
    payload = {"results": {"document": {"md_content": ("## 第 1 页\n\n扫描识别文字\n\n![构造示意图](images/page-1.png)")}}}

    markdown = extract_parser_markdown(payload)

    assert "扫描识别文字" in markdown
    assert "[文档图像：构造示意图]" in markdown
    assert "images/page-1.png" not in markdown


def test_extract_parser_markdown_rejects_invalid_envelope() -> None:
    with pytest.raises(UploadDocumentParseError):
        extract_parser_markdown({"results": {}})


def test_parse_conversation_document_preserves_pptx_media_type(monkeypatch) -> None:
    request_files = {}

    class FakeResponse:
        status_code = 200
        content = b'{"results":{"document":{"md_content":"# Slides"}}}'

        @staticmethod
        def json():
            return {"results": {"document": {"md_content": "# Slides"}}}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def post(self, endpoint, *, files):
            request_files.update(files)
            assert endpoint == "http://parser:50052/file_parse"
            return FakeResponse()

    monkeypatch.setattr(
        "app.gateway.upload_document_parser.httpx.AsyncClient",
        FakeAsyncClient,
    )

    result = asyncio.run(
        parse_conversation_document(
            endpoint="http://parser:50052/file_parse",
            filename="briefing.pptx",
            content=b"pptx-content",
        )
    )

    assert result == "# Slides"
    assert request_files["file"] == (
        "briefing.pptx",
        b"pptx-content",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )
