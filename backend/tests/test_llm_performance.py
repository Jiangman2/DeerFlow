"""Offline scalar telemetry tests; no HTTP, DB or model dependencies."""

import ast
import asyncio
import importlib.util
import json
import tempfile
import time
import unittest
import uuid
from collections.abc import Mapping
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "packages/harness/deerflow/models/llm_performance.py"
spec = importlib.util.spec_from_file_location("tested_performance", MODULE)
perf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(perf)
report_spec = importlib.util.spec_from_file_location("tested_report", ROOT.parent / "scripts/llm_performance_report.py")
report = importlib.util.module_from_spec(report_spec)
report_spec.loader.exec_module(report)


def message(content="", reasoning=None, tools=None, usage=None):
    return SimpleNamespace(content=content, additional_kwargs={"reasoning_content": reasoning}, tool_call_chunks=tools, usage_metadata=usage)


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        perf.identity()
        self.assertEqual(perf._counts, {})
        self.logs = []
        self.sink = patch.object(perf.logger, "info", side_effect=lambda fmt, body: self.logs.append(json.loads(body)))
        self.sink.start()
        self.addCleanup(self.sink.stop)

    def span(self, **kwargs):
        return perf.PerformanceSpan(model="test-model", mode="async", call_id=uuid.uuid4().hex, **kwargs)

    def tearDown(self):
        self.assertEqual(perf._counts, {})

    def test_two_records_no_content_and_meaningful_firsts(self):
        clock = [10.0]
        with patch.object(perf.time, "perf_counter", side_effect=lambda: clock[0]):
            span = self.span()
            clock[0] = 12
            span.admit()
            span.observe(message(""))
            span.observe(message("   ", tools=[{"id": "id-only", "index": 0}]))
            self.assertIsNone(span.first["effective"])
            clock[0] = 13
            span.observe(message(reasoning="PRIVATE REASONING"))
            clock[0] = 15
            span.observe(message("PRIVATE ANSWER"))
            clock[0] = 18
            span.finish("completed")
        self.assertEqual(len(self.logs), 2)
        end = self.logs[-1]
        self.assertEqual((end["queue_ms"], end["first_effective_ms"], end["first_content_ms"], end["total_ms"]), (2000, 1000, 3000, 8000))
        self.assertNotIn("PRIVATE", json.dumps(self.logs))

    def test_usage_only_not_first_token_and_missing_usage_is_null(self):
        span = self.span()
        span.admit()
        span.observe(message(usage={"input_tokens": 120, "output_tokens": 10, "output_token_details": {"reasoning": 4}}))
        span.observe(message(usage={"input_tokens": None, "output_tokens": True}))
        span.finish("completed")
        self.assertIsNone(self.logs[-1]["first_effective_ms"])
        self.assertEqual(self.logs[-1]["reasoning_tokens"], 4)
        self.assertEqual(self.logs[-1]["output_tokens"], 10)

    def test_tool_arguments_count_but_never_logged(self):
        span = self.span()
        span.admit()
        span.observe(message(tools=[{"args": "{PRIVATE TOOL DATA"}]))
        span.finish("completed")
        self.assertIsNotNone(self.logs[-1]["first_tool_ms"])
        self.assertIsNone(self.logs[-1]["first_content_ms"])
        self.assertNotIn("PRIVATE", json.dumps(self.logs))

    def test_nonstream_has_no_ttft(self):
        span = self.span()
        span.admit()
        span.observe(message("text", usage={"output_tokens": 12}), streaming=False)
        span.finish("completed")
        self.assertIsNone(self.logs[-1]["first_content_ms"])
        self.assertEqual(self.logs[-1]["output_tokens"], 12)

    def test_pending_cancel_never_active(self):
        span = self.span()
        span.finish("cancelled", asyncio.CancelledError())
        self.assertIsNone(self.logs[-1]["admitted_epoch"])
        self.assertEqual(self.logs[-1]["pending"], 0)

    def test_independent_concurrency_and_idempotent_finalization(self):
        a = self.span()
        a.admit()
        b = self.span()
        b.admit()
        self.assertEqual(b.active_at_admission, 2)
        a.finish("error", TimeoutError("PRIVATE URL KEY"))
        a.finish("completed")
        a.admit()
        b.finish("completed")
        self.assertEqual([r["active"] for r in self.logs if r["event"] == "end"], [1, 0])
        self.assertNotIn("PRIVATE", json.dumps(self.logs))

    def test_logger_failure_does_not_break_result_or_counters(self):
        with patch.object(perf.logger, "info", side_effect=RuntimeError("sink full")):
            span = self.span()
            span.admit()
            span.finish("completed")

    def test_structured_text_but_not_image_is_first_content(self):
        span = self.span()
        span.admit()
        span.observe(message([{"type": "image_url", "image_url": "PRIVATE"}]))
        self.assertIsNone(span.first["effective"])
        span.observe(message([{"type": "text", "text": "hello"}]))
        span.finish("completed")
        self.assertIsNotNone(self.logs[-1]["first_content_ms"])

    def test_disabled_default(self):
        with patch.dict(perf.os.environ, {}, clear=True):
            self.assertFalse(perf.enabled())

    def test_process_restart_changes_identity(self):
        old = perf.identity()
        with patch.object(perf.os, "getpid", return_value=old[0] + 1):
            self.assertNotEqual(perf.identity(), old)
        self.assertNotEqual(perf.identity()[1], old[1])

    def test_report_percentiles_and_concurrency(self):
        a = self.span()
        a.admit()
        b = self.span()
        b.admit()
        a.observe(message("hi"))
        a.finish("completed")
        b.finish("error", TimeoutError())
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "test.log"
            log.write_text("\n".join("prefix LLM_PERF " + json.dumps(r) for r in self.logs))
            calls, diagnostics = report.collect([log, log])
        details, summary, timeline = report.summarize(calls)
        self.assertEqual(len(details), 2)
        self.assertEqual(diagnostics["duplicate_records"], 4)
        self.assertEqual(summary[0]["completed"], 1)
        self.assertEqual(summary[0]["observed_closed_call_peak_concurrency"], 2)
        self.assertEqual(timeline[-1]["observed_closed_call_concurrency"], 0)
        self.assertEqual(report.percentile([1, 2, 3, 4], 0.95), 4)

    def test_report_unfinished_and_missing_start(self):
        a = self.span()
        a.admit()
        a.finish("completed")
        start, end = self.logs
        rows = {("p", "pid", "a"): {"start": start}, ("p", "pid", "b"): {"end": end}}
        _, summary, _ = report.summarize(rows)
        self.assertEqual(summary[0]["unfinished"], 1)
        self.assertEqual(summary[0]["missing_start"], 1)

    def test_report_invalid_and_oversized_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "x.log"
            log.write_bytes(b"x" * 70000 + b"\nLLM_PERF not-json\n")
            calls, diag = report.collect([log])
        self.assertEqual(calls, {})
        self.assertEqual(diag, {"oversized_lines": 1, "invalid_records": 1})


def actual_adapter_components():
    """Execute actual candidate methods with fake transport; no LangChain stub import."""
    tree = ast.parse((MODULE.parent / "robust_openai.py").read_text(encoding="utf-8"))
    timing = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_ModelStreamTiming")
    adapter = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "RobustChatOpenAI")
    method = next(n for n in adapter.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_astream")
    ns = dict(
        __name__=__name__,
        dataclass=dataclass,
        field=field,
        time=time,
        uuid=uuid,
        PerformanceSpan=perf.PerformanceSpan,
        performance_enabled=perf.enabled,
        _llm_timing_logs_enabled=lambda: False,
        _safe_endpoint_for_log=lambda x: "-",
        _timing_context_from_run_manager=lambda manager: ("thread", "run"),
        asyncio=asyncio,
        aclosing=aclosing,
        Mapping=Mapping,
    )
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias("annotations")], level=0), timing, method], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), "actual_adapter_methods", "exec"), ns)
    return ns


class AdapterBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, scenario):
        ns = actual_adapter_components()
        records = []

        @asynccontextmanager
        async def admission(model):
            if scenario == "queue_cancel":
                raise asyncio.CancelledError()
            yield

        ns["admit_model_stream"] = admission

        class Fake:
            model_name = "test"

            async def _astream_admitted(self, *args, _invocation_timing, **kwargs):
                timing = _invocation_timing
                if scenario == "prepare_error":
                    raise ValueError("payload")
                timing.start()
                try:
                    if scenario == "timeout":
                        raise TimeoutError("PRIVATE")
                    chunk = SimpleNamespace(message=message("PRIVATE"))
                    timing.observe_chunk(chunk)
                    yield chunk
                except BaseException as exc:
                    timing.finish(outcome="cancelled" if isinstance(exc, (GeneratorExit, asyncio.CancelledError)) else "error", error=exc)
                    raise
                else:
                    timing.finish(outcome="completed")

        with patch.dict(perf.os.environ, {"DEERFLOW_LLM_PERFORMANCE_LOGS": "true"}), patch.object(perf.logger, "info", side_effect=lambda fmt, body: records.append(json.loads(body))):
            stream = ns["_astream"](Fake(), [])
            try:
                if scenario == "close":
                    await anext(stream)
                    await stream.aclose()
                else:
                    async for _ in stream:
                        pass
            except (asyncio.CancelledError, ValueError, TimeoutError):
                pass
        self.assertEqual(perf._counts, {})
        self.assertEqual(len(records), 2)
        self.assertNotIn("PRIVATE", json.dumps(records))
        return records[-1]

    async def test_success(self):
        self.assertEqual((await self.exercise("success"))["outcome"], "completed")

    async def test_queue_cancellation(self):
        self.assertIsNone((await self.exercise("queue_cancel"))["admitted_epoch"])

    async def test_payload_error(self):
        self.assertEqual((await self.exercise("prepare_error"))["outcome"], "error")

    async def test_timeout(self):
        self.assertEqual((await self.exercise("timeout"))["error_class"], "TimeoutError")

    async def test_generator_close(self):
        self.assertEqual((await self.exercise("close"))["outcome"], "closed")


if __name__ == "__main__":
    unittest.main()
