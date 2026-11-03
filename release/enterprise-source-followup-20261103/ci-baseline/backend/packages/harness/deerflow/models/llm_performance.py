"""Opt-in, content-free adapter telemetry. No database, background task or queue."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
import uuid
from collections.abc import Mapping

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_counts: dict[str, list[int]] = {}  # pending admission, active; delete idle keys
_process_identity: tuple[int, str] | None = None


def enabled():
    return os.getenv('DEERFLOW_LLM_PERFORMANCE_LOGS', '').lower() in {'1', 'true', 'yes'}


def label(value):
    return re.sub(r'[^a-zA-Z0-9_.:/-]', '_', str(value or '-'))[:160]


def identity():
    global _process_identity
    pid = os.getpid()
    with _lock:
        if _process_identity is None or _process_identity[0] != pid:
            _counts.clear()
            _process_identity = (pid, uuid.uuid4().hex)
        return _process_identity


def _change(model, pending=0, active=0):
    with _lock:
        counters = _counts.setdefault(model, [0, 0])
        counters[0] += pending
        counters[1] += active
        result = dict(pending=counters[0], active=counters[1])
        if counters == [0, 0]:
            del _counts[model]
        return result


def _number(value):
    return value if type(value) is int and value >= 0 else None


def _text(value):
    # Never stringify a message, image or tool payload for diagnostics.
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(isinstance(block, Mapping) and block.get('type') in {'text', 'output_text'}
                   and _text(block.get('text')) for block in value)
    return False


class PerformanceSpan:
    """One adapter invocation, not necessarily one physical SDK retry attempt.

    Only scalar state is retained. Start/end snapshots are process-local;
    admitted/end epoch timestamps permit offline reconstruction across pods.
    """
    def __init__(self, *, model, mode, call_id, thread_id='-', run_id='-'):
        pid, process_id = identity()
        self.model = label(model)
        self.mode = mode
        self.fields = dict(schema=1, batch=label(os.getenv('DEERFLOW_LLM_PERFORMANCE_BATCH', 'default')),
                           pod=label(os.getenv('HOSTNAME')), pid=pid, process_id=process_id,
                           model=self.model, mode=mode, llm_call_id=label(call_id),
                           thread_id=label(thread_id), run_id=label(run_id),
                           concurrency_scope='process_model', request_scope='adapter_invocation')
        self.arrived = time.perf_counter()
        self.arrival_epoch = time.time()
        self.admitted = None
        self.admitted_epoch = None
        self.finished = False
        self.first = dict(effective=None, reasoning=None, content=None, tool=None)
        self.usage = dict(input_tokens=None, output_tokens=None, reasoning_tokens=None)
        self.active_at_admission = None
        _change(self.model, pending=1)

    def _emit(self, event, **values):
        # A broken log sink must not change a model result or leak a permit.
        try:
            logger.info('LLM_PERF %s', json.dumps(dict(self.fields, event=event, **values),
                                                separators=(',', ':'), ensure_ascii=True))
        except Exception:
            pass

    def admit(self):
        if self.admitted is not None or self.finished:
            return
        self.admitted = time.perf_counter()
        self.admitted_epoch = self.arrival_epoch + (self.admitted - self.arrived)
        snapshot = _change(self.model, pending=-1, active=1)
        self.active_at_admission = snapshot['active']
        self._emit('start', timestamp=self.admitted_epoch, arrived_epoch=self.arrival_epoch,
                   admitted_epoch=self.admitted_epoch, phase='admitted', **snapshot)

    def observe(self, message, *, streaming=True):
        usage = getattr(message, 'usage_metadata', None)
        if isinstance(usage, Mapping):
            for name in ('input_tokens', 'output_tokens'):
                value = _number(usage.get(name))
                if value is not None:
                    # LangChain chunk usage is additive; the adapter already
                    # converts provider cumulative snapshots into deltas.
                    self.usage[name] = (self.usage[name] or 0) + value if streaming else value
            details = usage.get('output_token_details')
            if isinstance(details, Mapping):
                value = _number(details.get('reasoning'))
                if value is not None:
                    self.usage['reasoning_tokens'] = (self.usage['reasoning_tokens'] or 0) + value if streaming else value
        if not streaming or self.admitted is None:
            return  # A completed non-stream response cannot measure TTFT.
        extra = getattr(message, 'additional_kwargs', None) or {}
        reasoning = isinstance(extra, Mapping) and _text(extra.get('reasoning_content'))
        tools = getattr(message, 'tool_call_chunks', None) or []
        tool = any(isinstance(item, Mapping) and any(_text(item.get(k)) for k in ('name', 'args'))
                   for item in tools)
        content = _text(getattr(message, 'content', None))
        now = None
        for kind, present in (('reasoning', reasoning), ('content', content), ('tool', tool),
                              ('effective', reasoning or content or tool)):
            if present and self.first[kind] is None:
                if now is None:
                    now = time.perf_counter()
                self.first[kind] = now

    def finish(self, outcome, error=None):
        if self.finished:
            return
        self.finished = True
        if isinstance(error, GeneratorExit):
            outcome = 'closed'
        elif isinstance(error, asyncio.CancelledError):
            outcome = 'cancelled'
        now = time.perf_counter()
        snapshot = _change(self.model, pending=-1) if self.admitted is None else _change(self.model, active=-1)
        if self.admitted is None:
            self._emit('start', timestamp=self.arrival_epoch + (now - self.arrived),
                       arrived_epoch=self.arrival_epoch, admitted_epoch=None,
                       phase='never_admitted', **snapshot)
        status = getattr(error, 'status_code', None)
        values = dict(timestamp=self.arrival_epoch + (now - self.arrived), arrived_epoch=self.arrival_epoch,
                      admitted_epoch=self.admitted_epoch, outcome=outcome,
                      error_class=type(error).__name__ if error is not None else None,
                      status_code=status if type(status) is int else None,
                      active_at_admission=self.active_at_admission,
                      total_ms=round((now - self.arrived) * 1000, 3),
                      queue_ms=round(((self.admitted or now) - self.arrived) * 1000, 3),
                      service_ms=None if self.admitted is None else round((now - self.admitted) * 1000, 3),
                      **snapshot, **self.usage)
        for kind, at in self.first.items():
            values['first_' + kind + '_ms'] = None if at is None else round((at - self.admitted) * 1000, 3)
        self._emit('end', **values)
