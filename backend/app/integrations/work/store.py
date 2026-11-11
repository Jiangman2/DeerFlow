"""Encrypted SQL state with owner-bound AEAD, optimistic CAS and atomic admission.

No provider writes are retried. An executing plan remains non-replayable even
if its process dies before saving its receipt. PostgreSQL is required for a
multi-host production deployment; SQLite is exercised in isolated tests.
"""

import base64
import hashlib
import json
import secrets
import time
from copy import deepcopy
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from deerflow.persistence.work.model import WorkRecordRow as Row

from .client import WorkError
from .scopes import GRANT_KINDS
from .tasks import TaskRef, TurnReservation


@dataclass
class Record:
    value: dict = field(repr=False)
    owner: str = field(repr=False)
    status: str
    revision: int
    expires_at: float


class WorkStore:
    def __init__(self, session_factory, *, key: str, namespace: str):
        try:
            raw = base64.b64decode(key, altchars=b"-_", validate=True)
            if len(raw) != 32:
                raise ValueError
        except Exception:
            raise ValueError("Work encryption key must be base64-encoded 32 random bytes") from None
        self._sf, self.key = session_factory, key
        self.namespace = self.hash(namespace)
        self._cipher = AESGCM(raw)
        self.signing_key = hashlib.sha256(raw + b"work-plan-v1" + self.namespace.encode()).digest()

    @staticmethod
    def hash(value):
        return hashlib.sha256(value.encode()).hexdigest()

    def _where(self, kind, key):
        return (Row.namespace == self.namespace, Row.kind == kind, Row.key_hash == self.hash(key))

    def _aad(self, kind, key, owner):
        return json.dumps([self.namespace, kind, self.hash(key), self.hash(owner)], separators=(",", ":")).encode()

    def _encrypt(self, kind, key, owner, value):
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        if len(raw) > 128_000:
            raise WorkError("work_state_too_large")
        nonce = secrets.token_bytes(12)
        return base64.urlsafe_b64encode(nonce + self._cipher.encrypt(nonce, raw, self._aad(kind, key, owner))).decode()

    def _record(self, row, kind, key, owner):
        try:
            raw = base64.urlsafe_b64decode(row.ciphertext)
            value = json.loads(self._cipher.decrypt(raw[:12], raw[12:], self._aad(kind, key, owner)))
            if not isinstance(value, dict):
                raise ValueError
            return Record(value, owner, row.status, row.revision, row.expires_at)
        except Exception:
            raise WorkError("work_state_unavailable") from None

    async def _lock_admission(self, session, now):
        # All task transactions acquire this before conversation/job/plan rows.
        # The UPDATE is also a real write lock on SQLite (FOR UPDATE is ignored).
        await session.execute(update(Row).where(*self._where("guard", "admission")).values(updated_at=now))

    def _new_row(self, kind, key, owner, value, *, status, ttl, now):
        return Row(
            namespace=self.namespace, kind=kind, key_hash=self.hash(key), owner_hash=self.hash(owner), status=status, revision=secrets.randbits(60), expires_at=now + ttl, updated_at=now, ciphertext=self._encrypt(kind, key, owner, value)
        )

    def _update_row(self, row, kind, key, owner, value, *, status=None, ttl=None, now):
        row.ciphertext = self._encrypt(kind, key, owner, value)
        row.status = row.status if status is None else status
        row.revision += 1
        row.updated_at = now
        if ttl is not None:
            row.expires_at = now + ttl
        return Record(value, owner, row.status, row.revision, row.expires_at)

    async def _conversation(self, session, owner, now, *, create=False, ttl=900):
        row = (await session.execute(select(Row).where(*self._where("conversation", owner)).with_for_update())).scalar_one_or_none()
        if row is not None and row.owner_hash != self.hash(owner):
            raise WorkError("work_state_unavailable")
        if row is not None and row.expires_at > now:
            return row, self._record(row, "conversation", owner, owner)
        if not create:
            raise WorkError("task_superseded")
        value = {"schema_version": 1, "turn_id": "", "task_ref": None, "task": {}, "context": {}, "context_expires_at": 0}
        if row is None:
            row = self._new_row("conversation", owner, owner, value, status="idle", ttl=ttl, now=now)
            session.add(row)
        else:
            self._update_row(row, "conversation", owner, owner, value, status="idle", ttl=ttl, now=now)
        await session.flush()
        return row, self._record(row, "conversation", owner, owner)

    def _check_task(self, conversation, task_ref):
        ref = TaskRef.parse(task_ref)
        if ref is None or TaskRef.parse(conversation.value.get("task_ref")) != ref:
            raise WorkError("task_superseded")
        return ref

    async def _cancel_unexecuted(self, session, owner, now):
        """Keep grants and terminal/executing receipts; invalidate old authority."""
        owned = (Row.namespace == self.namespace, Row.owner_hash == self.hash(owner))
        await session.execute(
            update(Row)
            .where(*owned, Row.kind == "job", Row.status.in_({"waiting", "pending", "authorizing", "authorized", "processing", "awaiting_input", "awaiting_authorization"}))
            .values(status="cancelled", revision=Row.revision + 1, updated_at=now)
        )
        await session.execute(update(Row).where(*owned, Row.kind == "plan", Row.status.in_({"waiting", "pending"})).values(status="cancelled", revision=Row.revision + 1, updated_at=now))
        # Legacy OAuth state is bearer-addressed (owner=''). An owner-bound
        # index created in the same transaction makes cancellation queryable
        # without decrypting or scanning other people's state payloads.
        index = Row.__table__.alias("task_state_index")
        states = select(index.c.key_hash).where(index.c.namespace == self.namespace, index.c.kind == "task_state", index.c.owner_hash == self.hash(owner))
        await session.execute(
            update(Row)
            .where(Row.namespace == self.namespace, Row.kind == "state", Row.owner_hash == self.hash(""), Row.key_hash.in_(states), Row.status == "authorizing")
            .values(status="cancelled", revision=Row.revision + 1, updated_at=now)
        )
        await session.execute(update(Row).where(*owned, Row.kind == "task_state").values(status="cancelled", revision=Row.revision + 1, updated_at=now))
        await session.execute(delete(Row).where(*owned, Row.kind == "context"))

    async def reserve_turn(self, owner, source, *, ttl=900, expected_ref=None):
        if not isinstance(source, str) or not source or len(source) > 4096:
            raise WorkError("missing_message_identity")
        # Ordinary Work chat also reserves turns; it must not depend on a later
        # business request to reclaim expired dedupe/conversation records.
        await self.purge()
        await self.create("guard", "admission", {}, ttl=10**10)
        now, key, turn_id = time.time(), owner + ":" + source, secrets.token_urlsafe(24)
        async with self._sf() as session:
            await self._lock_admission(session, now)
            previous = (await session.execute(select(Row).where(*self._where("turn", key)))).scalar_one_or_none()
            if previous is not None and previous.expires_at > now:
                return None
            if previous is not None:
                await session.delete(previous)
                await session.flush()
            row, old = await self._conversation(session, owner, now, create=expected_ref is None, ttl=ttl)
            if expected_ref is not None:
                self._check_task(old, expected_ref)
            value = dict(old.value)
            context = deepcopy(value.get("context", {})) if value.get("context_expires_at", 0) > now else {}
            reservation = TurnReservation(turn_id, TaskRef.parse(value.get("task_ref")), deepcopy(value.get("task", {})), context)
            value["turn_id"] = turn_id
            # The planner reasons over this exact bounded snapshot. A previous
            # task finishing while the planner awaits must not substitute a
            # different candidate list under the user's numbered selection.
            value["reserved_context"] = context
            value["reserved_context_expires_at"] = value.get("context_expires_at", 0)
            self._update_row(row, "conversation", owner, owner, value, ttl=ttl, now=now)
            session.add(self._new_row("turn", key, owner, {"turn_id": turn_id}, status="claimed", ttl=ttl, now=now))
            await session.commit()
        return reservation

    async def finish_turn(self, owner, turn_id, *, mode, task=None, ttl=900):
        if mode not in {"new", "continue", "exit"} or task is not None and not isinstance(task, dict):
            raise WorkError("invalid_task_transition")
        await self.create("guard", "admission", {}, ttl=10**10)
        now = time.time()
        async with self._sf() as session:
            await self._lock_admission(session, now)
            row, old = await self._conversation(session, owner, now)
            if not turn_id or old.value.get("turn_id") != turn_id:
                raise WorkError("task_superseded")
            previous = TaskRef.parse(old.value.get("task_ref"))
            if mode == "continue" and previous is None:
                raise WorkError("task_superseded")
            await self._cancel_unexecuted(session, owner, now)
            value = dict(old.value)
            ref = None if mode == "exit" else TaskRef.new(task_id=previous.task_id if mode == "continue" else None)
            keep_context = mode == "continue" and value.get("reserved_context_expires_at", 0) > now
            value.update(
                task_ref=ref.as_dict() if ref else None,
                task=deepcopy(task or {}) if ref else {},
                context=value.get("reserved_context", {}) if keep_context else {},
                context_expires_at=value.get("reserved_context_expires_at", 0) if keep_context else 0,
            )
            value.pop("reserved_context", None)
            value.pop("reserved_context_expires_at", None)
            if mode == "new":
                value["execution_index"] = {"task_id": ref.task_id, "started": False, "plan_ids": [], "complete": True}
            elif mode == "exit" or not isinstance(value.get("execution_index"), dict) or value["execution_index"].get("task_id") != ref.task_id:
                # Legacy continuations retain no false "not executed" marker.
                value.pop("execution_index", None)
            # A reservation is one-use; a delayed second finish cannot re-open it.
            value["turn_id"] = ""
            self._update_row(row, "conversation", owner, owner, value, status="active" if ref else "idle", ttl=ttl, now=now)
            await session.commit()
        return ref

    async def assert_task(self, owner, task_ref):
        conversation = await self.read("conversation", owner, owner=owner)
        if conversation is None:
            raise WorkError("task_superseded")
        self._check_task(conversation, task_ref)

    async def read_task_context(self, owner, task_ref):
        conversation = await self.read("conversation", owner, owner=owner)
        if conversation is None:
            raise WorkError("task_superseded")
        self._check_task(conversation, task_ref)
        expires = conversation.value.get("context_expires_at", 0)
        if expires <= time.time() or not conversation.value.get("context"):
            return None
        return Record(deepcopy(conversation.value["context"]), owner, "active", conversation.revision, expires)

    async def save_task_context(self, owner, task_ref, value, *, ttl=600):
        if not isinstance(value, dict):
            raise WorkError("invalid_task_transition")
        await self.create("guard", "admission", {}, ttl=10**10)
        now = time.time()
        async with self._sf() as session:
            await self._lock_admission(session, now)
            row, conversation = await self._conversation(session, owner, now)
            self._check_task(conversation, task_ref)
            data = dict(conversation.value)
            data.update(context=deepcopy(value), context_expires_at=now + ttl)
            result = self._update_row(row, "conversation", owner, owner, data, ttl=max(row.expires_at - now, ttl), now=now)
            await session.commit()
        return Record(deepcopy(value), owner, "active", result.revision, now + ttl)

    async def create_task_state(self, state, value, *, owner, task_ref, ttl=600):
        await self.create("guard", "admission", {}, ttl=10**10)
        now = time.time()
        async with self._sf() as session:
            await self._lock_admission(session, now)
            _, conversation = await self._conversation(session, owner, now)
            ref = self._check_task(conversation, task_ref)
            data = {**value, "task_owner": owner, "task_ref": ref.as_dict()}
            row = self._new_row("state", state, "", data, status="authorizing", ttl=ttl, now=now)
            session.add(row)
            session.add(self._new_row("task_state", state, owner, {}, status="active", ttl=ttl, now=now))
            result = Record(data, "", row.status, row.revision, row.expires_at)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return None
        return result

    async def change_task_record(self, kind, key, old, *, owner, task_ref, value=None, status=None, ttl=None):
        if kind not in {"job", "plan", "state"}:
            raise WorkError("invalid_task_transition")
        await self.create("guard", "admission", {}, ttl=10**10)
        now = time.time()
        async with self._sf() as session:
            await self._lock_admission(session, now)
            conversation_row, conversation = await self._conversation(session, owner, now)
            ref = self._check_task(conversation, task_ref)
            if kind == "state":
                if old.owner or old.value.get("task_owner") != owner or TaskRef.parse(old.value.get("task_ref")) != ref:
                    raise WorkError("task_superseded")
            elif old.owner != owner or TaskRef.parse(old.value.get("task_ref")) != ref:
                raise WorkError("task_superseded")
            row = (await session.execute(select(Row).where(*self._where(kind, key), Row.owner_hash == self.hash(old.owner)).with_for_update())).scalar_one_or_none()
            if row is None or row.revision != old.revision or row.status != old.status or row.expires_at <= now:
                return None
            data = old.value if value is None else dict(value)
            data = {**data, "task_ref": ref.as_dict()}
            if kind == "state":
                data["task_owner"] = owner
            if kind == "plan" and old.status == "pending" and status == "executing" and data.get("operation") in {"event_create", "event_update", "calendar_batch"}:
                conversation_data = dict(conversation.value)
                index = conversation_data.get("execution_index")
                if not isinstance(index, dict) or index.get("task_id") != ref.task_id:
                    # A legacy task may already have unknown older executions.
                    index = {"task_id": ref.task_id, "started": True, "plan_ids": [], "complete": False}
                else:
                    index = dict(index)
                ids = list(index.get("plan_ids", []))
                if key not in ids:
                    ids.append(key)
                index.update(started=True, plan_ids=ids[-10:], complete=index.get("complete") is True and len(ids) <= 10)
                conversation_data["execution_index"] = index
                self._update_row(conversation_row, "conversation", owner, owner, conversation_data, now=now)
            result = self._update_row(row, kind, key, old.owner, data, status=status, ttl=ttl, now=now)
            await session.commit()
        return result

    async def claim_plan(self, key, old, *, owner, task_ref, value, ttl=90 * 86400):
        if old.status != "pending":
            return None
        return await self.change_task_record("plan", key, old, owner=owner, task_ref=task_ref, value=value, status="executing", ttl=ttl)

    async def claim_batch_item(self, key, *, parent_id, owner, task_ref, value, ttl=90 * 86400):
        """A parent claim is not permission to start items after topic exit."""
        ref = TaskRef.parse(task_ref)
        await self.create("guard", "admission", {}, ttl=10**10)
        now = time.time()
        async with self._sf() as session:
            await self._lock_admission(session, now)
            if ref is not None:
                _, conversation = await self._conversation(session, owner, now)
                self._check_task(conversation, ref)
            parent = (await session.execute(select(Row).where(*self._where("plan", parent_id), Row.owner_hash == self.hash(owner)).with_for_update())).scalar_one_or_none()
            if parent is None or parent.status != "executing" or parent.expires_at <= now:
                raise WorkError("batch_parent_not_executing")
            parent_data = self._record(parent, "plan", parent_id, owner).value
            if parent_data.get("operation") != "calendar_batch" or TaskRef.parse(parent_data.get("task_ref")) != ref or key not in {item["id"] for item in parent_data.get("batch", [])}:
                raise WorkError("batch_parent_mismatch")
            previous = (await session.execute(select(Row).where(*self._where("batch_item", key)))).scalar_one_or_none()
            if previous is not None:
                return None
            data = {**value, "id": key, "parent_id": parent_id, "task_ref": ref.as_dict() if ref else None}
            row = self._new_row("batch_item", key, owner, data, status="executing", ttl=ttl, now=now)
            session.add(row)
            await session.commit()
        return Record(data, owner, "executing", row.revision, row.expires_at)

    async def recent_calendar_records(self, owner, *, limit=40):
        """Read a bounded set of authenticated receipts without plaintext IDs in SQL."""
        async with self._sf() as session:
            rows = (
                (
                    await session.execute(
                        select(Row)
                        .where(Row.namespace == self.namespace, Row.owner_hash == self.hash(owner), Row.kind.in_({"plan", "batch_item"}), Row.status == "completed", Row.expires_at > time.time())
                        .order_by(Row.updated_at.desc())
                        .limit(min(max(limit, 1), 100))
                    )
                )
                .scalars()
                .all()
            )
            result = []
            for row in rows:
                try:
                    raw = base64.urlsafe_b64decode(row.ciphertext)
                    aad = json.dumps([self.namespace, row.kind, row.key_hash, self.hash(owner)], separators=(",", ":")).encode()
                    value = json.loads(self._cipher.decrypt(raw[:12], raw[12:], aad))
                    if not isinstance(value, dict):
                        raise ValueError
                    result.append(value)
                except Exception:
                    raise WorkError("work_state_unavailable") from None
            return result

    async def calendar_execution_records(self, owner, *, task_ref=None, limit=100):
        """Read the current task index, or bounded legacy owner-only history.

        Truncation is explicit: callers must not infer no prior execution from
        a bounded scan. This is discovery metadata, never write authority.
        """
        ref = TaskRef.parse(task_ref)
        if ref is not None:
            conversation = await self.read("conversation", owner, owner=owner)
            if conversation is None:
                raise WorkError("task_superseded")
            self._check_task(conversation, ref)
            index = conversation.value.get("execution_index")
            if index is not None:
                if not isinstance(index, dict) or index.get("task_id") != ref.task_id or type(index.get("started")) is not bool or not isinstance(index.get("plan_ids"), list):
                    return [], False, True
                ids = index["plan_ids"]
                if len(ids) > 10 or any(not isinstance(key, str) or not key or len(key) > 128 for key in ids):
                    return [], False, True
                complete = index.get("complete") is True
                if not index["started"]:
                    return [], complete and not ids, bool(ids)
                records = []
                for key in ids:
                    record = await self.read("plan", key, owner=owner)
                    previous = TaskRef.parse(record.value.get("task_ref")) if record else None
                    if record is None or record.status not in {"executing", "completed", "partial", "uncertain"} or previous is None or previous.task_id != ref.task_id:
                        complete = False
                        continue
                    records.append({"status": record.status, "value": record.value})
                return records, complete and bool(records), True
        limit = min(max(limit, 1), 100)
        async with self._sf() as session:
            rows = (
                (
                    await session.execute(
                        select(Row)
                        .where(Row.namespace == self.namespace, Row.owner_hash == self.hash(owner), Row.kind == "plan", Row.status.in_({"executing", "completed", "partial", "uncertain"}), Row.expires_at > time.time())
                        .order_by(Row.updated_at.desc())
                        .limit(limit + 1)
                    )
                )
                .scalars()
                .all()
            )
            result = []
            for row in rows[:limit]:
                try:
                    raw = base64.urlsafe_b64decode(row.ciphertext)
                    aad = json.dumps([self.namespace, row.kind, row.key_hash, self.hash(owner)], separators=(",", ":")).encode()
                    value = json.loads(self._cipher.decrypt(raw[:12], raw[12:], aad))
                    if not isinstance(value, dict):
                        raise ValueError
                    result.append({"status": row.status, "value": value})
                except Exception:
                    raise WorkError("work_state_unavailable") from None
            return result, len(rows) <= limit, None

    async def read(self, kind, key, *, owner=""):
        async with self._sf() as session:
            row = (await session.execute(select(Row).where(*self._where(kind, key), Row.owner_hash == self.hash(owner), Row.expires_at > time.time()))).scalar_one_or_none()
            return self._record(row, kind, key, owner) if row else None

    async def create(self, kind, key, value, *, owner="", ttl=600, status="waiting"):
        now = time.time()
        record = Record(value, owner, status, secrets.randbits(60), now + ttl)
        async with self._sf() as session:
            session.add(
                Row(
                    namespace=self.namespace,
                    kind=kind,
                    key_hash=self.hash(key),
                    owner_hash=self.hash(owner),
                    status=status,
                    revision=record.revision,
                    expires_at=record.expires_at,
                    updated_at=now,
                    ciphertext=self._encrypt(kind, key, owner, value),
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return None
        return record

    async def change(self, kind, key, old: Record, *, value=None, status=None, ttl=None):
        # A task-marked record may not silently fall back to the legacy writer
        # when a caller omits the explicit fence. Already admitted execution
        # receipts must remain finishable after the user changes the subject.
        ref = TaskRef.parse(old.value.get("task_ref")) if kind in {"job", "plan", "state"} else None
        if ref is not None and not (kind == "plan" and old.status == "executing"):
            owner = old.value.get("task_owner", "") if kind == "state" else old.owner
            return await self.change_task_record(kind, key, old, owner=owner, task_ref=ref, value=value, status=status, ttl=ttl)
        now = time.time()
        new = Record(old.value if value is None else value, old.owner, status or old.status, old.revision + 1, old.expires_at if ttl is None else now + ttl)
        async with self._sf() as session:
            result = await session.execute(
                update(Row)
                .where(*self._where(kind, key), Row.owner_hash == self.hash(old.owner), Row.revision == old.revision, Row.status == old.status, Row.expires_at > now)
                .values(ciphertext=self._encrypt(kind, key, old.owner, new.value), status=new.status, revision=new.revision, expires_at=new.expires_at, updated_at=now)
            )
            await session.commit()
            return new if result.rowcount == 1 else None

    async def remove(self, kind, key, old: Record):
        async with self._sf() as session:
            result = await session.execute(delete(Row).where(*self._where(kind, key), Row.owner_hash == self.hash(old.owner), Row.revision == old.revision))
            await session.commit()
            return result.rowcount == 1

    async def put(self, kind, key, value, *, owner="", ttl=600, status="waiting"):
        for _ in range(4):
            old = await self.read(kind, key, owner=owner)
            if old:
                new = await self.change(kind, key, old, value=value, status=status, ttl=ttl)
            else:
                await self.purge()
                new = await self.create(kind, key, value, owner=owner, ttl=ttl, status=status)
            if new:
                return new
        raise WorkError("work_state_conflict")

    async def purge(self):
        async with self._sf() as session:
            await session.execute(delete(Row).where(Row.namespace == self.namespace, Row.expires_at <= time.time(), Row.kind != "guard"))
            await session.commit()

    async def admit(self, kind, key, value, *, owner, ttl, active, limit, owner_limit, task_ref=None):
        # An UPDATE of one namespace guard serializes capacity-check + insert in
        # the same transaction, also on SQLite where FOR UPDATE is ignored.
        task_ref = TaskRef.parse(task_ref) or TaskRef.parse(value.get("task_ref"))
        await self.create("guard", "admission", {}, ttl=10**10)
        now = time.time()
        async with self._sf() as session:
            await session.execute(update(Row).where(*self._where("guard", "admission")).values(updated_at=now))
            if task_ref is not None:
                _, conversation = await self._conversation(session, owner, now)
                ref = self._check_task(conversation, task_ref)
                if value.get("task_ref") is not None and TaskRef.parse(value["task_ref"]) != ref:
                    raise WorkError("task_superseded")
                value = {**value, "task_ref": ref.as_dict()}
            where = (Row.namespace == self.namespace, Row.kind == kind, Row.status.in_(active), Row.expires_at > now)
            total = (await session.execute(select(func.count()).where(*where))).scalar_one()
            personal = (await session.execute(select(func.count()).where(*where, Row.owner_hash == self.hash(owner)))).scalar_one()
            if total >= limit or personal >= owner_limit:
                await session.rollback()
                return None
            revision = secrets.randbits(60)
            session.add(
                Row(namespace=self.namespace, kind=kind, key_hash=self.hash(key), owner_hash=self.hash(owner), status="waiting", revision=revision, expires_at=now + ttl, updated_at=now, ciphertext=self._encrypt(kind, key, owner, value))
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return None
            return Record(value, owner, "waiting", revision, now + ttl)

    async def acquire(self, name, holder, *, ttl):
        old = await self.read("lease", name)
        if old:
            if old.value.get("holder") != holder:
                return None
            return await self.change("lease", name, old, ttl=ttl)
        await self.purge()
        return await self.create("lease", name, {"holder": holder}, ttl=ttl)

    async def release(self, name, holder):
        old = await self.read("lease", name)
        return bool(old and old.value.get("holder") == holder and await self.remove("lease", name, old))

    async def audit(self, principal, operation, target, stage, *, plan_id="", receipt=""):
        # Deliberately exclude request text, snapshots, document content and tokens.
        await self.create(
            "audit",
            secrets.token_urlsafe(32),
            {"principal": principal.__dict__, "operation": operation, "target": target, "stage": stage, "plan_id": plan_id, "receipt": receipt, "at": time.time()},
            owner="audit",
            ttl=90 * 86400,
            status=stage,
        )

    async def invalidate_owner(self, owner):
        await self.create("guard", "admission", {}, ttl=10**10)
        async with self._sf() as session:
            await session.execute(update(Row).where(*self._where("guard", "admission")).values(updated_at=time.time()))
            now = time.time()
            row = (await session.execute(select(Row).where(*self._where("conversation", owner), Row.owner_hash == self.hash(owner)).with_for_update())).scalar_one_or_none()
            if row is not None:
                value = {"schema_version": 1, "turn_id": "", "task_ref": None, "task": {}, "context": {}, "context_expires_at": 0}
                self._update_row(row, "conversation", owner, owner, value, status="idle", ttl=900, now=now)
            await self._cancel_unexecuted(session, owner, now)
            await session.execute(delete(Row).where(Row.namespace == self.namespace, Row.owner_hash == self.hash(owner), Row.kind.in_(GRANT_KINDS)))
            await session.commit()

    async def install_authorized(self, ticket, old, *, owner, value, ttl, task_ref=None, grant_kind="grant"):
        """Install a grant atomically with consuming its still-live OAuth job.

        This shares the revocation transaction guard. An old callback cannot
        re-install a token after a concurrent user explicitly revoked consent.
        """
        if grant_kind not in GRANT_KINDS:
            raise WorkError("invalid_grant_kind")
        task_ref = TaskRef.parse(task_ref) or TaskRef.parse(old.value.get("task_ref"))
        await self.create("guard", "admission", {}, ttl=10**10)
        now, revision = time.time(), secrets.randbits(60)
        async with self._sf() as session:
            await session.execute(update(Row).where(*self._where("guard", "admission")).values(updated_at=now))
            if task_ref is not None:
                _, conversation = await self._conversation(session, owner, now)
                ref = self._check_task(conversation, task_ref)
                if TaskRef.parse(old.value.get("task_ref")) != ref:
                    raise WorkError("task_superseded")
            result = await session.execute(
                update(Row).where(*self._where("job", ticket), Row.owner_hash == self.hash(owner), Row.status == "authorizing", Row.revision == old.revision, Row.expires_at > now).values(status="authorized", revision=Row.revision + 1)
            )
            if result.rowcount != 1:
                await session.rollback()
                raise WorkError("authorization_or_binding_invalid")
            await session.execute(delete(Row).where(*self._where(grant_kind, owner), Row.owner_hash == self.hash(owner)))
            session.add(
                Row(
                    namespace=self.namespace,
                    kind=grant_kind,
                    key_hash=self.hash(owner),
                    owner_hash=self.hash(owner),
                    status="active",
                    revision=revision,
                    expires_at=now + ttl,
                    updated_at=now,
                    ciphertext=self._encrypt(grant_kind, owner, owner, value),
                )
            )
            await session.commit()
        return Record(value, owner, "active", revision, now + ttl), Record(old.value, old.owner, "authorized", old.revision + 1, old.expires_at)
