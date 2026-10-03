#!/usr/bin/env python3
"""Render content-free Gateway timing logs as one row per DeerFlow run.

The operations host may run an older Python 3 release, so this script stays
compatible with Python 3.6+ and has no third-party dependencies.
"""

from __future__ import print_function

import argparse
import calendar
import re
from datetime import datetime


KEY_VALUE_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)")
TIMEZONE_SUFFIX_RE = re.compile(r"(?:[+-][0-9]{2}:[0-9]{2}|Z)$")
SANDBOX_TOOL_NAMES = frozenset(
    ("bash", "ls", "glob", "grep", "read_file", "write_file", "str_replace")
)


def _timestamp_ms(line):
    """Parse the leading Kubernetes RFC3339 timestamp in milliseconds."""
    token = line.split(None, 1)[0] if line else ""
    token = TIMEZONE_SUFFIX_RE.sub("", token)
    try:
        if "." in token:
            whole, fraction = token.split(".", 1)
            parsed = datetime.strptime(
                whole + "." + fraction[:6].ljust(6, "0"),
                "%Y-%m-%dT%H:%M:%S.%f",
            )
        else:
            parsed = datetime.strptime(token, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    # Durations are all that matter; use a timezone-independent wall clock.
    return calendar.timegm(parsed.timetuple()) * 1000 + parsed.microsecond // 1000


def _fields(line):
    return dict(KEY_VALUE_RE.findall(line))


def _as_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _duration_ms(intervals):
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


def _interval_overlap_ms(started_at, ended_at, intervals):
    """Return overlap with the union of intervals, in milliseconds."""
    return _duration_ms(
        (max(started_at, interval_start), min(ended_at, interval_end))
        for interval_start, interval_end in intervals
        if interval_end > started_at and interval_start < ended_at
    )


class RunSummary(object):
    def __init__(self):
        self.thread_id = "-"
        self.status = "running"
        self.run_start_ms = None
        self.run_end_ms = None
        self.run_elapsed_ms = 0
        self.llm_started = set()
        self.llm_finished = {}
        self.tool_end = []
        self.sandbox_acquire_end = []
        self.sandbox_ready_ms = []
        self.sandbox_tool_execution_total_ms = None
        self.sandbox_tool_count = None
        self.non_sandbox_tool_total_ms = None
        self.non_sandbox_tool_count = None

    def total_ms(self):
        if self.run_start_ms is not None and self.run_end_ms is not None:
            return max(0, self.run_end_ms - self.run_start_ms)
        return self.run_elapsed_ms

    def llm_intervals(self):
        return [(ended_at - elapsed, ended_at) for ended_at, elapsed in self.llm_finished.values()]

    def tool_intervals(self):
        return [(ended_at - elapsed, ended_at) for ended_at, elapsed, _ in self.tool_end]

    def sandbox_intervals(self):
        return [(ended_at - elapsed, ended_at) for ended_at, elapsed in self.sandbox_acquire_end]

    def sandbox_tool_ms(self):
        if self.sandbox_tool_execution_total_ms is not None:
            return self.sandbox_tool_execution_total_ms
        return sum(
            max(0, elapsed - _interval_overlap_ms(ended_at - elapsed, ended_at, self.sandbox_intervals()))
            for ended_at, elapsed, tool_name in self.tool_end
            if tool_name in SANDBOX_TOOL_NAMES
        )

    def non_sandbox_tool_ms(self):
        if self.non_sandbox_tool_total_ms is not None:
            return self.non_sandbox_tool_total_ms
        return sum(elapsed for _, elapsed, tool_name in self.tool_end if tool_name not in SANDBOX_TOOL_NAMES)

    def sandbox_tool_calls(self):
        if self.sandbox_tool_count is not None:
            return self.sandbox_tool_count
        return sum(1 for _, _, tool_name in self.tool_end if tool_name in SANDBOX_TOOL_NAMES)

    def non_sandbox_tool_calls(self):
        if self.non_sandbox_tool_count is not None:
            return self.non_sandbox_tool_count
        return sum(1 for _, _, tool_name in self.tool_end if tool_name not in SANDBOX_TOOL_NAMES)

    def matched_llm_calls(self):
        """Return LLM calls for which this capture contains both boundaries."""
        return self.llm_started.intersection(self.llm_finished)

    def orphan_llm_ends(self):
        """Return LLM ends whose matching start was absent from this capture."""
        return set(self.llm_finished).difference(self.llm_started)

    def covered_ms(self):
        return _duration_ms(self.llm_intervals() + self.tool_intervals() + self.sandbox_intervals())

def parse_lines(lines):
    """Parse filtered or unfiltered Gateway logs into per-run timing data."""
    summaries = {}
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
            llm_call_id = fields.get("llm_call_id")
            if llm_call_id:
                summary.llm_started.add(llm_call_id)
            continue
        if "LLM_TIMING end" in line:
            llm_call_id = fields.get("llm_call_id")
            if not llm_call_id:
                legacy_llm_sequence += 1
                llm_call_id = "legacy-{0}".format(legacy_llm_sequence)
            summary.llm_finished[llm_call_id] = (timestamp, _as_int(fields.get("total_ms")))
            continue

        event = fields.get("event")
        if event == "run_start":
            summary.run_start_ms = timestamp
        elif event == "run_end":
            summary.run_end_ms = timestamp
            summary.run_elapsed_ms = _as_int(fields.get("elapsed_ms"))
            summary.status = fields.get("status", "unknown")
            if "sandbox_tool_execution_total_ms" in fields:
                summary.sandbox_tool_execution_total_ms = _as_int(fields.get("sandbox_tool_execution_total_ms"))
            if "sandbox_tool_count" in fields:
                summary.sandbox_tool_count = _as_int(fields.get("sandbox_tool_count"))
            if "non_sandbox_tool_total_ms" in fields:
                summary.non_sandbox_tool_total_ms = _as_int(fields.get("non_sandbox_tool_total_ms"))
            if "non_sandbox_tool_count" in fields:
                summary.non_sandbox_tool_count = _as_int(fields.get("non_sandbox_tool_count"))
        elif event == "tool_end":
            summary.tool_end.append((timestamp, _as_int(fields.get("elapsed_ms")), fields.get("tool_name", "unknown")))
        elif event == "sandbox_acquire_end":
            summary.sandbox_acquire_end.append((timestamp, _as_int(fields.get("elapsed_ms"))))
        elif event == "sandbox_ready":
            summary.sandbox_ready_ms.append(_as_int(fields.get("elapsed_ms")))
    return summaries


def _seconds(milliseconds):
    return "{0:.1f}".format(milliseconds / 1000.0)


def render_table(summaries, include_incomplete=False):
    """Render an operational report without user or command content."""
    header = (
        "THREAD_ID RUN_ID STATUS TOTAL_S LLM_SUM_S "
        "SB_ACQ_S SB_READY_S SB_TOOLS_S OUT_TOOLS_S TOOLS_S UNATTR_S "
        "LLM_START# LLM_END# LLM_ORPHAN# SB_ACQ# SB_TOOL# OUT_TOOL# COV%"
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
        sandbox_tool_ms = summary.sandbox_tool_ms()
        non_sandbox_tool_ms = summary.non_sandbox_tool_ms()
        tool_ms = sandbox_tool_ms + non_sandbox_tool_ms
        unattributed_ms = max(0, total_ms - summary.covered_ms())
        started, finished = len(summary.llm_started), len(summary.llm_finished)
        matched = len(summary.matched_llm_calls())
        orphan_ends = len(summary.orphan_llm_ends())
        coverage = "-" if started == 0 else "{0:.0f}".format(matched * 100.0 / started)
        rows.append(" ".join([
            summary.thread_id, run_id, summary.status,
            _seconds(total_ms), _seconds(llm_sum_ms), _seconds(sandbox_acquire_ms),
            _seconds(sandbox_ready_ms), _seconds(sandbox_tool_ms), _seconds(non_sandbox_tool_ms),
            _seconds(tool_ms), _seconds(unattributed_ms),
            str(started), str(finished), str(orphan_ends), str(len(summary.sandbox_acquire_end)),
            str(summary.sandbox_tool_calls()), str(summary.non_sandbox_tool_calls()), coverage,
        ]))
    return "\n".join(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log_file", help="Gateway log captured with kubectl logs --timestamps")
    parser.add_argument("--all", action="store_true", help="include runs without a run_end record")
    args = parser.parse_args()
    with open(args.log_file, "r") as log_file:
        print(render_table(parse_lines(log_file), include_incomplete=args.all))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
