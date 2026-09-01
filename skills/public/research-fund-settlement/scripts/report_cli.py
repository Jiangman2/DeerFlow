#!/usr/bin/env python3
"""Command-line entrypoint for the research fund settlement skill."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from docx_builder import build_docx, inspect_docx
from report_model import (
    convert_money,
    initial_case,
    normalize_profile,
    validate,
    validation_report,
)


def read_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("case JSON 顶层必须是对象")
    return value


def write_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def print_report(report: dict[str, Any]) -> None:
    print(json.dumps(report, ensure_ascii=False, indent=2))


def cmd_init(args: argparse.Namespace) -> int:
    profile = normalize_profile(args.profile)
    case = initial_case(profile)
    write_json(args.output, case)
    print(f"CASE_INITIALIZED profile={profile} output={Path(args.output).resolve()}")
    return 0


def cmd_convert_money(args: argparse.Namespace) -> int:
    result = {
        "input": {"value": args.value, "unit": args.from_unit},
        "output": {
            "value": convert_money(args.value, args.from_unit, args.to_unit),
            "unit": args.to_unit,
        },
    }
    print_report(result)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    case = read_json(args.case)
    report = validation_report(case, mode=args.mode)
    if args.report:
        write_json(args.report, report)
    print_report(report)
    return 0 if report["status"] == "PASS" else 2


def cmd_normalize(args: argparse.Namespace) -> int:
    case = read_json(args.case)
    normalized, issues = validate(case, mode=args.mode)
    write_json(args.output, normalized)
    report = validation_report(case, mode=args.mode)
    if args.report:
        write_json(args.report, report)
    print_report(report)
    return 0 if not any(item["severity"] == "ERROR" for item in issues) else 2


def cmd_build(args: argparse.Namespace) -> int:
    case = read_json(args.case)
    normalized, issues = validate(case, mode=args.mode)
    report = validation_report(case, mode=args.mode)
    if args.validation_report:
        write_json(args.validation_report, report)
    errors = [item for item in issues if item["severity"] == "ERROR"]
    if errors:
        print_report(report)
        print(
            "BUILD_BLOCKED: validation errors must be resolved before document generation",
            file=sys.stderr,
        )
        return 2
    if args.normalized_case:
        write_json(args.normalized_case, normalized)
    output = build_docx(normalized, args.output, mode=args.mode)
    result = {
        "status": "PASS",
        "output": str(output.resolve()),
        "bytes": output.stat().st_size,
        "warnings": report["summary"]["warnings"],
    }
    print_report(result)
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    case = read_json(args.case)
    normalized, issues = validate(case, mode="final")
    validation_errors = [item for item in issues if item["severity"] == "ERROR"]
    inspection = inspect_docx(args.docx, normalized)
    result = {
        "status": "PASS"
        if not validation_errors and inspection["status"] == "PASS"
        else "FAIL",
        "validation_errors": validation_errors,
        "document": inspection,
    }
    print_report(result)
    return 0 if result["status"] == "PASS" else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="科研经费决算报告生成与校验")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init", help="生成指定项目类型的 case.json 骨架"
    )
    init_parser.add_argument(
        "--profile", required=True, help="group_company | national_science | miit"
    )
    init_parser.add_argument("--output", required=True)
    init_parser.set_defaults(func=cmd_init)

    convert_parser = subparsers.add_parser("convert-money", help="确定性换算金额单位")
    convert_parser.add_argument("--value", required=True)
    convert_parser.add_argument(
        "--from-unit", required=True, choices=["元", "千元", "万元", "亿元"]
    )
    convert_parser.add_argument(
        "--to-unit", default="万元", choices=["元", "千元", "万元", "亿元"]
    )
    convert_parser.set_defaults(func=cmd_convert_money)

    check_parser = subparsers.add_parser("check", help="校验 case.json 并计算派生值")
    check_parser.add_argument("--case", required=True)
    check_parser.add_argument("--mode", choices=["draft", "final"], default="draft")
    check_parser.add_argument("--report")
    check_parser.set_defaults(func=cmd_check)

    normalize_parser = subparsers.add_parser(
        "normalize", help="输出包含派生值的规范化 case.json"
    )
    normalize_parser.add_argument("--case", required=True)
    normalize_parser.add_argument("--output", required=True)
    normalize_parser.add_argument("--mode", choices=["draft", "final"], default="draft")
    normalize_parser.add_argument("--report")
    normalize_parser.set_defaults(func=cmd_normalize)

    build_parser_ = subparsers.add_parser("build", help="校验并生成 DOCX")
    build_parser_.add_argument("--case", required=True)
    build_parser_.add_argument("--output", required=True)
    build_parser_.add_argument("--mode", choices=["draft", "final"], default="final")
    build_parser_.add_argument("--validation-report")
    build_parser_.add_argument("--normalized-case")
    build_parser_.set_defaults(func=cmd_build)

    inspect_parser = subparsers.add_parser(
        "inspect", help="检查 DOCX 包结构、项目名称、栏目和占位符"
    )
    inspect_parser.add_argument("--docx", required=True)
    inspect_parser.add_argument("--case", required=True)
    inspect_parser.set_defaults(func=cmd_inspect)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
