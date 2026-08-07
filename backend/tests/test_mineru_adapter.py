"""Regression tests for the enterprise MinerU compatibility adapter."""

import base64
import io
import json
import sys
import threading
import time
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


def _fake_docx() -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<w:document />")
    return data.getvalue()


def _compound_directory_entry(
    name: str,
    object_type: int,
    *,
    left: int = 0xFFFFFFFF,
    right: int = 0xFFFFFFFF,
    child: int = 0xFFFFFFFF,
) -> bytes:
    entry = bytearray(128)
    encoded_name = name.encode("utf-16le") + b"\x00\x00"
    entry[: len(encoded_name)] = encoded_name
    entry[64:66] = len(encoded_name).to_bytes(2, "little")
    entry[66] = object_type
    entry[67] = 1
    entry[68:72] = left.to_bytes(4, "little")
    entry[72:76] = right.to_bytes(4, "little")
    entry[76:80] = child.to_bytes(4, "little")
    return bytes(entry)


def _fake_legacy_office(root_stream: str, *, embedded_stream: str | None = None) -> bytes:
    free_sector = 0xFFFFFFFF
    end_of_chain = 0xFFFFFFFE
    fat_sector = 0xFFFFFFFD
    header = bytearray(512)
    header[:8] = bytes.fromhex("D0CF11E0A1B11AE1")
    header[24:26] = (0x003E).to_bytes(2, "little")
    header[26:28] = (3).to_bytes(2, "little")
    header[28:30] = (0xFFFE).to_bytes(2, "little")
    header[30:32] = (9).to_bytes(2, "little")
    header[32:34] = (6).to_bytes(2, "little")
    header[44:48] = (1).to_bytes(4, "little")
    header[48:52] = (0).to_bytes(4, "little")
    header[56:60] = (4096).to_bytes(4, "little")
    header[60:64] = end_of_chain.to_bytes(4, "little")
    header[68:72] = end_of_chain.to_bytes(4, "little")
    header[76:80] = (1).to_bytes(4, "little")
    for offset in range(80, 512, 4):
        header[offset : offset + 4] = free_sector.to_bytes(4, "little")

    if embedded_stream is None:
        entries = [
            _compound_directory_entry("Root Entry", 5, child=1),
            _compound_directory_entry(root_stream, 2),
        ]
    else:
        entries = [
            _compound_directory_entry("Root Entry", 5, child=1),
            _compound_directory_entry(root_stream, 2, right=2),
            _compound_directory_entry("ObjectPool", 1, child=3),
            _compound_directory_entry(embedded_stream, 2),
        ]
    directory = b"".join(entries).ljust(512, b"\x00")
    fat = bytearray(free_sector.to_bytes(4, "little") * 128)
    fat[0:4] = end_of_chain.to_bytes(4, "little")
    fat[4:8] = fat_sector.to_bytes(4, "little")
    return bytes(header) + directory + bytes(fat)


def test_detect_input_suffix_recognises_pdf_pptx_and_docx():
    assert app.detect_input_suffix(b"%PDF-1.7\n") == ".pdf"
    assert app.detect_input_suffix(_fake_pptx()) == ".pptx"
    assert app.detect_input_suffix(_fake_docx()) == ".docx"


def test_detect_legacy_office_suffix_uses_compound_document_streams():
    content = _fake_legacy_office(
        "WordDocument",
        embedded_stream="PowerPoint Document",
    )

    assert app.detect_input_suffix(content, "document") == ".doc"


def test_detect_legacy_office_suffix_recognises_powerpoint_document():
    content = _fake_legacy_office("PowerPoint Document")

    assert app.detect_input_suffix(content, "upload") == ".ppt"


def test_detect_legacy_office_suffix_rejects_unknown_compound_document():
    try:
        app.detect_input_suffix(_fake_legacy_office("Workbook"), "document")
    except ValueError as exc:
        assert str(exc) == "Unsupported or encrypted legacy Office compound document"
    else:
        raise AssertionError("unknown legacy Office files must be rejected")


def test_detect_input_suffix_recognises_legacy_word_when_weknora_omits_extension(monkeypatch):
    content = _fake_legacy_office("WordDocument")

    assert app.detect_input_suffix(content, "document") == ".doc"


def test_builtin_pdf_adapter_extends_vector_figure_detection_to_chinese_captions():
    calls: list[str] = []

    def english_only_extractor(
        page,
        page_index,
        plain_text,
        raw,
        base_name,
        scale,
        quality,
        max_edge,
    ):
        calls.append(plain_text)
        return ["captured"] if "Figure 0" in plain_text else []

    module = SimpleNamespace(
        _FIGURE_CAPTION_RE=None,
        _FIGURE_CAPTION_SEARCH_RE=None,
        _extract_vector_figure_clips=english_only_extractor,
    )

    app.configure_builtin_pdf_visual_capture(module)

    assert module._FIGURE_CAPTION_SEARCH_RE.search("图 2 大数据背景下的数据资产管理特点特征")
    assert module._FIGURE_CAPTION_SEARCH_RE.search("图表3-1 处理架构")
    assert module._FIGURE_CAPTION_SEARCH_RE.search("Figure 4. Architecture")
    assert module._extract_vector_figure_clips(
        None,
        0,
        "图 2 大数据背景下的数据资产管理特点特征",
        None,
        "report",
        1.0,
        82,
        1920,
    ) == ["captured"]
    assert calls == ["Figure 0\n图 2 大数据背景下的数据资产管理特点特征"]


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


def test_visual_page_inspection_detects_chinese_figure_and_embedded_image():
    class _TextPage:
        def __init__(self, text):
            self.text = text

        def get_text_range(self):
            return self.text

        def close(self):
            return None

    class _Image:
        type = 3

        def get_bounds(self):
            return (80, 100, 530, 500)

    class _Page:
        def __init__(self, text, objects):
            self.text = text
            self.objects = objects

        def get_size(self):
            return (612, 792)

        def get_textpage(self):
            return _TextPage(self.text)

        def get_objects(self):
            return self.objects

        def close(self):
            return None

    class _PDF:
        def __init__(self, content):
            assert content == b"%PDF-visual"
            self.pages = [
                _Page("图 2 大数据背景下的数据资产管理特征", []),
                _Page("图片没有图题但属于正文证据", [_Image()]),
                _Page("纯文字说明，不需要生成视觉页证据", []),
            ]

        def __len__(self):
            return len(self.pages)

        def __getitem__(self, index):
            return self.pages[index]

        def close(self):
            return None

    evidence = app.inspect_pdf_visual_pages(
        b"%PDF-visual",
        pdfium_module=SimpleNamespace(PdfDocument=_PDF),
        raw_module=SimpleNamespace(FPDF_PAGEOBJ_IMAGE=3, FPDF_PAGEOBJ_PATH=2),
    )

    assert evidence == (
        app.VisualPageEvidence(
            page_number=1,
            context="图 2 大数据背景下的数据资产管理特征",
        ),
        app.VisualPageEvidence(
            page_number=2,
            context="图片没有图题但属于正文证据",
        ),
    )


def test_searchable_pdf_visual_evidence_keeps_caption_and_page_image(monkeypatch):
    monkeypatch.setattr(
        app,
        "inspect_pdf_visual_pages",
        lambda content: (
            app.VisualPageEvidence(
                page_number=10,
                context="图 2 大数据背景下的数据资产管理特征",
            ),
        ),
    )
    monkeypatch.setattr(
        app,
        "render_pdf_page_images",
        lambda content, page_numbers: (
            "![原始第 10 页](images/source-page-0010.jpg)",
            {"images/source-page-0010.jpg": "page-ten"},
        ),
    )

    markdown, images = app.preserve_searchable_pdf_visuals(
        b"%PDF-text",
        "# 白皮书\n\n第十页正文",
        {},
    )

    assert images == {"images/source-page-0010.jpg": "page-ten"}
    assert "第 10 页视觉证据" in markdown
    assert "图 2 大数据背景下的数据资产管理特征" in markdown
    assert "![图 2 大数据背景下的数据资产管理特点特征]" not in markdown
    assert "images/source-page-0010.jpg" in markdown


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


def test_searchable_word_uses_converted_pdf_visual_parser_without_calling_mineru(
    monkeypatch,
):
    source = _fake_docx()
    converted_pdf = b"%PDF-converted-word"
    monkeypatch.setattr(app, "convert_to_pdf", lambda content, filename: converted_pdf)
    monkeypatch.setattr(
        app,
        "inspect_pdf_routing",
        lambda content: app.PDFRoutingDecision(page_count=2, scanned_pages=()),
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        app,
        "parse_word_with_builtin",
        lambda content, filename, suffix: (
            "# Word 原生解析\n\n正文\n\n![原始位图](images/word-native.png)",
            {"images/word-native.png": "word-native-image"},
        ),
    )

    def parse_builtin(content, filename):
        captured.update({"content": content, "filename": filename})
        return (
            "# Word 报告\n\n![图 2](images/report_p1_fig1.jpg)\n\n图 2 数据资产管理特征",
            {"images/report_p1_fig1.jpg": "word-page-image"},
        )

    monkeypatch.setattr(app, "parse_pdf_with_builtin", parse_builtin)

    class _UnexpectedMinerU:
        def __init__(self, **kwargs):
            raise AssertionError("searchable Word documents must not call MinerU")

    monkeypatch.setattr(app, "EnterpriseMinerUClient", _UnexpectedMinerU)

    response = app.parse_upload(source, "数据资产管理实践白皮书.docx")

    assert captured == {
        "content": converted_pdf,
        "filename": "数据资产管理实践白皮书.pdf",
    }
    assert response["results"]["document"]["images"] == {"images/word-native.png": "word-native-image", "images/report_p1_fig1.jpg": "word-page-image"}
    assert "Word 原生解析" in response["results"]["document"]["md_content"]
    assert "文档视觉内容" in response["results"]["document"]["md_content"]
    assert "图 2 数据资产管理特征" in response["results"]["document"]["md_content"]


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


def test_parse_coordinator_serializes_native_parser_calls():
    coordinator = app.ParseCoordinator()
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    results: list[dict[str, str]] = []

    def first_operation():
        first_entered.set()
        assert release_first.wait(timeout=2)
        return {"request": "first"}

    def second_operation():
        second_entered.set()
        return {"request": "second"}

    first = threading.Thread(
        target=lambda: results.append(
            coordinator.run(
                first_operation,
                filename="first.pdf",
                content_bytes=100,
            )
        )
    )
    second = threading.Thread(
        target=lambda: results.append(
            coordinator.run(
                second_operation,
                filename="second.pdf",
                content_bytes=200,
            )
        )
    )

    first.start()
    assert first_entered.wait(timeout=2)
    second.start()

    deadline = time.monotonic() + 2
    while coordinator.snapshot()["waiting"] != 1 and time.monotonic() < deadline:
        time.sleep(0.01)

    assert coordinator.snapshot() == {"active": 1, "waiting": 1}
    assert not second_entered.is_set()

    release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert second_entered.is_set()
    assert results == [{"request": "first"}, {"request": "second"}]
    assert coordinator.snapshot() == {"active": 0, "waiting": 0}


def test_health_probe_remains_responsive_while_parse_is_running(monkeypatch):
    coordinator = app.ParseCoordinator()
    parse_entered = threading.Event()
    release_parse = threading.Event()
    monkeypatch.setattr(app, "_PARSE_COORDINATOR", coordinator)

    def blocking_parse(content, filename):
        assert content == b"%PDF-test"
        assert filename == "queued.pdf"
        parse_entered.set()
        assert release_parse.wait(timeout=2)
        return app.weknora_response("# Parsed")

    monkeypatch.setattr(app, "parse_upload", blocking_parse)
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.MinerUAdapterHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    boundary = "deerflow-serialized-parse"
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="queued.pdf"\r\nContent-Type: application/pdf\r\n\r\n').encode() + b"%PDF-test" + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}/file_parse",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    post_result: list[int] = []

    def post_document():
        with urllib.request.urlopen(request, timeout=3) as response:
            post_result.append(response.status)
            response.read()

    post_thread = threading.Thread(target=post_document)
    post_thread.start()
    try:
        assert parse_entered.wait(timeout=2)
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/health",
            timeout=1,
        ) as response:
            health = json.loads(response.read())

        assert response.status == 200
        assert health == {
            "status": "ok",
            "parser": {"active": 1, "waiting": 0},
        }
    finally:
        release_parse.set()
        post_thread.join(timeout=3)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)

    assert post_result == [200]


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
