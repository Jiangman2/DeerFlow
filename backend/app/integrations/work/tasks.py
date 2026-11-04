"""Server-owned task references, independent of encrypted-row CAS revisions.

Only a new trusted user turn may create a conversation/task. Callbacks and
background work may validate an existing reference, never recreate one.
"""

import re
import secrets
from dataclasses import dataclass, field

from .client import WorkError

_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{16,128}\Z")


@dataclass(frozen=True)
class TaskRef:
    task_id: str
    generation: str

    def __post_init__(self):
        if any(not isinstance(value, str) or not _IDENTIFIER.fullmatch(value) for value in (self.task_id, self.generation)):
            raise WorkError("invalid_task_reference")

    @classmethod
    def new(cls, *, task_id=None):
        return cls(task_id or secrets.token_urlsafe(24), secrets.token_urlsafe(24))

    @classmethod
    def parse(cls, value):
        if value is None:
            return None
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict) or set(value) != {"task_id", "generation"}:
            raise WorkError("invalid_task_reference")
        return cls(**value)

    def as_dict(self):
        return {"task_id": self.task_id, "generation": self.generation}


@dataclass(frozen=True)
class TurnReservation:
    turn_id: str
    task_ref: TaskRef | None
    task: dict = field(repr=False)
    context: dict = field(repr=False)
