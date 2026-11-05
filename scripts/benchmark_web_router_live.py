"""Paid opt-in live router acceptance in an isolated AppConfig, without an LLM.

Failures are real API authentication rejections of deliberate test-only keys.
No transport mocks, endpoint overrides, provider retries or shared config writes.
"""

# ruff: noqa: E402 -- Bootstrap the exact read-only source checkout under test.

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend/packages/harness"), str(ROOT / "backend")]

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from deerflow.agents.middlewares.tool_result_sanitization_middleware import (
    ToolResultSanitizationMiddleware,
)
from deerflow.config.app_config import (
    AppConfig,
    pop_current_app_config,
    push_current_app_config,
)
from deerflow.tools.tools import get_available_tools

QUERY = "中国海油 2024 年报 油气净产量"
PDF = "https://ltd.cnooc.com.cn/tzzgx/dqbg/nb/202503/P020250327605146009602.pdf"
HTML = "https://ltd.cnooc.com.cn/xwzx/gsxw/2025/202503/t20250327_114400.html"
NAMES = ("deepseek", "tavily", "exa")
BAD_KEYS = {"deepseek": "sk-" + "0" * 32, "tavily": "tvly-dev-" + "0" * 48}
CASES = [
    ("search_primary", "web_search", QUERY, (), ["deepseek"]),
    ("html_primary", "web_fetch", HTML, (), ["deepseek"]),
    ("pdf_auto", "web_fetch", PDF, (), ["tavily"]),
    (
        "search_fallback_tavily",
        "web_search",
        QUERY,
        ("deepseek",),
        ["deepseek", "tavily"],
    ),
    (
        "search_fallback_exa",
        "web_search",
        QUERY,
        ("deepseek", "tavily"),
        ["deepseek", "tavily", "exa"],
    ),
    ("pdf_fallback_exa", "web_fetch", PDF, ("tavily",), ["tavily", "exa"]),
    ("html_fallback_tavily", "web_fetch", HTML, ("deepseek",), ["deepseek", "tavily"]),
    (
        "html_fallback_exa",
        "web_fetch",
        HTML,
        ("deepseek", "tavily"),
        ["deepseek", "tavily", "exa"],
    ),
]


def build_config(keys, invalid):
    profiles = {p: {"api_key": BAD_KEYS[p] if p in invalid else keys[p]} for p in NAMES}
    return AppConfig.model_validate(
        {
            "sandbox": {
                "use": "deerflow.sandbox.local:LocalSandboxProvider",
                "allow_host_bash": False,
            },
            "models": [],
            "tool_groups": [{"name": "web"}],
            "tools": [
                {
                    "name": name,
                    "group": "web",
                    "use": f"deerflow.community.web_router.tools:{name}_tool",
                    "provider_order": list(NAMES),
                    "pdf_provider_order": ["tavily", "exa"],
                    "providers": profiles,
                    "max_results": 5,
                    "max_characters": 20000,
                    "snippet_max_characters": 1000,
                    "max_attempts": 3,
                    "timeout": 15,
                    "total_timeout": 40,
                }
                for name in ("web_search", "web_fetch")
            ],
        }
    )


def build_graph(config):
    tools = [
        t
        for t in get_available_tools(
            groups=["web"], include_mcp=False, app_config=config
        )
        if t.name in {"web_search", "web_fetch"}
    ]
    assert len(tools) == 2
    for tool in tools:
        properties = convert_to_openai_tool(tool)["function"]["parameters"][
            "properties"
        ]
        assert set(properties) == ({"query"} if tool.name == "web_search" else {"url"})
    middleware = ToolResultSanitizationMiddleware()
    graph = StateGraph(MessagesState)
    graph.add_node(
        "tools",
        ToolNode(
            tools,
            wrap_tool_call=middleware.wrap_tool_call,
            awrap_tool_call=middleware.awrap_tool_call,
        ),
    )
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    return graph.compile()


async def run_case(case, keys):
    sample, name, value, invalid, expected = case
    config = build_config(keys, invalid)
    row = {
        "sample_id": sample,
        "tool": name,
        "input": value,
        "injected_invalid_credentials": list(invalid),
        "expected_attempts": expected,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "layer": "real-registry-async-toolnode-sanitizer",
    }
    start = time.monotonic()
    push_current_app_config(config)
    try:
        graph = build_graph(config)
        async with asyncio.timeout(50):
            state = await graph.ainvoke(
                {
                    "messages": [
                        HumanMessage(content=f"测试：请使用工具核验 {value}"),
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "id": sample,
                                    "name": name,
                                    "args": {
                                        "query"
                                        if name == "web_search"
                                        else "url": value
                                    },
                                }
                            ],
                        ),
                    ]
                }
            )
        message = state["messages"][-1]
        content, artifact = message.content, message.artifact or {}
        route = artifact.get("web_routing", {})
        attempts = route.get("attempts", [])
        if name == "web_search":
            sources = json.loads(content)
            usable = (
                isinstance(sources, list)
                and bool(sources)
                and all(
                    isinstance(s, dict)
                    and s.get("url")
                    and s.get("provider") == expected[-1]
                    for s in sources
                )
            )
            row["result_count"] = len(sources) if isinstance(sources, list) else 0
        else:
            usable = (
                content.startswith("# ")
                and len(content.partition("\n\n")[2].strip()) > 100
            )
        ordered = [a.get("provider") for a in attempts] == expected
        injected_failed = all(
            a.get("outcome") == "failed"
            and a.get("error_code") == "AUTHENTICATION"
            and a.get("provider_http_status") == 401
            for a in attempts[:-1]
        )
        row.update(
            content=content,
            output_chars=len(content),
            artifact=artifact,
            tool_status=message.status,
            usable=bool(usable),
            pass_route=ordered,
            pass_injected_authentication=injected_failed,
            passed=bool(
                usable
                and ordered
                and injected_failed
                and route.get("provider") == expected[-1]
                and route.get("status") == "success"
            ),
        )
    except Exception as exc:
        row.update(passed=False, error_class=type(exc).__name__)
    finally:
        pop_current_app_config()
    row["elapsed_s"] = round(time.monotonic() - start, 3)
    return row


async def run(args, keys):
    args.output.mkdir(parents=True, exist_ok=False)
    rows = []
    for case in CASES:
        if case[0] not in args.cases:
            continue
        row = await run_case(case, keys)
        encoded = json.dumps(row, ensure_ascii=False, indent=2)
        for key in list(keys.values()) + list(BAD_KEYS.values()):
            encoded = encoded.replace(key, "[REDACTED]")
        (args.output / f"{case[0]}.json").write_text(encoded, encoding="utf-8")
        rows.append(json.loads(encoded))
        print(
            json.dumps(
                {
                    k: row.get(k)
                    for k in (
                        "sample_id",
                        "passed",
                        "elapsed_s",
                        "result_count",
                        "error_class",
                    )
                }
            ),
            flush=True,
        )
    summary = {
        "source_revision": args.source_revision,
        "runtime": "isolated local Docker; no Gateway/browser/LLM",
        "cases": len(rows),
        "passed": sum(r["passed"] for r in rows),
        "provider_attempts": sum(
            len(r.get("artifact", {}).get("web_routing", {}).get("attempts", []))
            for r in rows
        ),
        "source_sha256": {
            p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
            for p in (
                "backend/packages/harness/deerflow/community/web_router/tools.py",
                "backend/packages/harness/deerflow/community/web_router/providers.py",
            )
        },
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary), flush=True)
    return summary["passed"] == len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--check-config", action="store_true")
    parser.add_argument(
        "--cases",
        nargs="+",
        choices=[case[0] for case in CASES],
        default=[case[0] for case in CASES],
    )
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    raw = json.loads(args.key_file.read_text(encoding="utf-8-sig"))
    keys = {p: raw[p.upper() + "_API_KEY"].strip() for p in NAMES}
    if not all(keys.values()):
        raise ValueError("All three credentials are required")
    os.environ["DEER_FLOW_HOME"] = str(args.output.parent / "runtime")
    if args.check_config:
        for case in CASES:
            if case[0] not in args.cases:
                continue
            config = build_config(keys, case[3])
            push_current_app_config(config)
            try:
                build_graph(config)
            finally:
                pop_current_app_config()
        print(
            f"{len(set(args.cases))} isolated configurations and real tool graphs: OK; no requests sent"
        )
        return
    raise SystemExit(0 if asyncio.run(run(args, keys)) else 1)


if __name__ == "__main__":
    main()
