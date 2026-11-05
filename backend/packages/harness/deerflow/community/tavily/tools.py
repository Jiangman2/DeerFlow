import json
import os
import ssl

import requests
from langchain.tools import tool
from requests.adapters import HTTPAdapter
from tavily import TavilyClient

from deerflow.community.url_safety import validate_cloud_fetch_url
from deerflow.community.web_fetch_output import content_limit, format_page
from deerflow.config import get_app_config


class _TavilyTLSAdapter(HTTPAdapter):
    """Mount one verified SSL context on Tavily HTTPS and proxy pools."""

    def __init__(self, ssl_context: ssl.SSLContext, *args, **kwargs):
        self._tavily_ssl_context = ssl_context
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = self._tavily_ssl_context
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        proxy_kwargs["ssl_context"] = self._tavily_ssl_context
        return super().proxy_manager_for(proxy, **proxy_kwargs)


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _build_tavily_session() -> requests.Session | None:
    """Build a scoped verified TLS session for legacy enterprise proxies."""

    if not _truthy_env("TAVILY_TLS_COMPAT"):
        return None

    ca_file = next(
        (
            value
            for name in (
                "REQUESTS_CA_BUNDLE",
                "SSL_CERT_FILE",
                "CURL_CA_BUNDLE",
            )
            if (value := os.getenv(name, "").strip()) and os.path.isfile(value)
        ),
        None,
    )
    context = ssl.create_default_context(cafile=ca_file) if ca_file else ssl.create_default_context()
    context.set_ciphers("DEFAULT:@SECLEVEL=1")

    session = requests.Session()
    session.mount("https://", _TavilyTLSAdapter(context))
    return session


def _get_tavily_client(tool_name: str = "web_search") -> TavilyClient:
    config = get_app_config().get_tool_config(tool_name)
    api_key = None
    if config is not None and "api_key" in config.model_extra:
        api_key = config.model_extra.get("api_key")
    session = _build_tavily_session()
    if session is None:
        return TavilyClient(api_key=api_key)
    return TavilyClient(api_key=api_key, session=session)


@tool("web_search", parse_docstring=True)
def web_search_tool(query: str) -> str:
    """Search the web.

    Args:
        query: The query to search for.
    """
    config = get_app_config().get_tool_config("web_search")
    max_results = 5
    if config is not None and "max_results" in config.model_extra:
        max_results = config.model_extra.get("max_results")

    client = _get_tavily_client()
    res = client.search(query, max_results=max_results)
    normalized_results = [
        {
            "title": result["title"],
            "url": result["url"],
            "snippet": result["content"],
        }
        for result in res["results"]
    ]
    json_results = json.dumps(normalized_results, indent=2, ensure_ascii=False)
    return json_results


@tool("web_fetch", parse_docstring=True)
def web_fetch_tool(url: str) -> str:
    """Fetch the contents of a web page at a given URL.
    Only fetch EXACT URLs that have been provided directly by the user or have been returned in results from the web_search and web_fetch tools.
    This tool can NOT access content that requires authentication, such as private Google Docs or pages behind login walls.
    Do NOT add www. to URLs that do NOT have them.
    URLs must include the schema: https://example.com is a valid URL while example.com is an invalid URL.

    Args:
        url: The URL to fetch the contents of.
    """
    error = validate_cloud_fetch_url(url)
    if error:
        return error
    config = get_app_config().get_tool_config("web_fetch")
    limit = content_limit(config.model_extra if config else None)
    client = _get_tavily_client("web_fetch")
    res = client.extract([url])
    if "failed_results" in res and len(res["failed_results"]) > 0:
        return f"Error: {res['failed_results'][0]['error']}"
    elif "results" in res and len(res["results"]) > 0:
        result = res["results"][0]
        return format_page(result.get("title"), result.get("raw_content"), limit)
    else:
        return "Error: No results found"
