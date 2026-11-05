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
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from deerflow.persistence.work.model import WorkRecordRow as Row

from .client import WorkError


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

    async def admit(self, kind, key, value, *, owner, ttl, active, limit, owner_limit):
        # An UPDATE of one namespace guard serializes capacity-check + insert in
        # the same transaction, also on SQLite where FOR UPDATE is ignored.
        await self.create("guard", "admission", {}, ttl=10**10)
        now = time.time()
        async with self._sf() as session:
            await session.execute(update(Row).where(*self._where("guard", "admission")).values(updated_at=now))
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
            await session.execute(
                update(Row)
                .where(Row.namespace == self.namespace, Row.owner_hash == self.hash(owner), Row.kind.in_({"job", "plan"}), Row.status.in_({"waiting", "pending", "authorizing", "authorized"}))
                .values(status="cancelled", revision=Row.revision + 1)
            )
            await session.execute(delete(Row).where(Row.namespace == self.namespace, Row.owner_hash == self.hash(owner), Row.kind == "context"))
            await session.execute(delete(Row).where(Row.namespace == self.namespace, Row.owner_hash == self.hash(owner), Row.kind == "grant"))
            await session.commit()

    async def install_authorized(self, ticket, old, *, owner, value, ttl):
        """Install a grant atomically with consuming its still-live OAuth job.

        This shares the revocation transaction guard. An old callback cannot
        re-install a token after a concurrent user explicitly revoked consent.
        """
        await self.create("guard", "admission", {}, ttl=10**10)
        now, revision = time.time(), secrets.randbits(60)
        async with self._sf() as session:
            await session.execute(update(Row).where(*self._where("guard", "admission")).values(updated_at=now))
            result = await session.execute(
                update(Row).where(*self._where("job", ticket), Row.owner_hash == self.hash(owner), Row.status == "authorizing", Row.revision == old.revision, Row.expires_at > now).values(status="authorized", revision=Row.revision + 1)
            )
            if result.rowcount != 1:
                await session.rollback()
                raise WorkError("authorization_or_binding_invalid")
            await session.execute(delete(Row).where(*self._where("grant", owner), Row.owner_hash == self.hash(owner)))
            session.add(
                Row(
                    namespace=self.namespace,
                    kind="grant",
                    key_hash=self.hash(owner),
                    owner_hash=self.hash(owner),
                    status="active",
                    revision=revision,
                    expires_at=now + ttl,
                    updated_at=now,
                    ciphertext=self._encrypt("grant", owner, owner, value),
                )
            )
            await session.commit()
        return Record(value, owner, "active", revision, now + ttl), Record(old.value, old.owner, "authorized", old.revision + 1, old.expires_at)
