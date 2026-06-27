#!/usr/bin/env python3
"""Run one visible step of the tech-intel-daily pipeline.

This entrypoint is meant for DeerFlow native slash Skill execution where the
model updates the visible TodoList between command calls. The original
run_daily.py remains available as a one-shot pipeline.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date as date_cls
from pathlib import Path
from typing import Any

from run_daily import (
    SCRIPT_DIR,
    SOURCE_CATALOG,
    build_chat_summary,
    cleanup_internal_sidecars,
    cleanup_legacy_artifacts,
    collect,
    initialize_execution_todos,
    read_json,
    run_command,
    update_execution_todo,
    write_execution_todos,
)


STEP_ORDER = [
    "initialize",
    "load_requirements",
    "collect_candidates",
    "filter_knowledge_base",
    "enrich_full_text",
    "render_daily_report",
    "finalize",
]


def _day_dir(output_root: str, target_date: str) -> Path:
    return Path(output_root) / target_date


def _load_or_create_todos(day_dir: Path) -> list[dict[str, Any]]:
    path = day_dir / "execution_todo.json"
    if path.exists():
        return read_json(path, [])
    todos = initialize_execution_todos()
    write_execution_todos(day_dir, todos)
    return todos


def _complete_step(day_dir: Path, todos: list[dict[str, Any]], step_id: str, note: str = "") -> None:
    update_execution_todo(day_dir, todos, step_id, "completed", note)


def step_initialize(args: argparse.Namespace, day_dir: Path, todos: list[dict[str, Any]]) -> dict[str, Any]:
    update_execution_todo(day_dir, todos, "initialize", "in_progress")
    run_command([sys.executable, str(SCRIPT_DIR / "init_state.py"), "--date", args.date, "--output-root", args.output_root])
    cleanup_legacy_artifacts(day_dir, args.date)
    _complete_step(day_dir, todos, "initialize", f"输出目录：{day_dir.resolve()}")
    return {"day_dir": str(day_dir.resolve())}


def step_load_requirements(args: argparse.Namespace, day_dir: Path, todos: list[dict[str, Any]]) -> dict[str, Any]:
    update_execution_todo(day_dir, todos, "load_requirements", "in_progress")
    references = [
        SOURCE_CATALOG,
        SOURCE_CATALOG.parent / "filtering-rules.md",
        SOURCE_CATALOG.parent / "taxonomies.md",
        SOURCE_CATALOG.parent / "output-templates.md",
    ]
    loaded = []
    for path in references:
        if path.exists():
            _ = path.read_text(encoding="utf-8")
            loaded.append(path.name)
    _complete_step(day_dir, todos, "load_requirements", f"已加载 {len(loaded)} 个参考文件：{', '.join(loaded)}")
    return {"loaded_references": loaded}


def step_collect_candidates(args: argparse.Namespace, day_dir: Path, todos: list[dict[str, Any]]) -> dict[str, Any]:
    update_execution_todo(day_dir, todos, "collect_candidates", "in_progress")
    diagnostics = collect(args.date, day_dir, args.max_sources, args.max_candidates_per_source)
    summary = diagnostics["summary"]
    _complete_step(
        day_dir,
        todos,
        "collect_candidates",
        f"发现候选链接 {summary['candidate_links_found']} 个，抓取候选页 {summary['candidate_pages_fetched']} 个。",
    )
    return {"summary": summary, "diagnostics": str((day_dir / "collection_diagnostics.json").resolve())}


def step_filter_knowledge_base(args: argparse.Namespace, day_dir: Path, todos: list[dict[str, Any]]) -> dict[str, Any]:
    update_execution_todo(day_dir, todos, "filter_knowledge_base", "in_progress")
    diagnostics = read_json(day_dir / "collection_diagnostics.json", None)
    if not diagnostics:
        raise RuntimeError("collection_diagnostics.json 不存在，请先执行 collect_candidates 步骤。")
    knowledge_base = day_dir / "knowledge_base.jsonl"
    if not knowledge_base.exists():
        raise RuntimeError("knowledge_base.jsonl 不存在，请先执行 collect_candidates 步骤。")
    summary = diagnostics["summary"]
    _complete_step(
        day_dir,
        todos,
        "filter_knowledge_base",
        f"入选 {summary['selected_count']} 篇，过滤 {summary['rejected_count']} 篇。",
    )
    return {"summary": summary, "knowledge_base": str(knowledge_base.resolve())}


def step_enrich_full_text(args: argparse.Namespace, day_dir: Path, todos: list[dict[str, Any]]) -> dict[str, Any]:
    update_execution_todo(day_dir, todos, "enrich_full_text", "in_progress")
    run_command([sys.executable, str(SCRIPT_DIR / "enrich_full_text.py"), "--day-dir", str(day_dir)])
    _complete_step(day_dir, todos, "enrich_full_text")
    return {"knowledge_base": str((day_dir / "knowledge_base.jsonl").resolve())}


def step_render_daily_report(args: argparse.Namespace, day_dir: Path, todos: list[dict[str, Any]]) -> dict[str, Any]:
    update_execution_todo(day_dir, todos, "render_daily_report", "in_progress")
    run_command(
        [
            sys.executable,
            str(SCRIPT_DIR / "render_html_report.py"),
            "--date",
            args.date,
            "--day-dir",
            str(day_dir),
            "--diagnostics",
            str(day_dir / "collection_diagnostics.json"),
        ]
    )
    report = day_dir / "daily_report.html"
    if not report.exists() or report.stat().st_size == 0:
        raise RuntimeError(f"daily_report.html was not generated: {report}")
    _complete_step(day_dir, todos, "render_daily_report", f"已生成 {report.name}")
    return {"daily_report": str(report.resolve()), "size": report.stat().st_size}


def step_finalize(args: argparse.Namespace, day_dir: Path, todos: list[dict[str, Any]]) -> dict[str, Any]:
    update_execution_todo(day_dir, todos, "finalize", "in_progress")
    report = day_dir / "daily_report.html"
    diagnostics = read_json(day_dir / "collection_diagnostics.json", None)
    if not diagnostics:
        raise RuntimeError("collection_diagnostics.json 不存在，无法生成最终摘要。")
    if not report.exists() or report.stat().st_size == 0:
        raise RuntimeError(f"daily_report.html 不存在或为空：{report}")
    _complete_step(day_dir, todos, "finalize", "已完成校验，日报可展示。")
    run_command(
        [
            sys.executable,
            str(SCRIPT_DIR / "render_html_report.py"),
            "--date",
            args.date,
            "--day-dir",
            str(day_dir),
            "--diagnostics",
            str(day_dir / "collection_diagnostics.json"),
        ]
    )
    cleanup_internal_sidecars(day_dir)
    return {
        "daily_report": str(report.resolve()),
        "summary": diagnostics["summary"],
        "selected_articles": diagnostics.get("selected", []),
        "chat_summary": build_chat_summary(args.date, report, diagnostics),
    }


STEP_HANDLERS = {
    "initialize": step_initialize,
    "load_requirements": step_load_requirements,
    "collect_candidates": step_collect_candidates,
    "filter_knowledge_base": step_filter_knowledge_base,
    "enrich_full_text": step_enrich_full_text,
    "render_daily_report": step_render_daily_report,
    "finalize": step_finalize,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one step of the tech-intel-daily pipeline.")
    parser.add_argument("--step", required=True, choices=STEP_ORDER, help="Pipeline step to run.")
    parser.add_argument("--date", default=date_cls.today().isoformat(), help="Target publication date, YYYY-MM-DD.")
    parser.add_argument("--output-root", default="outputs/tech-intel-daily", help="Root output directory.")
    parser.add_argument("--max-sources", type=int, default=24, help="Maximum source entry pages to check.")
    parser.add_argument("--max-candidates-per-source", type=int, default=8, help="Maximum candidate article pages to fetch per source.")
    args = parser.parse_args()

    day_dir = _day_dir(args.output_root, args.date)
    day_dir.mkdir(parents=True, exist_ok=True)
    todos = _load_or_create_todos(day_dir)
    try:
        payload = STEP_HANDLERS[args.step](args, day_dir, todos)
    except Exception as exc:
        update_execution_todo(day_dir, todos, args.step, "failed", str(exc))
        raise

    result = {
        "status": "success",
        "date": args.date,
        "step": args.step,
        "day_dir": str(day_dir.resolve()),
        **payload,
    }
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
