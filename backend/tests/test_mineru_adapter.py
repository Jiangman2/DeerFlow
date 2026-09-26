"""Regression tests for the enterprise MinerU compatibility adapter."""

import base64
import io
import json
import sys
import threading
import time
import urllib.error
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


def _fake_docx_with_text(text: str = "大型文档恢复正文") -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>',
        )
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


@pytest.mark.parametrize(
    ("content", "expected"),
    (
        (b"\x89PNG\r\n\x1a\nimage", ".png"),
        (b"\xff\xd8\xff\xe0image", ".jpg"),
        (b"GIF89aimage", ".gif"),
        (b"BMimage", ".bmp"),
        (b"II*\x00image", ".tiff"),
        (b"MM\x00*image", ".tiff"),
        (b"RIFF\x04\x00\x00\x00WEBPimage", ".webp"),
        (b"\x00\x00\x00\x0cjP  \r\n\x87\nimage", ".jp2"),
    ),
)
def test_detect_input_suffix_recognises_raster_images_from_magic_bytes(
    content,
    expected,
):
    assert app.detect_input_suffix(content, "misleading.pdf") == expected


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
        lambda content: (app.VisualPageEvidence(page_number=10, context="图 2 完井结构"),),
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


def test_weknora_response_rejects_whitespace_only_markdown():
    with pytest.raises(ValueError, match="no readable text"):
        app.weknora_response(" \n\t ")


def test_searchable_word_returns_native_parse_without_visual_conversion(monkeypatch):
    source = _fake_docx()
    monkeypatch.setattr(
        app,
        "parse_word_with_builtin",
        lambda content, filename, suffix: (
            "# Word 原生解析\n\n正文\n\n![原始位图](images/word-native.png)",
            {"images/word-native.png": "word-native-image"},
        ),
    )
    monkeypatch.setattr(
        app,
        "convert_to_pdf",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("successful native Word parsing must not call LibreOffice")),
    )

    response = app.parse_upload(source, "数据资产管理实践白皮书.docx")

    assert response["results"]["document"]["images"] == {"images/word-native.png": "word-native-image"}
    assert "Word 原生解析" in response["results"]["document"]["md_content"]


def test_docx_uses_anydoc_before_builtin_parser(monkeypatch):
    source = _fake_docx()
    monkeypatch.setenv("DEERFLOW_ANYDOC_WORD_ENABLED", "true")
    monkeypatch.setattr(
        app,
        "parse_word_with_anydoc",
        lambda content, filename, suffix: ("# AnyDoc\n\n高速解析正文", {}),
    )
    monkeypatch.setattr(
        app,
        "parse_word_with_builtin",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("AnyDoc success must not call DocReader")),
    )

    response = app.parse_upload(
        source,
        "large-report.docx",
        parse_profile="conversation",
    )

    assert response["results"]["document"] == {
        "md_content": "# AnyDoc\n\n高速解析正文",
        "images": {},
    }


def test_anydoc_failure_falls_back_to_existing_word_parser_chain(monkeypatch):
    source = _fake_docx()
    monkeypatch.setenv("DEERFLOW_ANYDOC_WORD_ENABLED", "true")

    def failed_anydoc(content, filename, suffix):
        raise RuntimeError("AnyDoc subprocess timed out")

    monkeypatch.setattr(app, "parse_word_with_anydoc", failed_anydoc)
    monkeypatch.setattr(
        app,
        "parse_word_with_builtin",
        lambda content, filename, suffix: ("# DocReader fallback\n\n正文", {"images/fallback.png": "image"}),
    )

    response = app.parse_upload(
        source,
        "large-report.docx",
        parse_profile="conversation",
    )

    assert response["results"]["document"] == {
        "md_content": "# DocReader fallback\n\n正文",
        "images": {"images/fallback.png": "image"},
    }


def test_anydoc_is_disabled_by_default_and_does_not_change_legacy_doc(monkeypatch):
    source = _fake_legacy_office("WordDocument")
    monkeypatch.delenv("DEERFLOW_ANYDOC_WORD_ENABLED", raising=False)
    monkeypatch.setattr(
        app,
        "parse_word_with_anydoc",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("AnyDoc must remain opt-in")),
    )
    monkeypatch.setattr(
        app,
        "parse_word_with_builtin",
        lambda content, filename, suffix: ("# Legacy DOC\n\n正文", {}),
    )

    response = app.parse_upload(source, "legacy.doc")

    assert response["results"]["document"]["md_content"] == "# Legacy DOC\n\n正文"


def test_anydoc_does_not_change_weknora_knowledge_base_routes(monkeypatch):
    source = _fake_docx()
    monkeypatch.setenv("DEERFLOW_ANYDOC_WORD_ENABLED", "true")
    monkeypatch.setattr(
        app,
        "parse_word_with_anydoc",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("knowledge-base parsing must retain its configured route")),
    )
    monkeypatch.setattr(
        app,
        "parse_word_with_builtin",
        lambda content, filename, suffix: ("# Knowledge base route\n\n正文", {}),
    )

    response = app.parse_upload(source, "knowledge-base.docx")

    assert response["results"]["document"]["md_content"] == "# Knowledge base route\n\n正文"


def test_structurally_large_knowledge_docx_uses_anydoc_when_enabled(monkeypatch):
    source = _fake_docx_with_text()
    monkeypatch.setenv("DEERFLOW_ANYDOC_WORD_ENABLED", "true")
    monkeypatch.setenv("DEERFLOW_ANYDOC_KNOWLEDGE_WORD_ENABLED", "true")
    monkeypatch.setenv("DEERFLOW_DOCX_MARKITDOWN_MAX_DOCUMENT_XML_BYTES", "1")
    monkeypatch.setattr(
        app,
        "parse_word_with_anydoc",
        lambda content, filename, suffix: ("# AnyDoc knowledge\n\n大型正文", {}),
    )
    monkeypatch.setattr(
        app,
        "parse_word_with_builtin",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("AnyDoc success must return immediately")),
    )

    response = app.parse_upload(source, "large-knowledge.docx")

    assert response["results"]["document"]["md_content"] == "# AnyDoc knowledge\n\n大型正文"


def test_large_knowledge_docx_bypasses_markitdown_after_anydoc_limit(monkeypatch):
    source = _fake_docx_with_text()
    calls: list[bool] = []
    monkeypatch.setenv("DEERFLOW_ANYDOC_WORD_ENABLED", "true")
    monkeypatch.setenv("DEERFLOW_ANYDOC_KNOWLEDGE_WORD_ENABLED", "true")
    monkeypatch.setenv("DEERFLOW_DOCX_MARKITDOWN_MAX_DOCUMENT_XML_BYTES", "1")

    def resource_limited_anydoc(content, filename, suffix):
        raise RuntimeError("resource limit exceeded (max_xml_nodes)")

    def native_docreader(content, filename, suffix, *, bypass_markitdown=False):
        calls.append(bypass_markitdown)
        return "# DocReader 0.8 native\n\n恢复正文", {}

    monkeypatch.setattr(app, "parse_word_with_anydoc", resource_limited_anydoc)
    monkeypatch.setattr(app, "parse_word_with_builtin", native_docreader)

    response = app.parse_upload(source, "large-knowledge.docx")

    assert calls == [True]
    assert "DocReader 0.8 native" in response["results"]["document"]["md_content"]


def test_large_docx_streaming_recovery_is_last_native_fallback(monkeypatch):
    source = _fake_docx_with_text("超过 AnyDoc 节点上限后的恢复正文")
    monkeypatch.setenv("DEERFLOW_DOCX_MARKITDOWN_MAX_DOCUMENT_XML_BYTES", "1")
    monkeypatch.setattr(
        app,
        "parse_word_with_builtin",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("native parser timed out")),
    )
    monkeypatch.setattr(
        app,
        "parse_docx_with_streaming_recovery",
        lambda content, filename: app._parse_docx_streaming_in_process(content),
    )
    monkeypatch.setattr(
        app,
        "convert_to_pdf",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("streaming recovery must return immediately")),
    )

    response = app.parse_upload(source, "large-knowledge.docx")

    assert "超过 AnyDoc 节点上限后的恢复正文" in response["results"]["document"]["md_content"]


def test_docx_workload_routes_on_expanded_document_xml(monkeypatch):
    source = _fake_docx_with_text("正文" * 100)
    workload = app.inspect_docx_workload(source)
    monkeypatch.setenv("DEERFLOW_DOCX_MARKITDOWN_MAX_INPUT_BYTES", str(len(source) * 10))
    monkeypatch.setenv("DEERFLOW_DOCX_MARKITDOWN_MAX_UNCOMPRESSED_BYTES", str(workload.uncompressed_bytes * 10))
    monkeypatch.setenv("DEERFLOW_DOCX_MARKITDOWN_MAX_DOCUMENT_XML_BYTES", "1")

    assert workload.document_xml_bytes > len(source)
    assert app.docx_requires_large_document_route(workload) is True


def test_docx_streaming_recovery_extracts_text_without_images():
    markdown, images = app._parse_docx_streaming_in_process(_fake_docx_with_text("低内存流式正文"))

    assert markdown == "低内存流式正文"
    assert images == {}


def test_parse_word_with_anydoc_uses_bounded_isolated_subprocess(monkeypatch, tmp_path):
    captured = {}
    worker_path = tmp_path / "worker.py"
    worker_path.write_text("# test worker\n", encoding="utf-8")

    def fake_runner(command, *, timeout, termination_grace_seconds=5.0, env=None):
        captured["command"] = command
        captured["timeout"] = timeout
        captured["env"] = env
        Path(command[-1]).write_text("# AnyDoc\n\n足够长的有效正文内容，用于通过最小质量门槛。", encoding="utf-8")
        return app.subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("MINERU_API_TOKEN", "must-not-leak")
    monkeypatch.setenv("HTTPS_PROXY", "http://user:password@proxy.example")
    monkeypatch.setenv("DEERFLOW_ANYDOC_WORD_TIMEOUT_SECONDS", "37")
    monkeypatch.setenv("DEERFLOW_ANYDOC_WORD_MIN_TEXT_CHARACTERS", "8")
    monkeypatch.setenv("DEERFLOW_ANYDOC_WORKER_PATH", str(worker_path))
    monkeypatch.setattr(app, "_run_converter_command", fake_runner)

    markdown, images = app.parse_word_with_anydoc(_fake_docx(), "large-report.docx", ".docx")

    assert "有效正文" in markdown
    assert images == {}
    assert captured["timeout"] == 37
    assert captured["command"][1] == str(worker_path)
    assert captured["env"]["PYTHONNOUSERSITE"] == "1"
    assert "MINERU_API_TOKEN" not in captured["env"]
    assert "HTTPS_PROXY" not in captured["env"]


def test_parse_word_with_anydoc_rejects_thin_output(monkeypatch):
    def fake_runner(command, *, timeout, termination_grace_seconds=5.0, env=None):
        Path(command[-1]).write_text("![image](embedded)", encoding="utf-8")
        return app.subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("DEERFLOW_ANYDOC_WORD_MIN_TEXT_CHARACTERS", "8")
    monkeypatch.setattr(app, "_run_converter_command", fake_runner)

    with pytest.raises(ValueError, match="readable text threshold"):
        app.parse_word_with_anydoc(_fake_docx(), "image-only.docx", ".docx")


def test_parse_word_with_builtin_uses_bounded_isolated_subprocess(monkeypatch, tmp_path):
    captured = {}
    worker_path = tmp_path / "docreader-worker.py"
    worker_path.write_text("# test worker\n", encoding="utf-8")

    def fake_runner(command, *, timeout, termination_grace_seconds=5.0, env=None):
        captured["command"] = command
        captured["timeout"] = timeout
        captured["env"] = env
        Path(command[-1]).write_text(
            json.dumps(
                {
                    "markdown": "# DocReader\n\n有界子进程回退正文",
                    "images": {"images/fallback.png": "encoded"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return app.subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("MINERU_API_TOKEN", "must-not-leak")
    monkeypatch.setenv("HTTPS_PROXY", "http://user:password@proxy.example")
    monkeypatch.setenv("DEERFLOW_DOCREADER_WORD_TIMEOUT_SECONDS", "41")
    monkeypatch.setenv("DEERFLOW_DOCREADER_WORKER_PATH", str(worker_path))
    monkeypatch.setattr(app, "_run_converter_command", fake_runner)

    markdown, images = app.parse_word_with_builtin(
        _fake_docx(),
        "large-report.docx",
        ".docx",
    )

    assert "有界子进程" in markdown
    assert images == {"images/fallback.png": "encoded"}
    assert captured["timeout"] == 41
    assert captured["command"][1] == str(worker_path)
    assert captured["command"][2] == "first-success"
    assert captured["env"]["PYTHONNOUSERSITE"] == "1"
    assert "MINERU_API_TOKEN" not in captured["env"]
    assert "HTTPS_PROXY" not in captured["env"]


def test_large_docx_builtin_worker_explicitly_selects_native_parser(monkeypatch, tmp_path):
    captured = {}
    worker_path = tmp_path / "docreader-worker.py"
    worker_path.write_text("# test worker\n", encoding="utf-8")

    def fake_runner(command, *, timeout, termination_grace_seconds=5.0, env=None):
        captured["command"] = command
        Path(command[-1]).write_text(
            json.dumps({"markdown": "# Native DocxParser\n\n正文", "images": {}}),
            encoding="utf-8",
        )
        return app.subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("DEERFLOW_DOCREADER_WORKER_PATH", str(worker_path))
    monkeypatch.setattr(app, "_run_converter_command", fake_runner)

    markdown, images = app.parse_word_with_builtin(
        _fake_docx(),
        "large-report.docx",
        ".docx",
        bypass_markitdown=True,
    )

    assert "Native DocxParser" in markdown
    assert images == {}
    assert captured["command"][2] == "native-docx"


def test_optional_word_visual_conversion_failure_preserves_native_parse(monkeypatch):
    source = _fake_docx()
    native_markdown = "# Native Word\n\nRecovered body and tables"
    native_images = {"images/word-native.png": "word-native-image"}
    monkeypatch.setenv("MINERU_WORD_PDF_VISUAL_SUPPLEMENT_ENABLED", "true")
    monkeypatch.setattr(
        app,
        "parse_word_with_builtin",
        lambda content, filename, suffix: (native_markdown, native_images),
    )

    def failed_conversion(content, filename):
        raise RuntimeError("LibreOffice produced no PDF")

    monkeypatch.setattr(app, "convert_to_pdf", failed_conversion)

    response = app.parse_upload(source, "large-report.docx")

    assert response["results"]["document"] == {
        "md_content": native_markdown,
        "images": native_images,
    }


def test_optional_word_mineru_visual_failure_preserves_native_parse(monkeypatch):
    source = _fake_docx()
    native_markdown = "# Native Word\n\nRecovered body and tables"
    native_images = {"images/word-native.png": "word-native-image"}
    converted_pdf = b"%PDF-word-visual"
    monkeypatch.setenv("MINERU_WORD_PDF_VISUAL_SUPPLEMENT_ENABLED", "true")
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

    response = app.parse_upload(source, "large-report.docx")

    assert response["results"]["document"] == {
        "md_content": native_markdown,
        "images": native_images,
    }


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
        app,
        "_run_converter_command",
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


def test_converter_timeout_terminates_and_reaps_the_process_group(monkeypatch):
    signals: list[int] = []
    popen_kwargs: dict[str, object] = {}

    class Process:
        pid = 4321
        returncode = None

        def __init__(self):
            self.communicate_calls = 0

        def communicate(self, timeout=None):
            self.communicate_calls += 1
            if self.communicate_calls < 3:
                raise app.subprocess.TimeoutExpired(["libreoffice"], timeout)
            self.returncode = -9
            return "partial stdout", "partial stderr"

    process = Process()

    def fake_popen(command, **kwargs):
        assert command == ["libreoffice", "--headless"]
        popen_kwargs.update(kwargs)
        return process

    monkeypatch.setattr(app, "_POSIX_PROCESS_GROUPS", True)
    monkeypatch.setattr(app, "_SIGKILL", 9)
    monkeypatch.setattr(app.os, "getpgid", lambda pid: pid, raising=False)
    monkeypatch.setattr(
        app.os,
        "killpg",
        lambda _pgid, sig: signals.append(sig),
        raising=False,
    )
    monkeypatch.setattr(app.subprocess, "Popen", fake_popen)

    with pytest.raises(app.subprocess.TimeoutExpired) as exc_info:
        app._run_converter_command(
            ["libreoffice", "--headless"],
            timeout=1,
            termination_grace_seconds=0.1,
        )

    assert popen_kwargs["start_new_session"] is True
    assert signals == [app.signal.SIGTERM, 9]
    assert process.communicate_calls == 3
    assert exc_info.value.output == "partial stdout"
    assert exc_info.value.stderr == "partial stderr"


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


def test_raster_image_uses_mineru_without_pdf_conversion(monkeypatch):
    image = b"\x89PNG\r\n\x1a\nimage-payload"
    monkeypatch.setenv("MINERU_API_URL", "http://mineru.internal/parse/v1")
    monkeypatch.setenv("MINERU_API_TOKEN", "secret")

    class _MinerU:
        def __init__(self, **kwargs):
            assert kwargs["api_url"] == "http://mineru.internal/parse/v1"

        def parse_image(self, content, filename, suffix):
            assert content == image
            assert filename == "现场照片.png"
            assert suffix == ".png"
            return {
                "results": {
                    filename: {
                        "md_content": "# OCR\n\n设备编号 A-1024",
                        "images": {},
                    }
                }
            }

    monkeypatch.setattr(app, "EnterpriseMinerUClient", _MinerU)

    response = app.parse_upload(image, "现场照片.jpeg", parse_profile="conversation")

    assert response["results"]["document"]["md_content"] == "# OCR\n\n设备编号 A-1024"


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
                            "chart.png": "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nchart").decode(),
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
    assert captured["files"] == {"files": ("deck.pdf", b"%PDF-test", "application/pdf")}
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


def test_enterprise_mineru_client_submits_image_with_detected_media_type():
    captured = {}

    class _Response:
        status_code = 200

        def json(self):
            return {
                "results": {
                    "photo.png": {
                        "md_content": "OCR text",
                        "images": {},
                    }
                }
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

    result = client.parse_image(b"\x89PNG\r\n\x1a\nimage", "photo.png", ".png")

    assert result["results"]["photo.png"]["md_content"] == "OCR text"
    assert captured["files"] == {"files": ("photo.png", b"\x89PNG\r\n\x1a\nimage", "image/png")}


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

    assert result["results"]["deck.pdf"]["md_content"] == ("# Recovered bounded batch")
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
            statuses = [entry for method, entry, _kwargs in calls if method == "GET" and not entry.endswith("/result")]
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
    assert [call for call in calls if call[0] == "POST"] == [("POST", "https://model.ai.cnooc/member1/mineru-2605/tasks")]
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

    assert result["results"]["document.pdf"]["md_content"] == ("# Recovered after result 404")
    assert [call for call in calls if call[0] == "POST"] == [("POST", "https://model.ai.cnooc/member1/mineru-2605/tasks")]
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

    assert result["results"]["document.pdf"]["md_content"] == ("# Recovered after status timeout")
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

    assert result["results"]["document.pdf"]["md_content"] == ("# Recovered after result disconnect")
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

    assert result["results"]["document.pdf"]["md_content"] == ("# Recovered after separated timeouts")
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


def test_enterprise_mineru_client_explicitly_uses_ca_when_env_is_not_trusted(monkeypatch, tmp_path):
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
                    "images/chart.png": "data:image/png;base64," + base64.b64encode(png).decode(),
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
    markdown = f"# 正常标题\n\n## {giant_heading}\n\n正文保持不变\n\n```markdown\n### {giant_heading}\n```\n"

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


def test_parse_coordinator_rejects_a_second_in_process_parse():
    coordinator = app.ParseCoordinator()
    first_entered = threading.Event()
    release_first = threading.Event()
    results: list[dict[str, str]] = []

    def first_operation():
        first_entered.set()
        assert release_first.wait(timeout=2)
        return {"request": "first"}

    first = threading.Thread(
        target=lambda: results.append(
            coordinator.run(
                first_operation,
                filename="first.pdf",
                content_bytes=100,
            )
        )
    )
    first.start()
    assert first_entered.wait(timeout=2)

    with pytest.raises(app.ParseWorkerBusy, match="already processing"):
        coordinator.run(
            lambda: {"request": "second"},
            filename="second.pdf",
            content_bytes=200,
        )

    assert coordinator.snapshot() == {
        "active": 1,
        "waiting": 0,
        "rejected": 1,
    }

    release_first.set()
    first.join(timeout=2)

    assert not first.is_alive()
    assert results == [{"request": "first"}]
    assert coordinator.snapshot() == {
        "active": 0,
        "waiting": 0,
        "rejected": 1,
    }


def test_health_probe_remains_responsive_while_parse_is_running(monkeypatch):
    coordinator = app.ParseCoordinator()
    parse_entered = threading.Event()
    release_parse = threading.Event()
    monkeypatch.setattr(app, "_PARSE_COORDINATOR", coordinator)

    def blocking_parse(content, filename, *, parse_profile):
        assert content == b"%PDF-test"
        assert filename == "queued.pdf"
        assert parse_profile == "conversation"
        parse_entered.set()
        assert release_parse.wait(timeout=2)
        return app.weknora_response("# Parsed")

    monkeypatch.setattr(app, "parse_upload", blocking_parse)
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.MinerUAdapterHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    boundary = "deerflow-serialized-parse"
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="queued.pdf"\r\nContent-Type: application/pdf\r\n\r\n').encode() + b"%PDF-test" + f"\r\n--{boundary}--\r\n".encode()

    def parse_request():
        return urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/file_parse",
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "X-DeerFlow-Parse-Profile": "conversation",
            },
            method="POST",
        )

    post_result: list[int] = []

    def post_document():
        with urllib.request.urlopen(parse_request(), timeout=3) as response:
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
            "role": "worker",
            "parser": {"active": 1, "waiting": 0, "rejected": 0},
        }

        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/live",
            timeout=1,
        ) as live_response:
            assert live_response.status == 200

        with pytest.raises(urllib.error.HTTPError) as ready_error:
            urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/ready",
                timeout=1,
            )
        assert ready_error.value.code == 503
        ready = json.loads(ready_error.value.read())
        assert ready["status"] == "busy"
        assert ready["parser"]["active"] == 1

        with pytest.raises(urllib.error.HTTPError) as busy_error:
            urllib.request.urlopen(parse_request(), timeout=1)
        assert busy_error.value.code == 429
        busy = json.loads(busy_error.value.read())
        assert busy["retryable"] is True
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


def test_parse_dispatcher_round_robins_serialized_workers():
    dispatcher = app.ParseDispatcher(
        backend_host="workers.test",
        backend_port=50052,
        maximum_waiting=4,
        minimum_backends=1,
        wait_timeout_seconds=2,
        request_timeout_seconds=2,
        resolver=lambda _host: ["10.0.0.1", "10.0.0.2"],
        prober=lambda _backend, _path: True,
    )

    first, _, _ = dispatcher._acquire_backend("bulk")
    dispatcher._release_backend(first)
    second, _, _ = dispatcher._acquire_backend("bulk")
    dispatcher._release_backend(second)

    assert (first, second) == ("10.0.0.1", "10.0.0.2")
    assert dispatcher.snapshot() == {
        "discovered": 2,
        "minimum": 1,
        "busy": 0,
        "waiting": 0,
        "live": 2,
        "quarantined": 0,
        "ready": True,
        "capacity_target_met": True,
    }


def test_parse_dispatcher_prioritizes_interactive_uploads():
    dispatcher = app.ParseDispatcher(
        backend_host="workers.test",
        backend_port=50052,
        maximum_waiting=4,
        minimum_backends=1,
        wait_timeout_seconds=2,
        request_timeout_seconds=2,
        resolver=lambda _host: ["10.0.0.1"],
        prober=lambda _backend, _path: True,
    )
    active, _, _ = dispatcher._acquire_backend("bulk")
    order: list[str] = []

    def acquire(label: str, priority: str) -> None:
        backend, _, _ = dispatcher._acquire_backend(priority)
        order.append(label)
        dispatcher._release_backend(backend)

    queued_bulk = threading.Thread(target=acquire, args=("bulk", "bulk"))
    queued_bulk.start()
    deadline = time.monotonic() + 1
    while dispatcher.snapshot()["waiting"] != 1 and time.monotonic() < deadline:
        time.sleep(0.01)

    interactive = threading.Thread(
        target=acquire,
        args=("interactive", "interactive"),
    )
    interactive.start()
    deadline = time.monotonic() + 1
    while dispatcher.snapshot()["waiting"] != 2 and time.monotonic() < deadline:
        time.sleep(0.01)

    dispatcher._release_backend(active)
    interactive.join(timeout=2)
    queued_bulk.join(timeout=2)

    assert not interactive.is_alive()
    assert not queued_bulk.is_alive()
    assert order == ["interactive", "bulk"]


def test_dispatcher_forwards_request_without_automatic_resubmission():
    calls: list[dict[str, object]] = []

    class Response:
        status_code = 200
        content = b'{"status":"ok"}'
        headers = {"Content-Type": "application/json"}

    class Session:
        def post(self, url, *, headers, data, timeout):
            calls.append(
                {
                    "url": url,
                    "headers": headers,
                    "data": data,
                    "timeout": timeout,
                }
            )
            return Response()

    dispatcher = app.ParseDispatcher(
        backend_host="workers.test",
        backend_port=50052,
        maximum_waiting=4,
        minimum_backends=1,
        wait_timeout_seconds=2,
        request_timeout_seconds=30,
        interactive_request_timeout_seconds=17,
        resolver=lambda _host: ["10.0.0.7"],
        session=Session(),
        prober=lambda _backend, _path: True,
    )

    result = dispatcher.forward(
        body=b"multipart-body",
        content_type="multipart/form-data; boundary=test",
        priority="interactive",
    )

    assert result == (
        200,
        "application/json",
        b'{"status":"ok"}',
        "10.0.0.7",
    )
    assert calls == [
        {
            "url": "http://10.0.0.7:50052/file_parse",
            "headers": {
                "Content-Type": "multipart/form-data; boundary=test",
                "X-DeerFlow-Parse-Profile": "conversation",
            },
            "data": b"multipart-body",
            "timeout": (5.0, 17),
        }
    ]


def test_dispatcher_preserves_knowledge_profile_and_long_timeout_for_bulk_work():
    calls: list[dict[str, object]] = []

    class Response:
        status_code = 200
        content = b'{"status":"ok"}'
        headers = {"Content-Type": "application/json"}

    class Session:
        def post(self, url, *, headers, data, timeout):
            calls.append(
                {
                    "url": url,
                    "headers": headers,
                    "data": data,
                    "timeout": timeout,
                }
            )
            return Response()

    dispatcher = app.ParseDispatcher(
        backend_host="workers.test",
        backend_port=50052,
        maximum_waiting=4,
        minimum_backends=1,
        wait_timeout_seconds=2,
        request_timeout_seconds=1800,
        interactive_request_timeout_seconds=550,
        resolver=lambda _host: ["10.0.0.8"],
        session=Session(),
        prober=lambda _backend, _path: True,
    )

    result = dispatcher.forward(
        body=b"knowledge-document",
        content_type="multipart/form-data; boundary=test",
        priority="bulk",
    )

    assert result[0] == 200
    assert calls == [
        {
            "url": "http://10.0.0.8:50052/file_parse",
            "headers": {
                "Content-Type": "multipart/form-data; boundary=test",
                "X-DeerFlow-Parse-Profile": "knowledge",
            },
            "data": b"knowledge-document",
            "timeout": (5.0, 1800),
        }
    ]


def test_dispatcher_retries_only_worker_busy_rejection_on_another_backend():
    calls: list[str] = []

    class Response:
        headers = {"Content-Type": "application/json"}

        def __init__(self, status_code: int):
            self.status_code = status_code
            self.content = b'{"error":"busy","retryable":true}' if status_code == 429 else b'{"status":"ok"}'

    class Session:
        def post(self, url, **_kwargs):
            calls.append(url)
            return Response(429 if "10.0.0.1" in url else 200)

    dispatcher = app.ParseDispatcher(
        backend_host="workers.test",
        backend_port=50052,
        maximum_waiting=4,
        minimum_backends=1,
        wait_timeout_seconds=2,
        request_timeout_seconds=30,
        busy_retry_limit=2,
        resolver=lambda _host: ["10.0.0.1", "10.0.0.2"],
        session=Session(),
        prober=lambda _backend, _path: True,
    )

    result = dispatcher.forward(
        body=b"document",
        content_type="multipart/form-data; boundary=test",
        priority="interactive",
    )

    assert result[0] == 200
    assert result[3] == "10.0.0.2"
    assert calls == [
        "http://10.0.0.1:50052/file_parse",
        "http://10.0.0.2:50052/file_parse",
    ]


def test_dispatcher_quarantines_failed_worker_and_uses_healthy_worker_next():
    calls: list[str] = []

    class Response:
        headers = {"Content-Type": "application/json"}

        def __init__(self, status_code: int):
            self.status_code = status_code
            self.content = b'{"status":"error"}' if status_code >= 500 else b'{"status":"ok"}'

    class Session:
        def post(self, url, **_kwargs):
            calls.append(url)
            return Response(503 if "10.0.0.1" in url else 200)

    dispatcher = app.ParseDispatcher(
        backend_host="workers.test",
        backend_port=50052,
        maximum_waiting=4,
        minimum_backends=2,
        wait_timeout_seconds=2,
        request_timeout_seconds=30,
        failure_cooldown_seconds=30,
        resolver=lambda _host: ["10.0.0.1", "10.0.0.2"],
        session=Session(),
        prober=lambda _backend, _path: True,
    )

    first = dispatcher.forward(body=b"one", content_type="application/pdf", priority="bulk")
    snapshot = dispatcher.snapshot()
    second = dispatcher.forward(body=b"two", content_type="application/pdf", priority="bulk")

    assert first[0] == 503
    assert second[0] == 200
    assert calls == [
        "http://10.0.0.1:50052/file_parse",
        "http://10.0.0.2:50052/file_parse",
    ]
    assert snapshot["quarantined"] == 1
    assert snapshot["live"] == 2
    assert snapshot["ready"] is True
    assert snapshot["capacity_target_met"] is False


def test_dispatcher_quarantine_clears_only_after_worker_reports_ready():
    ready = False

    def prober(_backend: str, path: str) -> bool:
        return path == "/live" or ready

    dispatcher = app.ParseDispatcher(
        backend_host="workers.test",
        backend_port=50052,
        maximum_waiting=4,
        minimum_backends=1,
        wait_timeout_seconds=2,
        request_timeout_seconds=2,
        failure_cooldown_seconds=1,
        resolver=lambda _host: ["10.0.0.1"],
        prober=prober,
    )
    dispatcher._quarantined["10.0.0.1"] = 0

    assert dispatcher._ready_backends(("10.0.0.1",)) == ()
    assert "10.0.0.1" in dispatcher._quarantined

    ready = True
    dispatcher._quarantined["10.0.0.1"] = 0
    assert dispatcher._ready_backends(("10.0.0.1",)) == ("10.0.0.1",)
    assert "10.0.0.1" not in dispatcher._quarantined


def test_dispatcher_health_snapshot_recovers_an_idle_quarantined_worker():
    ready = False

    def prober(_backend: str, path: str) -> bool:
        return path == "/live" or ready

    dispatcher = app.ParseDispatcher(
        backend_host="workers.test",
        backend_port=50052,
        maximum_waiting=4,
        minimum_backends=1,
        wait_timeout_seconds=2,
        request_timeout_seconds=2,
        failure_cooldown_seconds=1,
        resolver=lambda _host: ["10.0.0.1"],
        prober=prober,
    )
    dispatcher._quarantined["10.0.0.1"] = 0

    unavailable = dispatcher.snapshot()
    assert unavailable["ready"] is False
    assert unavailable["quarantined"] == 1

    ready = True
    dispatcher._quarantined["10.0.0.1"] = 0
    recovered = dispatcher.snapshot()
    assert recovered["ready"] is True
    assert recovered["quarantined"] == 0


def test_dispatcher_never_assigns_a_live_but_busy_worker():
    dispatcher = app.ParseDispatcher(
        backend_host="workers.test",
        backend_port=50052,
        maximum_waiting=4,
        minimum_backends=1,
        wait_timeout_seconds=2,
        request_timeout_seconds=2,
        resolver=lambda _host: ["10.0.0.1", "10.0.0.2"],
        prober=lambda backend, path: path == "/live" or backend == "10.0.0.2",
    )

    backend, _, _ = dispatcher._acquire_backend("bulk")
    dispatcher._release_backend(backend)

    assert backend == "10.0.0.2"


@pytest.mark.parametrize("document_count", [3, 10, 30])
def test_parse_dispatcher_acceptance_matrix_bounds_worker_parallelism(
    document_count,
):
    state_lock = threading.Lock()
    active = 0
    maximum_active = 0
    backends: set[str] = set()

    class Response:
        status_code = 200
        content = b'{"status":"ok"}'
        headers = {"Content-Type": "application/json"}

    class Session:
        def post(self, url, **_kwargs):
            nonlocal active, maximum_active
            with state_lock:
                active += 1
                maximum_active = max(maximum_active, active)
                backends.add(url.split("//", 1)[1].split(":", 1)[0])
            time.sleep(0.03)
            with state_lock:
                active -= 1
            return Response()

    worker_addresses = [f"10.0.0.{index}" for index in range(1, 7)]
    dispatcher = app.ParseDispatcher(
        backend_host="workers.test",
        backend_port=50052,
        maximum_waiting=64,
        minimum_backends=3,
        wait_timeout_seconds=3,
        request_timeout_seconds=3,
        resolver=lambda _host: worker_addresses,
        session=Session(),
        prober=lambda _backend, _path: True,
    )
    results: list[tuple[int, str, bytes, str]] = []
    threads = [
        threading.Thread(
            target=lambda: results.append(
                dispatcher.forward(
                    body=b"document",
                    content_type="multipart/form-data; boundary=test",
                    priority="bulk",
                )
            )
        )
        for _ in range(document_count)
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == document_count
    assert all(result[0] == 200 for result in results)
    assert maximum_active == min(document_count, 6)
    assert len(backends) == min(document_count, 6)
    assert dispatcher.snapshot()["waiting"] == 0
