from __future__ import annotations

from deerflow.models.hecc_openai import HeccChatOpenAI


def test_hecc_chat_openai_applies_timeout_to_internal_http_clients():
    model = HeccChatOpenAI(
        model="deepseek-v3.2",
        api_key="test-key",
        base_url="https://hecc.example/v1",
        timeout=600,
        tls_verify=False,
    )

    assert model.http_client.timeout.read == 600
    assert model.http_async_client.timeout.read == 600
