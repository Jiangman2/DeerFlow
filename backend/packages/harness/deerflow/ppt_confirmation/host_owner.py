"""Controller adapter for the authenticated, exact task's Agent Host.

Construct only from Runner's verified Sandbox service and owned run Scope,
never a browser URL. The supplied client already has internal authentication
and disables environment proxies. No automatic request retries are performed.
This adapter alone neither starts a polling task nor creates user consent.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from deerflow.persistence.ppt_confirmation.sql import Scope
from deerflow.ppt_confirmation import lifecycle as core
from deerflow.ppt_confirmation.controller import ObservedProposal
from deerflow.ppt_confirmation.proposal import CapturedProposal

MAX_RESPONSE_BYTES = 300 * 1024


class HostOwner:
    def __init__(self, client, host_url: str, scope: Scope):
        url = urlsplit(host_url)
        core.require(url.scheme == "http" and url.hostname is not None and url.port == 8788 and not url.username and not url.password and not url.query and not url.fragment and url.path in {"", "/"}, "invalid_confirmation_host")
        core.require(isinstance(scope, Scope), "explicit_scope_required")
        self.client, self.scope = client, scope
        self.base = host_url.rstrip("/") + "/v1/tasks/" + scope.task_id + "/confirmation/"

    def _scope(self, scope):
        core.require(scope == self.scope, "confirmation_owner_scope_mismatch")

    async def _rpc(self, suffix, *, body=None, not_ready=False):
        try:
            async with self.client.stream("GET" if body is None else "POST", self.base + suffix, **({"json": body} if body is not None else {}), timeout=35.0) as response:
                if not_ready and response.status_code == 409:
                    return None
                if response.status_code != 200 or response.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
                    raise OSError("confirmation_host_unavailable")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise OSError("confirmation_host_response_limit")
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise OSError("confirmation_host_invalid_response")
                return value
        except Exception:
            # httpx transport failures and native-owner errors can contain
            # private URLs/payloads; caller receives only a fixed safe label.
            # CancelledError is a BaseException and deliberately propagates.
            raise OSError("confirmation_host_unavailable") from None

    async def observe(self, scope):
        self._scope(scope)
        value = await self._rpc("observation", not_ready=True)
        if value is None:
            return None
        core.require(value.get("task_id") == scope.task_id and value.get("thread_id") == scope.thread_id and value.get("confirmation_submitted") is False and isinstance(value.get("payload"), dict), "invalid_official_observation")
        raw = json.dumps(value["payload"], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        core.require(len(raw) <= 256 * 1024, "invalid_official_observation")
        proposed = CapturedProposal(value.get("version"), value.get("source_digest"), value.get("payload_digest"), raw, "Official Confirm UI", 0, 1, owner_version=value.get("owner_version"))
        return ObservedProposal(scope, value.get("project_id"), value.get("stage"), proposed).validate(scope)

    async def submit_guarded(self, scope, claim, proposal):
        self._scope(scope)
        core.require(
            isinstance(claim, dict)
            and all(claim.get(key) == getattr(scope, key) for key in ("owner_id", "thread_id", "task_id"))
            and claim.get("state") == "submission_pending"
            and isinstance(claim.get("submission"), dict)
            and claim["submission"].get("mode") == "automatic",
            "confirmation_claim_mismatch",
        )
        operation = core.identifier(claim["submission"].get("id"))
        # Host re-reads the committed claim and every native binding under its
        # cancellation guard; body metadata cannot authorize the effect.
        result = await self._rpc("submit", body={"operation_id": operation, "payload": proposal.payload})
        core.require(result.get("retry_permitted") is False and result.get("state") in {"receipt_required", "submission_unknown"}, "invalid_submission_ack")

    async def receipt(self, scope, operation):
        self._scope(scope)
        return await self._rpc("receipts/operation/" + core.identifier(operation))

    async def receipt_by_version(self, scope, version):
        self._scope(scope)
        return await self._rpc("receipts/by-version/" + core.fingerprint(version))

    async def continue_guarded(self, scope, gate, session):
        self._scope(scope)
        core.require(gate.get("state") == "continuation_pending" and gate.get("task_id") == scope.task_id, "invalid_continuation_claim")
        return await self._rpc("continue", body={"gate_id": core.fingerprint(gate["gate_id"]), "session_id": core.identifier(session)})

    async def continuation_receipt(self, scope, gate_id):
        self._scope(scope)
        return await self._rpc("continuation-receipts/" + core.fingerprint(gate_id))
