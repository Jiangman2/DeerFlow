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

import pytest

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


def test_searchable_pdf_visual_evidence_rejects_missing_required_page(monkeypatch):
    monkeypatch.setattr(
        app,
        "inspect_pdf_visual_pages",
        lambda content: (
            app.VisualPageEvidence(page_number=10, context="图 2 完井结构"),
        ),
    )
    monkeypatch.setattr(
        app,
        "render_pdf_page_images",
        lambda content, page_numbers: ("", {}),
    )

    with pytest.raises(RuntimeError, match="omitted required source page 10"):
        app.preserve_searchable_pdf_visuals(
            b"%PDF-text",
            "# 报告\n\n正文",
            {},
        )


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
    monkeypatch.setattr(app, "inspect_pdf_visual_pages", lambda content: ())

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
    monkeypatch.setattr(app, "inspect_pdf_visual_pages", lambda content: ())

    response = app.parse_upload(source, "数据资产管理实践白皮书.docx")

    assert captured == {
        "content": converted_pdf,
        "filename": "数据资产管理实践白皮书.pdf",
    }
    assert response["results"]["document"]["images"] == {"images/word-native.png": "word-native-image", "images/report_p1_fig1.jpg": "word-page-image"}
    assert "Word 原生解析" in response["results"]["document"]["md_content"]
    assert "文档视觉内容" in response["results"]["document"]["md_content"]
    assert "图 2 数据资产管理特征" in response["results"]["document"]["md_content"]


def test_word_visual_conversion_failure_rejects_incomplete_parse(monkeypatch):
    source = _fake_docx()
    native_markdown = "# Native Word\n\nRecovered body and tables"
    native_images = {"images/word-native.png": "word-native-image"}
    monkeypatch.setattr(
        app,
        "parse_word_with_builtin",
        lambda content, filename, suffix: (native_markdown, native_images),
    )

    def failed_conversion(content, filename):
        raise RuntimeError("LibreOffice produced no PDF")

    monkeypatch.setattr(app, "convert_to_pdf", failed_conversion)

    with pytest.raises(RuntimeError, match="LibreOffice produced no PDF"):
        app.parse_upload(source, "large-report.docx")


def test_word_mineru_visual_failure_rejects_incomplete_parse(monkeypatch):
    source = _fake_docx()
    native_markdown = "# Native Word\n\nRecovered body and tables"
    native_images = {"images/word-native.png": "word-native-image"}
    converted_pdf = b"%PDF-word-visual"
    monkeypatch.setenv("MINERU_API_URL", "https://model.ai.cnooc/mineru")
    monkeypatch.setenv("MINERU_API_TOKEN", "test-token")
    monkeypatch.setattr(
        app,
        "parse_word_with_builtin",
        lambda content, filename, suffix: (native_markdown, native_images),
    )
    monkeypatch.setattr(
        app,
        "convert_to_pdf",
        lambda content, filename: converted_pdf,
    )
    monkeypatch.setattr(
        app,
        "inspect_pdf_routing",
        lambda content: app.PDFRoutingDecision(
            page_count=3,
            scanned_pages=(2, 3),
        ),
    )

    class _FailedMinerU:
        def __init__(self, **kwargs):
            return None

        def parse_pdf(self, content, filename):
            raise app.requests.ConnectionError("upstream closed the connection")

    monkeypatch.setattr(app, "EnterpriseMinerUClient", _FailedMinerU)

    with pytest.raises(app.requests.ConnectionError, match="upstream closed"):
        app.parse_upload(source, "large-report.docx")


def test_pdf_mineru_failure_remains_visible_without_native_word_result(monkeypatch):
    monkeypatch.setenv("MINERU_API_URL", "https://model.ai.cnooc/mineru")
    monkeypatch.setenv("MINERU_API_TOKEN", "test-token")
    monkeypatch.setattr(
        app,
        "inspect_pdf_routing",
        lambda content: app.PDFRoutingDecision(
            page_count=1,
            scanned_pages=(1,),
        ),
    )

    class _FailedMinerU:
        def __init__(self, **kwargs):
            return None

        def parse_pdf(self, content, filename):
            raise app.requests.ConnectionError("upstream closed the connection")

    monkeypatch.setattr(app, "EnterpriseMinerUClient", _FailedMinerU)

    with pytest.raises(app.requests.ConnectionError, match="upstream closed"):
        app.parse_upload(b"%PDF-scan", "scan.pdf")


def test_convert_to_pdf_reports_silent_libreoffice_output_failure(monkeypatch):
    monkeypatch.setattr(
        app.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stderr="",
            stdout="convert input.docx using filter : writer_pdf_Export",
        ),
    )

    with pytest.raises(RuntimeError) as exc_info:
        app.convert_to_pdf(_fake_docx(), "large-report.docx")

    message = str(exc_info.value)
    assert "returncode=0" in message
    assert "output_exists=false" in message
    assert "input.docx" in message
    assert "writer_pdf_Export" in message


def test_word_conversion_failure_still_fails_when_native_parse_also_failed(monkeypatch):
    source = _fake_docx()

    def failed_native_parse(content, filename, suffix):
        raise RuntimeError("native parser failed")

    def failed_conversion(content, filename):
        raise RuntimeError("visual conversion failed")

    monkeypatch.setattr(app, "parse_word_with_builtin", failed_native_parse)
    monkeypatch.setattr(app, "convert_to_pdf", failed_conversion)

    with pytest.raises(RuntimeError, match="visual conversion failed"):
        app.parse_upload(source, "damaged.docx")


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
    monkeypatch.setattr(app, "inspect_pdf_visual_pages", lambda content: ())

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


def test_source_page_render_failure_rejects_incomplete_mineru_result(monkeypatch):
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

    with pytest.raises(RuntimeError, match="PDFium: Data format error"):
        app.parse_upload(b"%PDF-render-incompatible", "scan.pdf")


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


def test_enterprise_mineru_client_matches_native_rest_contract():
    captured = {}

    class _Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "backend": "pipeline",
                "version": "3.4.0",
                "results": {
                    "deck.pdf": {
                        "md_content": "# Parsed\n\n![chart](chart.png)",
                        "images": {
                            "chart.png": "data:image/png;base64,"
                            + base64.b64encode(b"\x89PNG\r\n\x1a\nchart").decode(),
                        },
                    }
                },
            }

    class _Session:
        def post(self, url, **kwargs):
            captured.update({"url": url, **kwargs})
            return _Response()

    client = app.EnterpriseMinerUClient(
        api_url="http://model.ai.cnooc/member1/mineru-2605",
        api_token="secret-token",
        timeout_seconds=123,
        session=_Session(),
    )

    result = client.parse_pdf(b"%PDF-test", "deck.pdf")

    assert result["results"]["deck.pdf"]["md_content"].startswith("# Parsed")
    assert captured["url"] == "http://model.ai.cnooc/member1/mineru-2605/file_parse"
    assert captured["headers"]["Authorization"] == "Bearer secret-token"
    assert captured["headers"]["Accept"] == "application/json"
    assert "Content-Type" not in captured["headers"]
    assert captured["files"] == {
        "files": ("deck.pdf", b"%PDF-test", "application/pdf")
    }
    assert captured["data"] == {
        "backend": "pipeline",
        "parse_method": "auto",
        "formula_enable": "true",
        "table_enable": "true",
        "return_md": "true",
        "return_images": "true",
        "response_format_zip": "false",
    }
    assert captured["timeout"] == 123


def test_split_pdf_for_sync_uses_lossless_page_ranges():
    sources = []
    batches = []

    class _Source:
        def __init__(self, content):
            assert content == b"%PDF-large"
            self.closed = False
            sources.append(self)

        def __len__(self):
            return 5

        def close(self):
            self.closed = True

    class _Batch:
        def __init__(self):
            self.pages = None
            self.closed = False
            batches.append(self)

        def import_pages(self, source, pages):
            assert source is sources[0]
            self.pages = pages

        def save(self, output):
            output.write(b"%PDF-batch-" + bytes(self.pages))

        def close(self):
            self.closed = True

    class _PdfDocument:
        def __new__(cls, content):
            return _Source(content)

        @staticmethod
        def new():
            return _Batch()

    result = app.split_pdf_for_sync(
        b"%PDF-large",
        2,
        pdfium_module=SimpleNamespace(PdfDocument=_PdfDocument),
    )

    assert [(first, last) for _pdf, first, last in result] == [
        (1, 2),
        (3, 4),
        (5, 5),
    ]
    assert [batch.pages for batch in batches] == [[0, 1], [2, 3], [4]]
    assert sources[0].closed is True
    assert all(batch.closed for batch in batches)


def test_enterprise_mineru_sync_retries_only_an_early_connection_failure(
    monkeypatch,
):
    calls = []

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": {
                    "deck.pdf": {
                        "md_content": "# Recovered",
                        "images": {},
                    }
                }
            }

    class _Session:
        def post(self, url, **kwargs):
            calls.append(url)
            if len(calls) == 1:
                raise app.requests.ConnectionError("connection reset")
            return _Response()

    monkeypatch.setattr(
        app,
        "split_pdf_for_sync",
        lambda pdf, maximum_pages: ((pdf, 1, 1),),
    )
    client = app.EnterpriseMinerUClient(
        api_url="https://model.ai.cnooc/member1/mineru-2605",
        api_token="secret-token",
        sync_transient_retries=1,
        sync_retry_delay_seconds=0,
        sync_retry_max_elapsed_seconds=60,
        session=_Session(),
    )

    result = client.parse_pdf(b"%PDF-test", "deck.pdf")

    assert result["results"]["deck.pdf"]["md_content"] == "# Recovered"
    assert len(calls) == 2


def test_enterprise_mineru_sync_retries_a_bounded_batch_after_late_disconnect(
    monkeypatch,
):
    calls = []

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": {
                    "deck.pdf": {
                        "md_content": "# Recovered bounded batch",
                        "images": {},
                    }
                }
            }

    class _Session:
        def post(self, url, **kwargs):
            calls.append(url)
            if len(calls) == 1:
                raise app.requests.ConnectionError("late disconnect")
            return _Response()

    monotonic_values = iter((100.0, 588.0, 600.0))
    monkeypatch.setattr(app.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(
        app,
        "split_pdf_for_sync",
        lambda pdf, maximum_pages: ((pdf, 1, 20),),
    )
    client = app.EnterpriseMinerUClient(
        api_url="https://model.ai.cnooc/member1/mineru-2605",
        api_token="secret-token",
        sync_transient_retries=1,
        sync_retry_delay_seconds=0,
        sync_retry_max_elapsed_seconds=600,
        session=_Session(),
    )

    result = client.parse_pdf(b"%PDF-test", "deck.pdf")

    assert result["results"]["deck.pdf"]["md_content"] == (
        "# Recovered bounded batch"
    )
    assert len(calls) == 2


def test_enterprise_mineru_sync_splits_and_merges_large_pdf(monkeypatch):
    calls = []

    class _Response:
        def __init__(self, filename):
            self.filename = filename

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": {
                    self.filename: {
                        "md_content": f"# Parsed {self.filename}",
                        "images": {},
                    }
                }
            }

    class _Session:
        def post(self, url, **kwargs):
            filename = kwargs["files"]["files"][0]
            calls.append(filename)
            return _Response(filename)

    monkeypatch.setattr(
        app,
        "split_pdf_for_sync",
        lambda pdf, maximum_pages: (
            (b"%PDF-pages-1-40", 1, 40),
            (b"%PDF-pages-41-69", 41, 69),
        ),
    )
    client = app.EnterpriseMinerUClient(
        api_url="https://model.ai.cnooc/member1/mineru-2605",
        api_token="secret-token",
        session=_Session(),
    )

    result = client.parse_pdf(b"%PDF-large", "large-report.pdf")
    document = result["results"]["large-report.pdf"]

    assert calls == [
        "large-report.pages-0001-0040.pdf",
        "large-report.pages-0041-0069.pdf",
    ]
    assert "原始第 1-40 页" in document["md_content"]
    assert "原始第 41-69 页" in document["md_content"]
    assert "Parsed large-report.pages-0001-0040.pdf" in document["md_content"]
    assert "Parsed large-report.pages-0041-0069.pdf" in document["md_content"]


def test_enterprise_mineru_client_uses_async_task_contract_without_trusting_urls():
    calls = []

    class _Response:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

        def json(self):
            return self._payload

    class _Session:
        def post(self, url, **kwargs):
            calls.append(("POST", url, kwargs))
            return _Response(
                202,
                {
                    "task_id": "task-2605-1",
                    "status": "pending",
                    "status_url": "http://untrusted.invalid/status",
                    "result_url": "http://untrusted.invalid/result",
                },
            )

        def get(self, url, **kwargs):
            calls.append(("GET", url, kwargs))
            if url.endswith("/result"):
                return _Response(
                    200,
                    {
                        "data": {
                            "results": {
                                "deck.pdf": {
                                    "md_content": "# Async parsed",
                                    "images": {},
                                }
                            }
                        }
                    },
                )
            statuses = [
                entry
                for method, entry, _kwargs in calls
                if method == "GET" and not entry.endswith("/result")
            ]
            status = "processing" if len(statuses) == 1 else "completed"
            return _Response(200, {"task_id": "task-2605-1", "status": status})

    client = app.EnterpriseMinerUClient(
        api_url="https://model.ai.cnooc/member1/mineru-2605/file_parse",
        api_token="secret-token",
        timeout_seconds=30,
        request_mode="async",
        poll_interval_seconds=0.001,
        session=_Session(),
    )

    result = client.parse_pdf(b"%PDF-test", "deck.pdf")

    assert result["results"]["deck.pdf"]["md_content"] == "# Async parsed"
    assert [url for _method, url, _kwargs in calls] == [
        "https://model.ai.cnooc/member1/mineru-2605/tasks",
        "https://model.ai.cnooc/member1/mineru-2605/tasks/task-2605-1",
        "https://model.ai.cnooc/member1/mineru-2605/tasks/task-2605-1",
        "https://model.ai.cnooc/member1/mineru-2605/tasks/task-2605-1/result",
    ]
    submission = calls[0][2]
    assert submission["headers"]["Authorization"] == "Bearer secret-token"
    assert submission["data"]["return_images"] == "true"
    assert submission["data"]["response_format_zip"] == "false"


def test_enterprise_mineru_async_retries_transient_20006_on_same_task():
    calls = []
    status_attempts = 0
    result_attempts = 0

    class _Response:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

        def json(self):
            return self._payload

    class _Session:
        def post(self, url, **kwargs):
            calls.append(("POST", url))
            return _Response(202, {"task_id": "task-transient-20006"})

        def get(self, url, **kwargs):
            nonlocal status_attempts, result_attempts
            calls.append(("GET", url))
            if url.endswith("/result"):
                result_attempts += 1
                if result_attempts == 1:
                    return _Response(
                        200,
                        {
                            "success": False,
                            "code": 20006,
                            "msg": "gateway and inference service IO exception",
                        },
                    )
                return _Response(
                    200,
                    {
                        "results": {
                            "document.pdf": {
                                "md_content": "# Recovered result",
                                "images": {},
                            }
                        }
                    },
                )

            status_attempts += 1
            if status_attempts == 1:
                return _Response(
                    200,
                    {
                        "success": False,
                        "code": 20006,
                        "msg": "gateway and inference service IO exception",
                    },
                )
            return _Response(200, {"status": "completed"})

    client = app.EnterpriseMinerUClient(
        api_url="https://model.ai.cnooc/member1/mineru-2605",
        api_token="secret-token",
        timeout_seconds=30,
        request_mode="async",
        poll_interval_seconds=0.001,
        transient_poll_retries=2,
        session=_Session(),
    )

    result = client.parse_pdf(b"%PDF-test", "document.pdf")

    assert result["results"]["document.pdf"]["md_content"] == "# Recovered result"
    assert [call for call in calls if call[0] == "POST"] == [
        ("POST", "https://model.ai.cnooc/member1/mineru-2605/tasks")
    ]
    assert calls[1:] == [
        (
            "GET",
            "https://model.ai.cnooc/member1/mineru-2605/tasks/task-transient-20006",
        ),
        (
            "GET",
            "https://model.ai.cnooc/member1/mineru-2605/tasks/task-transient-20006",
        ),
        (
            "GET",
            "https://model.ai.cnooc/member1/mineru-2605/tasks/task-transient-20006/result",
        ),
        (
            "GET",
            "https://model.ai.cnooc/member1/mineru-2605/tasks/task-transient-20006/result",
        ),
    ]


def test_enterprise_mineru_async_retries_transient_404_on_same_task():
    calls = []
    status_attempts = 0
    result_attempts = 0

    class _Response:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

        def json(self):
            return self._payload

    class _Session:
        def post(self, url, **kwargs):
            calls.append(("POST", url))
            return _Response(202, {"task_id": "task-transient-404"})

        def get(self, url, **kwargs):
            nonlocal status_attempts, result_attempts
            calls.append(("GET", url))
            if url.endswith("/result"):
                result_attempts += 1
                if result_attempts == 1:
                    return _Response(404, {"detail": "task result not visible"})
                return _Response(
                    200,
                    {
                        "results": {
                            "document.pdf": {
                                "md_content": "# Recovered after result 404",
                                "images": {},
                            }
                        }
                    },
                )

            status_attempts += 1
            if status_attempts == 1:
                return _Response(404, {"detail": "task not visible"})
            return _Response(200, {"status": "completed"})

    client = app.EnterpriseMinerUClient(
        api_url="https://model.ai.cnooc/member1/mineru-2605",
        api_token="secret-token",
        timeout_seconds=30,
        request_mode="async",
        poll_interval_seconds=0.001,
        transient_poll_retries=2,
        session=_Session(),
    )

    result = client.parse_pdf(b"%PDF-test", "document.pdf")

    assert result["results"]["document.pdf"]["md_content"] == (
        "# Recovered after result 404"
    )
    assert [call for call in calls if call[0] == "POST"] == [
        ("POST", "https://model.ai.cnooc/member1/mineru-2605/tasks")
    ]
    assert calls[1:] == [
        (
            "GET",
            "https://model.ai.cnooc/member1/mineru-2605/tasks/task-transient-404",
        ),
        (
            "GET",
            "https://model.ai.cnooc/member1/mineru-2605/tasks/task-transient-404",
        ),
        (
            "GET",
            "https://model.ai.cnooc/member1/mineru-2605/tasks/task-transient-404/result",
        ),
        (
            "GET",
            "https://model.ai.cnooc/member1/mineru-2605/tasks/task-transient-404/result",
        ),
    ]


def test_enterprise_mineru_async_retries_status_timeout_on_same_task():
    calls = []
    status_attempts = 0

    class _Response:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class _Session:
        def post(self, url, **kwargs):
            calls.append(("POST", url))
            return _Response(202, {"task_id": "task-status-timeout"})

        def get(self, url, **kwargs):
            nonlocal status_attempts
            calls.append(("GET", url))
            if url.endswith("/result"):
                return _Response(
                    200,
                    {
                        "results": {
                            "document.pdf": {
                                "md_content": "# Recovered after status timeout",
                                "images": {},
                            }
                        }
                    },
                )
            status_attempts += 1
            if status_attempts == 1:
                raise app.requests.ReadTimeout("status poll timed out")
            return _Response(200, {"status": "completed"})

    client = app.EnterpriseMinerUClient(
        api_url="https://model.ai.cnooc/member1/mineru-2605",
        api_token="secret-token",
        timeout_seconds=30,
        request_mode="async",
        poll_interval_seconds=0.001,
        transient_poll_retries=2,
        session=_Session(),
    )

    result = client.parse_pdf(b"%PDF-test", "document.pdf")

    assert result["results"]["document.pdf"]["md_content"] == (
        "# Recovered after status timeout"
    )
    assert len([call for call in calls if call[0] == "POST"]) == 1
    assert calls[1:] == [
        (
            "GET",
            "https://model.ai.cnooc/member1/mineru-2605/tasks/task-status-timeout",
        ),
        (
            "GET",
            "https://model.ai.cnooc/member1/mineru-2605/tasks/task-status-timeout",
        ),
        (
            "GET",
            "https://model.ai.cnooc/member1/mineru-2605/tasks/task-status-timeout/result",
        ),
    ]


def test_enterprise_mineru_async_retries_result_connection_error_on_same_task():
    calls = []
    result_attempts = 0

    class _Response:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class _Session:
        def post(self, url, **kwargs):
            calls.append(("POST", url))
            response = _Response({"task_id": "task-result-disconnect"})
            response.status_code = 202
            return response

        def get(self, url, **kwargs):
            nonlocal result_attempts
            calls.append(("GET", url))
            if not url.endswith("/result"):
                return _Response({"status": "completed"})
            result_attempts += 1
            if result_attempts == 1:
                raise app.requests.ConnectionError("result connection reset")
            return _Response(
                {
                    "results": {
                        "document.pdf": {
                            "md_content": "# Recovered after result disconnect",
                            "images": {},
                        }
                    }
                }
            )

    client = app.EnterpriseMinerUClient(
        api_url="https://model.ai.cnooc/member1/mineru-2605",
        api_token="secret-token",
        timeout_seconds=30,
        request_mode="async",
        poll_interval_seconds=0.001,
        transient_poll_retries=2,
        session=_Session(),
    )

    result = client.parse_pdf(b"%PDF-test", "document.pdf")

    assert result["results"]["document.pdf"]["md_content"] == (
        "# Recovered after result disconnect"
    )
    assert len([call for call in calls if call[0] == "POST"]) == 1
    assert result_attempts == 2


def test_enterprise_mineru_async_bounds_status_transport_retries():
    calls = []

    class _Response:
        status_code = 202

        def raise_for_status(self):
            return None

        def json(self):
            return {"task_id": "task-persistent-timeout"}

    class _Session:
        def post(self, url, **kwargs):
            calls.append(("POST", url))
            return _Response()

        def get(self, url, **kwargs):
            calls.append(("GET", url))
            raise app.requests.ReadTimeout("persistent status timeout")

    client = app.EnterpriseMinerUClient(
        api_url="https://model.ai.cnooc/member1/mineru-2605",
        api_token="secret-token",
        timeout_seconds=30,
        request_mode="async",
        poll_interval_seconds=0.001,
        transient_poll_retries=1,
        session=_Session(),
    )

    with pytest.raises(app.requests.ReadTimeout, match="persistent status timeout"):
        client.parse_pdf(b"%PDF-test", "document.pdf")

    assert len([call for call in calls if call[0] == "POST"]) == 1
    assert len([call for call in calls if call[0] == "GET"]) == 2


def test_enterprise_mineru_async_resets_result_transport_streak_after_not_ready():
    calls = []
    result_attempts = 0

    class _Response:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

        def json(self):
            return self._payload

    class _Session:
        def post(self, url, **kwargs):
            calls.append(("POST", url))
            return _Response(202, {"task_id": "task-reset-result-streak"})

        def get(self, url, **kwargs):
            nonlocal result_attempts
            calls.append(("GET", url))
            if not url.endswith("/result"):
                return _Response(200, {"status": "completed"})
            result_attempts += 1
            if result_attempts in {1, 3}:
                raise app.requests.ReadTimeout("separated result timeout")
            if result_attempts == 2:
                return _Response(202, {"status": "processing"})
            return _Response(
                200,
                {
                    "results": {
                        "document.pdf": {
                            "md_content": "# Recovered after separated timeouts",
                            "images": {},
                        }
                    }
                },
            )

    client = app.EnterpriseMinerUClient(
        api_url="https://model.ai.cnooc/member1/mineru-2605",
        api_token="secret-token",
        timeout_seconds=30,
        request_mode="async",
        poll_interval_seconds=0.001,
        transient_poll_retries=1,
        session=_Session(),
    )

    result = client.parse_pdf(b"%PDF-test", "document.pdf")

    assert result["results"]["document.pdf"]["md_content"] == (
        "# Recovered after separated timeouts"
    )
    assert len([call for call in calls if call[0] == "POST"]) == 1
    assert result_attempts == 4


def test_enterprise_mineru_async_bounds_transient_404_retries():
    calls = []

    class _Response:
        status_code = 404

        def raise_for_status(self):
            raise RuntimeError("HTTP 404")

        def json(self):
            return {"detail": "task not visible"}

    class _Session:
        def post(self, url, **kwargs):
            calls.append(("POST", url))

            class _Submission(_Response):
                status_code = 202

                def raise_for_status(self):
                    return None

                def json(self):
                    return {"task_id": "task-persistent-404"}

            return _Submission()

        def get(self, url, **kwargs):
            calls.append(("GET", url))
            return _Response()

    client = app.EnterpriseMinerUClient(
        api_url="https://model.ai.cnooc/member1/mineru-2605",
        api_token="secret-token",
        timeout_seconds=30,
        request_mode="async",
        poll_interval_seconds=0.001,
        transient_poll_retries=1,
        session=_Session(),
    )

    with pytest.raises(RuntimeError, match="HTTP 404"):
        client.parse_pdf(b"%PDF-test", "document.pdf")

    assert len([call for call in calls if call[0] == "POST"]) == 1
    assert len([call for call in calls if call[0] == "GET"]) == 2


def test_enterprise_mineru_async_bounds_transient_20006_retries():
    calls = []

    class _Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "success": False,
                "code": 20006,
                "msg": "gateway and inference service IO exception",
            }

    class _Session:
        def post(self, url, **kwargs):
            calls.append(("POST", url))

            class _Submission(_Response):
                def json(self):
                    return {"task_id": "task-persistent-20006"}

            return _Submission()

        def get(self, url, **kwargs):
            calls.append(("GET", url))
            return _Response()

    client = app.EnterpriseMinerUClient(
        api_url="https://model.ai.cnooc/member1/mineru-2605",
        api_token="secret-token",
        timeout_seconds=30,
        request_mode="async",
        poll_interval_seconds=0.001,
        transient_poll_retries=1,
        session=_Session(),
    )

    with pytest.raises(app.MinerUBusinessError, match="code=20006"):
        client.parse_pdf(b"%PDF-test", "document.pdf")

    assert len([call for call in calls if call[0] == "POST"]) == 1
    assert len([call for call in calls if call[0] == "GET"]) == 2


def test_enterprise_mineru_async_business_error_does_not_fallback_to_sync():
    calls = []

    class _Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "success": False,
                "code": 10006,
                "msg": "unmatched request path",
            }

    class _Session:
        def post(self, url, **kwargs):
            calls.append(url)
            return _Response()

    client = app.EnterpriseMinerUClient(
        api_url="https://model.ai.cnooc/member1/mineru-2605",
        api_token="secret-token",
        request_mode="async",
        session=_Session(),
    )

    with pytest.raises(RuntimeError, match="code=10006"):
        client.parse_pdf(b"%PDF-test", "document.pdf")

    assert calls == ["https://model.ai.cnooc/member1/mineru-2605/tasks"]


def test_enterprise_mineru_async_rejects_untrusted_task_identifier():
    class _Response:
        status_code = 202

        def raise_for_status(self):
            return None

        def json(self):
            return {"task_id": "../../internal/secret"}

    class _Session:
        def post(self, url, **kwargs):
            return _Response()

    client = app.EnterpriseMinerUClient(
        api_url="https://model.ai.cnooc/member1/mineru-2605",
        api_token="secret-token",
        request_mode="async",
        session=_Session(),
    )

    with pytest.raises(RuntimeError, match="invalid task identifier"):
        client.parse_pdf(b"%PDF-test", "document.pdf")


def test_enterprise_mineru_client_explicitly_uses_ca_when_env_is_not_trusted(
    monkeypatch, tmp_path
):
    ca_bundle = tmp_path / "enterprise-ca.pem"
    ca_bundle.write_text("test enterprise ca", encoding="utf-8")
    created = []

    class _Session:
        def __init__(self):
            self.trust_env = True
            self.verify = True

    def create_session():
        session = _Session()
        created.append(session)
        return session

    monkeypatch.setenv("MINERU_TRUST_ENV", "false")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(ca_bundle))
    monkeypatch.delenv("MINERU_CA_BUNDLE", raising=False)
    monkeypatch.setattr(app.requests, "Session", create_session)

    client = app.EnterpriseMinerUClient(
        api_url="https://model.ai.cnooc/member1/mineru-2605",
        api_token="secret-token",
    )

    assert client.session is created[0]
    assert client.session.trust_env is False
    assert client.session.verify == str(ca_bundle)


def test_enterprise_mineru_client_accepts_full_file_parse_url():
    captured = {}

    class _Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"results": {"document.pdf": {"md_content": "ok", "images": {}}}}

    class _Session:
        def post(self, url, **kwargs):
            captured["url"] = url
            return _Response()

    client = app.EnterpriseMinerUClient(
        api_url="http://model.ai.cnooc/member1/mineru-2605/file_parse/",
        api_token="secret-token",
        session=_Session(),
    )

    client.parse_pdf(b"%PDF-test", "document.pdf")

    assert captured["url"] == "http://model.ai.cnooc/member1/mineru-2605/file_parse"


def test_enterprise_mineru_client_rejects_http_200_business_error():
    class _Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "success": False,
                "code": 10013,
                "msg": "认证错误，未检测到TOKEN",
            }

    class _Session:
        def post(self, url, **kwargs):
            return _Response()

    client = app.EnterpriseMinerUClient(
        api_url="http://model.ai.cnooc/member1/mineru-2605",
        api_token="secret-token",
        session=_Session(),
    )

    with pytest.raises(RuntimeError, match="code=10013"):
        client.parse_pdf(b"%PDF-test", "document.pdf")


def test_native_mineru_response_preserves_markdown_and_images():
    png = b"\x89PNG\r\n\x1a\nnative-mineru"
    result = {
        "results": {
            "document.pdf": {
                "md_content": "# 原生结果\n\n![图 1](images/chart.png)",
                "images": {
                    "images/chart.png": "data:image/png;base64,"
                    + base64.b64encode(png).decode(),
                },
            }
        }
    }

    markdown, images = app.mineru_pages_to_markdown(result, filename="document.pdf")

    assert "# 原生结果" in markdown
    assert "images/chart.png" not in markdown
    assert len(images) == 1
    persistent_path = next(iter(images))
    assert persistent_path.startswith("images/mineru-")
    assert persistent_path in markdown
    assert base64.b64decode(images[persistent_path]) == png


def test_native_mineru_response_rejects_ambiguous_results():
    result = {
        "results": {
            "first.pdf": {"md_content": "first", "images": {}},
            "second.pdf": {"md_content": "second", "images": {}},
        }
    }

    with pytest.raises(RuntimeError, match="do not contain the requested document"):
        app.mineru_pages_to_markdown(result, filename="missing.pdf")


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


def test_weknora_response_demotes_only_implausibly_large_headings():
    giant_heading = "深层勘探视觉证据" * 600
    markdown = (
        "# 正常标题\n\n"
        f"## {giant_heading}\n\n"
        "正文保持不变\n\n"
        "```markdown\n"
        f"### {giant_heading}\n"
        "```\n"
    )

    response = app.weknora_response(markdown)
    normalized = response["results"]["document"]["md_content"]
    normalized_lines = normalized.splitlines()

    assert normalized.startswith("# 正常标题\n\n")
    assert f"## {giant_heading}" not in normalized_lines
    assert f"{giant_heading}\n\n正文保持不变" in normalized
    assert f"```markdown\n### {giant_heading}\n```" in normalized


def test_weknora_response_preserves_short_multibyte_heading():
    markdown = "## 南海深层勘探评价\n\n正文"

    response = app.weknora_response(markdown)

    assert response["results"]["document"]["md_content"] == markdown


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
