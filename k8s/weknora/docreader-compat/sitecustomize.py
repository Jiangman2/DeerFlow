"""Automatically enable DeerFlow's narrow DocReader compatibility hooks."""

from web_compat import install_docreader_web_compat_when_available
from xlsx_compat import install_openpyxl_sheet_format_compat

install_openpyxl_sheet_format_compat()
install_docreader_web_compat_when_available()
