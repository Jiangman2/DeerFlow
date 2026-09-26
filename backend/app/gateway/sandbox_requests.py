"""Lazy request holders for auxiliary sandbox work (never runner discovery)."""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import Request

from app.gateway.sandbox_authz import authorize_sandbox_for_request
from deerflow.sandbox.lease import SandboxClientLease, acquire_sandbox_client_lease
from deerflow.sandbox.sandbox_provider import SandboxProvider


@dataclass
class SandboxRequestSession:
    """One upload request borrows a client without parking the warm sandbox.

    The existing lease manager fences concurrent agent releases. Every blocking
    client operation must use the lease's cancellation-draining run_sync; the
    outer request releases its holder only after the last real operation ends.
    """

    request: Request
    provider: SandboxProvider
    thread_id: str
    user_id: str
    _lease: SandboxClientLease | None = field(default=None, init=False)
    _closed: bool = field(default=False, init=False)

    async def __aenter__(self):
        if self._closed:
            raise RuntimeError("Sandbox request session is closed")
        return self

    async def __aexit__(self, *_exc):
        self._closed = True
        if self._lease is not None:
            await self._lease.release()

    async def acquire(self):
        if self._closed:
            raise RuntimeError("Sandbox request session is closed")
        # Recheck before reuse as well: a fallback may occur well after the
        # initial upload sync decision and policy may have been revoked.
        await authorize_sandbox_for_request(self.request)
        if self._lease is None:
            self._lease = await acquire_sandbox_client_lease(
                self.provider,
                self.thread_id,
                user_id=self.user_id,
                owner_prefix="gateway-upload",
                release_on_last=False,
            )
        return self._lease.sandbox

    async def run_sync(self, func, /, *args, **kwargs):
        if self._closed or self._lease is None:
            raise RuntimeError("Sandbox request has no client lease")
        return await self._lease.run_sync(func, *args, **kwargs)
