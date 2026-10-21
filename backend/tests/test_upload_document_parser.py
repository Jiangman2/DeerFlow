import asyncio

import pytest

from app.gateway.upload_document_parser import (
    UploadDocumentParseError,
    _parser_error_detail,
    close_conversation_document_parser_client,
    conversation_document_parser_endpoint,
    extract_parser_markdown,
    parse_conversation_document,
)


def test_parser_error_detail_is_bounded_and_single_line() -> None:
    response = type(
        "Response",
        (),
        {"json": lambda self: {"error": "NPU   out of\n memory " + "x" * 500}},
    )()

    detail = _parser_error_detail(response)

    assert detail is not None
    assert detail.startswith("NPU out of memory")
    assert "\n" not in detail
    assert len(detail) == 300


def test_parser_error_detail_ignores_non_json_response() -> None:
    response = type(
        "Response",
        (),
        {"json": lambda self: (_ for _ in ()).throw(ValueError("not json"))},
    )()

    assert _parser_error_detail(response) is None


def test_parse_conversation_document_surfaces_adapter_failure_detail(
    monkeypatch,
) -> None:
    class FakeResponse:
        status_code = 503
        content = b'{"error":"MinerU NPU out of memory"}'

        @staticmethod
        def json():
            return {"error": "MinerU NPU out of memory"}

    class FakeClient:
        async def post(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(
        "app.gateway.upload_document_parser._get_parser_client",
        lambda: FakeClient(),
    )

    async def run_test() -> None:
        with pytest.raises(
            UploadDocumentParseError,
            match="HTTP 503: MinerU NPU out of memory",
        ):
            await parse_conversation_document(
                endpoint="http://parser:50052/file_parse",
                filename="scan.pdf",
                content=b"%PDF-test",
            )

    asyncio.run(run_test())


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
    created_clients = 0
    post_count = 0

    class FakeResponse:
        status_code = 200
        content = b'{"results":{"document":{"md_content":"# Slides"}}}'

        @staticmethod
        def json():
            return {"results": {"document": {"md_content": "# Slides"}}}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            nonlocal created_clients
            created_clients += 1
            assert kwargs["trust_env"] is False
            assert kwargs["limits"].max_connections == 32
            self.is_closed = False

        async def post(self, endpoint, *, headers, files, timeout):
            nonlocal post_count
            post_count += 1
            request_files.update(files)
            assert endpoint == "http://parser:50052/file_parse"
            assert headers == {"X-DeerFlow-Parse-Priority": "interactive"}
            assert timeout.read == 320
            return FakeResponse()

        async def aclose(self):
            self.is_closed = True

    monkeypatch.setattr(
        "app.gateway.upload_document_parser.httpx.AsyncClient",
        FakeAsyncClient,
    )

    async def run_test() -> str:
        try:
            first = await parse_conversation_document(
                endpoint="http://parser:50052/file_parse",
                filename="briefing.pptx",
                content=b"pptx-content",
            )
            second = await parse_conversation_document(
                endpoint="http://parser:50052/file_parse",
                filename="briefing.pptx",
                content=b"pptx-content",
            )
            assert second == "# Slides"
            return first
        finally:
            await close_conversation_document_parser_client()

    result = asyncio.run(run_test())

    assert result == "# Slides"
    assert created_clients == 1
    assert post_count == 2
    assert request_files["file"] == (
        "briefing.pptx",
        b"pptx-content",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )
