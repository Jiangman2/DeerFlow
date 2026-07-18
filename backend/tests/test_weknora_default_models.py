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

    async def connect():
        return fake_connection

    monkeypatch.setattr(weknora_identity, "_connect_weknora_database", connect)

    await weknora_identity.ensure_weknora_default_models()

    assert fake_connection.closed is True
    assert len(fake_connection.calls) == 2
    chat_args = fake_connection.calls[0][1]
    embedding_args = fake_connection.calls[1][1]
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
    assert embedding_args[0] == "deerflow-dashscope-embedding"
    assert embedding_args[3] == "Embedding"
    assert json.loads(embedding_args[6])["embedding_parameters"] == {
        "dimension": 1024,
        "truncate_prompt_tokens": 0,
    }


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
