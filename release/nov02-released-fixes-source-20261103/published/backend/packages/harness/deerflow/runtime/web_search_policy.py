"""Per-run opt-in for the browser's public web tools (not a network firewall)."""

from collections.abc import Mapping

WEB_TOOLS = frozenset({"web_search", "web_fetch"})
WEB_SEARCH_KEY = "web_search_enabled"


def web_search_enabled(context) -> bool:
    # Existing IM/scheduled/internal callers do not carry the browser toggle.
    # New browser requests always send an explicit boolean, defaulting to False.
    if not isinstance(context, Mapping) or WEB_SEARCH_KEY not in context:
        return True
    return context[WEB_SEARCH_KEY] is True


def filter_web_tools(tools, context):
    if web_search_enabled(context):
        return list(tools)
    return [tool for tool in tools if (tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", None)) not in WEB_TOOLS]
