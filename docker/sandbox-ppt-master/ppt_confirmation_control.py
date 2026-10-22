"""PPT Host control only: verify a durable claim, dispatch once, read receipts.

The caller holds the Host task/cancellation and cross-process admission locks.
No client/model body can grant consent: Gateway re-reads the owner-scoped SQL
claim. Unknown local intent or native response is never retried here. Native
stages/defaults/validators remain authoritative; this module never writes them.
"""

import hashlib
import http.client
import importlib.util
import json
import os
import re
import time
from pathlib import Path
from types import ModuleType

_spec = importlib.util.spec_from_file_location(
    "confirmation_control_pages", Path(__file__).with_name("ppt_confirmation_pages.py")
)
pages = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pages)

proposal = None  # Shared harness source is copied verbatim into the Host image.
LIMIT = 2 * 1024 * 1024
RELAY_PATH = "/api/codex/deepseek/v1/responses"
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")


def require(ok, label):
    if not ok:
        raise RuntimeError(label)


def shared_proposal():
    global proposal
    if proposal is None:
        import ppt_confirmation_proposal

        proposal = ppt_confirmation_proposal
    return proposal


def encoded(value, limit=LIMIT):
    try:
        raw = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        require(len(raw) <= limit, "confirmation_payload_too_large")
        return raw
    except (TypeError, ValueError, RecursionError):
        raise RuntimeError("invalid_confirmation_payload") from None


def request_json(host, port, path, *, body=None, headers=None):
    """Fixed internal destinations, direct sockets, no redirects or retries.

    Socket timeouts and a read deadline bound a slow/chunked response. The
    calling HTTP/Runner controller has a separate total request deadline; a
    missing acknowledgement remains unknown and cannot authorize a new POST.
    """
    native = host == "127.0.0.1" and type(port) is int and 5050 <= port <= 5099
    relay = host == "gateway" and port == 8001 and path == RELAY_PATH
    require(native or relay, "unapproved_confirmation_endpoint")
    require(
        relay
        or path
        in {"/api/health", "/api/recommendations", "/api/catalogs", "/api/confirm"}
        or re.fullmatch(
            r"/api/confirmation-receipts/(?:by-version/)?[A-Za-z0-9_-]{1,128}", path
        ),
        "unapproved_confirmation_endpoint",
    )
    connection = http.client.HTTPConnection(host, port, timeout=2)
    deadline = time.monotonic() + 6
    try:
        connection.request(
            "GET" if body is None else "POST",
            path,
            body=None if body is None else encoded(body, 256 * 1024),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                **(headers or {}),
            },
        )
        response = connection.getresponse()
        require(response.status == 200, "confirmation_endpoint_unavailable")
        require(
            response.getheader("Content-Type", "").split(";")[0].strip().lower()
            == "application/json",
            "invalid_confirmation_response",
        )
        raw = bytearray()
        while True:
            require(time.monotonic() < deadline, "confirmation_read_deadline")
            chunk = response.read1(min(16384, LIMIT + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
            require(len(raw) <= LIMIT, "confirmation_response_too_large")
        value = json.loads(raw)
        require(isinstance(value, dict), "invalid_confirmation_response")
        return value
    except (OSError, http.client.HTTPException, ValueError, RecursionError):
        raise RuntimeError("confirmation_transport_unconfirmed") from None
    finally:
        connection.close()


def candidate(task, workspace):
    require(
        not task.state.get("cancel_requested")
        and task.state.get("status") in {"running", "completed"}
        and not task.state.get("artifacts"),
        "confirmation_task_inactive",
    )
    value = task.state.get("confirmation_owner_candidate")
    require(isinstance(value, dict), "confirmation_owner_not_discovered")
    project = pages.project_path(value.get("project"), workspace)
    port = pages.port_number(value.get("port"))
    return project, port


def snapshot(task, *, workspace, runtime):
    project, port = candidate(task, workspace)
    module = shared_proposal()

    def get(path):
        return request_json("127.0.0.1", port, path)

    before = get("/api/health")
    recommendations = get("/api/recommendations")
    catalogs = get("/api/catalogs")
    after = get("/api/health")
    try:
        return module.prepare_snapshot(
            before=before,
            recommendations=recommendations,
            catalogs=catalogs,
            after=after,
            expected_project=str(project.resolve()),
            source_root=runtime / "scripts/confirm_ui",
        )
    except ValueError:
        raise RuntimeError("confirmation_snapshot_unavailable") from None


def initial_policy(task):
    """Read active SQL admission, not a boolean supplied by the task caller."""
    capability = task.env.get("PPT_RUNTIME_CAPABILITY")
    require(isinstance(capability, str) and 1 <= len(capability) <= 1024,
            "confirmation_capability_unavailable")
    value = request_json("gateway", 8001, RELAY_PATH, body={"task_id": task.task_id},
                         headers={"X-Ppt-Capability": capability,
                                  "X-Ppt-Runtime-Operation": "confirmation-policy"})
    require(value.get("admitted") is True and type(value.get("auto_enabled")) is bool
            and value.get("task_id") == task.task_id and value.get("thread_id") == task.thread_id
            and isinstance(value.get("owner_id"), str) and _ID.fullmatch(value["owner_id"]),
            "confirmation_policy_unavailable")
    return {"auto_enabled": value["auto_enabled"]}


def observe(task, *, workspace, runtime, capture, cache):
    """Read the native page; calculate choices offline without posting them.

    A cached calculation never substitutes for a fresh native snapshot. Expensive
    browser time is before gate registration, not part of the 60s countdown.
    The caller serializes captures, without holding the task cancellation lock.
    """
    project, _ = candidate(task, workspace)
    current = snapshot(task, workspace=workspace, runtime=runtime)
    key = (task.task_id, task.thread_id, str(project.resolve()), current.version,
           current.owner_version, current.source_digest)
    proposed = cache.get("proposal") if cache.get("key") == key else None
    if proposed is None:
        cache.clear()
        proposed = capture(current)
        # Cancellation, stage updates or native owner replacement during the
        # browser calculation invalidate the entire result.
        fresh_project, _ = candidate(task, workspace)
        fresh = snapshot(task, workspace=workspace, runtime=runtime)
        require(fresh_project == project and all(getattr(fresh, field) == getattr(current, field)
                for field in ("version", "owner_version", "source_digest", "stage")), "confirmation_owner_changed")
        require(all(getattr(proposed, field) == getattr(current, field)
                    for field in ("version", "owner_version", "source_digest"))
                and proposed.confirmation_submitted is False and proposed.console_errors == 0
                and hashlib.sha256(proposed.payload_json).hexdigest() == proposed.payload_digest,
                "invalid_confirmation_proposal")
        cache.update(key=key, proposal=proposed)
    return {"task_id": task.task_id, "thread_id": task.thread_id,
            "project_id": hashlib.sha256(str(project.resolve()).encode()).hexdigest(),
            "stage": "stage" + str(current.stage), "version": proposed.version,
            "owner_version": proposed.owner_version, "source_digest": proposed.source_digest,
            "payload_digest": proposed.payload_digest, "payload": proposed.payload,
            "confirmation_submitted": False}


def authority(task, operation_id):
    capability = task.env.get("PPT_RUNTIME_CAPABILITY")
    require(
        isinstance(capability, str) and 1 <= len(capability) <= 1024,
        "confirmation_capability_unavailable",
    )
    value = request_json(
        "gateway",
        8001,
        RELAY_PATH,
        body={"operation_id": operation_id},
        headers={
            "X-Ppt-Capability": capability,
            "X-Ppt-Runtime-Operation": "confirmation-claim",
        },
    )
    claim = value.get("claim")
    require(
        value.get("task_id") == task.task_id
        and value.get("thread_id") == task.thread_id
        and isinstance(claim, dict),
        "confirmation_claim_mismatch",
    )
    require(
        claim.get("task_id") == task.task_id
        and claim.get("thread_id") == task.thread_id
        and claim.get("owner_id") == value.get("owner_id")
        and isinstance(value.get("owner_id"), str)
        and claim.get("state") == "submission_pending"
        and isinstance(claim.get("submission"), dict)
        and claim["submission"].get("id") == operation_id
        and claim["submission"].get("mode") == "automatic",
        "confirmation_claim_mismatch",
    )
    return claim


def submit(task, body, *, receipts, workspace, runtime):
    project, port = candidate(task, workspace)
    require(
        isinstance(body, dict) and set(body) == {"operation_id", "payload"},
        "invalid_confirmation_request",
    )
    operation, payload = body["operation_id"], body["payload"]
    require(
        isinstance(operation, str)
        and _ID.fullmatch(operation)
        and isinstance(payload, dict)
        and "_confirmation_request" not in payload
        and "_confirmation_receipt" not in payload,
        "invalid_confirmation_request",
    )
    raw = encoded(payload, 256 * 1024)
    marker = task.task_dir / ("confirmation-" + operation + ".json")
    pages.safe_directory(task.task_dir)
    # Even a corrupt/partial existing intent is NO permission to re-dispatch.
    if os.path.lexists(marker):
        return {"state": "submission_unknown", "retry_permitted": False}
    claim = authority(task, operation)
    require(
        claim.get("project_id")
        == hashlib.sha256(str(project.resolve()).encode()).hexdigest()
        and claim.get("payload_digest") == hashlib.sha256(raw).hexdigest(),
        "confirmation_claim_mismatch",
    )
    observed = snapshot(task, workspace=workspace, runtime=runtime)
    require(
        all(
            getattr(observed, key) == claim.get(key)
            for key in ("version", "owner_version", "source_digest")
        )
        and claim.get("stage") == "stage" + str(observed.stage)
        and payload.get("stage") == ("stage1" if observed.stage == 1 else "final"),
        "confirmation_owner_changed",
    )
    require(authority(task, operation) == claim, "confirmation_claim_changed")
    receipts.atomic_json(
        marker,
        {
            "schema": 1,
            "task_id": task.task_id,
            "thread_id": task.thread_id,
            "operation_id": operation,
            "state": "dispatch_intent",
            "owner_version": claim["owner_version"],
            "payload_digest": claim["payload_digest"],
        },
    )
    try:
        # ONLY the native version-locked endpoint owns selection/result writes.
        request_json(
            "127.0.0.1",
            port,
            "/api/confirm",
            body={
                **payload,
                "_confirmation_request": {
                    "protocol": 1,
                    "version": observed.owner_version,
                    "operation_id": operation,
                },
            },
        )
    except (OSError, RuntimeError):
        return {"state": "submission_unknown", "retry_permitted": False}
    return {"state": "receipt_required", "retry_permitted": False}


def receipt(task, kind, identity, *, workspace, runtime):
    project, _ = candidate(task, workspace)
    pattern = _HASH if kind == "by-version" else _ID
    require(
        kind in {"operation", "by-version"}
        and isinstance(identity, str)
        and pattern.fullmatch(identity),
        "invalid_receipt_identity",
    )
    # The native page can exit immediately after final confirmation. Read its
    # durable proof using the exact pinned native validator, without restarting
    # the page or copying its business schema into the Host.
    source = runtime / "scripts/confirm_ui/conditional_protocol.py"
    raw = pages._verify_file(source, pages.PROTOCOL_HASH)
    module = ModuleType("native_confirmation_receipt_reader")
    exec(compile(raw, str(source), "exec"), module.__dict__)
    native = module.ConditionalConfirmation(project / "confirm_ui")
    reader = native.receipt_by_version if kind == "by-version" else native.receipt
    proof, _ = reader(identity)
    return proof


def continuation_authority(task, gate_id, *, workspace, runtime):
    """Authenticate a SQL claim AND read the native committed receipt."""
    require(isinstance(gate_id, str) and _HASH.fullmatch(gate_id), "invalid_continuation_gate")
    capability = refresh_capability(task)
    require(isinstance(capability, str) and 1 <= len(capability) <= 1024, "confirmation_capability_unavailable")
    value = request_json("gateway", 8001, RELAY_PATH, body={"gate_id": gate_id},
        headers={"X-Ppt-Capability": capability, "X-Ppt-Runtime-Operation": "confirmation-continuation"})
    gate = value.get("claim")
    require(value.get("task_id") == task.task_id and value.get("thread_id") == task.thread_id
        and isinstance(gate, dict) and gate.get("task_id") == task.task_id and gate.get("thread_id") == task.thread_id
        and gate.get("owner_id") == value.get("owner_id") and isinstance(value.get("owner_id"), str)
        and gate.get("state") == "continuation_pending" and gate.get("gate_id") == gate_id
        and gate.get("continuation") == {"run_id": task.task_id, "action": "resume_same_session"}, "continuation_claim_mismatch")
    observed = task.state.get("confirmation_observed_gate")
    fields = ("version", "owner_version", "source_digest", "payload_digest", "project_id", "stage")
    require(isinstance(observed, dict) and all(observed.get(key) == gate.get(key) for key in fields), "continuation_observation_changed")
    proof = receipt(task, "by-version", gate["owner_version"], workspace=workspace, runtime=runtime)
    require(proof.get("state") == "committed" and type(proof.get("protocol")) is int and proof["protocol"] == 1
        and proof.get("retry_permitted") is False and proof.get("version") == gate["owner_version"]
        and proof.get("receipt_sha256") == gate["receipt_digest"]
        and proof.get("stage") == ("stage1" if gate["stage"] == "stage1" else "final"), "continuation_native_receipt_mismatch")
    return gate


def refresh_capability(task):
    """Called only for a quiescent, exact-session continuation under Host locks.

    Credentials stay in process memory. Stable guard identity retains research
    counters and repeated-query fingerprints when the expiring token changes.
    Renewal never changes admission/auto policy or grants continuation by itself.
    """
    secret = os.environ.get("SANDBOX_API_KEY", "")
    require(bool(secret), "confirmation_service_credential_unavailable")
    value = request_json("gateway", 8001, RELAY_PATH,
        body={"run_id": task.task_id, "thread_id": task.thread_id},
        headers={"Authorization": "Bearer " + secret,
                 "X-Ppt-Runtime-Operation": "confirmation-capability-refresh"})
    capability = value.get("capability")
    require(value.get("task_id") == task.task_id and value.get("thread_id") == task.thread_id
        and value.get("confirmation_protocol") == "ppt-confirmation-v1"
        and isinstance(capability, str) and 1 <= len(capability) <= 1024,
        "confirmation_capability_refresh_unavailable")
    # The original seed already belongs to this task and is not a service key.
    old = task.env.get("PPT_RUNTIME_CAPABILITY")
    require(isinstance(old, str) and 1 <= len(old) <= 1024, "confirmation_capability_unavailable")
    task.env.setdefault("PPT_RESEARCH_GUARD_KEY", old)
    task.env["PPT_RUNTIME_CAPABILITY"] = capability
    if capability not in task.secret_values:
        task.secret_values.append(capability)
        task.secret_values.sort(key=len, reverse=True)
    return capability
