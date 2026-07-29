"""Unit tests for the WeKnora community tools."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest


@pytest.fixture(autouse=True)
def reset_warned_flags(monkeypatch: pytest.MonkeyPatch):
    import deerflow.community.weknora.tools as mod

    for name in (
        "WEKNORA_API_URL",
        "WEKNORA_API_KEY",
        "WEKNORA_KNOWLEDGE_BASE_ID",
        "WEKNORA_KNOWLEDGE_BASE_IDS",
        "WEKNORA_KNOWLEDGE_IDS",
        "WEKNORA_AGENT_ID",
        "WEKNORA_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    mod._missing_config_warned = set()
    yield
    mod._missing_config_warned = set()


def _config_for(extras_by_name: dict[str, dict]):
    app_config = MagicMock()
    app_config.get_tool_config.side_effect = lambda name: SimpleNamespace(model_extra=extras_by_name.get(name, {}))
    return app_config


def _response(data: dict) -> MagicMock:
    response = MagicMock()
    response.json.return_value = data
    response.raise_for_status = MagicMock()
    return response


class _StreamResponse:
    def __init__(self, lines: list[str]):
        self.lines = lines
        self.status_code = 200
        self.text = ""
        self.is_error = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        return None

    def read(self):
        return b""

    def iter_lines(self):
        yield from self.lines


def test_weknora_search_uses_config_scope_and_formats_results():
    app_config = _config_for(
        {
            "weknora_search": {
                "api_url": "http://weknora:8080",
                "api_key": "test-key",
                "knowledge_base_ids": ["kb-1", "kb-2"],
                "max_results": 1,
            }
        }
    )
    api_response = _response(
        {
            "success": True,
            "data": [
                {
                    "id": "chunk-1",
                    "content": "命中片段 1",
                    "score": 0.98,
                    "knowledge_id": "doc-1",
                    "knowledge_base_id": "kb-1",
                    "knowledge_title": "制度文档",
                    "knowledge_filename": "policy.pdf",
                    "chunk_index": 0,
                    "chunk_type": "text",
                    "knowledge_source": "file",
                },
                {"id": "chunk-2", "content": "命中片段 2"},
            ],
        }
    )

    with patch("deerflow.community.weknora.tools.get_app_config", return_value=app_config):
        with patch("deerflow.community.weknora.tools.httpx.Client") as client_cls:
            client = client_cls.return_value.__enter__.return_value
            client.post.return_value = api_response

            from deerflow.community.weknora.tools import weknora_search_tool

            raw = weknora_search_tool.invoke({"query": "报销标准"})

    client.post.assert_called_once()
    url = client.post.call_args.args[0]
    kwargs = client.post.call_args.kwargs
    assert url == "http://weknora:8080/api/v1/knowledge-search"
    assert kwargs["headers"]["X-API-Key"] == "test-key"
    assert kwargs["json"] == {"query": "报销标准", "knowledge_base_ids": ["kb-1", "kb-2"]}

    parsed = json.loads(raw)
    assert parsed["query"] == "报销标准"
    assert parsed["total_results"] == 1
    assert parsed["results"][0]["content"] == "命中片段 1"
    assert parsed["results"][0]["knowledge_base_id"] == "kb-1"
    assert parsed["results"][0]["knowledge_filename"] == "policy.pdf"
    assert parsed["results"][0]["citation_url"] == "/platform/knowledge-bases/kb-1?knowledge_id=doc-1"
    assert parsed["results"][0]["citation_markdown"].endswith("](/platform/knowledge-bases/kb-1?knowledge_id=doc-1)")
    assert "/mnt/" not in parsed["results"][0]["citation_markdown"]


def test_weknora_search_rewrites_resource_images_to_authenticated_gateway_proxy():
    app_config = _config_for(
        {
            "weknora_search": {
                "api_url": "http://weknora:8080",
                "api_key": "test-key",
                "knowledge_base_id": "kb-1",
            }
        }
    )
    api_response = _response(
        {
            "success": True,
            "data": [
                {
                    "id": "chunk-image",
                    "content": "原图：![井位图](resource://image_handle-1)",
                    "knowledge_id": "doc-image",
                    "knowledge_base_id": "kb-1",
                    "knowledge_title": "井位部署图.pdf",
                    "image_info": {
                        "url": "resource://image_handle-1",
                        "caption": "井位图",
                    },
                }
            ],
        }
    )

    with patch("deerflow.community.weknora.tools.get_app_config", return_value=app_config):
        with patch("deerflow.community.weknora.tools.httpx.Client") as client_cls:
            client_cls.return_value.__enter__.return_value.post.return_value = api_response

            from deerflow.community.weknora.tools import weknora_search_tool

            parsed = json.loads(weknora_search_tool.invoke({"query": "显示井位图"}))

    image = parsed["results"][0]["resource_images"][0]
    assert image["resource_uri"] == "resource://image_handle-1"
    assert image["url"] == ("/api/weknora/knowledge-bases/kb-1/files?file_path=resource%3A%2F%2Fimage_handle-1")
    assert image["alt"] == "井位图"
    assert image["markdown"] == (
        "![井位图](/api/weknora/knowledge-bases/kb-1/files?"
        "file_path=resource%3A%2F%2Fimage_handle-1)"
    )
    assert image["url"] in parsed["results"][0]["content"]
    assert "resource://" not in parsed["results"][0]["content"]


def test_weknora_search_rewrites_standalone_image_file_metadata():
    app_config = _config_for(
        {
            "weknora_search": {
                "api_url": "http://weknora:8080",
                "api_key": "test-key",
                "knowledge_base_id": "kb-images",
            }
        }
    )
    image_info = json.dumps(
        [
            {
                "url": "resource://standalone_image_handle-1",
                "original_url": "resource://standalone_image_handle-1",
                "caption": "海上平台四维元数据体系图",
                "ocr_text": "数据资源 数据服务 数据应用 数据治理",
            }
        ],
        ensure_ascii=False,
    )
    api_response = _response(
        {
            "success": True,
            "data": [
                {
                    "id": "chunk-standalone-image",
                    "content": "海上平台四维元数据体系图，包含数据资源、数据服务、数据应用和数据治理。",
                    "knowledge_id": "doc-standalone-image",
                    "knowledge_base_id": "kb-images",
                    "knowledge_title": "四维元数据体系.png",
                    "image_info": image_info,
                }
            ],
        }
    )

    with patch("deerflow.community.weknora.tools.get_app_config", return_value=app_config):
        with patch("deerflow.community.weknora.tools.httpx.Client") as client_cls:
            client_cls.return_value.__enter__.return_value.post.return_value = api_response

            from deerflow.community.weknora.tools import weknora_search_tool

            parsed = json.loads(
                weknora_search_tool.invoke({"query": "显示知识库中的四维元数据体系图片"})
            )

    image = parsed["results"][0]["resource_images"][0]
    assert image["resource_uri"] == "resource://standalone_image_handle-1"
    assert image["url"] == (
        "/api/weknora/knowledge-bases/kb-images/files?"
        "file_path=resource%3A%2F%2Fstandalone_image_handle-1"
    )
    assert image["alt"] == "海上平台四维元数据体系图"
    assert image["markdown"] == f"![海上平台四维元数据体系图]({image['url']})"


def test_weknora_search_accepts_explicit_file_scope_over_config():
    app_config = _config_for(
        {
            "weknora_search": {
                "api_url": "http://weknora:8080/api/v1",
                "api_key": "test-key",
                "knowledge_base_ids": ["kb-config"],
            }
        }
    )

    with patch("deerflow.community.weknora.tools.get_app_config", return_value=app_config):
        with patch("deerflow.community.weknora.tools.httpx.Client") as client_cls:
            client = client_cls.return_value.__enter__.return_value
            client.post.return_value = _response({"success": True, "data": []})

            from deerflow.community.weknora.tools import weknora_search_tool

            raw = weknora_search_tool.invoke({"query": "流程", "knowledge_ids": ["doc-1"], "max_results": 3})

    kwargs = client.post.call_args.kwargs
    assert client.post.call_args.args[0] == "http://weknora:8080/api/v1/knowledge-search"
    assert kwargs["json"] == {
        "query": "流程",
        "knowledge_base_ids": ["kb-config"],
        "knowledge_ids": ["doc-1"],
    }
    assert json.loads(raw)["total_results"] == 0


def test_weknora_search_returns_error_without_scope():
    app_config = _config_for({"weknora_search": {"api_key": "test-key"}})

    with patch("deerflow.community.weknora.tools.get_app_config", return_value=app_config):
        with patch("deerflow.community.weknora.tools.httpx.Client") as client_cls:
            from deerflow.community.weknora.tools import weknora_search_tool

            raw = weknora_search_tool.invoke({"query": "没有范围"})

    assert "knowledge scope is not configured" in json.loads(raw)["error"]
    client_cls.assert_not_called()


def test_weknora_search_returns_http_error_json():
    app_config = _config_for({"weknora_search": {"api_key": "test-key", "knowledge_base_id": "kb-1"}})
    response = MagicMock()
    response.status_code = 401
    response.text = "unauthorized"
    error = httpx.HTTPStatusError("401", request=MagicMock(), response=response)
    response.raise_for_status.side_effect = error

    with patch("deerflow.community.weknora.tools.get_app_config", return_value=app_config):
        with patch("deerflow.community.weknora.tools.httpx.Client") as client_cls:
            client_cls.return_value.__enter__.return_value.post.return_value = response

            from deerflow.community.weknora.tools import weknora_search_tool

            raw = weknora_search_tool.invoke({"query": "报错"})

    parsed = json.loads(raw)
    assert parsed["error"] == "WeKnora API error: HTTP 401"


def test_weknora_ask_parses_sse_answer_and_references():
    app_config = _config_for(
        {
            "weknora_ask": {
                "api_url": "http://weknora:8080",
                "api_key": "test-key",
                "knowledge_base_id": "kb-1",
                "agent_id": "builtin-quick-answer",
            }
        }
    )
    stream_response = _StreamResponse(
        [
            "event: message",
            'data: {"response_type":"references","knowledge_references":[{"id":"chunk-1","content":"依据片段","knowledge_id":"doc-1","knowledge_base_id":"kb-1","knowledge_title":"制度","knowledge_filename":"policy.pdf","score":0.91}]}',
            'data: {"response_type":"answer","content":"答案第一段<kb doc=\\"制度.pdf\\" chunk_id=\\"chunk-1\\" kb_id=\\"kb-1\\" />","done":false}',
            'data: {"response_type":"answer","content":"，答案第二段","done":false}',
            'data: {"response_type":"answer","content":"","done":true}',
        ]
    )

    with patch("deerflow.community.weknora.tools.get_app_config", return_value=app_config):
        with patch("deerflow.community.weknora.tools.httpx.Client") as client_cls:
            client = client_cls.return_value.__enter__.return_value
            client.stream.return_value = stream_response

            from deerflow.community.weknora.tools import weknora_ask_tool

            raw = weknora_ask_tool.invoke({"query": "报销标准是什么？", "session_id": "session-1"})

    client.stream.assert_called_once()
    args = client.stream.call_args.args
    kwargs = client.stream.call_args.kwargs
    assert args == ("POST", "http://weknora:8080/api/v1/knowledge-chat/session-1")
    assert kwargs["json"] == {
        "query": "报销标准是什么？",
        "knowledge_base_id": "kb-1",
        "agent_id": "builtin-quick-answer",
    }

    parsed = json.loads(raw)
    assert parsed["answer"] == "答案第一段[citation:制度](/platform/knowledge-bases/kb-1?knowledge_id=doc-1)，答案第二段"
    assert parsed["references"][0]["content"] == "依据片段"
    assert parsed["references"][0]["knowledge_base_id"] == "kb-1"
    assert parsed["references"][0]["knowledge_filename"] == "policy.pdf"
    assert parsed["references"][0]["citation_url"] == "/platform/knowledge-bases/kb-1?knowledge_id=doc-1"


def test_weknora_ask_preserves_answer_images_as_same_origin_resources():
    app_config = _config_for(
        {
            "weknora_ask": {
                "api_url": "http://weknora:8080",
                "api_key": "test-key",
                "knowledge_base_id": "kb-1",
            }
        }
    )
    stream_response = _StreamResponse(
        [
            (
                'data: {"response_type":"references","knowledge_references":'
                '[{"id":"chunk-image","content":"![井位图](resource://image_handle-1)",'
                '"knowledge_id":"doc-image","knowledge_base_id":"kb-1",'
                '"knowledge_title":"井位部署图.pdf",'
                '"image_info":{"url":"resource://image_handle-1"}}]}'
            ),
            ('data: {"response_type":"answer","content":"检索到的原图如下：\\n\\n![井位图](resource://image_handle-1)","done":true}'),
        ]
    )

    with patch("deerflow.community.weknora.tools.get_app_config", return_value=app_config):
        with patch("deerflow.community.weknora.tools.httpx.Client") as client_cls:
            client_cls.return_value.__enter__.return_value.stream.return_value = stream_response

            from deerflow.community.weknora.tools import weknora_ask_tool

            parsed = json.loads(
                weknora_ask_tool.invoke(
                    {
                        "query": "显示原图",
                        "session_id": "session-image",
                    }
                )
            )

    expected_url = "/api/weknora/knowledge-bases/kb-1/files?file_path=resource%3A%2F%2Fimage_handle-1"
    assert parsed["answer"] == f"检索到的原图如下：\n\n![井位图]({expected_url})"
    assert parsed["answer_images"] == [
        {
            "resource_uri": "resource://image_handle-1",
            "url": expected_url,
            "markdown": f"![井位图]({expected_url})",
        }
    ]
    assert parsed["references"][0]["resource_images"][0]["url"] == expected_url


def test_weknora_ask_creates_session_when_omitted():
    app_config = _config_for(
        {
            "weknora_ask": {
                "api_url": "http://weknora:8080",
                "api_key": "test-key",
                "knowledge_base_id": "kb-1",
            }
        }
    )
    stream_response = _StreamResponse(['data: {"response_type":"answer","content":"命中答案","done":true}'])

    with patch("deerflow.community.weknora.tools.get_app_config", return_value=app_config):
        with patch("deerflow.community.weknora.tools.httpx.Client") as client_cls:
            client = client_cls.return_value.__enter__.return_value
            client.post.return_value = _response({"success": True, "data": {"id": "session-new"}})
            client.stream.return_value = stream_response

            from deerflow.community.weknora.tools import weknora_ask_tool

            raw = weknora_ask_tool.invoke({"query": "验证口令是什么？"})

    client.post.assert_called_once()
    assert client.post.call_args.args[0] == "http://weknora:8080/api/v1/sessions"
    assert client.post.call_args.kwargs["json"]["title"] == "验证口令是什么？"
    assert client.stream.call_args.args == ("POST", "http://weknora:8080/api/v1/knowledge-chat/session-new")

    parsed = json.loads(raw)
    assert parsed["session_id"] == "session-new"
    assert parsed["answer"] == "命中答案"
