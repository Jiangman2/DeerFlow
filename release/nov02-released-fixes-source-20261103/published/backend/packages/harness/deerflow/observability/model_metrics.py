"""Bounded, content-free model telemetry; recording never performs network I/O.

The Gateway publisher exports idempotent per-process minute snapshots to Redis.
This measures application invocations, not provider-internal retries or GPU load.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager, contextmanager, suppress

from langchain_core.callbacks import BaseCallbackHandler


class ModelMetrics:
    def __init__(self, *, clock=time.time, monotonic=time.monotonic, enabled=False):
        self.clock, self.monotonic, self.enabled = clock, monotonic, enabled
        self.lock = threading.Lock()
        self.series: dict[str, dict] = {}
        self.active: dict[str, tuple[str, float]] = {}
        self.minutes: OrderedDict[int, dict] = OrderedDict()
        self.dropped = 0

    def _minute(self):
        minute = int(self.clock() // 60) * 60
        self.minutes.setdefault(minute, {})
        for old in list(self.minutes):
            if old < minute - 120:
                del self.minutes[old]
        return self.minutes[minute]

    def _row(self, key):
        return self._minute().setdefault(key, {"started": 0, "success": 0, "error": 0, "timeout": 0, "cancelled": 0, "duration_seconds": 0.0, "samples": {}})

    def start(self, model: str, kind="text", operation="invoke", *, call_id=None):
        if not self.enabled:
            return None
        # Labels are operator/model-profile names only, never caller input/URLs.
        if not model or len(model) > 128 or kind not in {"text", "image", "video", "audio", "integration"} or operation not in {"invoke", "submit", "poll", "session", "relay", "download"}:
            return None
        key = f"{kind}:{operation}:{model}"
        with self.lock:
            if key not in self.series and len(self.series) >= 64 or len(self.active) >= 8192:
                self.dropped += 1
                return None
            token = str(call_id or uuid.uuid4())
            if token in self.active:
                return token
            self.series.setdefault(key, {"model": model, "kind": kind, "operation": operation})
            self.active[token] = (key, self.monotonic())
            self._row(key)["started"] += 1
            return token

    def finish(self, token, outcome="success"):
        if token is None:
            return
        with self.lock:
            entry = self.active.pop(str(token), None)
            if entry is None:
                return
            key, started = entry
            row = self._row(key)
            row[outcome if outcome in {"success", "error", "timeout", "cancelled"} else "error"] += 1
            row["duration_seconds"] += max(0, self.monotonic() - started)

    def record_job_event(self, *, created=False, outcome=None, duration=0):
        """Observe a committed OA transition without creating a model invocation."""
        if not self.enabled:
            return
        key = "integration:job:oa-policy-flow"
        with self.lock:
            if key not in self.series and len(self.series) >= 64:
                self.dropped += 1
                return
            self.series.setdefault(key, {"model": "oa-policy-flow", "kind": "integration", "operation": "job"})
            row = self._row(key)
            row["started"] += int(created)
            if outcome in {"success", "error"}:
                row[outcome] += 1
                row["duration_seconds"] += max(0, duration)

    def snapshot(self):
        with self.lock:
            now = self.clock()
            active = {key: 0 for key in self.series}
            for key, _ in self.active.values():
                active[key] += 1
            slot = str(int(now // 5) * 5)
            for key in self.series:
                self._row(key)["samples"][slot] = active[key]
            return {
                "at": now,
                "dropped": self.dropped,
                "series": {k: {**v, "active": active[k]} for k, v in self.series.items()},
                "minutes": {str(m): {k: {**v, "samples": dict(v["samples"])} for k, v in rows.items()} for m, rows in self.minutes.items()},
            }


metrics = ModelMetrics()


def outcome_for(error: BaseException) -> str:
    if isinstance(error, (asyncio.CancelledError, GeneratorExit)):
        return "cancelled"
    return "timeout" if "timeout" in type(error).__name__.lower() else "error"


@contextmanager
def measure_model(model: str, kind: str, operation="invoke"):
    token = None
    with suppress(Exception):
        token = metrics.start(model, kind, operation)
    try:
        yield
    except BaseException as exc:
        with suppress(Exception):
            metrics.finish(token, outcome_for(exc))
        raise
    else:
        with suppress(Exception):
            metrics.finish(token)


@asynccontextmanager
async def measure_model_async(model: str, kind: str, operation="invoke"):
    with measure_model(model, kind, operation):
        yield


class ModelMetricsCallback(BaseCallbackHandler):
    run_inline = True
    raise_error = False

    def __init__(self, model: str):
        self.model = model

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        metrics.start(self.model, call_id=run_id)

    def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
        metrics.start(self.model, call_id=run_id)

    def on_llm_end(self, response, *, run_id, **kwargs):
        metrics.finish(run_id)

    def on_llm_error(self, error, *, run_id, **kwargs):
        metrics.finish(run_id, outcome_for(error))
