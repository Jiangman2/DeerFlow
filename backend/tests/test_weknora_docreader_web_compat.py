"""Regression tests for DeerFlow's isolated WeKnora URL-import shim."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
import types
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPAT_MODULE = REPO_ROOT / "k8s" / "weknora" / "docreader-compat" / "web_compat.py"


def _load_compat_module():
    name = f"deerflow_weknora_web_compat_{uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, COMPAT_MODULE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    def __init__(self, status: int, *, headers=None, body: bytes = b""):
        self.status_code = status
        self.headers = headers or {}
        self.encoding = "utf-8"
        self._body = body
        self.closed = False

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size: int):
        del chunk_size
        yield self._body

    def close(self) -> None:
        self.closed = True


class _FakeSession:
    def __init__(self, responses):
        self.responses = deque(responses)
        self.calls = []
        self.trust_env = True
        self.closed = False

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.popleft()

    def close(self) -> None:
        self.closed = True


def test_static_fetch_validates_redirect_and_uses_explicit_proxy_and_ca(tmp_path, monkeypatch):
    compat = _load_compat_module()
    ca_bundle = tmp_path / "enterprise-ca.pem"
    ca_bundle.write_text("test-ca", encoding="utf-8")
    monkeypatch.setenv("SSL_CERT_FILE", str(ca_bundle))

    redirect = _FakeResponse(302, headers={"Location": "/article"})
    success = _FakeResponse(
        200,
        headers={"Content-Type": "text/html; charset=utf-8"},
        body=b"<html><title>Article</title><main>Useful content</main></html>",
    )
    session = _FakeSession([redirect, success])
    checked = []

    page = compat.fetch_static_page(
        "https://example.com/start",
        http_proxy="http://proxy.local:8080",
        https_proxy="http://proxy.local:8080",
        ssrf_checker=lambda url: (checked.append(url) is None, ""),
        session_factory=lambda: session,
    )

    assert checked == ["https://example.com/start", "https://example.com/article"]
    assert page.final_url == "https://example.com/article"
    assert page.page_title == "Article"
    assert session.trust_env is False
    assert session.closed is True
    assert all(response.closed for response in (redirect, success))
    _, request_kwargs = session.calls[-1]
    assert request_kwargs["allow_redirects"] is False
    assert request_kwargs["proxies"] == {
        "http": "http://proxy.local:8080",
        "https": "http://proxy.local:8080",
    }
    assert request_kwargs["verify"] == str(ca_bundle)


def test_static_fetch_rejects_unsafe_redirect_before_second_request():
    compat = _load_compat_module()
    redirect = _FakeResponse(302, headers={"Location": "http://127.0.0.1/private"})
    session = _FakeSession([redirect])

    def checker(url: str):
        if "127.0.0.1" in url:
            return False, "loopback address"
        return True, ""

    with pytest.raises(RuntimeError, match="loopback address"):
        compat.fetch_static_page(
            "https://example.com/start",
            http_proxy="",
            https_proxy="",
            ssrf_checker=checker,
            session_factory=lambda: session,
        )

    assert len(session.calls) == 1
    assert redirect.closed is True
    assert session.closed is True


def test_static_fetch_rejects_oversized_html(monkeypatch):
    compat = _load_compat_module()
    monkeypatch.setattr(compat, "_MAX_HTML_BYTES", 4)
    response = _FakeResponse(
        200,
        headers={"Content-Type": "text/html"},
        body=b"12345",
    )
    session = _FakeSession([response])

    with pytest.raises(RuntimeError, match="10 MiB"):
        compat.fetch_static_page(
            "https://example.com/large",
            http_proxy="",
            https_proxy="",
            ssrf_checker=lambda _url: (True, ""),
            session_factory=lambda: session,
        )


def test_lazy_wechat_image_source_is_exposed_to_upstream_extractor():
    compat = _load_compat_module()
    original = '<img class="rich" data-src="https://mmbiz.qpic.cn/image?wx_fmt=png">'
    promoted = compat._promote_lazy_image_sources(original)
    assert 'src="https://mmbiz.qpic.cn/image?wx_fmt=png"' in promoted
    assert 'data-src="https://mmbiz.qpic.cn/image?wx_fmt=png"' in promoted


def _install_fake_docreader(monkeypatch, compat):
    scrape_result = types.SimpleNamespace

    class FakeStdWebParser:
        browser_calls = 0
        parse_content = "Error parsing web page: https://example.com"

        async def scrape(self, _url: str):
            type(self).browser_calls += 1
            return scrape_result(html="", visible_text="", page_title="")

        def parse_into_text(self, _content: bytes):
            return SimpleNamespace(content=type(self).parse_content)

    web_parser = types.ModuleType("docreader.parser.web_parser")
    web_parser.StdWebParser = FakeStdWebParser
    web_parser.CONFIG = SimpleNamespace(external_http_proxy="", external_https_proxy="")
    web_parser.is_ssrf_safe_url = lambda _url: (True, "")
    web_parser.extract_markdown_from_html = lambda html: "# Static article" if html else None
    web_parser._ScrapeResult = scrape_result

    parser_package = types.ModuleType("docreader.parser")
    parser_package.web_parser = web_parser
    docreader_package = types.ModuleType("docreader")
    docreader_package.parser = parser_package
    monkeypatch.setitem(sys.modules, "docreader", docreader_package)
    monkeypatch.setitem(sys.modules, "docreader.parser", parser_package)
    monkeypatch.setitem(sys.modules, "docreader.parser.web_parser", web_parser)
    assert compat.install_docreader_web_compat() is True
    assert compat.install_docreader_web_compat() is False
    return FakeStdWebParser


def test_static_article_short_circuits_broken_webkit(monkeypatch):
    compat = _load_compat_module()
    parser_class = _install_fake_docreader(monkeypatch, compat)
    monkeypatch.setattr(
        compat,
        "fetch_static_page",
        lambda *_args, **_kwargs: compat.StaticPage(
            html="<html><main>Static article</main></html>",
            final_url="https://example.com/article",
            page_title="Static article",
        ),
    )

    result = asyncio.run(parser_class().scrape("https://example.com/article"))

    assert result.html == "<html><main>Static article</main></html>"
    assert result.page_title == "Static article"
    assert parser_class.browser_calls == 0


def test_upstream_error_document_becomes_real_parser_failure(monkeypatch):
    compat = _load_compat_module()
    parser_class = _install_fake_docreader(monkeypatch, compat)

    with pytest.raises(RuntimeError, match="Error parsing web page"):
        parser_class().parse_into_text(b"https://example.com")


def test_sitecustomize_hook_waits_until_docreader_module_becomes_importable(tmp_path, monkeypatch):
    compat = _load_compat_module()
    package_root = tmp_path / "late-app"
    parser_root = package_root / "docreader" / "parser"
    parser_root.mkdir(parents=True)
    (package_root / "docreader" / "__init__.py").write_text("", encoding="utf-8")
    (parser_root / "__init__.py").write_text("", encoding="utf-8")
    (parser_root / "web_parser.py").write_text(
        "\n".join(
            (
                "from types import SimpleNamespace",
                "class StdWebParser:",
                "    async def scrape(self, url):",
                "        return SimpleNamespace(html='', visible_text='', page_title='')",
                "    def parse_into_text(self, content):",
                "        return SimpleNamespace(content='ok')",
                "CONFIG = SimpleNamespace(external_http_proxy='', external_https_proxy='')",
                "is_ssrf_safe_url = lambda url: (True, '')",
                "extract_markdown_from_html = lambda html: '# body'",
                "_ScrapeResult = SimpleNamespace",
            )
        ),
        encoding="utf-8",
    )
    for name in ("docreader.parser.web_parser", "docreader.parser", "docreader"):
        monkeypatch.delitem(sys.modules, name, raising=False)

    assert compat.install_docreader_web_compat_when_available() is True
    assert any(getattr(finder, compat._IMPORT_HOOK_MARKER, False) for finder in sys.meta_path)

    monkeypatch.syspath_prepend(str(package_root))
    importlib.invalidate_caches()
    web_parser = importlib.import_module("docreader.parser.web_parser")

    assert getattr(web_parser.StdWebParser.scrape, compat._PATCH_MARKER, False)
    assert getattr(web_parser.StdWebParser.parse_into_text, compat._PATCH_MARKER, False)
    assert not any(getattr(finder, compat._IMPORT_HOOK_MARKER, False) for finder in sys.meta_path)
