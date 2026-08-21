"""Harden WeKnora URL imports without replacing its pinned DocReader image.

The v0.7.0 DocReader web parser launches Playwright WebKit for every URL.  On
the reviewed ARM64 enterprise image that browser can abort before navigation,
even for server-rendered pages such as WeChat articles.  The upstream parser
then returns ``Error parsing web page: ...`` as a normal Document, so WeKnora
indexes the diagnostic text and records a false ``completed`` status.

This compatibility hook is deliberately narrow:

* fetch server-rendered HTTP(S) HTML first with the existing SSRF policy;
* validate every redirect before following it;
* use only DocReader's dedicated outbound proxy and verified CA bundle;
* retain upstream WebKit for genuinely dynamic pages; and
* turn the upstream error sentinel into a real parser failure.

No file parser, queue, chunker, embedding path, or persistent schema is changed.
"""

from __future__ import annotations

import asyncio
import html as html_module
import importlib.abc
import importlib.machinery
import logging
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

_PATCH_MARKER = "__deerflow_static_web_import_compat__"
_IMPORT_HOOK_MARKER = "__deerflow_docreader_web_import_hook__"
_WEB_PARSER_MODULE = "docreader.parser.web_parser"
_MAX_REDIRECTS = 5
_MAX_HTML_BYTES = 10 * 1024 * 1024
_REQUEST_TIMEOUT = (10, 30)
_ERROR_SENTINEL = "Error parsing web page:"
_HTML_CONTENT_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/plain",
)
_BLOCK_PAGE_MARKERS = (
    "访问过于频繁",
    "环境异常",
    "请完成验证",
    "请在微信客户端打开链接",
    "该内容已被发布者删除",
    "内容因违规无法查看",
)


@dataclass(frozen=True)
class StaticPage:
    """A bounded, validated HTML response used by the upstream extractor."""

    html: str
    final_url: str
    page_title: str = ""


def _ca_verify_value() -> bool | str:
    """Return the configured readable CA bundle, otherwise Requests defaults."""

    for name in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        value = os.environ.get(name, "").strip()
        if value and Path(value).is_file():
            return value
    return True


def _proxy_for_url(url: str, *, http_proxy: str, https_proxy: str) -> str:
    scheme = urlparse(url).scheme.lower()
    if scheme == "https":
        return https_proxy.strip()
    return http_proxy.strip()


def _extract_title(html: str) -> str:
    match = re.search(r"(?is)<title\b[^>]*>(.*?)</title>", html)
    if match is None:
        return ""
    title = re.sub(r"\s+", " ", re.sub(r"(?is)<[^>]+>", "", match.group(1)))
    return html_module.unescape(title).strip()


def _promote_lazy_image_sources(html: str) -> str:
    """Expose lazy image URLs to Trafilatura without rewriting remote content."""

    def replace_img(match: re.Match[str]) -> str:
        tag = match.group(0)
        if re.search(r"\ssrc\s*=", tag, flags=re.IGNORECASE):
            return tag
        lazy = re.search(
            r"\s(?:data-src|data-original)\s*=\s*([\"'])(.*?)\1",
            tag,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if lazy is None:
            return tag
        insertion = f" src={lazy.group(1)}{lazy.group(2)}{lazy.group(1)}"
        return tag[:-1] + insertion + ">"

    return re.sub(r"(?is)<img\b[^>]*>", replace_img, html)


def _is_block_page(markdown: str) -> bool:
    compact = re.sub(r"\s+", " ", markdown).strip()
    return len(compact) < 4000 and any(marker in compact for marker in _BLOCK_PAGE_MARKERS)


def fetch_static_page(
    url: str,
    *,
    http_proxy: str,
    https_proxy: str,
    ssrf_checker: Callable[[str], tuple[bool, str]],
    session_factory=None,
) -> StaticPage:
    """Fetch bounded HTML while applying the upstream SSRF rule to each hop."""

    if session_factory is None:
        import requests

        session_factory = requests.Session

    session = session_factory()
    session.trust_env = False
    current_url = url.strip()
    headers = {
        "Accept": "text/html,application/xhtml+xml;q=0.9,text/plain;q=0.8,*/*;q=0.1",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"),
    }

    try:
        for redirect_count in range(_MAX_REDIRECTS + 1):
            safe, reason = ssrf_checker(current_url)
            if not safe:
                raise RuntimeError(f"URL blocked by SSRF guard: {reason}")

            proxy = _proxy_for_url(
                current_url,
                http_proxy=http_proxy,
                https_proxy=https_proxy,
            )
            proxies = {"http": proxy, "https": proxy} if proxy else {}
            response = session.get(
                current_url,
                headers=headers,
                proxies=proxies,
                verify=_ca_verify_value(),
                timeout=_REQUEST_TIMEOUT,
                allow_redirects=False,
                stream=True,
            )
            try:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location", "").strip()
                    if not location:
                        raise RuntimeError("Redirect response omitted Location")
                    if redirect_count >= _MAX_REDIRECTS:
                        raise RuntimeError("Too many redirects")
                    current_url = urljoin(current_url, location)
                    continue

                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "").lower()
                if content_type and not any(value in content_type for value in _HTML_CONTENT_TYPES):
                    raise RuntimeError(f"Unsupported webpage content type: {content_type[:120]}")

                body = bytearray()
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    body.extend(chunk)
                    if len(body) > _MAX_HTML_BYTES:
                        raise RuntimeError("Webpage exceeds the 10 MiB static HTML limit")

                encoding = response.encoding or "utf-8"
                decoded = bytes(body).decode(encoding, errors="replace")
                return StaticPage(
                    html=_promote_lazy_image_sources(decoded),
                    final_url=current_url,
                    page_title=_extract_title(decoded),
                )
            finally:
                response.close()
    finally:
        session.close()

    raise RuntimeError("Static webpage fetch ended without a response")


def _patch_web_parser_module(web_parser) -> bool:
    """Patch an already imported DocReader web-parser module exactly once."""

    parser_class = web_parser.StdWebParser
    current_scrape = parser_class.scrape
    if getattr(current_scrape, _PATCH_MARKER, False):
        return False

    current_parse = parser_class.parse_into_text

    @wraps(current_scrape)
    async def compatible_scrape(self, url: str):
        static_page = None
        try:
            static_page = await asyncio.to_thread(
                fetch_static_page,
                url,
                http_proxy=web_parser.CONFIG.external_http_proxy,
                https_proxy=web_parser.CONFIG.external_https_proxy,
                ssrf_checker=web_parser.is_ssrf_safe_url,
            )
            markdown = web_parser.extract_markdown_from_html(static_page.html)
            if markdown and not _is_block_page(markdown):
                logger.info(
                    "DocReader URL compatibility used static HTML for %s (%d bytes)",
                    static_page.final_url,
                    len(static_page.html),
                )
                return web_parser._ScrapeResult(
                    html=static_page.html,
                    visible_text="",
                    page_title=static_page.page_title,
                )
            if markdown:
                logger.warning("Static URL response looks like an access/error page: %s", url)
            else:
                logger.info("Static URL response has no extractable body; trying WebKit: %s", url)
        except Exception as exc:
            logger.warning("Static URL fetch failed; trying upstream WebKit for %s: %s", url, exc)

        return await current_scrape(self, url)

    @wraps(current_parse)
    def compatible_parse(self, content: bytes):
        document = current_parse(self, content)
        if document.content.strip().startswith(_ERROR_SENTINEL):
            raise RuntimeError(document.content.strip())
        return document

    setattr(compatible_scrape, _PATCH_MARKER, True)
    setattr(compatible_parse, _PATCH_MARKER, True)
    parser_class.scrape = compatible_scrape
    parser_class.parse_into_text = compatible_parse
    return True


def install_docreader_web_compat(web_parser_module=None) -> bool:
    """Install the URL-parser compatibility hook on an available module."""

    if web_parser_module is None:
        from docreader.parser import web_parser as web_parser_module

    return _patch_web_parser_module(web_parser_module)


def _remove_import_hook(finder) -> None:
    try:
        sys.meta_path.remove(finder)
    except ValueError:
        pass


class _PostImportLoader(importlib.abc.Loader):
    """Delegate module loading, then apply the compatibility patch."""

    def __init__(self, delegate, finder):
        self._delegate = delegate
        self._finder = finder

    def create_module(self, spec):
        create_module = getattr(self._delegate, "create_module", None)
        return create_module(spec) if create_module is not None else None

    def exec_module(self, module) -> None:
        try:
            self._delegate.exec_module(module)
            _patch_web_parser_module(module)
        finally:
            _remove_import_hook(self._finder)


class _DocReaderWebParserFinder(importlib.abc.MetaPathFinder):
    """One-shot finder for DocReader images whose app path is added late."""

    def __init__(self):
        setattr(self, _IMPORT_HOOK_MARKER, True)

    def find_spec(self, fullname, path=None, target=None):
        del target
        if fullname != _WEB_PARSER_MODULE:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.loader is None:
            return None
        spec.loader = _PostImportLoader(spec.loader, self)
        return spec


def install_docreader_web_compat_when_available() -> bool:
    """Patch now or register one narrow hook until DocReader becomes importable.

    The pinned DocReader container starts Python before its application working
    directory is added to ``sys.path``.  ``sitecustomize`` therefore cannot
    import ``docreader`` eagerly.  This one-shot finder observes only the exact
    web-parser module, delegates normal loading, patches it, and removes itself.
    """

    existing = sys.modules.get(_WEB_PARSER_MODULE)
    if existing is not None and hasattr(existing, "StdWebParser"):
        return _patch_web_parser_module(existing)
    if any(getattr(finder, _IMPORT_HOOK_MARKER, False) for finder in sys.meta_path):
        return False
    sys.meta_path.insert(0, _DocReaderWebParserFinder())
    return True
