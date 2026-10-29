"""One model-visible tool per operation; provider selection is operator-owned."""

import asyncio
import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

from langchain.tools import tool

from deerflow.community.deepseek.tools import _original_question
from deerflow.community.url_safety import validate_cloud_fetch_url
from deerflow.community.web_fetch_output import format_page
from deerflow.community.web_router import providers
from deerflow.config import get_app_config
from deerflow.tools.sync import make_sync_tool_wrapper
from deerflow.tools.types import Runtime

logger = logging.getLogger(__name__)
NAMES = ("deepseek", "tavily", "exa")


@dataclass
class Settings:
    order: list[str]
    keys: dict[str, str] = field(repr=False)
    timeout: float = 15
    total_timeout: float = 40
    max_attempts: int = 3
    count: int = 5
    snippet_limit: int = 1000
    body_limit: int = 20000
    fallback_on_empty: bool = False
    send_original_question: bool = True


def _number(extras, name, default, low, high, *, integer=False):
    value = extras.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not low <= value <= high or (integer and int(value) != value):
        raise ValueError("Invalid router settings")
    return int(value) if integer else float(value)


def _order(value):
    if not isinstance(value, list) or not value or len(value) > 3 or any(not isinstance(p, str) or p not in NAMES for p in value) or len(set(value)) != len(value):
        raise ValueError("Invalid provider order")
    return value


def _settings(kind, pdf=False):
    config = get_app_config().get_tool_config(f"web_{kind}")
    extras = (config.model_extra or {}) if config else {}
    order = _order(extras.get("provider_order", list(NAMES)))
    if kind == "fetch" and pdf:
        order = _order(extras.get("pdf_provider_order", ["tavily", "exa"]))
    profiles = extras.get("providers", {})
    if not isinstance(profiles, dict) or any(p not in NAMES for p in profiles):
        raise ValueError("Invalid provider configuration")
    keys = {}
    for name in NAMES:
        profile = profiles.get(name, {})
        if not isinstance(profile, dict) or set(profile) - {"api_key", "enabled"} or not isinstance(profile.get("enabled", True), bool):
            raise ValueError("Invalid provider configuration")
        key = profile.get("api_key")
        if key is None:
            key = os.environ.get(name.upper() + "_API_KEY", "")
        if not isinstance(key, str):
            raise ValueError("Invalid credential configuration")
        if profile.get("enabled", True):
            keys[name] = key.strip()
    for name in ("fallback_on_empty", "send_original_question"):
        if name in extras and not isinstance(extras[name], bool):
            raise ValueError("Invalid router settings")
    return Settings(
        order=order,
        keys=keys,
        timeout=_number(extras, "timeout", 15, 1, 60),
        total_timeout=_number(extras, "total_timeout", 40, 1, 120),
        max_attempts=_number(extras, "max_attempts", 3, 1, 3, integer=True),
        count=_number(extras, "max_results", 5, 1, 20, integer=True),
        snippet_limit=_number(extras, "snippet_max_characters", 1000, 1, 10000, integer=True),
        body_limit=_number(extras, "max_characters", 20000, 1, 100000, integer=True),
        fallback_on_empty=extras.get("fallback_on_empty", False),
        send_original_question=extras.get("send_original_question", True),
    )


def _error(code, attempts, *, fetch=False):
    content = json.dumps({"error": "Web retrieval failed", "error_code": f"WEB_ROUTER_{code}", "attempts": attempts}, ensure_ascii=False)
    return ("Error: " + content if fetch else content), {"web_routing": {"status": "error", "attempts": attempts, "error_code": f"WEB_ROUTER_{code}"}}


async def _aroute(kind, value, runtime=None):
    attempts = []
    fetch = kind == "fetch"
    if fetch:
        if validate_cloud_fetch_url(value):
            return _error("INVALID_URL", attempts, fetch=True)
        pdf = unquote(urlsplit(value).path).lower().endswith(".pdf")
    else:
        value = value.strip()
        if not value or len(value) > 128:
            return _error("INVALID_QUERY", attempts)
        pdf = False
    try:
        settings = _settings(kind, pdf)
    except (ValueError, TypeError):
        return _error("CONFIG_ERROR", attempts, fetch=fetch)
    deadline = time.monotonic() + settings.total_timeout
    dispatched = 0
    for provider in settings.order:
        key = settings.keys.get(provider)
        if not key:
            attempts.append({"provider": provider, "outcome": "skipped", "error_code": "CREDENTIAL_MISSING_OR_DISABLED"})
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0 or dispatched >= settings.max_attempts:
            return _error("BUDGET_EXHAUSTED", attempts, fetch=fetch)
        dispatched += 1
        started = time.monotonic()
        try:
            async with asyncio.timeout(min(settings.timeout, remaining)):
                if fetch:
                    title, body = await providers.fetch(provider, key, value, settings.body_limit, min(settings.timeout, remaining))
                    content = format_page(title, body, settings.body_limit) + f"\n\n[Fetch provider: {provider}]"
                else:
                    original = _original_question(runtime) if settings.send_original_question else None
                    rows = await providers.search(provider, key, value, settings.count, settings.snippet_limit, original, min(settings.timeout, remaining))
                    if not rows and settings.fallback_on_empty:
                        raise providers.ProviderFailure("EMPTY_RESULTS")
                    content = json.dumps(rows, ensure_ascii=False)
        except (providers.ProviderFailure, TimeoutError) as exc:
            # An event-loop timer may wake within its clock resolution. Once the
            # shared-budget timer has expired, a positive monotonic remainder
            # must not permit another paid dispatch.
            shared_deadline_expired = isinstance(exc, TimeoutError) and (settings.total_timeout <= settings.timeout or remaining <= settings.timeout)
            if isinstance(exc, TimeoutError):
                exc = providers.ProviderFailure("TIMEOUT")
            attempt = {"provider": provider, "outcome": "failed", "error_code": exc.code, "elapsed_s": round(time.monotonic() - started, 3)}
            if exc.status is not None:
                attempt["provider_http_status"] = exc.status
            attempts.append(attempt)
            # No URLs, queries, keys or upstream messages in logs/artifacts.
            logger.info("Web router %s provider=%s outcome=%s", kind, provider, exc.code)
            if shared_deadline_expired:
                return _error("BUDGET_EXHAUSTED", attempts, fetch=fetch)
            if exc.stop:
                return _error("REQUEST_REJECTED", attempts, fetch=fetch)
            continue
        except Exception:
            return _error("INTERNAL", attempts, fetch=fetch)
        attempts.append({"provider": provider, "outcome": "success", "elapsed_s": round(time.monotonic() - started, 3)})
        return content, {"web_routing": {"status": "success", "provider": provider, "route": "pdf" if pdf else "default", "attempts": attempts}}
    return _error("ALL_PROVIDERS_FAILED", attempts, fetch=fetch)


_route = make_sync_tool_wrapper(_aroute, "web_router")


@tool("web_search", parse_docstring=True, response_format="content_and_artifact")
async def web_search_tool(query: str, runtime: Runtime = None) -> tuple[str, dict]:
    """Search the web for sources, using the configured primary and backup services.

    Args:
        query: Search keywords, between 1 and 128 Unicode characters.
    """
    return await _aroute("search", query, runtime)


@tool("web_fetch", parse_docstring=True, response_format="content_and_artifact")
async def web_fetch_tool(url: str) -> tuple[str, dict]:
    """Fetch a public URL as Markdown, routing PDFs to configured PDF services.
    Only fetch exact URLs supplied by the user or returned by web_search/web_fetch.

    Args:
        url: Complete public HTTP or HTTPS URL without credentials.
    """
    return await _aroute("fetch", url)


# Preserve direct synchronous use as well as the Agent native async path.
web_search_tool.func = make_sync_tool_wrapper(web_search_tool.coroutine, "web_search")
web_fetch_tool.func = make_sync_tool_wrapper(web_fetch_tool.coroutine, "web_fetch")
