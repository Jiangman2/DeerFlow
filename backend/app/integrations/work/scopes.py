"""Explicit local UAT consent bundles and per-operation scope requirements.

Use the read/write pair in this Work deployment's actual permission catalogue,
not fine-grained names present only in some of its API document templates.
Never infer a read permission from a write grant.
"""

from collections.abc import Set

DOCUMENT_SCOPE = "docx:document"
CALENDAR_READ = "calendar:calendar:readonly"
CALENDAR_WRITE = "calendar:calendar"
CALENDAR_SCOPES = (CALENDAR_READ, CALENDAR_WRITE)
CALENDAR_AUTH_SCOPE = " ".join(CALENDAR_SCOPES)
CALENDAR_CONSENT_DESCRIPTION = "读取日历、查询日程、创建日程、编辑日程"

API_SCOPES = {
    **dict.fromkeys(("document_create", "document_info", "document_content", "blocks", "block_get", "block_update", "block_append"), DOCUMENT_SCOPE),
    "calendar_primary": CALENDAR_READ,
    **dict.fromkeys(("events", "event_get", "event_attendees"), CALENDAR_READ),
    "event_create": CALENDAR_WRITE,
    "event_update": CALENDAR_WRITE,
}

# Write admission also needs discovery and readback permissions before preview
# and again before execution, not only when the individual HTTP call runs.
ACTION_SCOPES = {
    **dict.fromkeys(("document_read", "document_create", "document_rename", "document_append", "document_replace"), DOCUMENT_SCOPE),
    "events_list": CALENDAR_READ,
    "event_create": CALENDAR_AUTH_SCOPE,
    "event_update": CALENDAR_AUTH_SCOPE,
}


def has_scopes(granted: Set[str], requested: str) -> bool:
    """Every requested scope must be present on this one identity-bound token."""
    required = frozenset(requested.split())
    return bool(required) and required.issubset(granted)
