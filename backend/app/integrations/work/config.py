"""Operator-owned Work profile, independent of administrator-editable credentials."""

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

PRODUCTION_CALLBACK = "https://cnoocclaw.cnooc.com.cn/api/integrations/work/oauth/callback"
BROWSER_AUTHORIZE_PATH = "/api/integrations/work/oauth/authorize"
BROWSER_CALLBACK_PATH = "/api/integrations/work/oauth/callback"
UAT_APP_ID = "cli_aaec76b56d38d3c7"


def _url(value: str, *, loopback=False) -> str:
    parsed = urlsplit(value)
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Invalid Work endpoint configuration")
    if parsed.scheme != "https" and not (loopback and parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}):
        raise ValueError("Work endpoints require HTTPS")
    if not loopback and parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Production Work callback cannot be loopback")
    return value.rstrip("/")


def _integer(name, default, minimum, maximum):
    value = int(os.getenv(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError("Invalid Work capacity configuration")
    return value


@dataclass(frozen=True)
class WorkSettings:
    enabled: bool = False
    profile: str = "production"
    api_url: str = "https://open.w.cnooc.com.cn"
    authorize_url: str = "https://accounts.w.cnooc.com.cn/open-apis/authen/v1/authorize"
    callback_url: str = PRODUCTION_CALLBACK
    document_host: str = "hnwork.w.cnooc.com.cn"
    authorize_entry: str = "https://cnoocclaw.cnooc.com.cn" + BROWSER_AUTHORIZE_PATH
    allowed_owners: frozenset[str] | None = None
    persistent: bool = True
    max_jobs: int = 32
    per_user_jobs: int = 4
    concurrency: int = 2

    @classmethod
    def local_uat(cls):
        return cls(enabled=True, profile="uat", callback_url="http://localhost:10086/auth/callback", document_host="haiyoutest.w.cnooc.com.cn", authorize_entry="http://localhost:10086/authorize", persistent=False)

    @classmethod
    def from_env(cls):
        legacy = os.getenv("DEER_FLOW_WORK_DOCS_UAT_ENABLED") == "true"
        profile = os.getenv("DEER_FLOW_WORK_PROFILE", "uat" if legacy else "production").strip()
        if profile not in {"production", "uat"}:
            raise ValueError("Work profile must be production or uat")
        enabled = os.getenv("DEER_FLOW_WORK_ENABLED", "true" if legacy else "false") == "true"
        default = cls.local_uat() if profile == "uat" else cls()
        prefix = "DEER_FLOW_WORK_" + profile.upper() + "_"
        callback = _url(os.getenv(prefix + "CALLBACK_URL", default.callback_url), loopback=profile == "uat")
        api = _url(os.getenv(prefix + "API_URL", default.api_url))
        if urlsplit(api).path:
            raise ValueError("Work API must be an origin")
        authorize = _url(os.getenv(prefix + "AUTHORIZE_URL", default.authorize_url))
        host = os.getenv(prefix + "DOCUMENT_HOST", default.document_host).strip().lower()
        if host and (urlsplit("https://" + host).netloc != host or any(c in host for c in "/:@?# ")):
            raise ValueError("Work document host must be an exact hostname")
        owners = frozenset(x.strip() for x in os.getenv("DEER_FLOW_WORK_ALLOWED_OWNER_IDS", os.getenv("DEER_FLOW_WORK_DOCS_ALLOWED_OWNER_IDS", "")).split(",") if x.strip())
        if enabled and not host:
            raise ValueError("Work requires an exact document host")
        if profile == "production" and host == "haiyoutest.w.cnooc.com.cn":
            raise ValueError("Production must not use the UAT document host")
        entry = os.getenv(prefix + "AUTHORIZE_ENTRY", "")
        if not entry:
            parsed = urlsplit(callback)
            entry = default.authorize_entry if callback == default.callback_url else f"{parsed.scheme}://{parsed.netloc}{BROWSER_AUTHORIZE_PATH}"
        entry = _url(entry, loopback=profile == "uat")
        persistent = profile == "production" or os.getenv("DEER_FLOW_WORK_STORE", "memory") == "sql"
        return cls(
            enabled,
            profile,
            api,
            authorize,
            callback,
            host,
            entry,
            owners or None,
            persistent,
            _integer("DEER_FLOW_WORK_MAX_JOBS", 32, 1, 1000),
            _integer("DEER_FLOW_WORK_PER_USER_JOBS", 4, 1, 100),
            _integer("DEER_FLOW_WORK_CONCURRENCY", 2, 1, 32),
        )


def apply_work_credential_defaults(channels: dict):
    """UI/runtime overlay wins. Environment is a bootstrap fallback, never an override."""
    profile = os.getenv("DEER_FLOW_WORK_PROFILE", "uat" if os.getenv("DEER_FLOW_WORK_DOCS_UAT_ENABLED") == "true" else "production")
    if profile not in {"production", "uat"}:
        return
    prefix = "DEER_FLOW_WORK_" + profile.upper() + "_"
    app_id, secret = os.getenv(prefix + "APP_ID", ""), os.getenv(prefix + "APP_SECRET", "")
    current = channels.get("feishu")
    if current and (current.get("_runtime_disabled") or current.get("app_id") or current.get("app_secret")):
        selected = "DEER_FLOW_WORK_PROFILE" in os.environ or os.getenv("DEER_FLOW_WORK_DOCS_UAT_ENABLED") == "true" or os.getenv("DEER_FLOW_WORK_ENABLED") == "true"
        if selected and not current.get("_runtime_disabled") and (current.get("app_id") or current.get("app_secret")) and not current.get("domain"):
            channels["feishu"] = {**current, "domain": os.getenv(prefix + "API_URL", "https://open.w.cnooc.com.cn")}
        return
    if app_id and secret:
        channels["feishu"] = {**(current or {}), "enabled": True, "app_id": app_id, "app_secret": secret, "domain": os.getenv(prefix + "API_URL", "https://open.w.cnooc.com.cn")}
