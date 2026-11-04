"""Pod-local Work preflight. Default: no network, database, model, or writes.

Run with the Gateway Python/PYTHONPATH. --probe-network explicitly requests
two authentication/read-only bot-info checks; --database requests SELECT-only
PostgreSQL inspection. Neither option starts a receiver or a business task.
Output is allowlisted JSON, never configuration values or exception messages.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import hashlib
import hmac
import io
import ipaddress
import json
import logging
import os
import re
import stat
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

API_ORIGIN = "https://open.w.cnooc.com.cn"
ACCOUNT_ORIGIN = "https://accounts.w.cnooc.com.cn"
PRODUCTION_CALLBACK = (
    "https://cnoocclaw.cnooc.com.cn/api/integrations/work/oauth/callback"
)
PRODUCTION_DOCUMENT_HOST = "hnwork.w.cnooc.com.cn"
TOKEN_PATH = "/open-apis/auth/v3/tenant_access_token/internal"
BOT_PATH = "/open-apis/bot/v3/info"
MAX_BYTES = 65536
PROXY_NAMES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)


class UnsafeSnapshot(RuntimeError):
    """A fixed category; never include the failed input or underlying error."""


def digest(value: Any) -> str:
    def encode(item):
        if isinstance(item, (set, frozenset)):
            return sorted(item)
        raise TypeError("unsupported_fingerprint_input")

    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), default=encode
        ).encode()
    ).hexdigest()


def read_runtime(path: Path) -> dict:
    """Fresh bounded read, without creating directories or lock files.

    Existing cooperative writer locks are respected. Atomic replacement or an
    in-place change during the read fails instead of using an older cache.
    """
    lock_fd = None
    try:
        lock = path.with_name(path.name + ".lock")
        try:
            lock.lstat()
        except FileNotFoundError:
            pass
        else:
            import fcntl

            lock_fd = os.open(lock, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
                raise UnsafeSnapshot("runtime_unavailable")
            fcntl.flock(lock_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        try:
            initial = path.lstat()
        except FileNotFoundError:
            return {}
        if stat.S_ISLNK(initial.st_mode):
            raise UnsafeSnapshot("runtime_unavailable")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > 1048576:
                raise UnsafeSnapshot("runtime_unavailable")
            raw = stream.read(1048577)
            after = os.fstat(stream.fileno())
        current = path.stat(follow_symlinks=False)

        def signature(value):
            return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns

        if (
            signature(before) != signature(after)
            or signature(after) != signature(current)
            or len(raw) > 1048576
        ):
            raise UnsafeSnapshot("runtime_unavailable")
        data = json.loads(raw)
        if not isinstance(data, dict) or not all(
            isinstance(value, dict) for value in data.values()
        ):
            raise UnsafeSnapshot("runtime_unavailable")
        return data
    except Exception:
        raise UnsafeSnapshot("runtime_unavailable") from None
    finally:
        if lock_fd is not None:
            os.close(lock_fd)


@dataclass(frozen=True, repr=False)
class Snapshot:
    settings: Any
    credentials: dict
    database_url: str
    state_key: str
    runtime_present: bool
    channel_connections_enabled: bool
    bound_identity_required: bool


def load_snapshot() -> Snapshot:
    from app.channels.runtime_config_store import (
        ChannelRuntimeConfigStore,
        merge_runtime_channel_configs,
    )
    from app.integrations.work.config import (
        WorkSettings,
        apply_work_credential_defaults,
    )
    from deerflow.config.app_config import get_app_config
    from deerflow.config.paths import get_paths

    app_config = get_app_config()
    settings = WorkSettings.from_env()
    path = Path(get_paths().base_dir) / "channels/runtime-config.json"
    runtime = read_runtime(path)

    class ReadOnlyStore:
        def load_all(self):
            return {
                name: ChannelRuntimeConfigStore._policy(name, value)
                for name, value in runtime.items()
            }

    channels = dict((app_config.model_extra or {}).get("channels") or {})
    connections = getattr(app_config, "channel_connections", None)
    merge_runtime_channel_configs(channels, connections, store=ReadOnlyStore())
    apply_work_credential_defaults(channels)
    return Snapshot(
        settings,
        dict(channels.get("feishu") or {}),
        os.getenv("DATABASE_URL", ""),
        os.getenv("DEER_FLOW_WORK_STATE_KEY", ""),
        "feishu" in runtime,
        bool(connections and connections.enabled),
        bool(connections and connections.require_bound_identity),
    )


def decoded_key(snapshot: Snapshot) -> bytes | None:
    try:
        key = base64.b64decode(snapshot.state_key, altchars=b"-_", validate=True)
        return key if len(key) == 32 else None
    except Exception:
        return None


def namespaces(snapshot: Snapshot) -> tuple[str, str]:
    settings, config = snapshot.settings, snapshot.credentials
    app_id, secret = config.get("app_id", ""), config.get("app_secret", "")
    origins = repr(
        (
            settings.profile,
            settings.api_url,
            settings.authorize_url,
            settings.callback_url,
            settings.document_host,
        )
    )
    generation = hmac.new(
        secret.encode(), ("work-credential-generation:" + origins).encode(), "sha256"
    ).hexdigest()
    work = settings.profile + ":" + app_id + ":" + generation
    receiver = "receiver:" + settings.profile + ":" + settings.api_url + ":" + app_id
    return hashlib.sha256(work.encode()).hexdigest(), hashlib.sha256(
        receiver.encode()
    ).hexdigest()


def config_report(snapshot: Snapshot) -> dict:
    settings, config, key = (
        snapshot.settings,
        snapshot.credentials,
        decoded_key(snapshot),
    )
    app_id, secret = config.get("app_id", ""), config.get("app_secret", "")
    credentials_valid = all(
        isinstance(value, str)
        and 0 < len(value) <= 512
        and not any(ord(c) < 32 for c in value)
        for value in (app_id, secret)
    )
    checks = {
        "work_enabled": settings.enabled,
        "persistent": settings.persistent,
        "profile_production": settings.profile == "production",
        "profile_uat": settings.profile == "uat",
        "runtime_override_present": snapshot.runtime_present,
        "channel_enabled": config.get("enabled") is True,
        "runtime_disabled": config.get("_runtime_disabled") is True,
        "channel_connections_enabled": snapshot.channel_connections_enabled,
        "bound_identity_required": snapshot.bound_identity_required,
        "credentials_present_and_bounded": credentials_valid,
        "api_origin_approved": settings.api_url == API_ORIGIN,
        "authorize_origin_approved": urlsplit(settings.authorize_url).scheme == "https"
        and urlsplit(settings.authorize_url).netloc == "accounts.w.cnooc.com.cn",
        "channel_api_domain_matches": config.get("domain") == settings.api_url,
        "production_callback_matches": settings.callback_url == PRODUCTION_CALLBACK,
        "production_document_host_matches": settings.document_host
        == PRODUCTION_DOCUMENT_HOST,
        "state_key_valid_32_bytes": key is not None,
        "database_url_present": bool(snapshot.database_url),
        "owner_allowlist_configured": settings.allowed_owners is not None,
    }
    fingerprints = {
        "settings": digest(settings.__dict__),
        "api_origin": digest(settings.api_url),
        "callback": digest(settings.callback_url),
        "document_host": digest(settings.document_host),
        "owner_policy": digest(
            sorted(settings.allowed_owners)
            if settings.allowed_owners is not None
            else None
        ),
        "state_key": hashlib.sha256(key).hexdigest() if key is not None else None,
    }
    if credentials_valid:
        fingerprints["work_namespace"], fingerprints["receiver_namespace"] = namespaces(
            snapshot
        )
        fingerprints["app_id"] = digest(app_id)
        fingerprints["credentials"] = (
            hmac.new(key, (app_id + "\0" + secret).encode(), "sha256").hexdigest()
            if key is not None
            else None
        )
    try:
        from sqlalchemy.engine import make_url

        url = make_url(snapshot.database_url)
        checks["database_postgresql"] = url.get_backend_name() == "postgresql"
        fingerprints["database_target"] = digest(
            [url.get_backend_name(), url.host, url.port, url.database, url.username]
        )
    except Exception:
        checks["database_postgresql"] = False
        fingerprints["database_target"] = None
    return {
        "checks": checks,
        "fingerprints": fingerprints,
        "budgets": {
            "model_seconds": settings.turn_timeout,
            "queue_seconds": settings.turn_queue_timeout,
            "per_instance_concurrency": settings.concurrency,
        },
    }


def proxy_report(state_key: bytes | None = None) -> dict:
    environment = {name: os.getenv(name, "") for name in PROXY_NAMES}
    report = {
        "environment_present": {
            name: bool(value) for name, value in environment.items()
        },
        "environment_fingerprint": hmac.new(
            state_key,
            b"work-proxy-environment-v1:"
            + json.dumps(environment, sort_keys=True, separators=(",", ":")).encode(),
            "sha256",
        ).hexdigest()
        if isinstance(state_key, bytes) and len(state_key) == 32
        else None,
        "no_proxy_upper_lower_equal": environment["NO_PROXY"]
        == environment["no_proxy"],
        "httpx_probe_ignores_environment_proxies": True,
        "requests_routing_known": False,
        "websocket_routing_verified": False,
    }
    targets = {
        "tenant_token": ("POST", API_ORIGIN + TOKEN_PATH),
        "bot_info": ("GET", API_ORIGIN + BOT_PATH),
        "accounts_authorize": (
            "GET",
            ACCOUNT_ORIGIN + "/open-apis/authen/v1/authorize",
        ),
    }
    paths = dict.fromkeys(targets)
    report["requests_environment_paths"] = {
        name: request_proxy_selected(url) for name, (_, url) in targets.items()
    }
    report["requests_path_basis"] = "SESSION_MERGED_SETTINGS"
    try:
        import requests

        with requests.Session() as session:
            session.auth = lambda request: request
            report["requests_session_trust_env"] = session.trust_env is True
            report["requests_session_proxy_map_present"] = bool(session.proxies)
            for name, (method, url) in targets.items():
                _, _, paths[name] = prepare_requests(session, method, url)
    except Exception:
        pass
    report["requests_paths"] = paths
    report.update(
        requests_routing_known=all(type(value) is bool for value in paths.values()),
        requests_api_would_proxy=paths["bot_info"],
        requests_accounts_would_proxy=paths["accounts_authorize"],
    )
    return report


def request_proxy_selected(url: str) -> bool | None:
    """Compute Requests' environment selection for an exact API path; no I/O."""
    try:
        from requests.utils import get_environ_proxies, select_proxy

        return bool(select_proxy(url, get_environ_proxies(url)))
    except Exception:
        return None


def prepare_requests(session, method, url, payload=None, headers=None):
    """Sample the same effective proxy/TLS settings that send will consume."""
    import requests
    from requests.utils import select_proxy

    prepared = session.prepare_request(
        requests.Request(method, url, json=payload, headers=headers)
    )
    if prepared.url != url:
        raise UnsafeSnapshot("prepared_target_changed")
    merged = session.merge_environment_settings(prepared.url, {}, True, None, None)
    if not isinstance(merged, dict) or not isinstance(merged.get("proxies"), Mapping):
        raise UnsafeSnapshot("proxy_selection_unavailable")
    selected = bool(select_proxy(prepared.url, merged["proxies"]))
    return prepared, merged, selected


def denial_addresses(message: str) -> set[str]:
    addresses = set()
    for label in re.finditer(
        r"(?i)\b(?:client[_ -]?ip|source[_ -]?ip|ip)(?:\s+address)?\b", message
    ):
        tail = message[label.end() : label.end() + 128]
        punctuation = r"[\s:=：,，;；(（\[{【'\"<>-]*"
        tail = re.sub("^" + punctuation, "", tail)
        tail = re.sub(
            r"(?i)^(?:is\s+)?(?:denied|forbidden|blocked|rejected|invalid|not\s+allowed)\b"
            + punctuation,
            "",
            tail,
        )
        match = re.match(r"([0-9a-fA-F:.]{3,64})(?=$|[\s)）\]}】,，;；。'\"<>])", tail)
        if match:
            try:
                addresses.add(str(ipaddress.ip_address(match[1].rstrip("."))))
            except ValueError:
                pass
    return addresses


def response_status(status: int, data: Any, headers: dict | None = None) -> dict:
    """Only a 99991401 envelope may contribute strictly parsed IP/log ID."""
    code = data.get("code") if isinstance(data, dict) else None
    code = code if type(code) is int else None
    report = {
        "http_status": status if type(status) is int else None,
        "business_code": code,
    }
    if code != 99991401:
        return report
    message = data.get("msg", "")
    if isinstance(message, str) and len(message) <= 4096:
        addresses = denial_addresses(message)
        if len(addresses) == 1:
            report["observed_rejected_ip"] = addresses.pop()
            report["observed_ip_current_request_only"] = True
            report["stable_egress_verified"] = False
    error = data.get("error")
    candidates = [
        error.get("log_id") if isinstance(error, dict) else None,
        data.get("log_id"),
    ]
    candidates.extend(
        value
        for name, value in (headers or {}).items()
        if name.lower() in {"x-tt-logid", "x-lark-request-id"}
    )
    for value in candidates:
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{8,128}", value):
            report["log_id"] = value
            break
    return report


async def run_probe(snapshot: Snapshot, send) -> dict:
    if (
        snapshot.settings.api_url != API_ORIGIN
        or snapshot.credentials.get("domain") != API_ORIGIN
    ):
        return {"attempted": False, "approved_origin": False}
    config = snapshot.credentials
    if not config_report(snapshot)["checks"]["credentials_present_and_bounded"]:
        return {"attempted": False, "credentials_available": False}
    report = {
        "attempted": True,
        "approved_origin": True,
        "token": {},
        "bot_info": {"attempted": False},
    }
    try:
        status, data, headers = await send(
            "POST",
            TOKEN_PATH,
            {"app_id": config["app_id"], "app_secret": config["app_secret"]},
            {},
        )
        report["token"] = response_status(status, data, headers)
        token = data.get("tenant_access_token") if isinstance(data, dict) else None
        if (
            not 200 <= status < 300
            or not isinstance(data, dict)
            or data.get("code", 0) != 0
            or not isinstance(token, str)
            or not 1 <= len(token) <= 8192
            or any(ord(c) < 32 for c in token)
        ):
            return report
        status, data, headers = await send(
            "GET", BOT_PATH, None, {"Authorization": "Bearer " + token}
        )
        report["bot_info"] = {
            "attempted": True,
            **response_status(status, data, headers),
        }
    except Exception:
        report["transport_failed"] = True
    return report


async def httpx_probe(snapshot: Snapshot) -> dict:
    import httpx

    async with httpx.AsyncClient(
        trust_env=False, follow_redirects=False, timeout=httpx.Timeout(10, connect=5)
    ) as client:

        async def send(method, path, payload, headers):
            async with (
                asyncio.timeout(20),
                client.stream(
                    method,
                    API_ORIGIN + path,
                    json=payload,
                    headers=headers,
                    follow_redirects=False,
                ) as response,
            ):
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise UnsafeSnapshot("response_unavailable")
                return response.status_code, json.loads(body), dict(response.headers)

        report = await run_probe(snapshot, send)
        report["request_proxy_selection"] = {
            "tenant_token": False,
            "bot_info": False,
        }
        report["request_proxy_selection_basis"] = "HTTPX_TRUST_ENV_FALSE"
        return report


def requests_probe(snapshot: Snapshot) -> dict:
    import requests

    with requests.Session() as session:
        # Preserve Requests' default environment proxy and enterprise CA path,
        # while preventing application .netrc credentials from joining a probe.
        session.auth = lambda request: request
        selected = {}

        async def send(method, path, payload, headers):
            prepared, merged, chosen = prepare_requests(
                session, method, API_ORIGIN + path, payload, headers
            )
            selected["tenant_token" if path == TOKEN_PATH else "bot_info"] = chosen
            started = time.monotonic()
            with session.send(
                prepared,
                timeout=(5, 10),
                allow_redirects=False,
                **merged,
            ) as response:
                body = bytearray()
                for chunk in response.iter_content(4096):
                    body.extend(chunk)
                    if len(body) > MAX_BYTES or time.monotonic() - started > 20:
                        raise UnsafeSnapshot("response_unavailable")
                return response.status_code, json.loads(body), dict(response.headers)

        report = asyncio.run(run_probe(snapshot, send))
        report["request_proxy_selection"] = selected
        report["request_proxy_selection_basis"] = "SENT_MERGED_SETTINGS"
        return report


async def database_report(snapshot: Snapshot) -> dict:
    """No app bootstrap, session factory, grants, user rows, or schema changes."""
    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine

    url = make_url(snapshot.database_url)
    if url.get_backend_name() != "postgresql":
        return {"attempted": False, "postgresql_required": True}
    _, receiver_namespace = namespaces(snapshot)
    engine = create_async_engine(
        url.set(drivername="postgresql+asyncpg"),
        connect_args={
            "timeout": 5,
            "server_settings": {
                "default_transaction_read_only": "on",
                "statement_timeout": "5000",
                "lock_timeout": "1000",
            },
        },
    )
    try:
        async with asyncio.timeout(15), engine.connect() as connection:
            present = bool(
                await connection.scalar(
                    text("SELECT to_regclass('work_integration_records') IS NOT NULL")
                )
            )
            revisions = list(
                (
                    await connection.execute(
                        text("SELECT version_num FROM alembic_version LIMIT 2")
                    )
                ).scalars()
            )
            report = {
                "attempted": True,
                "read_only": True,
                "work_table_present": present,
                "alembic_head_expected": revisions == ["0022_work_integration_state"],
                "alembic_fingerprint": digest(sorted(revisions)),
                "receiver_lease_present": False,
            }
            if present:
                row = (
                    (
                        await connection.execute(
                            text(
                                "SELECT status, expires_at, updated_at, ciphertext FROM work_integration_records WHERE namespace=:ns AND kind='lease' AND key_hash=:key LIMIT 1"
                            ),
                            {
                                "ns": receiver_namespace,
                                "key": hashlib.sha256(b"receiver").hexdigest(),
                            },
                        )
                    )
                    .mappings()
                    .first()
                )
                if row:
                    report.update(
                        receiver_lease_present=True,
                        receiver_lease_unexpired=row["expires_at"] > time.time(),
                        receiver_updated_within_15s=time.time() - row["updated_at"]
                        <= 15,
                    )
                    try:
                        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

                        raw = base64.urlsafe_b64decode(row["ciphertext"])
                        aad = json.dumps(
                            [
                                receiver_namespace,
                                "lease",
                                hashlib.sha256(b"receiver").hexdigest(),
                                hashlib.sha256(b"").hexdigest(),
                            ],
                            separators=(",", ":"),
                        ).encode()
                        value = json.loads(
                            AESGCM(decoded_key(snapshot)).decrypt(
                                raw[:12], raw[12:], aad
                            )
                        )
                        report["receiver_transport_connected"] = (
                            value.get("connected") is True
                        )
                        report["receiver_business_code"] = (
                            value.get("business_code")
                            if type(value.get("business_code")) is int
                            else None
                        )
                    except Exception:
                        report["receiver_payload_verified"] = False
                    else:
                        report["receiver_payload_verified"] = True
            return report
    finally:
        await engine.dispose()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--probe-network",
        action="store_true",
        help="Explicitly request token + read-only bot-info for the two HTTP paths; no model or business operations.",
    )
    parser.add_argument(
        "--database",
        action="store_true",
        help="Inspect PostgreSQL using SELECT-only read-only connections; no bootstrap.",
    )
    args = parser.parse_args(argv)
    report = {
        "schema": "work-runtime-preflight.v1",
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "read_only": True,
        "configuration_loaded": False,
        "network_requested": args.probe_network,
        "database_requested": args.database,
        "production_acceptance_verified": False,
        "production_egress_ip_known": False,
    }
    disabled_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        try:
            snapshot = load_snapshot()
            report["configuration"] = config_report(snapshot)
            report["proxy"] = proxy_report(decoded_key(snapshot))
            report["configuration_loaded"] = True
        except Exception:
            pass
        if report["configuration_loaded"] and args.database:
            try:
                report["database"] = asyncio.run(database_report(snapshot))
            except Exception:
                report["database"] = {"attempted": True, "inspection_failed": True}
        if report["configuration_loaded"] and args.probe_network:
            report["network"] = {}
            for name, probe in (
                ("httpx_direct", lambda: asyncio.run(httpx_probe(snapshot))),
                ("requests_environment", lambda: requests_probe(snapshot)),
            ):
                try:
                    report["network"][name] = probe()
                except Exception:
                    report["network"][name] = {
                        "attempted": False,
                        "transport_failed": True,
                    }
    logging.disable(disabled_logging)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["configuration_loaded"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
