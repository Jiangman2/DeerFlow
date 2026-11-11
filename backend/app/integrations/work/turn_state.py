"""Bounded single-process task state for the explicitly local memory profile.

Production always uses WorkStore. This adapter shares its fencing contract so
local UAT does not revive old authorizations when the user changes topic.
"""

import asyncio
import copy
import secrets
import time
from types import SimpleNamespace

from .client import WorkError
from .tasks import TaskRef, TurnReservation


class MemoryTurns:
    def __init__(self):
        self._rows = {}
        self._seen = {}
        self._lock = asyncio.Lock()

    def _row(self, owner):
        row = self._rows.get(owner)
        return row if row and row["expires_at"] > time.monotonic() else None

    async def reserve_turn(self, owner, source, *, ttl=900):
        async with self._lock:
            now = time.monotonic()
            self._seen = {key: expiry for key, expiry in self._seen.items() if expiry > now}
            self._rows = {key: row for key, row in self._rows.items() if row["expires_at"] > now}
            key = (owner, source)
            if key in self._seen:
                return None
            if len(self._seen) >= 4096 or len(self._rows) >= 256 and owner not in self._rows:
                raise WorkError("turn_capacity")
            self._seen[key] = now + ttl
            row = self._rows.setdefault(owner, {"task_ref": None, "task": {}, "context": {}, "context_expires_at": 0})
            row.update(turn_id=secrets.token_urlsafe(24), expires_at=now + ttl)
            context = copy.deepcopy(row["context"]) if row["context_expires_at"] > now else {}
            row["reserved_context"] = context
            row["reserved_context_expires_at"] = row["context_expires_at"]
            return TurnReservation(row["turn_id"], row["task_ref"], copy.deepcopy(row["task"]), copy.deepcopy(context))

    async def finish_turn(self, owner, turn_id, *, mode="new", task=None, ttl=900):
        async with self._lock:
            row = self._row(owner)
            if not row or row["turn_id"] != turn_id:
                raise WorkError("task_superseded")
            old = row["task_ref"]
            if mode == "exit":
                row.update(task_ref=None, task={}, context={}, turn_id="", context_expires_at=0)
                return None
            if mode not in {"new", "continue"} or mode == "continue" and old is None:
                raise WorkError("task_superseded")
            ref = TaskRef(old.task_id if mode == "continue" else secrets.token_urlsafe(24), secrets.token_urlsafe(24))
            row.update(task_ref=ref, task=copy.deepcopy(task or {}), expires_at=time.monotonic() + ttl)
            if mode == "new":
                row["context"] = {}
                row["context_expires_at"] = 0
            else:
                expiry = row.get("reserved_context_expires_at", 0)
                row["context"] = copy.deepcopy(row["reserved_context"]) if expiry > time.monotonic() else {}
                row["context_expires_at"] = expiry
            row.pop("reserved_context", None)
            row.pop("reserved_context_expires_at", None)
            row["turn_id"] = ""
            return ref

    async def assert_task(self, owner, ref):
        async with self._lock:
            row = self._row(owner)
            if not ref or not row or row["task_ref"] != ref:
                raise WorkError("task_superseded")

    async def read_task_context(self, owner, ref):
        await self.assert_task(owner, ref)
        row = self._row(owner)
        return SimpleNamespace(value=copy.deepcopy(row["context"])) if row and row["context"] and row["context_expires_at"] > time.monotonic() else None

    async def save_task_context(self, owner, ref, value, *, ttl=600):
        async with self._lock:
            row = self._row(owner)
            if not ref or not row or row["task_ref"] != ref:
                raise WorkError("task_superseded")
            row["context"] = copy.deepcopy(value)
            row["context_expires_at"] = time.monotonic() + ttl

    def clear(self):
        self._rows.clear()
        self._seen.clear()
