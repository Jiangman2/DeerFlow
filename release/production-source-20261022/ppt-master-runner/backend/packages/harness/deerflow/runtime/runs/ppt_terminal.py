"""Run-local terminal receipt fallback when LangGraph streaming is cancelled.

Only records events already emitted by the external PPT bridge. It neither
decides task status nor synthesizes model steps, usage or confirmation receipts.
The owning run worker persists an unconsumed receipt through its existing event
buffer before closing the public stream. No global/per-user registry is used.
"""
from copy import deepcopy
from typing import Any


class PptTerminalReceipt:
    def __init__(self, thread_id: str, run_id: str) -> None:
        self.thread_id = thread_id
        self.run_id = run_id
        self._pending: dict[str, Any] | None = None
        self._streamed: dict[str, Any] | None = None

    def _matches(self, event: Any) -> bool:
        return (
            isinstance(event, dict)
            and event.get('type') in {'task_completed', 'task_failed', 'task_cancelled'}
            and event.get('thread_id') == self.thread_id
            and event.get('task_id') == self.run_id
            and event.get('model_name') == 'ppt-master-agent-host'
        )

    def capture(self, event: Any) -> None:
        if self._matches(event):
            self._pending = deepcopy(event)

    def observe_streamed(self, event: Any) -> None:
        if self._matches(event):
            self._streamed = deepcopy(event)

    def take_unstreamed(self) -> dict[str, Any] | None:
        pending, self._pending = self._pending, None
        return pending if pending != self._streamed else None
