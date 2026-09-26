from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "docker" / "sandbox-ppt-master" / "markdown_to_pdf.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("deerflow_sandbox_markdown_to_pdf", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pdf_resource_fetcher_denies_network_and_directory_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module()
    monkeypatch.setitem(
        sys.modules,
        "weasyprint",
        SimpleNamespace(default_url_fetcher=lambda url: {"url": url}),
    )
    fetch = module._local_only_fetcher(tmp_path)

    with pytest.raises(ValueError, match="External resource URLs"):
        fetch("https://example.invalid/image.png")
    with pytest.raises(ValueError, match="escapes the Markdown directory"):
        fetch((tmp_path.parent / "outside.png").as_uri())


def test_pdf_resource_fetcher_allows_data_and_in_tree_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module()
    monkeypatch.setitem(
        sys.modules,
        "weasyprint",
        SimpleNamespace(default_url_fetcher=lambda url: {"url": url}),
    )
    local = tmp_path / "image.png"
    local.write_bytes(b"png")
    fetch = module._local_only_fetcher(tmp_path)

    assert fetch("data:image/png;base64,eA==")["url"].startswith("data:")
    assert fetch(local.as_uri())["url"] == local.as_uri()
