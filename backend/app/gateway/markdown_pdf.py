from __future__ import annotations

import re
from functools import lru_cache
from io import BytesIO
from pathlib import Path

_EMOJI_SEQ_RE = re.compile(
    r"(?:"
    r"(?:[\U0001F300-\U0001FAFF\u2600-\u27BF][\U0001F3FB-\U0001F3FF]?)(?:\uFE0F)?"
    r"(?:\u200d(?:[\U0001F300-\U0001FAFF\u2600-\u27BF][\U0001F3FB-\U0001F3FF]?)(?:\uFE0F)?)*"
    r")",
)


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

_TEXT_FONT_CANDIDATES: tuple[tuple[Path, int | None], ...] = (
    # Enterprise release images install this variable TrueType font at a
    # deterministic path so ReportLab can always embed Simplified Chinese.
    (Path("/usr/share/fonts/truetype/deerflow/NotoSansSC-VF.ttf"), None),
    # Production Linux images. WenQuanYi uses TrueType outlines and is
    # understood by ReportLab; Noto CJK is retained as a secondary candidate
    # for distributions that ship a compatible TTC build.
    (Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"), 0),
    (Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"), 0),
    (Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"), 2),
    (Path("/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf"), None),
    # Developer workstations.
    (Path(r"C:\Windows\Fonts\msyh.ttc"), 0),
    (Path(r"C:\Windows\Fonts\simhei.ttf"), None),
    (Path(r"C:\Windows\Fonts\simkai.ttf"), None),
    (Path(r"C:\Windows\Fonts\simfang.ttf"), None),
    (Path(r"C:\Windows\Fonts\arial.ttf"), None),
)


@lru_cache(maxsize=1)
def _get_font_name() -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for path, subfont_index in _TEXT_FONT_CANDIDATES:
        if not path.exists():
            continue
        try:
            index_suffix = "" if subfont_index is None else f"_{subfont_index}"
            name = f"DeerFlowFont_{path.stem}{index_suffix}"
            kwargs = {} if subfont_index is None else {"subfontIndex": subfont_index}
            pdfmetrics.registerFont(TTFont(name, str(path), **kwargs))
            return name
        except Exception:
            continue
    return "Helvetica"


def _get_emoji_font_name() -> str | None:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    candidates = [
        Path(r"C:\Windows\Fonts\seguiemj.ttf"),
        Path(r"C:\Windows\Fonts\seguisym.ttf"),
    ]

    for path in candidates:
        if not path.exists():
            continue
        try:
            name = f"DeerFlowEmojiFont_{path.stem}"
            pdfmetrics.registerFont(TTFont(name, str(path)))
            return name
        except Exception:
            continue
    return None


def _sanitize_text(text: str) -> str:
    text = text.replace("\u00a0", " ").replace("\u2028", "\n").replace("\u2029", "\n")
    text = re.sub(r"[\u200b\u200c\u2060\ufeff]", "", text)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)


def _require_embedded_cjk_font(text: str, font_name: str) -> None:
    if font_name == "Helvetica" and _CJK_RE.search(text):
        raise RuntimeError("A compatible embedded CJK font is required to generate this PDF. Install WenQuanYi Zen Hei or Noto Sans CJK in the Gateway image.")


def markdown_to_pdf_bytes(markdown: str, *, title: str = "report") -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import ListFlowable, Paragraph, Preformatted, SimpleDocTemplate, Spacer

    clean_markdown = _sanitize_text(markdown)
    clean_title = _sanitize_text(title)
    font_name = _get_font_name()
    _require_embedded_cjk_font(f"{clean_title}\n{clean_markdown}", font_name)
    emoji_font_name = _get_emoji_font_name()
    styles = getSampleStyleSheet()
    base = ParagraphStyle(
        name="Base",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=10.5,
        leading=14,
    )
    h1 = ParagraphStyle(name="H1", parent=base, fontSize=18, leading=22, spaceAfter=8, spaceBefore=8)
    h2 = ParagraphStyle(name="H2", parent=base, fontSize=14, leading=18, spaceAfter=6, spaceBefore=10)
    h3 = ParagraphStyle(name="H3", parent=base, fontSize=12, leading=16, spaceAfter=4, spaceBefore=8)
    code_style = ParagraphStyle(name="Code", parent=base, fontName=font_name, fontSize=9.5, leading=12)

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=clean_title,
    )

    def escape_para(text: str) -> str:
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
        if not emoji_font_name:
            return _EMOJI_SEQ_RE.sub("", text)
        return _EMOJI_SEQ_RE.sub(lambda m: f'<font face="{emoji_font_name}">{m.group(0)}</font>', text)

    lines = clean_markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    story = []
    in_code = False
    code_lines: list[str] = []
    para_lines: list[str] = []
    list_items: list[str] = []

    def flush_paragraph() -> None:
        nonlocal para_lines
        text = " ".join(x.strip() for x in para_lines).strip()
        para_lines = []
        if text:
            story.append(Paragraph(escape_para(text), base))
            story.append(Spacer(1, 4))

    def flush_list() -> None:
        nonlocal list_items
        if not list_items:
            return
        story.append(ListFlowable([Paragraph(escape_para(item), base) for item in list_items], bulletType="bullet", leftIndent=12))
        story.append(Spacer(1, 6))
        list_items = []

    def flush_code() -> None:
        nonlocal code_lines, in_code
        if code_lines:
            story.append(Preformatted("\n".join(code_lines), code_style))
            story.append(Spacer(1, 6))
        code_lines = []
        in_code = False

    for raw in lines:
        line = _sanitize_text(raw.rstrip("\n"))
        if line.strip().startswith("```"):
            flush_paragraph()
            flush_list()
            if in_code:
                flush_code()
            else:
                in_code = True
            continue

        if in_code:
            code_lines.append(line)
            continue

        if not line.strip():
            flush_paragraph()
            flush_list()
            continue

        if line.startswith("# "):
            flush_paragraph()
            flush_list()
            story.append(Paragraph(escape_para(line[2:].strip()), h1))
            story.append(Spacer(1, 6))
            continue
        if line.startswith("## "):
            flush_paragraph()
            flush_list()
            story.append(Paragraph(escape_para(line[3:].strip()), h2))
            story.append(Spacer(1, 4))
            continue
        if line.startswith("### "):
            flush_paragraph()
            flush_list()
            story.append(Paragraph(escape_para(line[4:].strip()), h3))
            story.append(Spacer(1, 3))
            continue

        if line.lstrip().startswith(("- ", "* ")):
            flush_paragraph()
            list_items.append(line.lstrip()[2:].strip())
            continue

        if m := re.match(r"^\s*\d+\.\s+(.*)$", line):
            flush_paragraph()
            list_items.append(m.group(1).strip())
            continue

        para_lines.append(line)

    flush_paragraph()
    flush_list()
    flush_code()
    doc.build(story)
    return buf.getvalue()
