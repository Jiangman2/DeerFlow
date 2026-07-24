import json

import pytest

from app.gateway import weknora_identity


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
    def __init__(self):
        self.calls = []
        self.closed = False

    def transaction(self):
        return _FakeTransaction()

    async def execute(self, sql, *args):
        self.calls.append((sql, args))

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def reset_default_model_seed(monkeypatch):
    monkeypatch.setattr(weknora_identity, "_default_models_seeded", False)


@pytest.mark.asyncio
async def test_ensure_weknora_default_models_seeds_platform_builtins(monkeypatch):
    fake_connection = _FakeConnection()
    monkeypatch.setenv("WEKNORA_DB_PASSWORD", "secret")
    monkeypatch.setenv("LLM_API_KEY", "llm-secret")
    monkeypatch.setenv("EMBEDDING_API_KEY", "embedding-secret")
    monkeypatch.setenv("DeepSeek_V4_API_KEY", "internal-deepseek-secret")

    async def connect():
        return fake_connection

    monkeypatch.setattr(weknora_identity, "_connect_weknora_database", connect)

    await weknora_identity.ensure_weknora_default_models()

    assert fake_connection.closed is True
    assert len(fake_connection.calls) == 7
    chat_args = fake_connection.calls[0][1]
    deepseek_args = fake_connection.calls[1][1]
    internal_deepseek_args = fake_connection.calls[2][1]
    embedding_args = fake_connection.calls[3][1]
    rerank_args = fake_connection.calls[4][1]
    vision_args = fake_connection.calls[5][1]
    asr_args = fake_connection.calls[6][1]
    assert chat_args[:6] == (
        "deerflow-qwen-plus",
        10000,
        "qwen-plus",
        "KnowledgeQA",
        "remote",
        "DeerFlow WeKnora chat model",
    )
    assert json.loads(chat_args[6]) == {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "llm-secret",
        "provider": "aliyun",
    }
    assert chat_args[7] is True
    assert deepseek_args[:6] == (
        "deerflow-deepseek-v4-pro",
        10000,
        "deepseek-v4-pro",
        "KnowledgeQA",
        "remote",
        "DeerFlow WeKnora DeepSeek chat model",
    )
    assert json.loads(deepseek_args[6]) == {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "llm-secret",
        "provider": "aliyun",
    }
    assert deepseek_args[7] is False
    assert internal_deepseek_args[:6] == (
        "deerflow-cnooc-deepseek-v4-flash",
        10000,
        "DeepSeek-V4-Flash",
        "KnowledgeQA",
        "remote",
        "DeerFlow WeKnora CNOOC DeepSeek chat model",
    )
    assert json.loads(internal_deepseek_args[6]) == {
        "base_url": "http://model.ai.cnooc/member1/deepseek-v4-flash-291b-1m/v1",
        "api_key": "internal-deepseek-secret",
        "provider": "generic",
    }
    assert internal_deepseek_args[7] is False
    assert embedding_args[0] == "deerflow-dashscope-embedding"
    assert embedding_args[3] == "Embedding"
    assert json.loads(embedding_args[6])["embedding_parameters"] == {
        "dimension": 1024,
        "truncate_prompt_tokens": 0,
    }
    assert rerank_args[:6] == (
        "deerflow-dashscope-rerank",
        10000,
        "qwen3-rerank",
        "Rerank",
        "remote",
        "DeerFlow WeKnora rerank model",
    )
    assert json.loads(rerank_args[6]) == {
        "base_url": "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank",
        "api_key": "llm-secret",
        "provider": "aliyun",
    }
    assert vision_args[:6] == (
        "deerflow-dashscope-vlm",
        10000,
        "qwen3-vl-plus",
        "VLLM",
        "remote",
        "DeerFlow WeKnora vision model",
    )
    assert json.loads(vision_args[6]) == {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "llm-secret",
        "provider": "aliyun",
    }
    assert asr_args[:6] == (
        "deerflow-dashscope-asr",
        10000,
        "qwen3-asr-flash",
        "ASR",
        "remote",
        "DeerFlow WeKnora speech recognition model",
    )
    assert json.loads(asr_args[6]) == {
        "base_url": "http://gateway:8001/api/weknora/bailian/v1",
        "api_key": "llm-secret",
        "provider": "generic",
    }


def test_weknora_default_models_omit_internal_deepseek_without_key(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "llm-secret")
    monkeypatch.setenv("EMBEDDING_API_KEY", "embedding-secret")
    monkeypatch.delenv("CNOOC_DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DeepSeek_V4_API_KEY", raising=False)

    models = weknora_identity._weknora_default_models()

    assert [model.id for model in models] == [
        "deerflow-qwen-plus",
        "deerflow-deepseek-v4-pro",
        "deerflow-dashscope-embedding",
        "deerflow-dashscope-rerank",
        "deerflow-dashscope-vlm",
        "deerflow-dashscope-asr",
    ]


@pytest.mark.asyncio
async def test_ensure_weknora_default_models_skips_without_database(monkeypatch):
    async def fail_connect():
        raise AssertionError("should not connect without WeKnora DB config")

    monkeypatch.delenv("WEKNORA_DATABASE_URL", raising=False)
    monkeypatch.delenv("WEKNORA_DB_PASSWORD", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "llm-secret")
    monkeypatch.setenv("EMBEDDING_API_KEY", "embedding-secret")
    monkeypatch.setattr(weknora_identity, "_connect_weknora_database", fail_connect)

    await weknora_identity.ensure_weknora_default_models()


@pytest.mark.asyncio
async def test_ensure_weknora_default_mineru_preserves_existing_settings(monkeypatch):
    calls = []

    class _Response:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, **kwargs):
            calls.append(("GET", url, kwargs))
            return _Response(
                200,
                {
                    "success": True,
                    "data": {
                        "mineru_model": "pipeline",
                        "mineru_enable_table": True,
                    },
                },
            )

        async def put(self, url, **kwargs):
            calls.append(("PUT", url, kwargs))
            return _Response(200, {"success": True})

    monkeypatch.setenv("WEKNORA_MINERU_ENDPOINT", "http://deer-flow-mineru-adapter:50052/")
    monkeypatch.setattr(
        weknora_identity,
        "weknora_endpoint",
        lambda path: f"http://weknora.test/api/v1{path}",
    )
    monkeypatch.setattr(weknora_identity, "weknora_timeout", lambda: 10.0)
    monkeypatch.setattr(weknora_identity.httpx, "AsyncClient", lambda **kwargs: _Client())
    session = weknora_identity.WeKnoraSession(
        token="token",
        refresh_token="refresh",
        user={"id": "user"},
        tenant={"id": 100},
        memberships=[
            {"tenant_id": 100, "role": "owner"},
            {"tenant_id": 200, "role": "viewer"},
        ],
    )

    await weknora_identity.ensure_weknora_default_mineru(session)

    assert [call[0] for call in calls] == ["GET", "PUT"]
    headers = calls[0][2]["headers"]
    assert headers["X-Tenant-ID"] == "100"
    assert calls[1][2]["json"] == {
        "mineru_model": "pipeline",
        "mineru_enable_table": True,
        "mineru_endpoint": "http://deer-flow-mineru-adapter:50052",
    }


@pytest.mark.asyncio
async def test_ensure_weknora_default_mineru_skips_unchanged_endpoint(monkeypatch):
    put_called = False

    class _Response:
        status_code = 200

        def json(self):
            return {
                "success": True,
                "data": {"mineru_endpoint": "http://deer-flow-mineru-adapter:50052/"},
            }

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, **kwargs):
            return _Response()

        async def put(self, url, **kwargs):
            nonlocal put_called
            put_called = True
            return _Response()

    monkeypatch.setenv("WEKNORA_MINERU_ENDPOINT", "http://deer-flow-mineru-adapter:50052")
    monkeypatch.setattr(
        weknora_identity,
        "weknora_endpoint",
        lambda path: f"http://weknora.test/api/v1{path}",
    )
    monkeypatch.setattr(weknora_identity, "weknora_timeout", lambda: 10.0)
    monkeypatch.setattr(weknora_identity.httpx, "AsyncClient", lambda **kwargs: _Client())
    session = weknora_identity.WeKnoraSession(
        token="token",
        refresh_token="refresh",
        user={"id": "user"},
        tenant={"id": 100},
        memberships=[{"tenant_id": 100, "role": "owner"}],
    )

    await weknora_identity.ensure_weknora_default_mineru(session)

    assert put_called is False


@pytest.mark.asyncio
@pytest.mark.parametrize(("deerflow_role", "expected_admin"), [("admin", True), ("user", False)])
async def test_sync_weknora_role_mirrors_deerflow_system_role(monkeypatch, deerflow_role, expected_admin):
    fake_connection = _FakeConnection()
    monkeypatch.setenv("WEKNORA_DB_PASSWORD", "secret")

    async def connect():
        return fake_connection

    monkeypatch.setattr(weknora_identity, "_connect_weknora_database", connect)
    session = weknora_identity.WeKnoraSession(
        token="token",
        refresh_token="refresh",
        user={"id": "wk-user", "email": "user@example.com", "is_system_admin": not expected_admin},
        tenant={"id": 1, "name": "Workspace"},
        memberships=[{"tenant_id": 1, "role": "owner"}],
    )

    synced = await weknora_identity.sync_weknora_role(
        type("DeerFlowUser", (), {"system_role": deerflow_role, "email": "user@example.com"})(),
        session,
    )

    assert fake_connection.closed is True
    assert len(fake_connection.calls) == 1
    sql, args = fake_connection.calls[0]
    assert "UPDATE users" in sql
    assert args == (expected_admin, "wk-user", "user@example.com")
    assert synced.user["is_system_admin"] is expected_admin
    assert synced.user["can_access_all_tenants"] is expected_admin


@pytest.mark.asyncio
async def test_weknora_session_is_reused_for_repeated_proxy_calls(monkeypatch):
    calls = 0
    session = weknora_identity.WeKnoraSession(
        token="token",
        refresh_token="refresh",
        user={"id": "wk-user", "email": "user@example.com"},
        tenant={"id": 1, "name": "Workspace"},
        memberships=[{"tenant_id": 1, "role": "owner"}],
    )

    async def create_session(user):
        nonlocal calls
        calls += 1
        return session

    monkeypatch.setattr(weknora_identity, "_create_weknora_session", create_session)
    monkeypatch.setattr(weknora_identity, "_session_cache", {})
    user = type("DeerFlowUser", (), {"id": "df-user", "email": "user@example.com", "system_role": "user"})()

    first = await weknora_identity.ensure_weknora_session_for_deerflow_user(user)
    second = await weknora_identity.ensure_weknora_session_for_deerflow_user(user)

    assert first is session
    assert second is session
    assert calls == 1
