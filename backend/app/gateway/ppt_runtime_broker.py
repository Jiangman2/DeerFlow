"""Internal PPT research/receipt broker. No browser auth, keys or arbitrary code.

Mounted under the already self-authenticated enterprise relay router. A Runner
may mint a short-lived capability only for an existing PPT run. Provider keys
stay in Gateway. Numeric receipts reuse RunEventStore, never a parallel invoice.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import socket
import ssl
import time
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

_slots = asyncio.Semaphore(4)
_budget_lock = asyncio.Lock()
EVENT = "ppt.provider_receipt"
RESEARCH = "ppt.research_request"
RESEARCH_RESULT = "ppt.research_result"


class ResearchFailure(ValueError):
    """A locally classified failure; the category is never provider prose."""


def research_error_category(error):
    pending, seen, kinds = [error], set(), set()
    while pending and len(seen) < 16:
        item = pending.pop()
        if not isinstance(item, BaseException) or id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, ResearchFailure):
            return str(item) if str(item) in {"invalid_response", "extract_failed", "invalid_arguments"} else "unknown_research_failure"
        if isinstance(item, ssl.SSLCertVerificationError):
            return "tls_verification"
        if isinstance(item, TimeoutError):
            kinds.add("network_timeout")
        module, name = type(item).__module__, type(item).__name__
        if module.startswith(("requests.", "urllib3.", "httpx.", "httpcore.")):
            if name == "SSLError":
                kinds.add("tls_error")
            if name == "ProxyError":
                kinds.add("proxy_connection")
            if name in {"Timeout", "ConnectTimeout", "ReadTimeout", "ConnectTimeoutError", "ReadTimeoutError", "PoolTimeout"}:
                kinds.add("network_timeout")
            if name in {"ConnectionError", "ConnectError", "NewConnectionError", "RemoteProtocolError"}:
                kinds.add("network_connection")
            status = getattr(getattr(item, "response", None), "status_code", None)
            if type(status) is int:
                kinds.add({401: "provider_auth", 403: "provider_auth", 429: "provider_rate_limit", 432: "provider_quota"}.get(status, "provider_http"))
        if module.startswith("tavily."):
            if name == "InvalidAPIKeyError":
                kinds.add("provider_auth")
            elif name == "UsageLimitExceededError":
                # This SDK type covers more than one HTTP status. No guess
                # about credits versus rate limiting from an exception name.
                kinds.add("provider_limit_unknown")
        pending.extend([item.__cause__, item.__context__, getattr(item, "reason", None)])
        pending.extend(arg for arg in item.args[:8] if isinstance(arg, BaseException))
    for category in ("tls_error", "proxy_connection", "network_timeout", "network_connection", "provider_auth", "provider_quota", "provider_rate_limit", "provider_limit_unknown", "provider_http"):
        if category in kinds:
            return category
    return "unknown_research_failure"


async def _research_outcome(store, scope, name, attempt_id, started, category):
    content = {"name": name, "attempt_id": attempt_id, "outcome": "failed" if category else "success", "error_category": category, "elapsed_ms": min(86400000, max(0, int((time.monotonic() - started) * 1000)))}
    try:
        with _receipt_owner_context(scope):
            await asyncio.wait_for(store.put(thread_id=scope["thread"], run_id=scope["run"], event_type=RESEARCH_RESULT, category="tool", content=content, metadata={"task_id": scope["run"]}), timeout=2)
    except Exception:
        # Receipt availability must not repeat a provider call or turn useful
        # research into an opaque failure. Never log the underlying exception.
        logging.getLogger(__name__).warning("PPT research outcome persistence unavailable")


def _key():
    value = os.environ.get("PPT_MASTER_RESPONSES_RELAY_KEY", "")
    if len(value) < 16:
        raise HTTPException(503, "PPT relay credential unavailable")
    return value.encode()


def _id(value):
    try:
        valid = isinstance(value, str) and str(uuid.UUID(value)) == value
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(400, "Invalid PPT scope")
    return value


def encode_scope(run_id, thread_id, *, now=None):
    now = int(time.time() if now is None else now)
    raw = json.dumps({"run": _id(run_id), "thread": _id(thread_id), "exp": now + 12 * 3600, "aud": "ppt-runtime-v1"}, sort_keys=True, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    signature = hmac.new(_key(), b"ppt-capability:" + body.encode(), hashlib.sha256).hexdigest()
    return body + "." + signature


def decode_scope(value, *, now=None):
    try:
        if not isinstance(value, str) or len(value) > 1024:
            raise ValueError()
        body, signature = value.split(".")
        expected = hmac.new(_key(), b"ppt-capability:" + body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        scope = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        clock = int(time.time() if now is None else now)
        if set(scope) != {"run", "thread", "exp", "aud"} or scope["aud"] != "ppt-runtime-v1":
            raise ValueError()
        if type(scope["exp"]) is not int or not clock < scope["exp"] <= clock + 12 * 3600:
            raise ValueError()
        _id(scope["run"])
        _id(scope["thread"])
        return scope
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise HTTPException(401, "Invalid PPT capability") from None


def _valid_owner(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 64 or value.strip() != value or any(ord(c) < 32 for c in value):
        raise HTTPException(403, "PPT run owner unavailable")
    return value


async def _service_run_owner(request, run_id, thread_id):
    """Resolve ONE authenticated service scope; never disable global isolation.

    Callers must first verify the internal key or signed capability. The sole
    unfiltered read bootstraps ownership from an exact primary-key run, never
    from a browser/header user_id. It exposes no row content. All following
    reads/writes use that owner, and the authoritative thread must match too.
    """
    from app.gateway.deps import get_run_store, get_thread_store

    run_id, thread_id = _id(run_id), _id(thread_id)
    row = await get_run_store(request).get(run_id, user_id=None)
    if not isinstance(row, dict) or row.get("run_id") != run_id or row.get("thread_id") != thread_id or row.get("assistant_id") != "ppt-master-runner":
        raise HTTPException(403, "PPT run scope unavailable")
    owner = _valid_owner(row.get("user_id"))
    thread = await get_thread_store(request).get(thread_id, user_id=owner)
    if not isinstance(thread, dict) or thread.get("thread_id") != thread_id or thread.get("user_id") != owner:
        raise HTTPException(403, "PPT thread owner unavailable")
    return row, owner


@contextmanager
def _receipt_owner_context(scope):
    """Restore previous context on success, error AND cancellation.

    This surrounds each actual store operation, including deferred streaming
    writes; it does not assume the HTTP handler's task survives into SSE.
    The scope is internal state returned by require_scope, not a JSON body.
    """
    from deerflow.runtime.user_context import reset_current_user, set_current_user

    owner = _valid_owner(scope.get("owner"))
    token = set_current_user(SimpleNamespace(id=owner))
    try:
        yield
    finally:
        reset_current_user(token)


async def require_scope(request, *, active=True):
    scope = decode_scope(request.headers.get("X-Ppt-Capability"))
    # Signature/expiry are verified before the service-scoped record lookup.
    row, owner = await _service_run_owner(request, scope["run"], scope["thread"])
    if active and (row.get("status") not in {"pending", "running"} or row.get("cancel_action")):
        raise HTTPException(409, "PPT run no longer active")
    return {**scope, "owner": owner}


async def bounded_body(request, limit=8192):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise HTTPException(413, "PPT request too large")
    try:
        body = json.loads(data)
    except (ValueError, UnicodeError):
        raise HTTPException(400, "Invalid PPT request") from None
    if not isinstance(body, dict):
        raise HTTPException(400, "Invalid PPT request")
    return body


async def mint(request: Request):
    secret = os.environ.get("SANDBOX_API_KEY", "")
    if not secret or not hmac.compare_digest(request.headers.get("Authorization", ""), "Bearer " + secret):
        raise HTTPException(401, "Internal authentication required")
    body = await bounded_body(request)
    if set(body) != {"run_id", "thread_id"}:
        raise HTTPException(400, "Invalid PPT scope")
    run_id, thread_id = _id(body["run_id"]), _id(body["thread_id"])
    # The service credential above is the authentication boundary, not a
    # browser session or a caller-selected user_id.
    row, owner = await _service_run_owner(request, run_id, thread_id)
    if row.get("status") not in {"pending", "running"} or row.get("cancel_action"):
        raise HTTPException(403, "PPT run scope unavailable")
    metadata = {}
    # Availability is a deployment gate, NOT global consent. Activate only
    # after all candidate runtimes and the additive migration are ready.
    if os.environ.get("PPT_CONFIRMATION_TRANSPORT_ENABLED") == "true":
        snapshot = await admit_confirmation(request, row, owner)
        if snapshot is not None:
            metadata["confirmation_protocol"] = "ppt-confirmation-v1"
    return JSONResponse({"capability": encode_scope(run_id, thread_id), **metadata}, headers={"Cache-Control": "no-store"})


async def admit_confirmation(request, row, owner):
    from app.gateway.deps import get_run_store
    from deerflow.persistence import get_session_factory
    from deerflow.persistence.ppt_confirmation.sql import ConfirmationRepository, Scope
    from deerflow.ppt_confirmation.admission import admit_initial_task
    from deerflow.ppt_confirmation.lifecycle import ConsentConflict

    factory = get_session_factory()
    if factory is None:
        raise HTTPException(503, "Confirmation store unavailable")
    try:
        return await admit_initial_task(Scope(owner, row["thread_id"], row["run_id"]), row, run_store=get_run_store(request), repository=ConfirmationRepository(factory))
    except ConsentConflict:
        raise HTTPException(409, "Confirmation requires an eligible new PPT task") from None


async def refresh_confirmation_capability(request):
    """Renew transport for an already-admitted live task, never renew consent.

    Human waiting can outlive a capability. The Host service credential and
    fresh run/thread ownership plus existing SQL admission are all required.
    No state is created, reset or resumed by this read-only operation.
    """
    from deerflow.persistence import get_session_factory
    from deerflow.persistence.ppt_confirmation.sql import ConfirmationRepository, Scope

    secret = os.environ.get("SANDBOX_API_KEY", "")
    if not secret or not hmac.compare_digest(request.headers.get("Authorization", ""), "Bearer " + secret):
        raise HTTPException(401, "Internal authentication required")
    body = await bounded_body(request)
    if set(body) != {"run_id", "thread_id"}:
        raise HTTPException(400, "Invalid PPT scope")
    run_id, thread_id = _id(body["run_id"]), _id(body["thread_id"])
    row, owner = await _service_run_owner(request, run_id, thread_id)
    if row.get("status") not in {"pending", "running"} or row.get("cancel_action"):
        raise HTTPException(409, "PPT run no longer active")
    factory = get_session_factory()
    if factory is None:
        raise HTTPException(503, "Confirmation store unavailable")
    snapshot = await ConfirmationRepository(factory).get(Scope(owner, thread_id, run_id))
    if snapshot is None or snapshot["cancelled"]:
        raise HTTPException(409, "Confirmation admission unavailable")
    return JSONResponse({"capability": encode_scope(run_id, thread_id), "thread_id": thread_id, "task_id": run_id, "confirmation_protocol": "ppt-confirmation-v1"}, headers={"Cache-Control": "no-store"})


def numeric_usage(raw):
    if not isinstance(raw, dict):
        return None
    values = [raw.get("prompt_tokens", raw.get("input_tokens")), raw.get("completion_tokens", raw.get("output_tokens"))]
    if any(type(v) is not int or not 0 <= v <= 10**9 for v in values):
        return None
    input_tokens, output_tokens = values
    total = raw.get("total_tokens", input_tokens + output_tokens)
    if type(total) is not int or total != input_tokens + output_tokens:
        return None
    details = raw.get("input_tokens_details")
    cached = raw.get("prompt_cache_hit_tokens", details.get("cached_tokens", 0) if isinstance(details, dict) else 0)
    if type(cached) is not int or not 0 <= cached <= input_tokens:
        return None
    return {"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": total, "cache_read_tokens": cached}


async def persist_provider_receipt(request, scope, response_id, attempt, usage, *, model):
    from app.gateway.deps import get_run_event_store

    numeric = numeric_usage(usage)
    # Unknown cost is an audit receipt, not an invented zero-Token LLM call.
    source = "ppt-relay:" + scope["run"] + ":" + response_id + ":" + str(attempt)
    content = {
        "source_run_id": source,
        "task_id": scope["run"],
        "thread_id": scope["thread"],
        "model_name": model,
        "usage_state": "reported" if numeric else "unknown",
        "caller": "subagent:ppt-master-agent-host",
        "llm_call_count": 1,
        "scope": "current_run_all_codex_agents_not_session_cumulative",
    }
    if numeric:
        content.update(numeric)
    # Await persistence BEFORE publishing function calls or starting correction.
    with _receipt_owner_context(scope):
        await get_run_event_store(request).put(thread_id=scope["thread"], run_id=scope["run"], event_type=EVENT, category="llm", content=content, metadata={"task_id": scope["run"]})


async def receipts(request: Request, after_seq: int = 0):
    from app.gateway.deps import get_run_event_store

    scope = await require_scope(request, active=False)
    if not 0 <= after_seq < 2**63:
        raise HTTPException(400, "Invalid receipt cursor")
    with _receipt_owner_context(scope):
        rows = await get_run_event_store(request).list_events(scope["thread"], scope["run"], event_types=[EVENT], limit=500, after_seq=after_seq)
    return JSONResponse({"run_id": scope["run"], "thread_id": scope["thread"], "rows": rows}, headers={"Cache-Control": "no-store"})


def public_url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 33 for c in value):
        raise ValueError("public_url_required")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.port not in {None, 80, 443}:
        raise ValueError("public_url_required")
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("public_url_required")
    return value


def configured_tool(name):
    from deerflow.config import get_app_config
    from deerflow.reflection import resolve_variable

    config = get_app_config().get_tool_config(name)
    # Only the native vetted Tavily tools; no arbitrary reflected operator code.
    expected = "deerflow.community.tavily.tools:" + name + "_tool"
    if config is None or config.use != expected:
        raise ValueError("native_research_tool_unavailable")
    return resolve_variable(expected)


async def research(request: Request):
    from app.gateway.deps import get_run_event_store

    scope = await require_scope(request)
    body = await bounded_body(request)
    name, args = body.get("name"), body.get("arguments")
    field = "query" if name == "web_search" else "url" if name == "web_fetch" else None
    if set(body) != {"name", "arguments"} or field is None or not isinstance(args, dict) or set(args) != {field}:
        raise HTTPException(400, "Unsupported research arguments")
    value = args[field]
    if not isinstance(value, str) or not value.strip() or len(value) > (1000 if field == "query" else 2048) or "\x00" in value:
        raise HTTPException(400, "Invalid research arguments")
    store = get_run_event_store(request)
    started, attempt_id = time.monotonic(), uuid.uuid4().hex
    fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    # Serialize admission in this Gateway process, including concurrent Workers.
    # Cross-process scale-out requires a transactional shared reservation; do
    # not claim this local guard is a multi-replica global quota.
    with _receipt_owner_context(scope):
        async with _budget_lock:
            history = await store.list_events(scope["thread"], scope["run"], event_types=[RESEARCH], limit=65, after_seq=0)
            if len(history) >= 64 or sum((row.get("content") or {}).get("fingerprint") == fingerprint for row in history) >= 3:
                raise HTTPException(429, {"research_error": "research_budget"})
            await store.put(thread_id=scope["thread"], run_id=scope["run"], event_type=RESEARCH, category="tool", content={"name": name, "fingerprint": fingerprint, "attempt_id": attempt_id}, metadata={"task_id": scope["run"]})

    def invoke():
        if field == "url":
            try:
                public_url(value)
            except ValueError:
                raise ResearchFailure("invalid_arguments") from None
        result = configured_tool(name).invoke(args)
        if not isinstance(result, str) or len(result.encode()) > 32768:
            raise ResearchFailure("invalid_response")
        if name == "web_fetch" and result.startswith("Error:"):
            raise ResearchFailure("extract_failed")
        return result

    try:
        await asyncio.wait_for(_slots.acquire(), timeout=2)
    except TimeoutError:
        await _research_outcome(store, scope, name, attempt_id, started, "service_busy")
        raise HTTPException(503, {"research_error": "service_busy"}) from None
    task = asyncio.create_task(asyncio.to_thread(invoke))

    def finished(done):
        _slots.release()
        if not done.cancelled():
            done.exception()  # A timed-out OS thread still owns its slot.

    task.add_done_callback(finished)
    try:
        result = await asyncio.wait_for(asyncio.shield(task), timeout=35)
    except Exception as exc:
        category = research_error_category(exc)
        await _research_outcome(store, scope, name, attempt_id, started, category)
        raise HTTPException(502, {"research_error": category}) from None
    await _research_outcome(store, scope, name, attempt_id, started, None)
    return JSONResponse({"name": name, "content": result, "untrusted_external_content": True}, headers={"Cache-Control": "no-store"})


async def confirmation_authority(request, *, claim=False, continuation=False):
    """Read durable authorization for ONE active PPT task, never admit it.

    The Host must also serialize a subsequent native POST with its cancellation
    guard. This read is not an effect lock or permission to retry a lost POST.
    Model text, body-provided owner/consent and elapsed time are not authority.
    """
    from deerflow.persistence import get_session_factory
    from deerflow.persistence.ppt_confirmation.sql import ConfirmationRepository, Scope
    from deerflow.ppt_confirmation import lifecycle as core

    scope = await require_scope(request)
    body = await bounded_body(request)
    expected = {"gate_id"} if continuation else {"operation_id"} if claim else {"task_id"}
    if set(body) != expected or not (claim or continuation) and body["task_id"] != scope["run"]:
        raise HTTPException(400, "Invalid confirmation request")
    if claim:
        try:
            core.identifier(body["operation_id"])
        except core.ConsentConflict:
            raise HTTPException(400, "Invalid confirmation request") from None
    if continuation:
        try:
            core.fingerprint(body["gate_id"])
        except core.ConsentConflict:
            raise HTTPException(400, "Invalid confirmation request") from None
    factory = get_session_factory()
    if factory is None:
        raise HTTPException(503, "Confirmation store unavailable")
    snapshot = await ConfirmationRepository(factory).get(Scope(scope["owner"], scope["thread"], scope["run"]))
    identity = {"owner_id": scope["owner"], "thread_id": scope["thread"], "task_id": scope["run"]}
    if not (claim or continuation):
        admitted = snapshot is not None and not snapshot["cancelled"]
        return JSONResponse({**identity, "admitted": admitted, "auto_enabled": admitted and snapshot["policy"]["auto_enabled"] is True}, headers={"Cache-Control": "no-store"})
    gate = snapshot.get("gate") if snapshot is not None else None
    if continuation:
        if (
            snapshot is None
            or snapshot["cancelled"]
            or not isinstance(gate, dict)
            or gate.get("state") != "continuation_pending"
            or gate.get("gate_id") != body["gate_id"]
            or any(gate.get(key) != value for key, value in identity.items())
            or gate.get("continuation") != {"run_id": scope["run"], "action": "resume_same_session"}
            or not isinstance(gate.get("receipt_digest"), str)
        ):
            raise HTTPException(409, "Confirmation continuation unavailable")
        keys = ("owner_id", "thread_id", "task_id", "project_id", "stage", "gate_id", "revision", "state", "version", "owner_version", "source_digest", "payload_digest", "receipt_digest", "continuation")
        return JSONResponse({**identity, "claim": {key: gate[key] for key in keys}}, headers={"Cache-Control": "no-store"})
    if (
        snapshot is None
        or snapshot["cancelled"]
        or snapshot["policy"]["auto_enabled"] is not True
        or not isinstance(gate, dict)
        or gate.get("state") != "submission_pending"
        or any(gate.get(key) != value for key, value in identity.items())
        or not isinstance(gate.get("submission"), dict)
        or gate["submission"].get("mode") != "automatic"
        or gate["submission"].get("id") != body["operation_id"]
    ):
        raise HTTPException(409, "Confirmation claim unavailable")
    keys = ("owner_id", "thread_id", "task_id", "project_id", "stage", "gate_id", "revision", "state", "version", "owner_version", "source_digest", "payload_digest", "submission")
    return JSONResponse({**identity, "claim": {key: gate[key] for key in keys}}, headers={"Cache-Control": "no-store"})


async def dispatch_runtime(request, operation):
    """Multiplex the exact existing self-authenticated Responses path.

    Never enlarge browser-auth/CSRF exceptions. Each operation authenticates
    independently; a browser session, relay key alone or arbitrary run ID is
    not a research capability. No model/tool request falls through here.
    """
    if operation == "capability":
        return await mint(request)
    if operation == "research":
        return await research(request)
    if operation in {"confirmation-policy", "confirmation-claim"}:
        return await confirmation_authority(request, claim=operation == "confirmation-claim")
    if operation == "confirmation-continuation":
        return await confirmation_authority(request, continuation=True)
    if operation == "confirmation-capability-refresh":
        return await refresh_confirmation_capability(request)
    if operation == "receipts":
        body = await bounded_body(request)
        if set(body) != {"after_seq"} or type(body["after_seq"]) is not int:
            raise HTTPException(400, "Invalid receipt cursor")
        return await receipts(request, after_seq=body["after_seq"])
    raise HTTPException(400, "Unsupported PPT runtime operation")
