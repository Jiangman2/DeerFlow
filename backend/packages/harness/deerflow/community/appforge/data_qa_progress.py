"""Closed-vocabulary UI events; never forward upstream prose or credentials."""

from collections.abc import Mapping

RUNNING = frozenset({"waiting", "processing", "answering"})
TERMINAL = frozenset({"completed", "empty", "clarification", "failed", "timeout", "cancelled"})


class DataQAProgress:
    def __init__(self, runtime):
        context = getattr(runtime, "context", None)
        config = getattr(runtime, "config", None)
        context = context if isinstance(context, Mapping) else {}
        config = config if isinstance(config, Mapping) else {}
        configurable = config.get("configurable", {})
        configurable = configurable if isinstance(configurable, Mapping) else {}
        self.thread_id = context.get("thread_id") or configurable.get("thread_id")
        self.run_id = context.get("run_id") or configurable.get("run_id")
        self.call_id = getattr(runtime, "tool_call_id", None)
        self.writer = getattr(runtime, "stream_writer", None)
        self.seen = set()
        self.finished = False
        self.sequence = 0

    def emit(self, stage):
        if self.finished or stage in self.seen or stage not in RUNNING | TERMINAL:
            return
        self.seen.add(stage)
        self.finished = stage in TERMINAL
        self.sequence += 1
        if not callable(self.writer) or not all(isinstance(v, str) and 0 < len(v) <= 256 for v in (self.thread_id, self.run_id, self.call_id)):
            return
        try:
            self.writer({"type": "data_qa_progress", "thread_id": self.thread_id, "run_id": self.run_id, "tool_call_id": self.call_id, "tool": "datalake_data_qa", "stage": stage, "sequence": self.sequence})
        except Exception:
            # A closed observer must not change the query/result contract.
            self.writer = None
