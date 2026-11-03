"""Narrow runtime compatibility for XLSX files produced by non-standard exporters.

Some enterprise spreadsheet generators emit ``defaultColWidthPt`` on the
worksheet ``sheetFormatPr`` element.  The attribute is formatting-only and is
not part of the OOXML shape understood by openpyxl 3.x, so the otherwise valid
workbook is rejected before WeKnora can read any cell content.

This module intentionally ignores only that observed extension.  It does not
rewrite the uploaded workbook and does not hide unrelated workbook errors.
"""

from __future__ import annotations

from functools import wraps

from openpyxl.worksheet.dimensions import SheetFormatProperties


_PATCH_MARKER = "__deerflow_default_col_width_pt_compat__"


def install_openpyxl_sheet_format_compat() -> bool:
    """Install the compatibility shim once and report whether it was installed."""

    current_init = SheetFormatProperties.__init__
    if getattr(current_init, _PATCH_MARKER, False):
        return False

    @wraps(current_init)
    def compatible_init(self, *args, **kwargs):
        kwargs.pop("defaultColWidthPt", None)
        return current_init(self, *args, **kwargs)

    setattr(compatible_init, _PATCH_MARKER, True)
    SheetFormatProperties.__init__ = compatible_init
    return True
