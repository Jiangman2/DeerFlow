import json
import re

import pytest

from app.gateway import weknora_identity


def test_weknora_v08_password_is_policy_compatible_and_legacy_is_preserved(monkeypatch):
    monkeypatch.setattr(weknora_identity, "_sso_secret", lambda: "test-sso-secret")

    current = weknora_identity._password("user-id", "user@example.com")
    legacy = weknora_identity._legacy_password("user-id", "user@example.com")

    assert len(current) == 32
    assert re.search(r"[A-Z]", current)
    assert re.search(r"[a-z]", current)
    assert re.search(r"[0-9]", current)
    assert re.search(r"[!@#$%^&*()_+\-=\[\]{}|;:,.<>?]", current)
    assert len(legacy) == 46
    assert weknora_identity._password_candidates("user-id", "user@example.com") == (current, legacy)


@pytest.mark.asyncio
async def test_weknora_session_falls_back_to_legacy_password(monkeypatch):
    monkeypatch.setattr(weknora_identity, "_sso_secret", lambda: "test-sso-secret")
    monkeypatch.setattr(weknora_identity, "weknora_timeout", lambda: 10.0)
    monkeypatch.setattr(weknora_identity, "weknora_api_url", lambda: "http://weknora")

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

        async def post(self, url, json=None, **kwargs):
            calls.append((url, json))
            if url.endswith("/auth/register"):
                return _Response(400, {"message": "user with this email already exists"})
            current = weknora_identity._password("user-id", "user@example.com")
            if json["password"] == current:
                return _Response(401, {"success": False, "message": "Invalid email or password"})
            assert json["password"] == weknora_identity._legacy_password("user-id", "user@example.com")
            return _Response(
                200,
                {
                    "success": True,
                    "token": "token",
                    "refresh_token": "refresh",
                    "user": {"id": "wk-user", "email": "user@example.com"},
                    "active_tenant": {"id": 1, "name": "Workspace"},
                    "memberships": [{"tenant_id": 1, "role": "owner"}],
                },
            )

    async def _identity_session(_user, session):
        return session

    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(weknora_identity.httpx, "AsyncClient", lambda **kwargs: _Client())
    monkeypatch.setattr(weknora_identity, "sync_weknora_role", _identity_session)
    monkeypatch.setattr(weknora_identity, "ensure_weknora_default_models", _noop)
    monkeypatch.setattr(weknora_identity, "ensure_weknora_default_mineru", _noop)
    monkeypatch.setattr(weknora_identity, "ensure_weknora_default_web_search", _noop)

    user = type("DeerFlowUser", (), {"id": "user-id", "email": "user@example.com", "system_role": "user"})()
    session = await weknora_identity._create_weknora_session(user)

    assert session.token == "token"
    assert calls[0] == (
        "http://weknora/api/v1/auth/register",
        {
            "username": weknora_identity._username("user@example.com", "user-id"),
            "email": "user@example.com",
            "password": weknora_identity._password("user-id", "user@example.com"),
        },
    )
    login_passwords = [payload["password"] for url, payload in calls if url.endswith("/auth/login")]
    assert login_passwords == [
        weknora_identity._password("user-id", "user@example.com"),
        weknora_identity._legacy_password("user-id", "user@example.com"),
    ]


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
    monkeypatch.delenv("WEKNORA_ENTERPRISE_INTERNAL_MODELS_ONLY", raising=False)


@pytest.mark.asyncio
async def test_ensure_weknora_default_models_seeds_platform_builtins(monkeypatch):
    fake_connection = _FakeConnection()
    monkeypatch.setenv("WEKNORA_DB_PASSWORD", "secret")
    monkeypatch.setenv("LLM_API_KEY", "llm-secret")
    monkeypatch.setenv("EMBEDDING_API_KEY", "embedding-secret")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://gateway:8001/api/weknora/bailian/v1")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "generic")
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
    assert chat_args[7] is False
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
    assert internal_deepseek_args[7] is True
    assert embedding_args[0] == "deerflow-dashscope-embedding"
    assert embedding_args[3] == "Embedding"
    embedding_parameters = json.loads(embedding_args[6])
    assert embedding_parameters["base_url"] == "http://gateway:8001/api/weknora/bailian/v1"
    assert embedding_parameters["provider"] == "generic"
    assert embedding_parameters["embedding_parameters"] == {
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
    assert next(model for model in models if model.id == "deerflow-qwen-plus").is_default is True


def test_weknora_default_models_can_opt_out_of_internal_deepseek_preference(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "llm-secret")
    monkeypatch.setenv("EMBEDDING_API_KEY", "embedding-secret")
    monkeypatch.setenv("DeepSeek_V4_API_KEY", "internal-deepseek-secret")
    monkeypatch.setenv("WEKNORA_PREFER_CNOOC_DEEPSEEK", "false")

    models = weknora_identity._weknora_default_models()
    defaults = {model.id: model.is_default for model in models if model.type == "KnowledgeQA"}

    assert defaults == {
        "deerflow-qwen-plus": True,
        "deerflow-deepseek-v4-pro": False,
        "deerflow-cnooc-deepseek-v4-flash": False,
    }


def test_weknora_enterprise_internal_only_models_keep_bailian_vlm_and_asr(monkeypatch):
    monkeypatch.setenv("WEKNORA_ENTERPRISE_INTERNAL_MODELS_ONLY", "true")
    monkeypatch.setenv("LLM_API_KEY", "bailian-media-secret")
    monkeypatch.setenv("DeepSeek_V4_API_KEY", "internal-deepseek-secret")
    monkeypatch.setenv("WEKNORA_INTERNAL_MODEL_RELAY_KEY", "relay-secret")

    models = weknora_identity._weknora_default_models()

    assert [model.id for model in models] == [
        "deerflow-cnooc-deepseek-v4-flash",
        "deerflow-cnooc-qwen3-embedding-8b",
        "deerflow-cnooc-qwen3-reranker-8b",
        "deerflow-dashscope-vlm",
        "deerflow-dashscope-asr",
    ]
    assert models[1].parameters == {
        "base_url": "http://gateway:8001/api/weknora/internal-models/v1",
        "api_key": "relay-secret",
        "provider": "generic",
        "embedding_parameters": {
            "dimension": 1024,
            "truncate_prompt_tokens": 0,
        },
    }
    assert models[2].parameters == {
        "base_url": "http://gateway:8001/api/weknora/internal-models/v1",
        "api_key": "relay-secret",
        "provider": "generic",
    }
    assert all(model.is_default for model in models)


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
async def test_ensure_weknora_default_web_search_creates_tavily_for_owned_workspace(monkeypatch):
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
            return _Response(200, {"success": True, "data": []})

        async def post(self, url, **kwargs):
            calls.append(("POST", url, kwargs))
            return _Response(200, {"success": True, "data": {"id": "provider-id"}})

    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")
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

    await weknora_identity.ensure_weknora_default_web_search(session)

    assert [call[0] for call in calls] == ["GET", "POST"]
    assert calls[0][2]["headers"]["X-Tenant-ID"] == "100"
    assert calls[1][2]["json"] == {
        "name": "DeerFlow Tavily",
        "provider": "tavily",
        "description": "DeerFlow enterprise default web search",
        "parameters": {"api_key": "tavily-secret"},
        "is_default": True,
    }


@pytest.mark.asyncio
async def test_ensure_weknora_default_web_search_rotates_existing_key(monkeypatch):
    post_called = False
    put_calls = []

    class _Response:
        status_code = 200

        def json(self):
            return {
                "success": True,
                "data": [
                    {
                        "id": "existing-tavily",
                        "provider": "tavily",
                        "is_default": True,
                    }
                ],
            }

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, **kwargs):
            return _Response()

        async def post(self, url, **kwargs):
            nonlocal post_called
            post_called = True
            return _Response()

        async def put(self, url, **kwargs):
            put_calls.append((url, kwargs))
            return _Response()

    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")
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

    await weknora_identity.ensure_weknora_default_web_search(session)

    assert post_called is False
    assert put_calls == [
        (
            "http://weknora.test/api/v1/web-search-providers/existing-tavily",
            {
                "headers": {
                    "Authorization": "Bearer token",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-Tenant-ID": "100",
                },
                "json": {
                    "name": "DeerFlow Tavily",
                    "provider": "tavily",
                    "description": "DeerFlow enterprise default web search",
                    "parameters": {"api_key": "tavily-secret"},
                    "is_default": True,
                },
            },
        )
    ]


@pytest.mark.asyncio
async def test_ensure_weknora_default_web_search_skips_without_tavily_key(monkeypatch):
    client_created = False

    class _Client:
        def __init__(self):
            nonlocal client_created
            client_created = True

    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setattr(weknora_identity.httpx, "AsyncClient", lambda **kwargs: _Client())
    session = weknora_identity.WeKnoraSession(
        token="token",
        refresh_token="refresh",
        user={"id": "user"},
        tenant={"id": 100},
        memberships=[{"tenant_id": 100, "role": "owner"}],
    )

    await weknora_identity.ensure_weknora_default_web_search(session)

    assert client_created is False


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


@pytest.mark.parametrize(
    ("deerflow_role", "expected_mode", "full_ui", "can_create_workspace"),
    [
        ("admin", "system_admin", True, True),
        ("user", "personal_workspace_owner", False, False),
    ],
)
def test_weknora_ui_policy_follows_deerflow_system_role(
    deerflow_role,
    expected_mode,
    full_ui,
    can_create_workspace,
):
    policy = weknora_identity.build_weknora_ui_policy(type("DeerFlowUser", (), {"system_role": deerflow_role})())

    assert policy["mode"] == expected_mode
    assert policy["full_ui"] is full_ui
    assert policy["can_create_workspace"] is can_create_workspace
    assert policy["default_path"] == "/platform/knowledge-bases"
    if full_ui:
        assert policy["allowed_navigation"] == ["*"]
    else:
        assert policy["allowed_navigation"] == [
            "/platform/creatChat",
            "/platform/knowledge-bases",
            "/platform/organizations",
            "/platform/agents",
        ]


def test_weknora_login_response_can_carry_ui_policy():
    session = weknora_identity.WeKnoraSession(
        token="token",
        refresh_token="refresh",
        user={"id": "wk-user"},
        tenant={"id": 1},
        memberships=[{"tenant_id": 1, "role": "owner"}],
    )
    policy = weknora_identity.build_weknora_ui_policy(type("DeerFlowUser", (), {"system_role": "user"})())

    response = session.as_login_response(ui_policy=policy)

    assert response["ui_policy"] == policy
    assert response["token"] == "token"


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
