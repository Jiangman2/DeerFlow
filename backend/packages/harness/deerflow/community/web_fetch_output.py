"""Shared, deployment-controlled body budget for web fetch providers."""


def content_limit(extras: dict | None) -> int:
    value = (extras or {}).get("max_characters", 4096)
    try:
        if isinstance(value, bool):
            raise ValueError("boolean is not a character limit")
        return max(1, min(int(value), 100000))
    except (TypeError, ValueError, OverflowError):
        return 4096


def format_page(title: str | None, content: str | None, limit: int) -> str:
    if not isinstance(content, str) or not content.strip():
        return "Error: No page content returned"
    suffix = f"\n\n[Content truncated to {limit} characters]" if len(content) > limit else ""
    return f"# {title or 'Untitled'}\n\n{content[:limit]}{suffix}"
