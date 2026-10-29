"""Paid adapter integration smoke test; no LLM and no production config writes.

Uses real AppConfig, DeerFlow's registry, LangGraph ToolNode and the remote-result
sanitizer. Each provider receives five searches and five fixed fetch URLs.
Run using backend/.venv/Scripts/python.exe with provider key env vars or --key-file.
"""

# ruff: noqa: E402 -- This source-tree CLI must bootstrap the harness import path.

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend" / "packages" / "harness"), str(ROOT / "backend")]

from langchain_core.messages import AIMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.prebuilt import ToolNode
from langgraph.graph import END, START, MessagesState, StateGraph

from deerflow.agents.middlewares.tool_result_sanitization_middleware import (
    ToolResultSanitizationMiddleware,
)
from deerflow.config.app_config import (
    AppConfig,
    pop_current_app_config,
    push_current_app_config,
)
from deerflow.tools.tools import get_available_tools

from benchmark_search_fetch import PAGES, PROVIDERS, SEARCHES


def provider_config(provider, key):
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
                    "name": "web_search",
                    "group": "web",
                    "use": f"deerflow.community.{provider}.tools:web_search_tool",
                    "api_key": key,
                    "max_results": 5,
                    "timeout": 30,
                },
                {
                    "name": "web_fetch",
                    "group": "web",
                    "use": f"deerflow.community.{provider}.tools:web_fetch_tool",
                    "api_key": key,
                    "max_characters": 20000,
                    "timeout": 30,
                },
            ],
        }
    )


def build_node(config):
    tools = [
        tool
        for tool in get_available_tools(
            groups=["web"], include_mcp=False, app_config=config
        )
        if tool.name in {"web_search", "web_fetch"}
    ]
    assert {tool.name for tool in tools} == {"web_search", "web_fetch"}
    for tool in tools:
        parameters = convert_to_openai_tool(tool)["function"]["parameters"]
        expected = "query" if tool.name == "web_search" else "url"
        assert set(parameters["properties"]) == {expected}
    node = ToolNode(
        tools,
        wrap_tool_call=ToolResultSanitizationMiddleware().wrap_tool_call,
        handle_tool_errors=lambda exc: (
            f"Error: adapter exception ({type(exc).__name__})"
        ),
    )
    graph = StateGraph(MessagesState)
    graph.add_node("tools", node)
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    return graph.compile()


def invoke(node, provider, kind, sample_id, value):
    name = f"web_{kind}"
    field = "query" if kind == "search" else "url"
    call_id = f"{provider}-{kind}-{sample_id}"
    start = time.perf_counter()
    record = {
        "provider": provider,
        "kind": kind,
        "sample_id": sample_id,
        "input": value,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "layer": "registry-toolnode-sanitizer",
    }
    try:
        result = node.invoke(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": name,
                                "args": {field: value},
                                "id": call_id,
                                "type": "tool_call",
                            }
                        ],
                    )
                ]
            }
        )
        message = result["messages"][-1]
        output = message.content
        assert message.tool_call_id == call_id and message.name == name
        record.update(
            tool_status=message.status, content=output, output_chars=len(output)
        )
        if kind == "search":
            rows = json.loads(output)
            valid = (
                isinstance(rows, list)
                and bool(rows)
                and all(
                    isinstance(row, dict)
                    and all(
                        isinstance(row.get(k), str) for k in ("title", "url", "snippet")
                    )
                    for row in rows
                )
            )
            record.update(
                success=valid, result_count=len(rows) if isinstance(rows, list) else 0
            )
        else:
            record["success"] = (
                message.status == "success"
                and isinstance(output, str)
                and output.startswith("# ")
                and bool(output.partition("\n\n")[2].strip())
            )
    except Exception as exc:
        record.update(success=False, error=type(exc).__name__)
    record["elapsed_s"] = round(time.perf_counter() - start, 3)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    logging.disable(
        logging.CRITICAL
    )  # Third-party SDK exception logs must not echo credentials.
    keys = (
        json.loads(args.key_file.read_text(encoding="utf-8-sig"))
        if args.key_file
        else os.environ
    )
    secrets = {p: keys[info[0]].strip() for p, info in PROVIDERS.items()}
    assert all(secrets.values()), "All three keys must be configured"
    args.output.mkdir(parents=True, exist_ok=False)
    records = []
    for provider, key in secrets.items():
        config = provider_config(provider, key)
        push_current_app_config(config)
        try:
            node = build_node(config)
            for kind, samples in (("search", SEARCHES), ("fetch", PAGES)):
                for sample_id, value in samples:
                    row = invoke(node, provider, kind, sample_id, value)
                    encoded = json.dumps(row, ensure_ascii=False)
                    for secret in secrets.values():
                        encoded = encoded.replace(secret, "[REDACTED]")
                    (args.output / f"{provider}-{kind}-{sample_id}.json").write_text(
                        encoded, encoding="utf-8"
                    )
                    records.append(json.loads(encoded))
                    print(
                        json.dumps(
                            {
                                k: row.get(k)
                                for k in (
                                    "provider",
                                    "kind",
                                    "sample_id",
                                    "success",
                                    "elapsed_s",
                                    "result_count",
                                    "output_chars",
                                    "error",
                                )
                            }
                        ),
                        flush=True,
                    )
        finally:
            pop_current_app_config()
    summary = []
    for provider in PROVIDERS:
        for kind in ("search", "fetch"):
            rows = [
                r for r in records if r["provider"] == provider and r["kind"] == kind
            ]
            good = [r for r in rows if r["success"]]
            summary.append(
                {
                    "provider": provider,
                    "kind": kind,
                    "attempted": len(rows),
                    "success": len(good),
                    "success_median_s": round(
                        statistics.median(r["elapsed_s"] for r in good), 3
                    )
                    if good
                    else None,
                }
            )
    (args.output / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print("SUMMARY " + json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
