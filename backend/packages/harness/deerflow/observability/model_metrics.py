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
from contextvars import ContextVar
from functools import lru_cache

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
            scope = _invocation_scope.get()
            if scope is not None and scope.owner is _current_task():
                scope.tokens.append((self, token))
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
            monotonic_now = self.monotonic()
            active = {key: 0 for key in self.series}
            oldest = dict.fromkeys(self.series, 0.0)
            for key, started in self.active.values():
                active[key] += 1
                oldest[key] = max(oldest[key], max(0, monotonic_now - started))
            slot = str(int(now // 5) * 5)
            for key in self.series:
                self._row(key)["samples"][slot] = active[key]
            return {
                "at": now,
                "dropped": self.dropped,
                "series": {k: {**v, "active": active[k], "oldest_active_seconds": oldest[k]} for k, v in self.series.items()},
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
        self.on_chat_model_start(serialized, prompts, run_id=run_id, **kwargs)

    def on_llm_end(self, response, *, run_id, **kwargs):
        metrics.finish(run_id)

    def on_llm_error(self, error, *, run_id, **kwargs):
        metrics.finish(run_id, outcome_for(error))


def _current_task():
    with suppress(RuntimeError):
        return asyncio.current_task()
    return None


class _InvocationScope:
    def __init__(self):
        self.owner = _current_task()
        self.tokens = []


_invocation_scope: ContextVar[_InvocationScope | None] = ContextVar("model_metrics_invocation", default=None)


class ModelMetricsMixin:
    """Balance callbacks at the actual async model invocation boundary.

    LangChain 1.3.3 agenerate skips terminal callbacks if its gather is cancelled.
    A task-done hook is insufficient: wait_for/asyncio.timeout can cancel the
    current task and the caller can catch that timeout and continue. This scope
    ends before control returns to that caller. Direct stream methods retain
    LangChain's generator-close handling, including cross-task stream handoff.
    """

    async def agenerate(self, *args, **kwargs):
        scope = _InvocationScope()
        reset = _invocation_scope.set(scope)
        outcome = "success"
        try:
            return await super().agenerate(*args, **kwargs)
        except BaseException as exc:
            outcome = outcome_for(exc)
            raise
        finally:
            _invocation_scope.reset(reset)
            for collector, token in scope.tokens:
                # Normal callbacks win. This is only an idempotent fallback;
                # telemetry errors must never replace a provider result/error.
                with suppress(Exception):
                    collector.finish(token, outcome)


@lru_cache(maxsize=64)
def instrument_model_class(model_class):
    """Preserve provider fields/methods and isinstance, without global patches."""
    if issubclass(model_class, ModelMetricsMixin):
        return model_class

    @classmethod
    def lc_id(cls):
        # Tracing/serialization should still identify the configured provider,
        # not an unimportable runtime-generated instrumentation class.
        return model_class.lc_id()

    return type("Observed" + model_class.__name__, (ModelMetricsMixin, model_class), {"__module__": __name__, "lc_id": lc_id})
