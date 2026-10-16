"""Process-wide Python startup customizations for backend entrypoints.

When ``backend/`` is on ``sys.path``, Python imports this module during
interpreter startup. Keep changes here suitable for all gateway, script,
migration, and test entrypoints that run in that environment.
"""

from __future__ import annotations

import asyncio
import sys


def _configure_windows_event_loop_policy() -> None:
    if sys.platform != "win32":
        return

    selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if selector_policy is None:
        return

    if not isinstance(asyncio.get_event_loop_policy(), selector_policy):
        asyncio.set_event_loop_policy(selector_policy())


def _install_openpyxl_default_col_width_pt_compat() -> None:
    """Ignore the formatting-only ``defaultColWidthPt`` worksheet attribute.

    Some enterprise spreadsheet generators (WPS, newer Excel save paths) emit
    ``defaultColWidthPt`` on the worksheet ``sheetFormatPr`` element. The
    attribute is not part of the OOXML shape understood by openpyxl 3.x, so a
    valid workbook is rejected with ``TypeError: SheetFormatProperties.__init__()
    got an unexpected keyword argument 'defaultColWidthPt'`` before any cell can
    be read. That rejects gateway uploads through the MarkItDown XlsxConverter
    path (``deerflow.utils.file_conversion``) with a 503.

    This shim is the same narrow, idempotent, marker-guarded fix that the
    WeKnora DocReader sidecar installs via sitecustomize; it ignores only that
    observed extension, never rewrites the workbook, and does not hide unrelated
    openpyxl errors. ``openpyxl`` is a third-party runtime dependency, so this
    stays within the harness/app boundary.
    """
    marker = "__deerflow_default_col_width_pt_compat__"
    try:
        from openpyxl.worksheet.dimensions import SheetFormatProperties
    except Exception:  # pragma: no cover - openpyxl absent in slim entrypoints
        return

    current_init = SheetFormatProperties.__init__
    if getattr(current_init, marker, False):
        return

    from functools import wraps

    @wraps(current_init)
    def compatible_init(self, *args, **kwargs):
        kwargs.pop("defaultColWidthPt", None)
        return current_init(self, *args, **kwargs)

    setattr(compatible_init, marker, True)
    SheetFormatProperties.__init__ = compatible_init


_configure_windows_event_loop_policy()
_install_openpyxl_default_col_width_pt_compat()
