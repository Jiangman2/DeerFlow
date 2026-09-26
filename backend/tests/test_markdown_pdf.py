from __future__ import annotations

import pytest

from app.gateway import markdown_pdf


def test_cjk_pdf_fails_closed_without_embedded_font(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(markdown_pdf, "_get_font_name", lambda: "Helvetica")

    with pytest.raises(RuntimeError, match="embedded CJK font"):
        markdown_pdf.markdown_to_pdf_bytes("# 中文报告")


def test_ascii_pdf_remains_available_without_cjk_font(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(markdown_pdf, "_get_font_name", lambda: "Helvetica")

    rendered = markdown_pdf.markdown_to_pdf_bytes("# English report")

    assert rendered.startswith(b"%PDF-")


def test_linux_cjk_fonts_are_checked_before_workstation_fonts() -> None:
    # A Windows literal is a POSIX relative path on Linux; compare portable
    # spellings without changing the runtime's platform-specific Path lookup.
    candidates = [path.as_posix().replace("\\", "/") for path, _ in markdown_pdf._TEXT_FONT_CANDIDATES]

    assert candidates[0] == "/usr/share/fonts/truetype/deerflow/NotoSansSC-VF.ttf"
    assert "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc" in candidates
    assert "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc" in candidates
    assert candidates.index("C:/Windows/Fonts/simhei.ttf") > candidates.index("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")


def test_cjk_pdf_uses_an_embedded_font_when_runtime_provides_one() -> None:
    font_name = markdown_pdf._get_font_name()
    if font_name == "Helvetica":
        pytest.skip("No compatible CJK font is installed in this test runtime")

    rendered = markdown_pdf.markdown_to_pdf_bytes("# 中文报告\n\n字体嵌入验收。")

    assert rendered.startswith(b"%PDF-")
    assert b"/FontFile" in rendered
