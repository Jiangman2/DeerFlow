"""Shared command definitions used by all channel implementations.

Keeping the authoritative command set in one place ensures that channel
parsers (e.g. Feishu) and the ChannelManager dispatcher stay in sync
automatically — adding or removing a command here is the single edit
required.
"""

from __future__ import annotations

import re

KNOWN_CHANNEL_COMMANDS: frozenset[str] = frozenset(
    {
        "/bootstrap",
        "/goal",
        "/new",
        "/status",
        "/models",
        "/memory",
        "/help",
    }
)


_LEADING_MENTION = re.compile(r"(?:@[^\s<>@]+|<@!?[^\s<>|]+(?:\|[^\s<>]+)?>)")


def strip_leading_mentions(text: str) -> str:
    """Drop only complete, flush-leading platform mentions, not chat prefixes."""
    while text and not text[0].isspace():
        parts = text.split(maxsplit=1)
        if not _LEADING_MENTION.fullmatch(parts[0]):
            break
        text = parts[1] if len(parts) == 2 else ""
    return text


def extract_connect_code(text: str) -> str | None:
    """Extract the one-time channel binding code from a connect command."""
    parts = strip_leading_mentions(text.strip()).split()
    if len(parts) < 2:
        return None
    command = parts[0].lower()
    if command == "/connect":
        return parts[1]
    return None


def is_known_channel_command(text: str) -> bool:
    """Return whether text starts with a registered channel control command."""
    if not text.startswith("/"):
        return False
    return text.split(maxsplit=1)[0].lower() in KNOWN_CHANNEL_COMMANDS
