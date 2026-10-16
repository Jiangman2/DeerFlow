"""Regression tests for the gateway ``sitecustomize.py`` XLSX shim.

The gateway container runs ``cd backend && PYTHONPATH=.``, so ``backend/`` is
on ``sys.path`` and Python auto-imports ``backend/sitecustomize.py`` at
interpreter startup. That module installs a narrow openpyxl compatibility shim
so workbooks carrying the formatting-only ``defaultColWidthPt`` sheetFormatPr
attribute are readable instead of raising ``TypeError``. Without it, the
MarkItDown ``XlsxConverter`` path in ``deerflow.utils.file_conversion`` rejects
the upload with a 503.
"""

from __future__ import annotations

import importlib.util
import io
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.dimensions import SheetFormatProperties

REPO_ROOT = Path(__file__).resolve().parents[1]
SITECUSTOMIZE_MODULE = REPO_ROOT / "sitecustomize.py"

_MARKER = "__deerflow_default_col_width_pt_compat__"


def _load_sitecustomize():
    spec = importlib.util.spec_from_file_location("deerflow_gateway_sitecustomize", SITECUSTOMIZE_MODULE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _workbook_bytes() -> bytes:
    output = io.BytesIO()
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "生产数据"
    worksheet["A1"] = "井号"
    worksheet["B1"] = "NB27-1-1"
    workbook.save(output)
    return output.getvalue()


def _inject_default_col_width_pt(payload: bytes) -> bytes:
    """Add the non-standard formatting attribute observed in production files."""
    output = io.BytesIO()
    with ZipFile(io.BytesIO(payload)) as source, ZipFile(output, "w", ZIP_DEFLATED) as target:
        for member in source.infolist():
            content = source.read(member.filename)
            if member.filename == "xl/worksheets/sheet1.xml":
                marker = b"<sheetFormatPr "
                assert marker in content
                content = content.replace(marker, marker + b'defaultColWidthPt="64" ', 1)
            target.writestr(member, content)
    return output.getvalue()


def _reset_marker(monkeypatch) -> None:
    """Restore a pristine ``SheetFormatProperties.__init__`` so the shim runs.

    The sitecustomize shim only ever installs once and marks its wrapper, so
    re-running it in-process would no-op. Reset to the original init and drop
    the marker to make the test deterministic, then let monkeypatch restore the
    original init on teardown so no other test observes the patch.
    """
    original_init = SheetFormatProperties.__init__
    monkeypatch.setattr(SheetFormatProperties, "__init__", original_init)


def test_sitecustomize_shim_lets_default_col_width_pt_workbook_load(monkeypatch):
    _reset_marker(monkeypatch)
    module = _load_sitecustomize()
    module._install_openpyxl_default_col_width_pt_compat()

    wrapped = getattr(SheetFormatProperties.__init__, _MARKER, False)
    assert wrapped, "openpyxl shim was not wrapped by sitecustomize"

    workbook = load_workbook(io.BytesIO(_inject_default_col_width_pt(_workbook_bytes())))
    assert workbook["生产数据"]["A1"].value == "井号"
    assert workbook["生产数据"]["B1"].value == "NB27-1-1"


def test_sitecustomize_shim_is_idempotent(monkeypatch):
    _reset_marker(monkeypatch)
    module = _load_sitecustomize()

    module._install_openpyxl_default_col_width_pt_compat()
    first_wrapper = SheetFormatProperties.__init__
    module._install_openpyxl_default_col_width_pt_compat()

    assert SheetFormatProperties.__init__ is first_wrapper


def test_sitecustomize_shim_preserves_standard_workbook(monkeypatch):
    _reset_marker(monkeypatch)
    module = _load_sitecustomize()
    module._install_openpyxl_default_col_width_pt_compat()

    workbook = load_workbook(io.BytesIO(_workbook_bytes()))
    assert workbook["生产数据"]["B1"].value == "NB27-1-1"


def test_unpatched_openpyxl_still_reproduces_production_failure():
    # Guard: prove the fixture payload genuinely triggers the production error
    # when the shim is NOT installed, so passing tests mean the shim matters.
    with pytest.raises(TypeError, match="unexpected keyword argument 'defaultColWidthPt'"):
        load_workbook(io.BytesIO(_inject_default_col_width_pt(_workbook_bytes())))
