from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "deerflow_timing_report.py"
SPEC = importlib.util.spec_from_file_location("deerflow_timing_report", SCRIPT_PATH)
assert SPEC and SPEC.loader
REPORT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = REPORT
SPEC.loader.exec_module(REPORT)


def test_report_uses_wall_clock_union_for_unattributed_time() -> None:
    lines = [
        "2026-09-10T20:00:00.000000000+08:00 RUN_TIMING event=run_start deerflow_thread_id=thread-1 deerflow_run_id=run-1\n",
        "2026-09-10T20:00:01.000000000+08:00 LLM_TIMING start llm_call_id=call-1 deerflow_thread_id=thread-1 deerflow_run_id=run-1\n",
        "2026-09-10T20:00:04.000000000+08:00 LLM_TIMING end llm_call_id=call-1 deerflow_thread_id=thread-1 deerflow_run_id=run-1 total_ms=3000\n",
        "2026-09-10T20:00:05.000000000+08:00 RUN_TIMING event=tool_end deerflow_thread_id=thread-1 deerflow_run_id=run-1 elapsed_ms=1000 tool_name=write_file outcome=completed\n",
        "2026-09-10T20:00:07.000000000+08:00 RUN_TIMING event=sandbox_acquire_end deerflow_thread_id=thread-1 deerflow_run_id=run-1 elapsed_ms=2000 sandbox_id=sandbox-1\n",
        "2026-09-10T20:00:07.000000000+08:00 RUN_TIMING event=sandbox_ready deerflow_thread_id=thread-1 deerflow_run_id=run-1 elapsed_ms=1500 sandbox_id=sandbox-1\n",
        "2026-09-10T20:00:10.000000000+08:00 RUN_TIMING event=run_end deerflow_thread_id=thread-1 deerflow_run_id=run-1 elapsed_ms=10000 status=success sandbox_command_total_ms=500 sandbox_command_count=1\n",
    ]
    rendered = REPORT.render_table(REPORT.parse_lines(lines), include_incomplete=False)
    assert "run-1 success 10.0 3.0 3.0 2.0 1.5 0.5 1.0 4.0 1 1 1 1 100" in rendered


def test_report_uses_run_end_sandbox_command_total() -> None:
    lines = [
        "2026-09-10T20:00:00+08:00 RUN_TIMING event=run_start deerflow_thread_id=thread-2 deerflow_run_id=run-2\n",
        "2026-09-10T20:00:01+08:00 RUN_TIMING event=tool_end deerflow_thread_id=thread-2 deerflow_run_id=run-2 elapsed_ms=900 tool_name=bash outcome=completed\n",
        "2026-09-10T20:00:02+08:00 RUN_TIMING event=run_end deerflow_thread_id=thread-2 deerflow_run_id=run-2 elapsed_ms=2000 status=success sandbox_command_total_ms=700 sandbox_command_count=1\n",
    ]
    rendered = REPORT.render_table(REPORT.parse_lines(lines), include_incomplete=False)
    assert "run-2 success 2.0 0.0 0.0 0.0 0.0 0.7 0.9 1.1 0 0 0 1 -" in rendered
