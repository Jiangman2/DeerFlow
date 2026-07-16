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
