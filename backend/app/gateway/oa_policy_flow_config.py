"""Environment-owned configuration for the Group OA policy-flow bridge."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    value = default if raw is None else int(raw)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True, slots=True)
class OAPolicyFlowConfig:
    enabled: bool
    ingest_key: str
    owner_user_id: str
    assistant_id: str
    callback_enabled: bool
    callback_url: str
    callback_bearer_token: str
    callback_signing_secret: str
    callback_ca_bundle: str | None
    poll_interval_seconds: int
    run_poll_seconds: int
    lease_seconds: int
    max_concurrent_jobs: int
    max_process_attempts: int
    max_callback_attempts: int
    callback_timeout_seconds: int
    run_timeout_seconds: int
    max_document_bytes: int
    max_result_bytes: int


def load_oa_policy_flow_config() -> OAPolicyFlowConfig:
    enabled = _truthy("OA_POLICY_FLOW_ENABLED")
    ingest_key = os.getenv("OA_POLICY_FLOW_INGEST_KEY", "").strip()
    owner_user_id = os.getenv("OA_POLICY_FLOW_OWNER_USER_ID", "").strip()
    assistant_id = os.getenv("OA_POLICY_FLOW_AGENT_NAME", "").strip()
    callback_enabled = _truthy("OA_POLICY_FLOW_CALLBACK_ENABLED", True)
    callback_url = os.getenv("OA_POLICY_FLOW_CALLBACK_URL", "").strip()
    callback_bearer_token = os.getenv("OA_POLICY_FLOW_CALLBACK_BEARER_TOKEN", "").strip()
    callback_signing_secret = os.getenv("OA_POLICY_FLOW_CALLBACK_SIGNING_SECRET", "").strip()
    callback_ca_bundle = os.getenv("OA_POLICY_FLOW_CALLBACK_CA_BUNDLE", "").strip() or None

    if enabled:
        missing = [
            name
            for name, value in (
                ("OA_POLICY_FLOW_INGEST_KEY", ingest_key),
                ("OA_POLICY_FLOW_OWNER_USER_ID", owner_user_id),
                ("OA_POLICY_FLOW_AGENT_NAME", assistant_id),
            )
            if not value
        ]
        if callback_enabled and not callback_url:
            missing.append("OA_POLICY_FLOW_CALLBACK_URL")
        if missing:
            raise ValueError(f"OA policy-flow integration is enabled but missing: {', '.join(missing)}")
        if len(ingest_key) < 32:
            raise ValueError("OA_POLICY_FLOW_INGEST_KEY must contain at least 32 characters")
        if callback_enabled:
            if not callback_bearer_token and not callback_signing_secret:
                raise ValueError("Configure an OA callback Bearer token, signing secret, or both")
            if callback_signing_secret and len(callback_signing_secret) < 32:
                raise ValueError("OA_POLICY_FLOW_CALLBACK_SIGNING_SECRET must contain at least 32 characters")
            parsed = urlsplit(callback_url)
            allow_http = _truthy("OA_POLICY_FLOW_CALLBACK_ALLOW_HTTP")
            if parsed.scheme not in ({"https", "http"} if allow_http else {"https"}) or not parsed.hostname:
                raise ValueError("OA_POLICY_FLOW_CALLBACK_URL must be a fixed HTTPS URL (HTTP requires the explicit allow flag)")
            if parsed.username or parsed.password or parsed.fragment:
                raise ValueError("OA_POLICY_FLOW_CALLBACK_URL must not contain credentials or a fragment")
            if callback_ca_bundle is not None and not Path(callback_ca_bundle).is_file():
                raise ValueError("OA_POLICY_FLOW_CALLBACK_CA_BUNDLE does not point to a readable file")

    return OAPolicyFlowConfig(
        enabled=enabled,
        ingest_key=ingest_key,
        owner_user_id=owner_user_id,
        assistant_id=assistant_id,
        callback_enabled=callback_enabled,
        callback_url=callback_url,
        callback_bearer_token=callback_bearer_token,
        callback_signing_secret=callback_signing_secret,
        callback_ca_bundle=callback_ca_bundle,
        poll_interval_seconds=_bounded_int("OA_POLICY_FLOW_POLL_SECONDS", 2, 1, 60),
        run_poll_seconds=_bounded_int("OA_POLICY_FLOW_RUN_POLL_SECONDS", 5, 1, 300),
        lease_seconds=_bounded_int("OA_POLICY_FLOW_LEASE_SECONDS", 90, 15, 900),
        max_concurrent_jobs=_bounded_int("OA_POLICY_FLOW_MAX_CONCURRENT_JOBS", 2, 1, 16),
        max_process_attempts=_bounded_int("OA_POLICY_FLOW_MAX_PROCESS_ATTEMPTS", 5, 1, 20),
        max_callback_attempts=_bounded_int("OA_POLICY_FLOW_MAX_CALLBACK_ATTEMPTS", 8, 1, 20),
        callback_timeout_seconds=_bounded_int("OA_POLICY_FLOW_CALLBACK_TIMEOUT_SECONDS", 30, 2, 120),
        run_timeout_seconds=_bounded_int("OA_POLICY_FLOW_RUN_TIMEOUT_SECONDS", 30 * 60, 60, 2 * 60 * 60),
        max_document_bytes=_bounded_int("OA_POLICY_FLOW_MAX_DOCUMENT_BYTES", 10 * 1024 * 1024, 1024, 10 * 1024 * 1024),
        max_result_bytes=_bounded_int("OA_POLICY_FLOW_MAX_RESULT_BYTES", 10 * 1024 * 1024, 1024, 50 * 1024 * 1024),
    )
