"""One leased Work receiver; other Gateways remain capable of sending OAuth results.

The lease is scoped to app/API/profile (not secret generation). A standby uses
the existing SDK HTTP sender without opening a WebSocket. Every inbound bind
and dispatch rechecks the lease, so a stale worker cannot process new messages.
"""

import asyncio
import logging
import os
import secrets

from .config import UAT_APP_ID, WorkSettings
from .store import WorkStore

logger = logging.getLogger(__name__)


class WorkReceiver:
    def __init__(self, service, store, config):
        self.service, self.store, self.config = service, store, dict(config)
        self.holder = secrets.token_urlsafe(32)
        self.task = None
        self.owns = False
        self.channel = None
        self.closed = False

    @classmethod
    async def prepare(cls, service, config):
        settings = WorkSettings.from_env()
        if not settings.enabled or not settings.persistent:
            return None
        if settings.profile == "production" and config.get("app_id") == UAT_APP_ID:
            raise ValueError("Production Work cannot use the known UAT application")
        from deerflow.persistence import get_session_factory

        sf = get_session_factory()
        if sf is None:
            raise ValueError("Production Work receiver requires SQL persistence")
        store = WorkStore(sf, key=os.getenv("DEER_FLOW_WORK_STATE_KEY", ""), namespace="receiver:" + settings.profile + ":" + settings.api_url + ":" + config.get("app_id", ""))
        instance = cls(service, store, config)
        instance.owns = bool(await store.acquire("receiver", instance.holder, ttl=60))
        return instance

    def channel_config(self, config):
        return {**config, "_work_send_only": not self.owns, "_work_receiver_guard": self.valid, "_work_receiver_ready": False}

    async def valid(self):
        if self.closed or not self.owns:
            return False
        row = await self.store.read("lease", "receiver")
        return bool(row and row.value.get("holder") == self.holder)

    def start(self, channel):
        self.channel = channel
        self.task = asyncio.create_task(self._watch())

    async def _watch(self):
        try:
            while not self.closed:
                await asyncio.sleep(5)
                if not self.service._running:
                    return
                if self.owns:
                    record = await self.store.acquire("receiver", self.holder, ttl=60)
                    if not record:
                        self.owns = False
                        return
                    failure = getattr(self.channel, "_ws_failure", None)
                    if not await self.store.change("lease", "receiver", record, value={"holder": self.holder, "connected": self.channel.transport_connected, "business_code": failure.business_code if failure else None}, ttl=60):
                        self.owns = False
                        return
                else:
                    record = await self.store.read("lease", "receiver")
                    self.channel.config["_work_receiver_ready"] = bool(record and record.value.get("connected"))
                    from app.channels.feishu_transport import HandshakeFailure

                    code = record.value.get("business_code") if record else None
                    self.channel._ws_failure = HandshakeFailure(business_code=code) if type(code) is int else None
                    if not record:
                        return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("work_receiver stage=lease_or_config_unavailable")
            self.owns = False
        finally:
            coordinator = getattr(self.service, "_work_coordinator", None)
            if coordinator and not self.closed:
                coordinator.wake()

    async def close(self):
        self.closed = True
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        try:
            await self.store.release("receiver", self.holder)
        except Exception:
            # Socket stop/join precedes this. SQL unavailability must not abort
            # local cleanup; the fenced lease expires and is never force-deleted.
            logger.warning("work_receiver stage=lease_release_deferred")
