"""Unit tests for the AppForge-backed DataLake community tools."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest


class _StreamResponse:
    def __init__(self, lines: list[str], *, status_code: int = 200, text: str = ""):
        self._lines = lines
        self.status_code = status_code
        self.text = text
        self.is_error = status_code >= 400

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.text.encode()

    def raise_for_status(self):
        if self.is_error:
            request = httpx.Request("POST", "http://appforge.invalid/invoke")
            response = httpx.Response(self.status_code, request=request, text=self.text)
            raise httpx.HTTPStatusError("failed", request=request, response=response)

    def iter_lines(self):
        yield from self._lines


@pytest.fixture(autouse=True)
def reset_appforge_state(monkeypatch: pytest.MonkeyPatch):
    import deerflow.community.appforge.tools as module

    for name in (
        "APPFORGE_API_URL",
        "APPFORGE_API_KEY",
        "APPFORGE_HYTOKEN",
        "APPFORGE_HYACCOUNT",
        "APPFORGE_TRUST_ENV",
    ):
        monkeypatch.delenv(name, raising=False)
    module._missing_config_warned = set()
    yield
    module._missing_config_warned = set()


def _app_config(**extras_by_tool: dict):
    app = MagicMock()
    configs = {name: SimpleNamespace(model_extra=extras) for name, extras in extras_by_tool.items()}
    app.get_tool_config.side_effect = lambda name: configs.get(name)
    return app


def _runtime(*, user_id: str = "user-1", thread_id: str = "thread-1"):
    return SimpleNamespace(
        context={"user_id": user_id, "thread_id": thread_id},
        config={"configurable": {"user_id": user_id, "thread_id": thread_id}},
    )


def _invoke_with_stream(tool_name: str, app, lines: list[str]):
    import deerflow.community.appforge.tools as module

    response = _StreamResponse(lines)
    with patch.object(module, "get_app_config", return_value=app):
        with patch.object(module.httpx, "Client") as client_cls:
            client = client_cls.return_value.__enter__.return_value
            client.stream.return_value = response
            raw = module._invoke_tool(tool_name, "测试问题", _runtime())
            call = client.stream.call_args
            client_kwargs = client_cls.call_args.kwargs
    return json.loads(raw), call, client_kwargs


def test_public_tool_schemas_expose_only_the_business_query():
    from deerflow.community.appforge.tools import (
        datalake_detail_qa_tool,
        datalake_metrics_qa_tool,
        datalake_smart_qa_tool,
    )

    for tool in (
        datalake_smart_qa_tool,
        datalake_metrics_qa_tool,
        datalake_detail_qa_tool,
    ):
        assert set(tool.tool_call_schema.model_json_schema()["properties"]) == {"query"}


def test_conversation_id_is_stable_per_user_thread_and_tool():
    import deerflow.community.appforge.tools as module

    with patch.object(module, "get_app_config", return_value=_app_config()):
        first = module._conversation_id("datalake_smart_qa", _runtime())
        assert first == module._conversation_id("datalake_smart_qa", _runtime())
        assert first != module._conversation_id(
            "datalake_smart_qa",
            _runtime(thread_id="thread-2"),
        )
        assert first != module._conversation_id("datalake_metrics_qa", _runtime())
    assert first.startswith("ApiKeyAgent_")


def test_smart_qa_invocation_uses_secret_config_and_parses_sse():
    app = _app_config(
        datalake_smart_qa={
            "api_url": "http://appforge.internal:9080",
            "api_key": "secret-key",
            "app_id": "app-smart",
            "trust_env": False,
            "inputs": {"business": "exploration"},
            "hytoken": "server-side-hytoken",
            "hyaccount": "server-side-account",
        }
    )
    lines = [
        'data: {"type":"thinking","content":"检索中"}',
        'data: {"type":"knowledge","content":"[{\\"chunk_id\\":\\"c1\\",\\"knowledge_filename\\":\\"well.pdf\\",\\"content\\":\\"命中内容\\"}]"}',
        'data: {"type":"answer","content":"最大井斜为 42 度。"}',
        'data: {"type":"done","xx_ceai_extra_data":{"status":"done","inputToken":10,"outputToken":8,"totalToken":18}}',
        "data: [DONE]",
    ]

    result, call, client_kwargs = _invoke_with_stream(
        "datalake_smart_qa",
        app,
        lines,
    )

    assert result["answer"] == "最大井斜为 42 度。"
    assert result["knowledge"][0] == {
        "chunk_id": "c1",
        "document_name": "well.pdf",
        "content": "命中内容",
    }
    assert result["status"] == "done"
    assert result["usage"]["totalToken"] == 18
    assert client_kwargs == {"timeout": 180.0, "trust_env": False}
    assert call.args[:2] == (
        "POST",
        "http://appforge.internal:9080/appforge/openapi/v1/InvokeApp/app-smart",
    )
    assert call.kwargs["headers"]["Authorization"] == "Bearer secret-key"
    assert call.kwargs["json"]["inputs"] == {
        "business": "exploration",
        "hytoken": "server-side-hytoken",
        "hyaccount": "server-side-account",
    }
    assert call.kwargs["json"]["conversationId"].startswith("ApiKeyAgent_")
    assert "secret-key" not in json.dumps(result, ensure_ascii=False)
    assert "server-side-hytoken" not in json.dumps(result, ensure_ascii=False)


def test_workflow_answer_interrupt_and_finished_events_are_supported():
    app = _app_config(
        datalake_metrics_qa={
            "api_url": "http://appforge.internal:9080",
            "api_key": "secret-key",
        }
    )
    lines = [
        'data: {"event":"answer","data":{"text":"处理中","node_type":"progress"}}',
        'data: {"event":"answer","data":{"text":"计划工作量 12 平方公里","node_type":"end"}}',
        'data: {"event":"workflow_interrupt","data":{"status":"interrupted","outputs":{"choices":["天津分公司","湛江分公司"]}}}',
    ]

    result, _, _ = _invoke_with_stream("datalake_metrics_qa", app, lines)

    assert result["answer"] == "计划工作量 12 平方公里"
    assert result["progress"] == "处理中\n计划工作量 12 平方公里"
    assert result["interrupt"] == {"choices": ["天津分公司", "湛江分公司"]}
    assert result["status"] == "interrupted"


def test_output_is_valid_json_and_stays_below_tool_budget():
    import deerflow.community.appforge.tools as module

    app = _app_config(
        datalake_detail_qa={
            "api_url": "http://appforge.internal:9080",
            "api_key": "secret-key",
        }
    )
    huge_knowledge = json.dumps(
        [
            {
                "chunk_id": f"chunk-{index}",
                "knowledge_filename": f"document-{index}.pdf",
                "content": "内容" * 10_000,
            }
            for index in range(30)
        ],
        ensure_ascii=False,
    )
    lines = [
        f"data: {json.dumps({'type': 'answer', 'content': '回答' * 10_000}, ensure_ascii=False)}",
        f"data: {json.dumps({'type': 'knowledge', 'content': huge_knowledge}, ensure_ascii=False)}",
    ]

    raw_result, _, _ = _invoke_with_stream("datalake_detail_qa", app, lines)
    encoded = json.dumps(raw_result, ensure_ascii=False, indent=2)

    assert len(encoded) <= module._OUTPUT_CHAR_LIMIT
    assert raw_result["truncated"] is True
    assert len(raw_result["knowledge"]) <= module._MAX_KNOWLEDGE_ITEMS


def test_missing_key_and_empty_query_are_safe_structured_errors():
    import deerflow.community.appforge.tools as module

    app = _app_config(datalake_smart_qa={"api_url": "http://internal.invalid"})
    with patch.object(module, "get_app_config", return_value=app):
        missing = json.loads(module._invoke_tool("datalake_smart_qa", "问题", _runtime()))
        empty = json.loads(module._invoke_tool("datalake_smart_qa", "  ", _runtime()))

    assert missing == {
        "error": "AppForge authentication is not configured",
        "code": "missing_api_key",
    }
    assert empty == {"error": "query is required", "code": "invalid_query"}


def test_http_and_network_errors_do_not_expose_internal_details():
    import deerflow.community.appforge.tools as module

    app = _app_config(
        datalake_smart_qa={
            "api_url": "http://10.77.124.142:9080",
            "api_key": "secret-key",
        }
    )
    response = _StreamResponse(
        [],
        status_code=401,
        text="credential secret-key rejected by 10.77.124.142",
    )
    with patch.object(module, "get_app_config", return_value=app):
        with patch.object(module.httpx, "Client") as client_cls:
            client_cls.return_value.__enter__.return_value.stream.return_value = response
            http_error = module._invoke_tool(
                "datalake_smart_qa",
                "问题",
                _runtime(),
            )
        with patch.object(
            module,
            "_stream_invoke",
            side_effect=httpx.ConnectError("failed at http://10.77.124.142:9080"),
        ):
            network_error = module._invoke_tool(
                "datalake_smart_qa",
                "问题",
                _runtime(),
            )

    assert json.loads(http_error) == {
        "error": "AppForge request was rejected",
        "code": "http_401",
        "retryable": False,
    }
    assert json.loads(network_error) == {
        "error": "AppForge request failed",
        "code": "ConnectError",
        "retryable": True,
    }
    assert "10.77" not in http_error + network_error
    assert "secret-key" not in http_error + network_error
