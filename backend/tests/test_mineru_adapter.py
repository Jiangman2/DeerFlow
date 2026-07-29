"""Regression tests for the enterprise MinerU compatibility adapter."""

import base64
import io
import sys
import threading
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from docker.mineru_adapter import app


def _fake_pptx() -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("ppt/presentation.xml", "<p:presentation />")
    return data.getvalue()


def test_detect_input_suffix_recognises_pdf_and_pptx():
    assert app.detect_input_suffix(b"%PDF-1.7\n") == ".pdf"
    assert app.detect_input_suffix(_fake_pptx()) == ".pptx"


def test_pdf_inspection_routes_text_pages_to_builtin():
    class _TextPage:
        def get_text_range(self):
            return "This PDF has a real searchable text layer."

        def close(self):
            return None

    class _Page:
        def get_size(self):
            return (612, 792)

        def get_textpage(self):
            return _TextPage()

        def get_objects(self):
            return []

        def close(self):
            return None

    class _PDF:
        def __init__(self, content):
            assert content.startswith(b"%PDF-")

        def __len__(self):
            return 1

        def __getitem__(self, index):
            assert index == 0
            return _Page()

        def close(self):
            return None

    decision = app.inspect_pdf_routing(
        b"%PDF-text",
        pdfium_module=SimpleNamespace(PdfDocument=_PDF),
        raw_module=SimpleNamespace(FPDF_PAGEOBJ_IMAGE=3),
    )

    assert decision.page_count == 1
    assert decision.scanned_pages == ()
    assert decision.use_mineru is False


def test_pdf_inspection_routes_full_page_image_to_mineru():
    class _TextPage:
        def get_text_range(self):
            return "embedded OCR text that must not hide the scanned page"

        def close(self):
            return None

    class _Image:
        type = 3

        def get_bounds(self):
            return (0, 0, 612, 792)

    class _Page:
        def get_size(self):
            return (612, 792)

        def get_textpage(self):
            return _TextPage()

        def get_objects(self):
            return [_Image()]

        def close(self):
            return None

    class _PDF:
        def __init__(self, content):
            assert content.startswith(b"%PDF-")

        def __len__(self):
            return 1

        def __getitem__(self, index):
            assert index == 0
            return _Page()

        def close(self):
            return None

    decision = app.inspect_pdf_routing(
        b"%PDF-scan",
        pdfium_module=SimpleNamespace(PdfDocument=_PDF),
        raw_module=SimpleNamespace(FPDF_PAGEOBJ_IMAGE=3),
    )

    assert decision.page_count == 1
    assert decision.scanned_pages == (1,)
    assert decision.use_mineru is True


def test_text_pdf_uses_weknora_builtin_without_calling_mineru(monkeypatch):
    monkeypatch.setattr(
        app,
        "inspect_pdf_routing",
        lambda content: app.PDFRoutingDecision(page_count=2, scanned_pages=()),
    )
    monkeypatch.setattr(
        app,
        "parse_pdf_with_builtin",
        lambda content, filename: ("# Native PDF\n\nsearchable text", {"images/chart.png": "abc"}),
    )

    class _UnexpectedMinerU:
        def __init__(self, **kwargs):
            raise AssertionError("text PDFs must not call MinerU")

    monkeypatch.setattr(app, "EnterpriseMinerUClient", _UnexpectedMinerU)

    response = app.parse_upload(b"%PDF-text", "report.pdf")

    assert response == {
        "results": {
            "document": {
                "md_content": "# Native PDF\n\nsearchable text",
                "images": {"images/chart.png": "abc"},
            }
        }
    }


def test_pdfium_inspection_failure_uses_builtin_parser(monkeypatch):
    def failed_inspection(content):
        raise RuntimeError("PDFium: Data format error")

    monkeypatch.setattr(app, "inspect_pdf_routing", failed_inspection)
    monkeypatch.setattr(
        app,
        "parse_pdf_with_builtin",
        lambda content, filename: (
            "# Recovered PDF\n\nsearchable text",
            {"images/chart.png": "abc"},
        ),
    )

    class _UnexpectedMinerU:
        def __init__(self, **kwargs):
            raise AssertionError("a PDF recovered by the builtin parser must not call MinerU")

    monkeypatch.setattr(app, "EnterpriseMinerUClient", _UnexpectedMinerU)

    response = app.parse_upload(b"%PDF-pdfium-incompatible", "recovered.pdf")

    assert response["results"]["document"] == {
        "md_content": "# Recovered PDF\n\nsearchable text",
        "images": {"images/chart.png": "abc"},
    }


def test_builtin_pdf_failure_falls_back_to_mineru(monkeypatch):
    monkeypatch.setattr(
        app,
        "inspect_pdf_routing",
        lambda content: app.PDFRoutingDecision(page_count=2, scanned_pages=()),
    )

    def failed_builtin(content, filename):
        raise RuntimeError("builtin parser rejected the PDF")

    monkeypatch.setattr(app, "parse_pdf_with_builtin", failed_builtin)
    monkeypatch.setattr(
        app,
        "render_pdf_page_images",
        lambda content, page_numbers: ("", {}),
    )
    monkeypatch.setenv("MINERU_API_URL", "http://mineru.internal/parse/v1")
    monkeypatch.setenv("MINERU_API_TOKEN", "secret")

    class _MinerU:
        def __init__(self, **kwargs):
            pass

        def parse_pdf(self, pdf, filename):
            assert pdf == b"%PDF-builtin-incompatible"
            assert filename == "fallback.pdf"
            return {
                "pages": [
                    {
                        "page": 1,
                        "blocks": [{"type": "text", "content": "MinerU fallback result"}],
                    }
                ]
            }

    monkeypatch.setattr(app, "EnterpriseMinerUClient", _MinerU)

    response = app.parse_upload(b"%PDF-builtin-incompatible", "fallback.pdf")

    assert "MinerU fallback result" in response["results"]["document"]["md_content"]


def test_scanned_pdf_uses_mineru_instead_of_builtin(monkeypatch):
    monkeypatch.setattr(
        app,
        "inspect_pdf_routing",
        lambda content: app.PDFRoutingDecision(page_count=2, scanned_pages=(1,)),
    )

    def unexpected_builtin(content, filename):
        raise AssertionError("scanned PDFs must not use the text-layer parser")

    monkeypatch.setattr(app, "parse_pdf_with_builtin", unexpected_builtin)
    monkeypatch.setattr(
        app,
        "render_pdf_page_images",
        lambda content, page_numbers: ("", {}),
    )
    monkeypatch.setenv("MINERU_API_URL", "http://mineru.internal/parse/v1")
    monkeypatch.setenv("MINERU_API_TOKEN", "secret")

    class _MinerU:
        def __init__(self, **kwargs):
            pass

        def parse_pdf(self, pdf, filename):
            assert pdf == b"%PDF-scan"
            assert filename == "scan.pdf"
            return {"pages": [{"page": 1, "blocks": [{"type": "text", "content": "OCR result"}]}]}

    monkeypatch.setattr(app, "EnterpriseMinerUClient", _MinerU)

    response = app.parse_upload(b"%PDF-scan", "scan.pdf")

    assert response["results"]["document"]["md_content"] == "## 第 1 页\n\nOCR result"


def test_source_page_render_failure_does_not_discard_mineru_result(monkeypatch):
    monkeypatch.setattr(
        app,
        "inspect_pdf_routing",
        lambda content: app.PDFRoutingDecision(page_count=1, scanned_pages=(1,)),
    )
    monkeypatch.setenv("MINERU_API_URL", "http://mineru.internal/parse/v1")
    monkeypatch.setenv("MINERU_API_TOKEN", "secret")

    class _MinerU:
        def __init__(self, **kwargs):
            pass

        def parse_pdf(self, pdf, filename):
            return {
                "pages": [
                    {
                        "page": 1,
                        "blocks": [
                            {
                                "type": "text",
                                "content": "OCR survived rendering failure",
                            }
                        ],
                    }
                ]
            }

    def failed_render(pdf, page_numbers):
        raise RuntimeError("PDFium: Data format error")

    monkeypatch.setattr(app, "EnterpriseMinerUClient", _MinerU)
    monkeypatch.setattr(app, "render_pdf_page_images", failed_render)

    response = app.parse_upload(b"%PDF-render-incompatible", "scan.pdf")
    document = response["results"]["document"]

    assert "OCR survived rendering failure" in document["md_content"]
    assert document["images"] == {}


def test_mineru_pages_to_markdown_preserves_page_and_block_content():
    result = {
        "pages": [
            {
                "page": 1,
                "blocks": [
                    {"type": "text", "content": "项目结论"},
                    {"type": "table", "content": "| 指标 | 数值 |\n|---|---|\n|产量|42|"},
                ],
            },
            {"page": 2, "blocks": [{"type": "text", "content": "风险提示"}]},
        ]
    }

    markdown, images = app.mineru_pages_to_markdown(result)

    assert "## 第 1 页" in markdown
    assert "项目结论" in markdown
    assert "|产量|42|" in markdown
    assert "## 第 2 页" in markdown
    assert "风险提示" in markdown
    assert images == {}


def test_mineru_markdown_extracts_inline_images_into_weknora_assets():
    jpeg = b"\xff\xd8\xff\xe0deerflow-jpeg"
    png = b"\x89PNG\r\n\x1a\ndeerflow-png"
    result = {
        "pages": [
            {
                "page": 1,
                "blocks": [
                    {
                        "type": "table",
                        "content": (f'<table><tr><td>签字<img alt="签字" src="data:image/jpeg;base64,{base64.b64encode(jpeg).decode()}"/></td></tr></table>'),
                    },
                    {
                        "type": "text",
                        "content": (f"![stamp](data:image/png;base64,{base64.b64encode(png).decode()})"),
                    },
                ],
            }
        ]
    }

    markdown, images = app.mineru_pages_to_markdown(result)

    assert "data:image" not in markdown
    assert "base64," not in markdown
    assert "签字" in markdown
    assert len(images) == 2
    assert all(path.startswith("images/mineru-") for path in images)
    assert {base64.b64decode(value) for value in images.values()} == {jpeg, png}
    assert all(path in markdown for path in images)


def test_mineru_markdown_replaces_invalid_inline_images_with_safe_marker():
    result = {
        "pages": [
            {
                "page": 1,
                "blocks": [
                    {
                        "type": "text",
                        "content": "![broken](data:image/png;base64,not-valid-base64!)",
                    }
                ],
            }
        ]
    }

    markdown, images = app.mineru_pages_to_markdown(result)

    assert "data:image" not in markdown
    assert images == {}
    assert "[图片]" in markdown


def test_scanned_pdf_preserves_source_page_images_when_mineru_has_no_images(
    monkeypatch,
):
    monkeypatch.setattr(
        app,
        "inspect_pdf_routing",
        lambda content: app.PDFRoutingDecision(page_count=2, scanned_pages=(1, 2)),
    )
    monkeypatch.setattr(
        app,
        "render_pdf_page_images",
        lambda content, page_numbers: (
            "![原始第 1 页](images/source-page-0001.jpg)",
            {"images/source-page-0001.jpg": "page-image"},
        ),
    )
    monkeypatch.setenv("MINERU_API_URL", "http://mineru.internal/parse/v1")
    monkeypatch.setenv("MINERU_API_TOKEN", "secret")

    class _MinerU:
        def __init__(self, **kwargs):
            pass

        def parse_pdf(self, pdf, filename):
            return {
                "pages": [
                    {
                        "page": 1,
                        "blocks": [{"type": "text", "content": "OCR result"}],
                    }
                ]
            }

    monkeypatch.setattr(app, "EnterpriseMinerUClient", _MinerU)

    response = app.parse_upload(b"%PDF-scan", "scan.pdf")
    document = response["results"]["document"]

    assert "OCR result" in document["md_content"]
    assert "## 原始页面图像" in document["md_content"]
    assert "images/source-page-0001.jpg" in document["md_content"]
    assert document["images"] == {"images/source-page-0001.jpg": "page-image"}


def test_render_pdf_page_images_returns_persistent_references():
    class _Image:
        def convert(self, mode):
            assert mode == "RGB"
            return self

        def save(self, output, **kwargs):
            assert kwargs["format"] == "JPEG"
            output.write(b"\xff\xd8rendered-page")

        def close(self):
            return None

    class _Bitmap:
        def to_pil(self):
            return _Image()

        def close(self):
            return None

    class _Page:
        def render(self, scale):
            assert scale >= 1
            return _Bitmap()

        def close(self):
            return None

    class _PDF:
        def __init__(self, content):
            assert content == b"%PDF-render"

        def __len__(self):
            return 3

        def __getitem__(self, index):
            assert index in {0, 2}
            return _Page()

        def close(self):
            return None

    markdown, images = app.render_pdf_page_images(
        b"%PDF-render",
        (1, 3),
        pdfium_module=SimpleNamespace(PdfDocument=_PDF),
    )

    assert "![原始第 1 页](images/source-page-0001.jpg)" in markdown
    assert "![原始第 3 页](images/source-page-0003.jpg)" in markdown
    assert set(images) == {
        "images/source-page-0001.jpg",
        "images/source-page-0003.jpg",
    }
    assert all(base64.b64decode(encoded) == b"\xff\xd8rendered-page" for encoded in images.values())


def test_enterprise_client_uses_bearer_token_and_pdf_base64():
    captured = {}

    class _Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"pages": [{"page": 1, "blocks": [{"type": "text", "content": "ok"}]}]}

    class _Session:
        def post(self, url, **kwargs):
            captured.update({"url": url, **kwargs})
            return _Response()

    client = app.EnterpriseMinerUClient(
        api_url="http://mineru.internal/parse/v1",
        api_token="secret-token",
        timeout_seconds=123,
        session=_Session(),
    )

    result = client.parse_pdf(b"%PDF-test", "deck.pdf")

    assert result["pages"][0]["blocks"][0]["content"] == "ok"
    assert captured["headers"]["Authorization"] == "Bearer secret-token"
    assert captured["json"]["filename"] == "deck.pdf"
    assert base64.b64decode(captured["json"]["pdf_base64"]) == b"%PDF-test"
    assert captured["timeout"] == 123


def test_weknora_response_wraps_markdown_and_images_in_expected_schema():
    response = app.weknora_response("# Parsed", {"images/chart.png": "abc"})

    assert response == {
        "results": {
            "document": {
                "md_content": "# Parsed",
                "images": {"images/chart.png": "abc"},
            }
        }
    }


def test_parse_multipart_upload_accepts_weknora_files_field():
    body = b'--deerflow-test\r\nContent-Disposition: form-data; name="files"; filename="deck.pdf"\r\nContent-Type: application/pdf\r\n\r\n%PDF-test\r\n--deerflow-test--\r\n'

    filename, content = app.parse_multipart_upload('multipart/form-data; boundary="deerflow-test"', body)

    assert filename == "deck.pdf"
    assert content == b"%PDF-test"


def test_weknora_docs_probe_is_supported():
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.MinerUAdapterHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/docs", timeout=2) as response:
            assert response.status == 200
            assert response.read() == b'{"status": "ok"}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
