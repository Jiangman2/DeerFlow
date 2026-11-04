"""Per-run message provenance; channel labels never confer user authority."""

from collections.abc import Mapping
from typing import Any, Literal, NotRequired, TypedDict

# Match ChannelService's registered providers without importing its transports
# or manager into the Gateway's authentication boundary.
CHANNEL_PROVIDERS = frozenset({"dingtalk", "discord", "feishu", "github", "slack", "telegram", "wechat", "wecom"})


class MessageSource(TypedDict):
    kind: Literal["web", "im", "internal"]
    provider: NotRequired[str]


def stamp_message_source(config: dict[str, Any], *, internal: bool, request_context: Mapping[str, Any] | None = None) -> None:
    """Replace caller/checkpoint labels using only this request's trusted input.

    ``internal`` comes from AuthMiddleware, never from a submitted context.
    The top-level internal channel name is an assertion by a trusted channel
    adapter, not evidence that its sender is bound or has OAuth permission.
    Browser/API sessions are both ``web``; internal jobs without a registered
    provider remain ``internal`` and must not be mistaken for Work messages.
    """
    for section in ("context", "configurable"):
        values = config.get(section)
        if isinstance(values, dict):
            values.pop("channel_name", None)
            values.pop("channel_source", None)
            values.pop("message_source", None)
            values.pop("work_capabilities", None)

    context = config.setdefault("context", {})
    if not isinstance(context, dict):
        raise TypeError("run context must be a mapping")

    provider = request_context.get("channel_name") if internal is True and isinstance(request_context, Mapping) else None
    if not isinstance(provider, str) or provider not in CHANNEL_PROVIDERS:
        provider = None
    source: MessageSource = {"kind": "im", "provider": provider} if provider else {"kind": "internal" if internal is True else "web"}
    context["message_source"] = source
    # Explicit None also shadows a legacy configurable value a checkpoint may
    # carry when the same IM-origin thread is continued through the web UI.
    context["channel_name"] = provider
    # Availability is a channel adapter hint, not a grant or an execution tool.
    # Keep None explicit so a checkpoint cannot supply an older hint instead.
    context["work_capabilities"] = None
    capabilities = request_context.get("work_capabilities") if provider == "feishu" and isinstance(request_context, Mapping) else None
    if isinstance(capabilities, Mapping) and set(capabilities) == {"available"} and type(capabilities["available"]) is bool:
        context["work_capabilities"] = {"available": capabilities["available"]}
