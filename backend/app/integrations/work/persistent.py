"""Durable semantic tasks and OAuth, fenced through the existing encrypted table."""

import hashlib
import hmac
import secrets
import time
from dataclasses import asdict

from app.channels.message_bus import InboundMessage

from .client import WorkError
from .service import DocumentJob, WorkDocumentService
from .tasks import TaskRef


class PersistentWorkService(WorkDocumentService):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._job_records = {}

    @staticmethod
    def _data(job):
        message = asdict(job.message)
        message["files"] = []
        message["metadata"] = {key: value for key, value in message["metadata"].items() if key in {"chat_type", "message_id"}}
        return {
            "message": message,
            "document_id": job.document_id,
            "document_url": job.document_url,
            "scope": job.scope,
            "state": job.state,
            "browser_session": job.browser_session,
            "intent": job.intent,
            "task_ref": job.task_ref,
            "protocol": job.protocol,
        }

    async def _job(self, ticket):
        pointer = await self.store.read("ticket", ticket)
        if not pointer:
            raise WorkError("invalid_or_expired_ticket")
        record = await self.store.read("job", ticket, owner=pointer.value["owner"])
        if not record or record.status not in {"waiting", "authorizing", "authorized", "processing"}:
            raise WorkError("invalid_or_expired_ticket")
        data = dict(record.value)
        if data.get("protocol") != 2:
            # Unfinished pre-draft jobs must be requested again. Their old
            # single-event proposal may have lost a finite repetition request.
            raise WorkError("job_protocol_upgrade_required")
        ref = TaskRef.parse(data.get("task_ref"))
        if not ref or not data.get("intent"):
            # Old jobs are readable as expired, never executed under a new
            # protocol merely because an old browser link is still open.
            raise WorkError("task_reference_required")
        await self.store.assert_task(pointer.value["owner"], ref)
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
        record = await self.store.admit(
            "job", ticket, self._data(job), owner=owner, task_ref=TaskRef.parse(job.task_ref), ttl=600, active={"waiting", "authorizing", "authorized", "processing"}, limit=self.settings.max_jobs, owner_limit=self.settings.per_user_jobs
        )
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
        p = self._identity(job.message)
        owner, ref = self.vault.principal_key(p), TaskRef.parse(job.task_ref)
        record = await self.store.change_task_record("job", ticket, old, owner=owner, task_ref=ref, value=self._data(job), status="authorizing")
        if not record:
            raise WorkError("invalid_or_expired_ticket")
        self._job_records[ticket] = record
        state = await self.store.create_task_state(job.state, {"ticket": ticket, "browser_hash": hashlib.sha256(job.browser_session.encode()).hexdigest()}, owner=owner, task_ref=ref, ttl=max(1, record.expires_at - time.time()))
        if not state:
            raise WorkError("invalid_or_expired_ticket")
        return result

    async def callback(self, *, state, code, browser_session, error=""):
        record = await self.store.read("state", state)
        if not record or record.status != "authorizing" or not browser_session or not hmac.compare_digest(record.value["browser_hash"], hashlib.sha256(browser_session.encode()).hexdigest()):
            raise WorkError("invalid_state_or_browser")
        owner, ref = record.value.get("task_owner"), TaskRef.parse(record.value.get("task_ref"))
        if not owner or not ref:
            raise WorkError("task_reference_required")
        claimed = await self.store.change_task_record("state", state, record, owner=owner, task_ref=ref, status="consumed")
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
            try:
                p = self._identity(job.message)
                new = await self.store.change_task_record("job", ticket, old, owner=self.vault.principal_key(p), task_ref=TaskRef.parse(job.task_ref), value=self._data(job), status=job.stage, ttl=900)
                if new:
                    self._job_records[ticket] = new
            except WorkError as exc:
                if exc.stage != "task_superseded":
                    raise

    async def _install_grant(self, job, grant):
        p, ticket = self._identity(job.message), self._ticket(job)
        if not await self.vault.bound(p):
            raise WorkError("binding_changed")
        owner = self.vault.principal_key(p)
        record, job_record = await self.store.install_authorized(
            ticket, self._job_records[ticket], owner=owner, task_ref=TaskRef.parse(job.task_ref), value={"token": grant.token, "scopes": sorted(grant.scopes)}, ttl=max(1, grant.expires_at - time.monotonic())
        )
        self._job_records[ticket] = job_record
        grant.revision = record.revision
        self.vault._records[p] = record
        self.vault.put(p, grant)

    async def _control(self, msg, p, verb, plan_id):
        await super()._control(msg, p, verb, plan_id)
        plan = self.tools.plans.get(plan_id)
        if plan and plan.task_ref:
            try:
                await self._save_task_context(p, plan.task_ref)
            except WorkError as exc:
                if exc.stage != "task_superseded":
                    raise

    async def _run(self, job):
        ticket, p = self._ticket(job), self._identity(job.message)
        key, ref = self.vault.principal_key(p), TaskRef.parse(job.task_ref)
        lease_name, holder = "planning:" + key + ":" + ref.task_id + ":" + ref.generation, secrets.token_urlsafe(32)
        old = self._job_records[ticket]
        acquired = False
        try:
            record = await self.store.change_task_record("job", ticket, old, owner=key, task_ref=ref, value=self._data(job), status="processing", ttl=300)
            if not record:
                return
            self._job_records[ticket] = record
            acquired = bool(await self.store.acquire(lease_name, holder, ttl=300))
            if not acquired:
                return
            await super()._run(job)
            await self.store.audit(p, "request", job.document_id or "calendar", job.stage)
        except WorkError as exc:
            if exc.stage != "task_superseded":
                raise
            job.stage = "cancelled"
        finally:
            await self._save_job(ticket, job)
            if acquired:
                await self.store.release(lease_name, holder)

    async def close(self):
        await super().close()
        self._job_records.clear()
