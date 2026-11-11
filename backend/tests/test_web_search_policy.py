"""No network: exercise model schema filtering and actual graph tool dispatch."""

import asyncio
from types import SimpleNamespace

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from deerflow.agents.middlewares.web_search_middleware import WebSearchMiddleware
from deerflow.runtime.web_search_policy import filter_web_tools, web_search_enabled


class ToolCallingModel(FakeMessagesListChatModel):
    bound_names: list[list[str]] = []

    def bind_tools(self, tools, **kwargs):
        self.bound_names.append([t.name for t in tools])
        return self


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("name", ["web_search", "web_fetch"])
def test_compiled_graph_blocks_hallucinated_calls_without_network(enabled, asynchronous, name):
    calls = []

    @tool(name)
    def synthetic_web(query: str) -> str:
        """Return a synthetic web result."""
        calls.append(query)
        return "synthetic result"

    model = ToolCallingModel(
        responses=[
            AIMessage("", tool_calls=[{"name": name, "id": "web-call", "args": {"query": "synthetic"}}]),
            AIMessage("finished"),
        ]
    )
    graph = create_agent(model=model, tools=[synthetic_web], middleware=[WebSearchMiddleware()])
    kwargs = {"context": {"web_search_enabled": enabled}}
    state = {"messages": [HumanMessage("synthetic request")]}
    result = asyncio.run(graph.ainvoke(state, **kwargs)) if asynchronous else graph.invoke(state, **kwargs)
    receipts = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(receipts) == 1
    assert receipts[0].status == ("success" if enabled else "error")
    assert calls == (["synthetic"] if enabled else [])
    assert all((name in names) == enabled for names in model.bound_names)
    if not enabled:
        assert "web_search_disabled" in receipts[0].content


@pytest.mark.parametrize("value", [None, "true", "false", 1, 0, [], {}])
def test_only_explicit_true_enables_browser_web_tools(value):
    assert not web_search_enabled({"web_search_enabled": value})


def test_missing_toggle_preserves_existing_channel_behavior_and_other_tools():
    tools = [SimpleNamespace(name=n) for n in ["web_search", "web_fetch", "bash", "knowledge_search", "datalake_data_qa"]]
    assert filter_web_tools(tools, {}) == tools
    assert [t.name for t in filter_web_tools(tools, {"web_search_enabled": False})] == ["bash", "knowledge_search", "datalake_data_qa"]
    assert filter_web_tools(tools, {"web_search_enabled": True}) == tools


def test_same_middleware_has_no_cross_user_or_previous_turn_permission():
    middleware = WebSearchMiddleware()
    for enabled in [False, True, False, True]:
        request = SimpleNamespace(runtime=SimpleNamespace(context={"web_search_enabled": enabled}), tool_call={"name": "web_fetch", "id": "t"})
        calls = []
        result = middleware.wrap_tool_call(request, lambda _: calls.append("executed"))
        assert calls == (["executed"] if enabled else [])
        assert (result is None) == enabled


@pytest.mark.parametrize("value", [False, True])
def test_gateway_runtime_choice_overrides_config_without_checkpoint_persistence(value):
    from app.gateway.services import merge_run_context_overrides

    config = {"context": {"web_search_enabled": not value}, "configurable": {"web_search_enabled": not value}}
    merge_run_context_overrides(config, {"web_search_enabled": value})
    assert config["context"]["web_search_enabled"] is value
    assert "web_search_enabled" not in config["configurable"]


@pytest.mark.parametrize("value", [None, "true", "false", 1, 0, []])
def test_gateway_rejects_non_boolean_toggle(value):
    from fastapi import HTTPException

    from app.gateway.services import merge_run_context_overrides

    with pytest.raises(HTTPException) as exc:
        merge_run_context_overrides({}, {"web_search_enabled": value})
    assert exc.value.status_code == 400
