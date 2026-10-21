"""Merged-release regressions: full rows, original integrity and narrow failures.

Synthetic workbooks only; not a claim of acceptance of the incident workbook.
"""
import asyncio
import io
import threading
from concurrent.futures import ThreadPoolExecutor
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from openpyxl import Workbook
from openpyxl.worksheet.dimensions import SheetFormatProperties

from deerflow.utils import file_conversion

MARKER = "__deerflow_default_col_width_pt_compat__"


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    original = SheetFormatProperties.__init__
    while getattr(original, MARKER, False):
        original = original.__wrapped__
    monkeypatch.setattr(SheetFormatProperties, "__init__", original)
    monkeypatch.setattr(file_conversion, "_get_pdf_converter", lambda: "auto")


def workbook_bytes(rows, attribute):
    output = io.BytesIO()
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "地层设计"
    sheet.append(["唯一行号", "地层", "设计值"])
    for index in range(rows):
        sheet.append([f"ROW_{index:05d}_END", f"地层{index % 20}", index + 0.25])
    other = workbook.create_sheet("说明")
    other.append(["第二工作表完整保留", "中文说明"])
    workbook.save(output)
    workbook.close()
    raw = output.getvalue()
    if attribute is None:
        return raw
    result = io.BytesIO()
    with ZipFile(io.BytesIO(raw)) as source, ZipFile(result, "w", ZIP_DEFLATED) as dest:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename.startswith("xl/worksheets/") and info.filename.endswith(".xml"):
                data = data.replace(b"<sheetFormatPr ", b"<sheetFormatPr " + attribute + b" ", 1)
            dest.writestr(info, data)
    return result.getvalue()


@pytest.mark.parametrize("affected", [False, True], ids=["standard", "defaultColWidthPt"])
def test_large_workbook_all_12037_rows_and_sheets_survive(tmp_path, affected):
    raw = workbook_bytes(12037, b'defaultColWidthPt="64"' if affected else None)
    source = tmp_path / "full-table.XLSX"
    source.write_bytes(raw)
    converted = asyncio.run(file_conversion.convert_file_to_markdown(source))
    assert converted is not None
    text = converted.read_text(encoding="utf-8")
    assert "地层设计" in text and "第二工作表完整保留" in text
    # Markdown escaping changes representation, not the original cell value.
    text = text.replace("\\_", "_")
    for index in range(12037):
        assert text.count(f"ROW_{index:05d}_END") == 1
    assert source.read_bytes() == raw


def test_concurrent_install_is_one_wrapper():
    original = SheetFormatProperties.__init__
    barrier = threading.Barrier(16)

    def install():
        barrier.wait(timeout=10)
        file_conversion._install_openpyxl_default_col_width_pt_compat()
        return SheetFormatProperties.__init__

    with ThreadPoolExecutor(max_workers=16) as pool:
        wrappers = list(pool.map(lambda _: install(), range(16)))
    assert all(wrapper is wrappers[0] for wrapper in wrappers)
    assert wrappers[0].__wrapped__ is original


@pytest.mark.parametrize("kind", ["truncated-zip", "unknown-attribute"])
def test_bad_workbook_not_silently_accepted(tmp_path, kind):
    raw = workbook_bytes(2, None)[:200] if kind == "truncated-zip" else workbook_bytes(2, b'unrelated_attribute="bad"')
    source = tmp_path / "invalid.xlsx"
    source.write_bytes(raw)
    assert asyncio.run(file_conversion.convert_file_to_markdown(source)) is None
    assert not source.with_suffix(".md").exists()
    assert source.read_bytes() == raw


def test_mislabeled_plain_text_retains_existing_converter_behavior(tmp_path, monkeypatch):
    source = tmp_path / "text-named-as-workbook.xlsx"
    raw = b"This is plain text, not an Excel workbook."
    source.write_bytes(raw)
    original = SheetFormatProperties.__init__
    after = file_conversion._convert_with_markitdown(source)
    monkeypatch.setattr(SheetFormatProperties, "__init__", original)
    monkeypatch.setattr(file_conversion, "_install_openpyxl_default_col_width_pt_compat", lambda: None)
    before = file_conversion._convert_with_markitdown(source)
    assert after == before == raw.decode()
    assert source.read_bytes() == raw
