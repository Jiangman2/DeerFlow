#!/usr/bin/env python3
"""Render content-free Gateway timing logs as one row per DeerFlow run."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable


KEY_VALUE_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)")


def _timestamp_ms(line: str) -> int | None:
    """Parse the leading Kubernetes RFC3339 timestamp in milliseconds."""
    token = line.split(maxsplit=1)[0] if line else ""
    try:
        return round(datetime.fromisoformat(token.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def _fields(line: str) -> dict[str, str]:
    return dict(KEY_VALUE_RE.findall(line))


def _as_int(value: str | None) -> int:
    try:
        return int(value or 0)
    except ValueError:
        return 0


def _duration_ms(intervals: list[tuple[int, int]]) -> int:
    """Return the union duration of valid wall-clock intervals."""
    ordered = sorted((start, end) for start, end in intervals if end > start)
    if not ordered:
        return 0
    total = 0
    active_start, active_end = ordered[0]
    for start, end in ordered[1:]:
        if start <= active_end:
            active_end = max(active_end, end)
        else:
            total += active_end - active_start
            active_start, active_end = start, end
    return total + active_end - active_start


@dataclass
class RunSummary:
    thread_id: str = "-"
    status: str = "running"
    run_start_ms: int | None = None
    run_end_ms: int | None = None
    run_elapsed_ms: int = 0
    llm_started: set[str] = field(default_factory=set)
    llm_finished: dict[str, tuple[int, int]] = field(default_factory=dict)
    tool_end: list[tuple[int, int, str]] = field(default_factory=list)
    sandbox_acquire_end: list[tuple[int, int]] = field(default_factory=list)
    sandbox_ready_ms: list[int] = field(default_factory=list)
    sandbox_command_total_ms: int | None = None
    sandbox_command_count: int = 0

    def total_ms(self) -> int:
        if self.run_start_ms is not None and self.run_end_ms is not None:
            return max(0, self.run_end_ms - self.run_start_ms)
        return self.run_elapsed_ms

    def llm_intervals(self) -> list[tuple[int, int]]:
        return [(ended_at - elapsed, ended_at) for ended_at, elapsed in self.llm_finished.values()]

    def tool_intervals(self) -> list[tuple[int, int]]:
        return [(ended_at - elapsed, ended_at) for ended_at, elapsed, _ in self.tool_end]

    def sandbox_intervals(self) -> list[tuple[int, int]]:
        return [(ended_at - elapsed, ended_at) for ended_at, elapsed in self.sandbox_acquire_end]

    def sandbox_command_ms(self) -> int:
        if self.sandbox_command_total_ms is not None:
            return self.sandbox_command_total_ms
        return sum(elapsed for _, elapsed, tool_name in self.tool_end if tool_name == "bash")

    def covered_ms(self) -> int:
        return _duration_ms(self.llm_intervals() + self.tool_intervals() + self.sandbox_intervals())

    def llm_span_ms(self) -> int:
        intervals = self.llm_intervals()
        return 0 if not intervals else max(end for _, end in intervals) - min(start for start, _ in intervals)


def parse_lines(lines: Iterable[str]) -> dict[str, RunSummary]:
    """Parse filtered or unfiltered Gateway logs into per-run timing data."""
    summaries: dict[str, RunSummary] = {}
    legacy_llm_sequence = 0
    for line in lines:
        if "RUN_TIMING" not in line and "LLM_TIMING" not in line:
            continue
        timestamp = _timestamp_ms(line)
        if timestamp is None:
            continue
        fields = _fields(line)
        run_id = fields.get("deerflow_run_id", "-")
        if run_id == "-":
            continue
        summary = summaries.setdefault(run_id, RunSummary())
        summary.thread_id = fields.get("deerflow_thread_id", summary.thread_id)
        if "LLM_TIMING start" in line:
            if llm_call_id := fields.get("llm_call_id"):
                summary.llm_started.add(llm_call_id)
            continue
        if "LLM_TIMING end" in line:
            llm_call_id = fields.get("llm_call_id")
            if not llm_call_id:
                legacy_llm_sequence += 1
                llm_call_id = f"legacy-{legacy_llm_sequence}"
            summary.llm_finished[llm_call_id] = (timestamp, _as_int(fields.get("total_ms")))
            continue
        event = fields.get("event")
        if event == "run_start":
            summary.run_start_ms = timestamp
        elif event == "run_end":
            summary.run_end_ms = timestamp
            summary.run_elapsed_ms = _as_int(fields.get("elapsed_ms"))
            summary.status = fields.get("status", "unknown")
            summary.sandbox_command_total_ms = _as_int(fields.get("sandbox_command_total_ms"))
            summary.sandbox_command_count = _as_int(fields.get("sandbox_command_count"))
        elif event == "tool_end":
            summary.tool_end.append((timestamp, _as_int(fields.get("elapsed_ms")), fields.get("tool_name", "unknown")))
        elif event == "sandbox_acquire_end":
            summary.sandbox_acquire_end.append((timestamp, _as_int(fields.get("elapsed_ms"))))
        elif event == "sandbox_ready":
            summary.sandbox_ready_ms.append(_as_int(fields.get("elapsed_ms")))
    return summaries


def _seconds(milliseconds: int) -> str:
    return f"{milliseconds / 1000:.1f}"


def render_table(summaries: dict[str, RunSummary], *, include_incomplete: bool) -> str:
    """Render an operational report without user or command content."""
    header = (
        "THREAD_ID RUN_ID STATUS TOTAL_S LLM_SUM_S LLM_SPAN_S "
        "SB_ACQ_S SB_READY_S SB_CMD_S TOOLS_S UNATTR_S "
        "LLM_START# LLM_END# SB# TOOL# COV%"
    )
    rows = [header]
    ordered = sorted(summaries.items(), key=lambda item: item[1].total_ms(), reverse=True)
    for run_id, summary in ordered:
        if not include_incomplete and summary.run_end_ms is None:
            continue
        total_ms = summary.total_ms()
        llm_sum_ms = sum(elapsed for _, elapsed in summary.llm_finished.values())
        sandbox_acquire_ms = sum(elapsed for _, elapsed in summary.sandbox_acquire_end)
        sandbox_ready_ms = sum(summary.sandbox_ready_ms)
        tool_ms = sum(elapsed for _, elapsed, _ in summary.tool_end)
        unattributed_ms = max(0, total_ms - summary.covered_ms())
        started, finished = len(summary.llm_started), len(summary.llm_finished)
        coverage = "-" if started == 0 else f"{finished * 100 / started:.0f}"
        rows.append(" ".join([
            summary.thread_id, run_id, summary.status,
            _seconds(total_ms), _seconds(llm_sum_ms), _seconds(summary.llm_span_ms()),
            _seconds(sandbox_acquire_ms), _seconds(sandbox_ready_ms),
            _seconds(summary.sandbox_command_ms()), _seconds(tool_ms), _seconds(unattributed_ms),
            str(started), str(finished), str(len(summary.sandbox_acquire_end)),
            str(len(summary.tool_end)), coverage,
        ]))
    return "\n".join(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log_file", type=Path, help="Gateway log captured with kubectl logs --timestamps")
    parser.add_argument("--all", action="store_true", help="include runs without a run_end record")
    args = parser.parse_args()
    with args.log_file.open(encoding="utf-8", errors="replace") as log_file:
        print(render_table(parse_lines(log_file), include_incomplete=args.all))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
