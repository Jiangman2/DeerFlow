"""Durable OAuth jobs and cross-process planning, retaining the typed executor."""

import hashlib
import hmac
import secrets
import time
from dataclasses import asdict

from app.channels.message_bus import InboundMessage

from .calendar import CalendarContext
from .client import WorkError
from .service import CalendarDialogue, DocumentJob, WorkDocumentService


class PersistentWorkService(WorkDocumentService):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._job_records = {}
        self._holder = secrets.token_urlsafe(32)

    @staticmethod
    def _data(job):
        message = asdict(job.message)
        # Preserve only the trusted envelope needed for routing and binding.
        message["files"] = []
        message["metadata"] = {k: v for k, v in message["metadata"].items() if k in {"chat_type", "message_id"}}
        return {"message": message, "document_id": job.document_id, "document_url": job.document_url, "scope": job.scope, "state": job.state, "browser_session": job.browser_session}

    async def _job(self, ticket):
        pointer = await self.store.read("ticket", ticket)
        if not pointer:
            raise WorkError("invalid_or_expired_ticket")
        record = await self.store.read("job", ticket, owner=pointer.value["owner"])
        if not record:
            raise WorkError("invalid_or_expired_ticket")
        data = dict(record.value)
        data["message"] = InboundMessage(**data["message"])
        stage = "awaiting_authorization" if record.status == "waiting" else record.status
        job = DocumentJob(**data, expires_at=time.monotonic() + record.expires_at - time.time(), stage=stage)
        self.jobs[ticket], self._job_records[ticket] = job, record
        return job

    def _ticket(self, job):
        return next(key for key, value in self.jobs.items() if value is job)

    async def _register_job(self, ticket, job, principal, source):
        owner = self.vault.principal_key(principal)
        dedupe = owner + ":" + source
        await self.store.purge()
        claimed = await self.store.create("message", dedupe, {}, owner=owner, ttl=900)
        if not claimed:
            return False
        record = await self.store.admit("job", ticket, self._data(job), owner=owner, ttl=600, active={"waiting", "authorizing", "authorized", "processing"}, limit=self.settings.max_jobs, owner_limit=self.settings.per_user_jobs)
        if not record:
            await self.store.remove("message", dedupe, claimed)
            await self._reply(job.message, "【海能 Work】当前活跃任务较多，请稍后再试；已完成任务不占此额度。")
            return False
        await self.store.create("ticket", ticket, {"owner": owner}, ttl=600)
        self._job_records[ticket] = record
        return True

    async def authorize(self, ticket, browser_session):
        await self._job(ticket)
        result = await super().authorize(ticket, browser_session)
        job, old = self.jobs[ticket], self._job_records[ticket]
        record = await self.store.change("job", ticket, old, value=self._data(job), status="authorizing")
        if not record:
            raise WorkError("invalid_or_expired_ticket")
        self._job_records[ticket] = record
        await self.store.create("state", job.state, {"ticket": ticket, "browser_hash": hashlib.sha256(job.browser_session.encode()).hexdigest()}, ttl=max(1, record.expires_at - time.time()), status="authorizing")
        return result

    async def callback(self, *, state, code, browser_session, error=""):
        record = await self.store.read("state", state)
        if not record or record.status != "authorizing" or not hmac.compare_digest(record.value["browser_hash"], hashlib.sha256(browser_session.encode()).hexdigest()):
            raise WorkError("invalid_state_or_browser")
        claimed = await self.store.change("state", state, record, status="consumed")
        if not claimed:
            raise WorkError("invalid_state_or_browser")
        ticket = record.value["ticket"]
        job = await self._job(ticket)
        if job.stage != "authorizing":
            raise WorkError("authorization_or_binding_invalid")
        self._states[state] = ticket
        try:
            return await super().callback(state=state, code=code, browser_session=browser_session, error=error)
        except BaseException:
            await self._save_job(ticket, job)
            raise

    async def _save_job(self, ticket, job):
        old = self._job_records.get(ticket)
        if old:
            new = await self.store.change("job", ticket, old, value=self._data(job), status=job.stage, ttl=900)
            if new:
                self._job_records[ticket] = new

    async def _install_grant(self, job, grant):
        p, ticket = self._identity(job.message), self._ticket(job)
        if not await self.vault.bound(p):
            raise WorkError("binding_changed")
        owner = self.vault.principal_key(p)
        record, job_record = await self.store.install_authorized(ticket, self._job_records[ticket], owner=owner, value={"token": grant.token, "scopes": sorted(grant.scopes)}, ttl=max(1, grant.expires_at - time.monotonic()))
        self._job_records[ticket] = job_record
        grant.revision = record.revision
        self.vault._records[p] = record
        self.vault.put(p, grant)

    async def _load_context(self, p):
        key = self.vault.principal_key(p)
        record = await self.store.read("context", key, owner=key)
        if not record:
            return
        data = record.value
        expires = time.monotonic() + record.expires_at - time.time()
        if data.get("document"):
            self._contexts[p] = (expires, data["document"])
        if data.get("dialogue"):
            self._calendar_dialogues[p] = CalendarDialogue(expires, data["dialogue"])
        if data.get("calendar"):
            self.calendar.contexts[p] = CalendarContext(expires, data["calendar"]["candidates"], data["calendar"]["recent_id"])
            self.tools.remember_events(p, [x["event_id"] for x in data["calendar"]["candidates"]])

    async def _save_context(self, p):
        key = self.vault.principal_key(p)
        context, dialogue = self.calendar.contexts.get(p), self._calendar_dialogues.get(p)
        await self.store.put(
            "context",
            key,
            {"document": self._contexts.get(p, (0, ""))[1], "dialogue": dialogue.turns if dialogue else [], "calendar": {"candidates": context.candidates, "recent_id": context.recent_id} if context else {}},
            owner=key,
            ttl=600,
        )

    async def handle(self, msg):
        try:
            p = self._identity(msg)
        except WorkError:
            return await super().handle(msg)
        await self._load_context(p)
        if msg.text.strip() in {"/work 撤销授权", "/work 退出授权"}:
            if not await self.vault.bound(p):
                return await super().handle(msg)
            await self.store.invalidate_owner(self.vault.principal_key(p))
        return await super().handle(msg)

    async def _control(self, msg, p, verb, plan_id):
        await super()._control(msg, p, verb, plan_id)
        await self._save_context(p)

    async def _run(self, job):
        ticket, p = self._ticket(job), self._identity(job.message)
        key = self.vault.principal_key(p)
        lease_name, holder = "planning:" + key, secrets.token_urlsafe(32)
        old = self._job_records[ticket]
        record = await self.store.change("job", ticket, old, value=self._data(job), status="processing", ttl=300)
        if not record:
            return
        self._job_records[ticket] = record
        acquired = False
        try:
            acquired = bool(await self.store.acquire(lease_name, holder, ttl=300))
            if not acquired:
                job.stage = "failed"
                await self._reply(job.message, "【海能 Work】您上一条操作还在处理中，请等待结果后再发送；本次未修改数据。")
                return
            await self._load_context(p)
            await super()._run(job)
            await self._save_context(p)
            await self.store.audit(p, "request", job.document_id or "calendar", job.stage)
        finally:
            await self._save_job(ticket, job)
            if acquired:
                await self.store.release(lease_name, holder)

    async def close(self):
        # Process shutdown clears local copies, never the shared valid grants.
        await super().close()
        self._job_records.clear()
