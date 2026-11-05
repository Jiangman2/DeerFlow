"""Runs only in the bounded acceptance child; no provider calls or code injection."""


def check_stream_runtime(fixed, files, require):
    import hashlib
    import importlib.metadata
    import pathlib

    root = pathlib.Path("/app/backend/packages/harness/deerflow/models")
    base = {
        "enterprise_glm.py": "61d4ff275ad1ba5f811ebaa551c1c86d2292f1ae73a822870ad62b75adcc45d5",
        "robust_openai.py": "91793fafb98f430aae43856250558f2bb483219c9ca39c2443643e631057c51e",
        "output_safety.py": "1ca9f89f49dd7f497ea0c8e61613c5ce139d1864efa5ee2c8ea263e7f64ba48b",
    }
    expected = {p.rsplit("/", 1)[-1]: v["sha256"] for p, v in files.items()} if fixed else base
    for name, digest in expected.items():
        p = root / name
        require(not p.is_symlink() and p.stat().st_size <= 262144, "stream_source_path")
        raw = p.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == digest, "stream_source_mismatch")
    version = importlib.metadata.version("openai")
    require(version == "2.32.0", "stream_sdk_version")
    if fixed:
        import httpx
        from openai import OpenAI, Stream
        from openai.types.chat import ChatCompletionChunk

        from deerflow.models.enterprise_glm import EnterpriseGLMChat
        from deerflow.models.output_safety import ModelOutputSafetyError

        def no_network(request):
            raise AssertionError("network_forbidden")

        terminal = b'data: {"id":"offline","object":"chat.completion.chunk","created":1,"model":"GLM-5.3-Flash","choices":[{"index":0,"delta":{"content":"OK"},"finish_reason":"stop"}]}\n\n'
        with httpx.Client(transport=httpx.MockTransport(no_network), trust_env=False) as http:
            with OpenAI(api_key="offline-only", http_client=http, max_retries=0) as client:
                for complete in (True, False):
                    response = httpx.Response(200, content=terminal + (b"data: [DONE]\n\n" if complete else b""))
                    stream = Stream(cast_to=ChatCompletionChunk, response=response, client=client)
                    EnterpriseGLMChat._prepare_provider_stream(None, stream)
                    refused = False
                    try:
                        list(stream)
                    except ModelOutputSafetyError:
                        refused = True
                    finally:
                        stream.close()
                    require(refused is (not complete), "stream_completion_semantics")
    return dict(status="PASS", fixed=fixed, sdk_version=version, source_count=len(expected), offline_terminal_and_done_checked=fixed, provider_requests=0)
