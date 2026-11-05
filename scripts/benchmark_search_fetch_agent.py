"""Paid, isolated DeerFlow lead-agent comparison with a real DeepSeek model.

Two public research tasks per provider. Uses make_lead_agent, its real model
factory, tool registry and middleware chain. No Gateway/browser/PVC deployment.
"""

# ruff: noqa: E402 -- This source-tree CLI must bootstrap the harness import path.

import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend" / "packages" / "harness"), str(ROOT / "backend")]

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.agents.lead_agent.agent import make_lead_agent
from deerflow.config.app_config import (
    AppConfig,
    pop_current_app_config,
    push_current_app_config,
)

from benchmark_search_fetch import PROVIDERS
from benchmark_search_fetch_adapters import provider_config

TASKS = {
    "cn_energy": "请核实中国海油2024年的油气净产量和同比增幅。先用 web_search 搜索且只搜索一次，查询词固定为“中国海油 2024 年报 油气净产量”，再用 web_fetch 阅读搜索结果中的官方原始来源。最多抓取3页。用不超过200字回答，附上实际来源URL，并说明是否成功读取了正文。读取失败时如实说明只能依据搜索摘要，不要假装已核验全文。不要创建文件或使用其他工具。",
    "python_taskgroup": "请依据Python官方文档解释：asyncio.TaskGroup中一个任务抛出非CancelledError异常后，同组其他任务会怎样？最后异常如何向调用者报告？先只搜索一次，查询词固定为“Python asyncio TaskGroup cancellation documentation”，再抓取官方文档 https://docs.python.org/3/library/asyncio-task.html （该URL由用户明确提供）。最多抓取3页。用不超过250字中文回答并附来源URL，明确是否成功读取正文。若读取失败，应说明证据限制。不要创建文件或使用其他工具。",
}


class Progress(AsyncCallbackHandler):
    raise_error = True

    def __init__(self, provider, task_id):
        self.provider = provider
        self.task_id = task_id
        self.model_calls = 0
        self.completed_tools = []
        self.pending_tools = {}
        self.tool_counts = {"web_search": 0, "web_fetch": 0}

    async def on_chat_model_start(self, serialized, messages, **kwargs):
        if self.model_calls >= 6:
            raise RuntimeError("Comparison model call ceiling reached")
        self.model_calls += 1
        print(
            json.dumps(
                {
                    "provider": self.provider,
                    "task": self.task_id,
                    "event": "model_start",
                    "round": self.model_calls,
                }
            ),
            flush=True,
        )

    async def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
        name = serialized.get("name", "unknown")
        if self.tool_counts.get(name, 0) >= {"web_search": 1, "web_fetch": 3}.get(
            name, 0
        ):
            raise RuntimeError("Comparison tool budget exhausted or tool not enabled")
        self.tool_counts[name] += 1
        self.pending_tools[str(run_id)] = name
        print(
            json.dumps(
                {
                    "provider": self.provider,
                    "task": self.task_id,
                    "event": "tool_start",
                    "tool": name,
                }
            ),
            flush=True,
        )

    async def on_tool_end(self, output, *, run_id, **kwargs):
        content = output.content if isinstance(output, ToolMessage) else str(output)
        self.completed_tools.append(
            {"name": self.pending_tools.pop(str(run_id), "unknown"), "content": content}
        )


def agent_config(provider, key, model_key, output):
    data = provider_config(provider, key).model_dump()
    data.update(
        models=[
            {
                "name": "comparison-deepseek",
                "use": "deerflow.models.patched_deepseek:PatchedChatDeepSeek",
                "model": "deepseek-flash",
                "api_key": model_key,
                "api_base": "https://api.deepseek.com",
                "supports_thinking": False,
                "extra_body": {"thinking": {"type": "disabled"}},
                "max_tokens": 1500,
                "max_retries": 0,
                "timeout": 45,
                "streaming": True,
            }
        ],
        memory={"enabled": False, "injection_enabled": False},
        title={"enabled": False},
        summarization={"enabled": False},
        skills={"path": str(output / "empty-skills"), "deferred_discovery": False},
        tool_output={
            "enabled": True,
            "externalize_min_chars": 0,
            "fallback_max_chars": 22000,
            "fallback_head_chars": 18000,
            "fallback_tail_chars": 3000,
        },
    )
    return AppConfig.model_validate(data)


async def run_case(provider, task_id, config):
    progress = Progress(provider, task_id)
    thread_id = f"search-fetch-{provider}-{task_id}-{uuid.uuid4().hex[:8]}"
    runtime_config = {
        "configurable": {
            "thread_id": thread_id,
            "app_config": config,
            "model_name": "comparison-deepseek",
            "thinking_enabled": False,
            "subagent_enabled": False,
            "is_plan_mode": False,
            "non_interactive": True,
            "user_id": "search-fetch-test",
        },
        "recursion_limit": 100,
        "callbacks": [progress],
    }
    record = {
        "provider": provider,
        "task_id": task_id,
        "model": "deepseek-flash",
        "prompt": TASKS[task_id],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "layer": "deerflow-make_lead_agent",
    }
    start = time.perf_counter()
    push_current_app_config(config)
    try:
        graph = make_lead_agent(runtime_config)
        # The real graph is unchanged. A fail-closed callback additionally caps paid
        # requests; the original middleware guards, recursion and deadline still apply.
        state = await asyncio.wait_for(
            graph.ainvoke(
                {"messages": [HumanMessage(content=TASKS[task_id])]},
                config=runtime_config,
                context={"thread_id": thread_id, "user_id": "search-fetch-test"},
            ),
            timeout=180,
        )
        messages = state.get("messages", [])
        record["messages"] = [
            {
                "type": m.type,
                "name": getattr(m, "name", None),
                "content": m.content,
                "tool_calls": getattr(m, "tool_calls", None),
                "usage": getattr(m, "usage_metadata", None),
                "tool_status": getattr(m, "status", None),
            }
            for m in messages
        ]
        answers = [
            m.content for m in messages if isinstance(m, AIMessage) and not m.tool_calls
        ]
        record.update(status="completed", answer=answers[-1] if answers else "")
    except Exception as exc:
        record.update(status="failed", error=type(exc).__name__)
    finally:
        pop_current_app_config()
    record.update(
        elapsed_s=round(time.perf_counter() - start, 3),
        model_calls=progress.model_calls,
        completed_tools=progress.completed_tools,
        tool_counts=progress.tool_counts,
    )
    return record


async def run(args, secrets):
    for provider in args.providers:
        config = agent_config(
            provider, secrets[provider], secrets["deepseek"], args.output
        )
        for task_id in TASKS:
            row = await run_case(provider, task_id, config)
            encoded = json.dumps(row, ensure_ascii=False)
            for key in secrets.values():
                encoded = encoded.replace(key, "[REDACTED]")
            (args.output / f"{provider}-{task_id}.json").write_text(
                encoded, encoding="utf-8"
            )
            print(
                json.dumps(
                    {
                        k: row.get(k)
                        for k in (
                            "provider",
                            "task_id",
                            "status",
                            "elapsed_s",
                            "model_calls",
                            "error",
                        )
                    }
                ),
                flush=True,
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--providers", nargs="+", choices=list(PROVIDERS), default=list(PROVIDERS)
    )
    parser.add_argument("--check-config", action="store_true")
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    keys = json.loads(args.key_file.read_text(encoding="utf-8-sig"))
    secrets = {p: keys[v[0]].strip() for p, v in PROVIDERS.items()}
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "empty-skills").mkdir()
    os.environ["DEER_FLOW_HOME"] = str(args.output / "runtime")
    if args.check_config:
        for provider in args.providers:
            config = agent_config(
                provider, secrets[provider], secrets["deepseek"], args.output
            )
            push_current_app_config(config)
            try:
                make_lead_agent(
                    {
                        "configurable": {
                            "app_config": config,
                            "thinking_enabled": False,
                            "non_interactive": True,
                        }
                    }
                )
                print(provider + ": graph build OK", flush=True)
            finally:
                pop_current_app_config()
        return
    asyncio.run(run(args, secrets))


if __name__ == "__main__":
    main()
