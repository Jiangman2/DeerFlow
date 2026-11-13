"""Build current-conversation links from host identity and operator configuration."""

import os
import re
from urllib.parse import urlsplit

from deerflow.config import get_app_config
from deerflow.utils.thread_id import validate_thread_id


def validated_web_origin(value: str | None, *, profile: str = "production") -> str:
    """Apply the Work notification origin rules without trusting request headers."""
    if not isinstance(value, str):
        return ""
    raw = value.strip()
    if not raw or re.search(r"[\s\\]", raw):
        return ""
    try:
        parsed = urlsplit(raw)
        # Accessing port also rejects malformed and out-of-range ports.
        parsed.port
        local = profile == "uat" and parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
        if not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            return ""
        return raw.rstrip("/") if parsed.scheme == "https" or local else ""
    except ValueError:
        return ""


def current_conversation_url(thread_id: str | None) -> str:
    """Use only the host's current thread and an explicitly configured frontend."""
    try:
        thread = validate_thread_id(thread_id)
    except ValueError:
        return ""

    origin = ""
    profile = os.getenv("DEER_FLOW_WORK_PROFILE", "production").strip()
    try:
        auth = get_app_config().auth
        for provider in (auth.iam, auth.oidc):
            if provider.enabled and provider.frontend_base_url:
                # A configured but invalid origin must not silently select another site.
                origin = validated_web_origin(provider.frontend_base_url, profile=profile)
                return f"{origin}/workspace/chats/{thread}" if origin else ""
    except Exception:
        # Link enrichment must never make a successful file publication fail.
        # Do not expose configuration (which may contain credentials) in diagnostics.
        return ""

    origin = validated_web_origin(os.getenv("DEER_FLOW_WORK_SCHEDULED_NOTIFICATION_WEB_URL"), profile=profile)
    return f"{origin}/workspace/chats/{thread}" if origin else ""


def energy_daily_files(paths: list[str]) -> bool:
    """Use the actual publication's complete file set to choose a daily label."""
    extensions = set()
    names = set()
    for path in paths:
        match = re.fullmatch(r"(?:.*/)?(油气与金融市场日报_\d{4}-\d{2}-\d{2})\.(png|html|json|txt)", path)
        if match:
            names.add(match.group(1))
            extensions.add(match.group(2))
    return len(names) == 1 and extensions == {"png", "html", "json", "txt"}


def append_conversation_link(text: str, url: str, *, daily: bool = False, delivery_failed: bool = False) -> str:
    """Put the host-owned link last, moving any existing occurrence there."""
    if not url:
        return text
    escaped = re.escape(url)
    standalone = re.compile(r"(?:查看完整结果[:：]\s*)?(?:" + escaped + r"|<" + escaped + r">|\[[^\]\n]*\]\(" + escaped + r"\))")
    # These lines claim to identify this conversation. They are not research
    # citations: replace them using host identity, including a guessed drive URL.
    claimed = re.compile(r"^\s*(?:[-*>]\s*)?(?:平台入口|会话ID|会话链接|当前会话链接|完整日报链接|点击查看完整日报)\s*[:：].*$", re.IGNORECASE)
    own_footers = {f"[点击查看完整日报]({url})", f"文件交付未完成。[查看本次对话]({url})"}
    lines = [line for line in text.splitlines() if line.strip() not in own_footers and not standalone.fullmatch(line.strip()) and not claimed.fullmatch(line)]
    body = "\n".join(lines)
    # Keep the label/prose of inline references while moving their destination
    # into the final footer. Never rewrite a different thread or signed URL.
    body = re.sub(r"\[([^\]\n]*)\]\(" + escaped + r"\)", lambda match: match.group(1) if match.group(1) != url else "本次对话", body)
    body = body.replace(f"<{url}>", "本次对话")
    body = re.sub(escaped + r"(?=$|[\s<>\)\]。！，,;；])", "本次对话", body).rstrip()
    if delivery_failed:
        footer = f"文件交付未完成。[查看本次对话]({url})"
    elif daily:
        footer = f"[点击查看完整日报]({url})"
    else:
        footer = f"查看完整结果：{url}"
    return f"{body}\n\n{footer}" if body.strip() else footer
