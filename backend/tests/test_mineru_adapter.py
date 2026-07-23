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


def test_scanned_pdf_uses_mineru_instead_of_builtin(monkeypatch):
    monkeypatch.setattr(
        app,
        "inspect_pdf_routing",
        lambda content: app.PDFRoutingDecision(page_count=2, scanned_pages=(1,)),
    )

    def unexpected_builtin(content, filename):
        raise AssertionError("scanned PDFs must not use the text-layer parser")

    monkeypatch.setattr(app, "parse_pdf_with_builtin", unexpected_builtin)
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

    markdown = app.mineru_pages_to_markdown(result)

    assert "## 第 1 页" in markdown
    assert "项目结论" in markdown
    assert "|产量|42|" in markdown
    assert "## 第 2 页" in markdown
    assert "风险提示" in markdown


def test_mineru_markdown_strips_inline_base64_images_before_embedding():
    result = {
        "pages": [
            {
                "page": 1,
                "blocks": [
                    {
                        "type": "table",
                        "content": '<table><tr><td>签字<img src="data:image/jpeg;base64,/9j/AAAA"/></td></tr></table>',
                    },
                    {
                        "type": "text",
                        "content": "![stamp](data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==)",
                    },
                ],
            }
        ]
    }

    markdown = app.mineru_pages_to_markdown(result)

    assert "data:image" not in markdown
    assert "base64," not in markdown
    assert markdown.count("[图片]") == 2
    assert "签字" in markdown


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
