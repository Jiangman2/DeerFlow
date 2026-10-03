"""Unit tests for Retrieve-backed and AppForge-backed DataLake tools."""

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
        "DATALAKE_RETRIEVE_API_URL",
        "DATALAKE_RETRIEVE_PROJECT_ID",
        "DATALAKE_RETRIEVE_DOCUMENT_STORE_ID",
        "DATALAKE_RETRIEVE_DOCUMENT_STORE_IDS",
        "DATALAKE_RETRIEVE_TRUST_ENV",
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


def _invoke_with_retrieve(app, response_payload: dict):
    import deerflow.community.appforge.tools as module

    response = httpx.Response(
        200,
        request=httpx.Request("POST", "http://datalake.invalid/retrieve"),
        json=response_payload,
    )
    with patch.object(module, "get_app_config", return_value=app):
        with patch.object(module.httpx, "Client") as client_cls:
            client = client_cls.return_value.__enter__.return_value
            client.post.return_value = response
            raw = module._invoke_retrieve_tool("测试问题", _runtime())
            call = client.post.call_args
            client_kwargs = client_cls.call_args.kwargs
    return json.loads(raw), call, client_kwargs


def test_public_tool_schemas_expose_only_the_business_query():
    from deerflow.community.appforge.tools import (
        datalake_detail_qa_tool,
        datalake_md_search_tool,
        datalake_metrics_qa_tool,
        datalake_smart_qa_tool,
    )

    for tool in (
        datalake_smart_qa_tool,
        datalake_metrics_qa_tool,
        datalake_detail_qa_tool,
        datalake_md_search_tool,
    ):
        assert set(tool.tool_call_schema.model_json_schema()["properties"]) == {"query"}


def test_disabled_data_lake_context_rejects_before_configuration_or_network():
    import deerflow.community.appforge.tools as module

    runtime = SimpleNamespace(
        context={"datalake_enabled": False},
        config={"configurable": {}},
    )
    with patch.object(module, "get_app_config", side_effect=AssertionError("config must not be read")):
        with patch.object(module.httpx, "Client", side_effect=AssertionError("network must not start")):
            result = json.loads(module._invoke_retrieve_tool("问题", runtime))

    assert result == {
        "error": "Data Lake use is disabled for this run",
        "code": "datalake_disabled",
        "retryable": False,
    }


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


def test_metrics_qa_invocation_uses_secret_config_and_parses_sse():
    app = _app_config(
        datalake_metrics_qa={
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
        "datalake_metrics_qa",
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
    assert call.kwargs["json"]["conversationId"].startswith("WorkflowAgent_")
    assert "secret-key" not in json.dumps(result, ensure_ascii=False)
    assert "server-side-hytoken" not in json.dumps(result, ensure_ascii=False)


def test_master_data_search_uses_its_own_workflow_and_is_not_toggle_gated():
    app = _app_config(
        datalake_md_search={
            "api_url": "http://appforge.internal:9080",
            "api_key": "master-data-secret",
            "app_id": "app-1qdkpgka",
            "trust_env": False,
        }
    )
    result, call, _ = _invoke_with_stream(
        "datalake_md_search",
        app,
        ['data: {"type":"answer","content":"中海油有两家分公司。"}'],
    )

    assert result["answer"] == "中海油有两家分公司。"
    assert call.args[:2] == (
        "POST",
        "http://appforge.internal:9080/appforge/openapi/v1/InvokeApp/app-1qdkpgka",
    )
    assert call.kwargs["headers"]["Authorization"] == "Bearer master-data-secret"
    assert call.kwargs["json"]["inputs"] == {"hytoken": "", "hyaccount": ""}
    assert call.kwargs["json"]["conversationId"].startswith("SquareWorkflow_")
    assert "master-data-secret" not in json.dumps(result, ensure_ascii=False)

    import deerflow.community.appforge.tools as module

    with patch.object(module, "get_app_config", return_value=app):
        with patch.object(module, "_stream_invoke", return_value={"answer": "可用"}):
            enabled = json.loads(
                module._invoke_tool(
                    "datalake_md_search",
                    "中海油有哪些分公司",
                    SimpleNamespace(context={"datalake_enabled": False}),
                )
            )
    assert enabled["answer"] == "可用"


def test_smart_qa_uses_retrieve_without_appforge_credentials():
    app = _app_config(
        datalake_smart_qa={
            "retrieve_api_url": "http://datalake.internal/retrieve",
            "project_id": "proj-tested",
            "document_store_ids": ["docstore-tested"],
            "retrieve_type": "Hybrid",
            "reranking_enabled": False,
            "top_k": 10,
            "score_threshold_enabled": False,
            "weight": 0.5,
            "trust_env": False,
        }
    )
    response_payload = {
        "id": "retrieve-1",
        "query": "测试问题",
        "type": "Hybrid",
        "strategy": {"weight": 0.5},
        "retrieveChunks": [
            {
                "id": "chunk-1",
                "documentID": "document-1",
                "documentStoreID": "docstore-tested",
                "documentName": "测试文档.pdf",
                "documentStoreName": "默认文档库",
                "fileUUID": "file-1",
                "type": "text",
                "content": "命中的叙述证据",
                "score": 0.91,
            }
        ],
    }

    result, call, client_kwargs = _invoke_with_retrieve(app, response_payload)

    assert client_kwargs == {"timeout": 30.0, "trust_env": False}
    assert call.args[:2] == ("http://datalake.internal/retrieve",)
    assert call.args[0] == "http://datalake.internal/retrieve"
    assert call.kwargs["json"] == {
        "projectID": "proj-tested",
        "documentStoreIDs": ["docstore-tested"],
        "documentIDs": [],
        "query": "测试问题",
        "type": "Hybrid",
        "extra_data": {},
        "rerankingEnabled": False,
        "topK": 10,
        "scoreThresholdEnabled": False,
        "weight": 0.5,
    }
    assert result["chunks"] == [
        {
            "chunk_id": "chunk-1",
            "document_id": "document-1",
            "document_store_id": "docstore-tested",
            "document_name": "测试文档.pdf",
            "document_store_name": "默认文档库",
            "file_uuid": "file-1",
            "type": "text",
            "content": "命中的叙述证据",
            "score": 0.91,
        }
    ]


def test_smart_qa_retrieve_requires_project_and_document_store():
    import deerflow.community.appforge.tools as module

    app = _app_config(datalake_smart_qa={"retrieve_api_url": "http://datalake.internal/retrieve"})
    with patch.object(module, "get_app_config", return_value=app):
        with patch.object(module.httpx, "Client", side_effect=AssertionError("network must not start")):
            result = json.loads(module._invoke_retrieve_tool("问题", _runtime()))

    assert result == {
        "error": "DataLake Retrieve is not configured",
        "code": "missing_retrieve_project_id",
        "retryable": False,
    }


def test_retrieve_normalizes_chinese_image_preview_urls():
    import deerflow.community.appforge.tools as module

    raw = module._safe_retrieve_json(
        {
            "query": "图片",
            "chunks": [
                {
                    "content": "http://10.77.124.142:30900/img-preview/中文目录/井身结构 图.png",
                }
            ],
        }
    )

    result = json.loads(raw)
    proxied = result["chunks"][0]["content"]

    assert proxied.startswith("/api/datalake/image-preview?src=")
    assert "10.77.124.142%3A30900" in proxied
    assert "%25E4%25B8%25AD%25E6%2596%2587%25E7%259B%25AE%25E5%25BD%2595" in proxied
    assert "%25E4%25BA%2595%25E8%25BA%25AB%25E7%25BB%2593%25E6%259E%2584%2520%25E5%259B%25BE.png" in proxied


def test_retrieve_does_not_proxy_an_unapproved_image_preview_origin():
    import deerflow.community.appforge.tools as module

    raw_url = "http://example.invalid:30900/img-preview/document/image.png"

    assert module._normalize_image_preview_urls(raw_url) == raw_url


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

    app = _app_config(datalake_metrics_qa={"api_url": "http://internal.invalid"})
    with patch.object(module, "get_app_config", return_value=app):
        missing = json.loads(module._invoke_tool("datalake_metrics_qa", "问题", _runtime()))
        empty = json.loads(module._invoke_tool("datalake_metrics_qa", "  ", _runtime()))

    assert missing == {
        "error": "AppForge authentication is not configured",
        "code": "missing_api_key",
    }
    assert empty == {"error": "query is required", "code": "invalid_query"}


def test_http_and_network_errors_do_not_expose_internal_details():
    import deerflow.community.appforge.tools as module

    app = _app_config(
        datalake_metrics_qa={
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
                "datalake_metrics_qa",
                "问题",
                _runtime(),
            )
        with patch.object(
            module,
            "_stream_invoke",
            side_effect=httpx.ConnectError("failed at http://10.77.124.142:9080"),
        ):
            network_error = module._invoke_tool(
                "datalake_metrics_qa",
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
