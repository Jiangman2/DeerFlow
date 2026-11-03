from __future__ import annotations

import os

import httpx
from openai import OpenAI


api_key = os.environ.get("DeepSeek_V4_API_KEY", "").strip()
if len(api_key) < 32:
    raise RuntimeError("DeepSeek_V4_API_KEY is missing")
base_url = "https://aiapi.cnooc.com.cn/DeepSeek-V4-Flash/FEAT-511-001/v1"
ca_bundle = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE") or True
client = OpenAI(
    api_key=api_key,
    base_url=base_url,
    timeout=90.0,
    max_retries=0,
    http_client=httpx.Client(trust_env=False, follow_redirects=False, verify=ca_bundle, timeout=90.0),
)
for enabled in (False, True):
    chunks = 0
    stream = client.chat.completions.create(
        model="DeepSeek-V4-Flash",
        messages=[{"role": "user", "content": "仅回复：OK"}],
        max_tokens=64,
        temperature=0.0,
        stream=True,
        extra_body={"enable_thinking": enabled},
    )
    for _chunk in stream:
        chunks += 1
    if chunks < 1:
        raise RuntimeError("stream returned no chunks")
    print(f"deerflow_primary_model_live_probe=PASS thinking={str(enabled).lower()} chunks={chunks}")
