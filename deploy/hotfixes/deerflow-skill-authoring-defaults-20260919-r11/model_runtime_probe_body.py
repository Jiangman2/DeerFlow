from __future__ import annotations

import json
import os
import sys

import httpx
from openai import OpenAI


RUNTIME = json.load(sys.stdin)
required = {
    "api_key",
    "base_url",
    "file_sha256",
    "model",
    "stream",
    "supports_thinking_toggle",
    "temperature",
}
if set(RUNTIME) != required:
    raise RuntimeError("unexpected model runtime fields")
ca_bundle = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE") or True
client = OpenAI(
    api_key=RUNTIME["api_key"],
    base_url=RUNTIME["base_url"],
    timeout=90.0,
    max_retries=0,
    http_client=httpx.Client(trust_env=False, follow_redirects=False, verify=ca_bundle, timeout=90.0),
)
for enabled in (False, True):
    chunks = 0
    finish_reasons = []
    stream = client.chat.completions.create(
        model=RUNTIME["model"],
        messages=[{"role": "user", "content": "仅回复：OK"}],
        max_tokens=64,
        temperature=RUNTIME["temperature"],
        stream=True,
        extra_body={"enable_thinking": enabled},
    )
    for chunk in stream:
        chunks += 1
        for choice in chunk.choices:
            if choice.finish_reason:
                finish_reasons.append(choice.finish_reason)
    if chunks < 1:
        raise RuntimeError("stream returned no chunks")
    print(
        "deerflow_primary_model_stream_probe=PASS "
        f"thinking={str(enabled).lower()} chunks={chunks} "
        f"finish={','.join(finish_reasons) or 'none'}"
    )
