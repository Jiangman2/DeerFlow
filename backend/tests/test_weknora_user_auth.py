"""User-scoped WeKnora authentication behavior."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _config_for(extras_by_name: dict[str, dict]):
    app_config = MagicMock()
    app_config.get_tool_config.side_effect = lambda name: SimpleNamespace(model_extra=extras_by_name.get(name, {}))
    return app_config


def _response(data: dict) -> MagicMock:
    response = MagicMock()
    response.json.return_value = data
    response.raise_for_status = MagicMock()
    return response


def test_weknora_search_prefers_runtime_bearer_token_over_api_key():
    app_config = _config_for(
        {
            "weknora_search": {
                "api_url": "http://weknora:8080",
                "api_key": "tenant-api-key",
                "knowledge_base_id": "kb-1",
            }
        }
    )

    with patch("deerflow.community.weknora.tools.get_app_config", return_value=app_config):
        with patch("deerflow.community.weknora.tools.httpx.Client") as client_cls:
            client = client_cls.return_value.__enter__.return_value
            client.post.return_value = _response({"success": True, "data": []})

            from deerflow.community.weknora.tools import weknora_search_tool
            from deerflow.runtime.weknora_context import reset_current_weknora_access_token, set_current_weknora_access_token

            token = set_current_weknora_access_token("user-token")
            try:
                weknora_search_tool.invoke({"query": "policy"})
            finally:
                reset_current_weknora_access_token(token)

    headers = client.post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer user-token"
    assert "X-API-Key" not in headers


def test_weknora_search_keeps_api_key_fallback_without_runtime_token():
    app_config = _config_for(
        {
            "weknora_search": {
                "api_url": "http://weknora:8080",
                "api_key": "tenant-api-key",
                "knowledge_base_id": "kb-1",
            }
        }
    )

    with patch("deerflow.community.weknora.tools.get_app_config", return_value=app_config):
        with patch("deerflow.community.weknora.tools.httpx.Client") as client_cls:
            client = client_cls.return_value.__enter__.return_value
            client.post.return_value = _response({"success": True, "data": []})

            from deerflow.community.weknora.tools import weknora_search_tool

            weknora_search_tool.invoke({"query": "policy"})

    headers = client.post.call_args.kwargs["headers"]
    assert headers["X-API-Key"] == "tenant-api-key"
    assert "Authorization" not in headers
