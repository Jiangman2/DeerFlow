"""Reproduce middleware super-step overhead with a fully local fake agent.

This is a configuration fixture, not a reconstruction of the live compiled
production graph. No real middleware body, model provider, shell, sandbox,
application startup, database, or network operation is executed.
"""

from __future__ import annotations

import ast
from collections import Counter
import importlib.metadata
import json
import os
from pathlib import Path

# Do not inherit an optional remote tracing callback from the caller's shell.
for tracing_key in ("LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING"):
    os.environ[tracing_key] = "false"

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.errors import GraphRecursionError


VERSIONS = {"langchain": "1.3.14", "langchain-core": "1.4.9", "langgraph": "1.2.9"}
HOOK_NAMES = {"before_agent", "before_model", "after_model", "after_agent"}
# Summarization is absent in this fixture. These choices have not been proved
# to match the production task's enabled middleware configuration.
MIDDLEWARE_NAMES = [
    "DataLakeSourceMiddleware",
    "ThreadDataMiddleware",
    "UploadsMiddleware",
    "SandboxMiddleware",
    "DanglingToolCallMiddleware",
    "ToolProgressMiddleware",
    "DynamicContextMiddleware",
    "WeKnoraScopeMiddleware",
    "SkillActivationMiddleware",
    "SkillWorkflowMiddleware",
    "DurableContextMiddleware",
    "TokenUsageMiddleware",
    "TitleMiddleware",
    "MemoryMiddleware",
    "SubagentLimitMiddleware",
    "LoopDetectionMiddleware",
    "TokenBudgetMiddleware",
    "ArtifactDeliveryMiddleware",
    "TerminalResponseMiddleware",
    "WeKnoraCitationMiddleware",
    "SafetyFinishReasonMiddleware",
]
EXPECTED_HOOK_COUNTS = {
    "before_agent": 13,
    "before_model": 3,
    "after_model": 13,
    "after_agent": 8,
}


class OfflineModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


@tool
def bash(command: str) -> str:
    """Return synthetic success without executing the command or any shell."""
    return '{"status":"SYNTHETIC_SUCCESS"}'


def noop(self, state, runtime):
    return None


def load_fixture_hooks() -> dict[str, set[str]]:
    repo_root = Path(__file__).resolve().parents[3]
    harness_root = repo_root / "backend/packages/harness/deerflow"
    files = sorted((harness_root / "agents/middlewares").glob("*.py"))
    files.append(harness_root / "sandbox/middleware.py")
    classes: dict[str, set[str]] = {}
    for path in files:
        # Parse declarations only. Never import the real middleware modules.
        for declaration in ast.parse(path.read_text(encoding="utf-8")).body:
            if not isinstance(declaration, ast.ClassDef):
                continue
            if declaration.name not in MIDDLEWARE_NAMES:
                continue
            hooks = set()
            for node in declaration.body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if node.name in HOOK_NAMES:
                    hooks.add(node.name)
                elif node.name.startswith("a") and node.name[1:] in HOOK_NAMES:
                    hooks.add(node.name[1:])
            if declaration.name in classes:
                raise RuntimeError(f"Duplicate fixture class: {declaration.name}")
            classes[declaration.name] = hooks
    if set(classes) != set(MIDDLEWARE_NAMES):
        raise RuntimeError("The source middleware fixture is incomplete")
    counts = dict(Counter(hook for name in MIDDLEWARE_NAMES for hook in classes[name]))
    if counts != EXPECTED_HOOK_COUNTS:
        raise RuntimeError(f"The source hook fixture changed: {counts}")
    return classes


def trace_case(limit: int, hooks: dict[str, set[str]]) -> dict:
    middleware = [
        type(name, (AgentMiddleware,), {hook: noop for hook in hooks[name]})()
        for name in MIDDLEWARE_NAMES
    ]
    responses = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "bash",
                    "args": {"command": f"synthetic-{index}"},
                    "id": f"call-{index}",
                }
            ],
        )
        for index in range(16)
    ]
    responses.append(AIMessage(content="Accepted synthetic completion."))
    graph = create_agent(
        model=OfflineModel(responses=responses),
        tools=[bash],
        middleware=middleware,
    )
    seen = []
    status = "SUCCESS"
    try:
        for event in graph.stream(
            {"messages": [HumanMessage(content="Run local fake tools only.")]},
            {"recursion_limit": limit},
            stream_mode="debug",
        ):
            if event.get("type") == "task":
                seen.append((event["step"], event["payload"]["name"]))
    except GraphRecursionError:
        status = "RECURSION_CAPPED"
    counts = Counter(name.rsplit(".", 1)[-1] for _, name in seen)
    return {
        "limit": limit,
        "status": status,
        "tasks": len(seen),
        "counts": dict(counts),
        "last_tasks": seen[-4:],
    }


def main() -> None:
    actual_versions = {name: importlib.metadata.version(name) for name in VERSIONS}
    if actual_versions != VERSIONS:
        raise RuntimeError(f"The historical library fixture changed: {actual_versions}")
    hooks = load_fixture_hooks()
    cases = [trace_case(limit, hooks) for limit in (300, 1000)]
    assert cases[0]["status"] == "RECURSION_CAPPED"
    assert cases[0]["tasks"] == 300
    assert cases[0]["counts"] == {
        "before_agent": 13,
        "before_model": 48,
        "model": 16,
        "after_model": 208,
        "tools": 15,
    }
    assert cases[1]["status"] == "SUCCESS"
    assert cases[1]["tasks"] == 326
    assert cases[1]["counts"] == {
        "before_agent": 13,
        "before_model": 51,
        "model": 17,
        "after_model": 221,
        "tools": 16,
        "after_agent": 8,
    }
    print(json.dumps({
        "schema": "scheduler-step-budget-local-fixture.v1",
        "versions": actual_versions,
        "live_compiled_graph_proven": False,
        "real_model_requests": 0,
        "shell_commands_executed": 0,
        "production_modified": False,
        "hook_counts": EXPECTED_HOOK_COUNTS,
        "cases": cases,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
