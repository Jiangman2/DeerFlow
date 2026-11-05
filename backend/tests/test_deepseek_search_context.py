"""DeepSeek context, bounded source metadata and safe provider error contracts."""

import json
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from deerflow.community.deepseek import tools
from deerflow.runtime.context_keys import CURRENT_RUN_PRE_EXISTING_MESSAGE_IDS_KEY


def configure(monkeypatch, **extras):
    monkeypatch.setattr(tools, "get_app_config", lambda: SimpleNamespace(get_tool_config=lambda _: SimpleNamespace(model_extra=extras)))


def capture_search(monkeypatch, **extras):
    configure(monkeypatch, **extras)
    calls = []
    monkeypatch.setattr(tools, "_post", lambda *args: calls.append(args) or {"results": []})
    return calls


def test_model_schema_does_not_expose_runtime_or_provider_controls():
    schema = convert_to_openai_tool(tools.web_search_tool)["function"]["parameters"]
    assert set(schema["properties"]) == {"query"}


def test_original_question_uses_real_user_not_hidden_context_or_model(monkeypatch):
    calls = capture_search(monkeypatch)
    runtime = SimpleNamespace(
        state={
            "messages": [
                HumanMessage(content="old", id="old", name="user-input"),
                HumanMessage(content=[{"type": "text", "text": "核实  中国海油\n"}, {"type": "image_url", "image_url": {"url": "DO_NOT_SEND"}}, {"type": "text", "text": "2024 年产量"}], id="new", name="user-input"),
                HumanMessage(content="internal injected instruction", additional_kwargs={"hide_from_ui": True}),
                HumanMessage(content="workflow recovery", name="workflow-recovery"),
                AIMessage(content="model invented question"),
            ]
        },
        context={CURRENT_RUN_PRE_EXISTING_MESSAGE_IDS_KEY: ["old"]},
    )
    tools.web_search_tool.func("query", runtime=runtime)
    assert calls[0][2]["ori_question"] == "核实 中国海油 2024 年产量"


def test_original_question_is_unicode_bounded_and_can_be_disabled(monkeypatch):
    calls = capture_search(monkeypatch)
    runtime = SimpleNamespace(state={"messages": [HumanMessage(content="海😀" * 100)]}, context={})
    tools.web_search_tool.func("query", runtime=runtime)
    assert calls[0][2]["ori_question"] == "海😀" * 64
    calls = capture_search(monkeypatch, send_original_question=False)
    tools.web_search_tool.func("query", runtime=runtime)
    assert "ori_question" not in calls[0][2]


def test_no_old_question_fallback_for_current_image_only_message(monkeypatch):
    calls = capture_search(monkeypatch)
    runtime = SimpleNamespace(state={"messages": [HumanMessage(content="old"), HumanMessage(content=[{"type": "image_url", "image_url": {"url": "not-text"}}])]}, context={})
    tools.web_search_tool.func("query", runtime=runtime)
    assert "ori_question" not in calls[0][2]


def test_preexisting_history_is_not_sent_as_current_question(monkeypatch):
    calls = capture_search(monkeypatch)
    runtime = SimpleNamespace(state={"messages": [HumanMessage(content="old", id="old")]}, context={CURRENT_RUN_PRE_EXISTING_MESSAGE_IDS_KEY: ["old"]})
    tools.web_search_tool.func("query", runtime=runtime)
    assert "ori_question" not in calls[0][2]


def test_unidentified_historical_message_is_not_used_across_run_boundary(monkeypatch):
    calls = capture_search(monkeypatch)
    runtime = SimpleNamespace(state={"messages": [HumanMessage(content="unidentified old question")]}, context={CURRENT_RUN_PRE_EXISTING_MESSAGE_IDS_KEY: ["older-id"]})
    tools.web_search_tool.func("query", runtime=runtime)
    assert "ori_question" not in calls[0][2]


@pytest.mark.parametrize("source,expected", [("summary", "Long"), ("snippet", "Shor")])
def test_source_kind_date_and_truncation_are_preserved(monkeypatch, source, expected):
    configure(monkeypatch, snippet_source=source, snippet_max_characters=4)
    monkeypatch.setattr(tools, "_post", lambda *args: {"results": [{"title": "Title", "url": "https://example.com", "summary": "Long summary", "snippet": "Short", "date": "2026-10-18T00:00:00Z"}]})
    result = json.loads(tools.web_search_tool.invoke({"query": "test"}))[0]
    assert result["snippet"] == expected
    assert result["snippet_type"] == source
    assert result["snippet_truncated"] is True
    assert result["published_at"] == "2026-10-18T00:00:00Z"


def test_missing_optional_metadata_is_not_invented(monkeypatch):
    configure(monkeypatch, snippet_source="summary")
    monkeypatch.setattr(tools, "_post", lambda *args: {"results": [{"title": "Title", "url": "https://example.com", "snippet": "Short", "date": None}]})
    result = json.loads(tools.web_search_tool.invoke({"query": "test"}))[0]
    assert result["snippet_type"] == "snippet"
    assert "published_at" not in result
    assert "snippet_truncated" not in result


def test_invalid_source_configuration_does_not_dispatch(monkeypatch):
    calls = capture_search(monkeypatch, snippet_source="invented")
    result = json.loads(tools.web_search_tool.invoke({"query": "test"}))
    assert result["error_code"] == "DEEPSEEK_CONFIG_ERROR"
    assert calls == []


@pytest.mark.parametrize("status,code,retryable", [(401, "DEEPSEEK_AUTHENTICATION", False), (402, "DEEPSEEK_BILLING", False), (429, "DEEPSEEK_RATE_LIMITED", True), (503, "DEEPSEEK_UNAVAILABLE", True)])
def test_structured_provider_errors_are_safe(monkeypatch, status, code, retryable):
    configure(monkeypatch, api_key="SECRET")
    real = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(status, text="SECRET and private diagnostic"))
    monkeypatch.setattr(tools.httpx, "Client", lambda **kwargs: real(transport=transport, **kwargs))
    output = tools.web_search_tool.invoke({"query": "test"})
    result = json.loads(output)
    assert result["error_code"] == code
    assert result["retryable"] is retryable
    assert result["http_status"] == status
    assert result["error_scope"] == "provider_api"
    assert "SECRET" not in output and "private diagnostic" not in output


def test_fetch_404_is_provider_status_not_target_webpage_status(monkeypatch):
    configure(monkeypatch, api_key="SECRET")
    real = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(404))
    monkeypatch.setattr(tools.httpx, "Client", lambda **kwargs: real(transport=transport, **kwargs))
    output = tools.web_fetch_tool.invoke({"url": "https://example.com"})
    assert output.startswith("Error: ")
    result = json.loads(output.removeprefix("Error: "))
    assert result["error_code"] == "DEEPSEEK_PROVIDER_NOT_FOUND"
    assert result["error_scope"] == "provider_api"
    assert result["http_status"] == 404
    assert "target_http_status" not in result


def test_network_timeout_has_retryable_code_without_raw_exception_text(monkeypatch):
    configure(monkeypatch, api_key="SECRET")
    real = httpx.Client

    def fail(request):
        raise httpx.ReadTimeout("SECRET from exception", request=request)

    transport = httpx.MockTransport(fail)
    monkeypatch.setattr(tools.httpx, "Client", lambda **kwargs: real(transport=transport, **kwargs))
    output = tools.web_search_tool.invoke({"query": "test"})
    assert json.loads(output)["error_code"] == "DEEPSEEK_TIMEOUT"
    assert json.loads(output)["retryable"] is True
    assert "SECRET" not in output


def test_registry_toolnode_injects_real_user_question(monkeypatch):
    from benchmark_search_fetch_adapters import build_node, provider_config

    from deerflow.config.app_config import pop_current_app_config, push_current_app_config

    calls = []
    monkeypatch.setattr(tools, "_post", lambda *args: calls.append(args) or {"results": []})
    config = provider_config("deepseek", "test-only-key")
    push_current_app_config(config)
    try:
        result = build_node(config).invoke({"messages": [HumanMessage(content="Real original question", name="user-input"), AIMessage(content="", tool_calls=[{"id": "tc-search", "name": "web_search", "args": {"query": "short query"}}])]})
    finally:
        pop_current_app_config()
    assert isinstance(result["messages"][-1], ToolMessage)
    assert calls[0][2]["ori_question"] == "Real original question"


def test_structured_error_remains_visible_to_existing_tool_progress(monkeypatch):
    from deerflow.agents.middlewares.tool_result_meta import normalize_tool_message

    configure(monkeypatch, api_key="SECRET")
    real = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(429))
    monkeypatch.setattr(tools.httpx, "Client", lambda **kwargs: real(transport=transport, **kwargs))
    output = tools.web_search_tool.invoke({"query": "test"})
    message = normalize_tool_message(ToolMessage(content=output, tool_call_id="tc-429", name="web_search"))
    assert message.additional_kwargs["deerflow_tool_meta"]["status"] == "error"
    assert message.additional_kwargs["deerflow_tool_meta"]["error_type"] == "rate_limited"


def test_cancellation_is_not_wrapped_as_an_adapter_failure(monkeypatch):
    import asyncio

    configure(monkeypatch)

    def cancel(*args):
        raise asyncio.CancelledError

    monkeypatch.setattr(tools, "_post", cancel)
    with pytest.raises(asyncio.CancelledError):
        tools.web_search_tool.invoke({"query": "test"})
