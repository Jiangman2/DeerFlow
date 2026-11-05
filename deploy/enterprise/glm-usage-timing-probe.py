"""One synthetic request through the installed adapter; export counters only."""

import asyncio
import hashlib
import json
import pathlib
import time
from urllib.parse import urlsplit


class UsageObserver:
    def __init__(self):
        self.started = time.monotonic()
        self.rows = []
        self.counts = {"upstream": 0, "converted": 0, "delivered": 0}
        self.usage_counts = dict.fromkeys(self.counts, 0)
        self.first_usage = {}
        self.last_usage = {}
        self.first_text = None
        self.first_terminal = None
        self.nonzero_before_terminal = False

    def elapsed(self):
        return round(time.monotonic() - self.started, 3)

    def record(self, layer, usage):
        self.counts[layer] += 1
        if self.counts[layer] > 20000:
            raise ValueError("FRAME_LIMIT")
        if not isinstance(usage, dict):
            return
        keys = (
            ("prompt_tokens", "completion_tokens", "total_tokens")
            if layer == "upstream"
            else ("input_tokens", "output_tokens", "total_tokens")
        )
        safe = {
            k: usage[k]
            for k in keys
            if type(usage.get(k)) is int and 0 <= usage[k] <= 1000000000
        }
        if not safe:
            return
        self.usage_counts[layer] += 1
        row = {
            "layer": layer,
            "frame": self.counts[layer],
            "seconds": self.elapsed(),
            "usage": safe,
        }
        if any(safe.values()):
            self.first_usage.setdefault(layer, row)
            if layer == "upstream" and self.first_terminal is None:
                self.nonzero_before_terminal = True
        self.last_usage[layer] = row
        if len(self.rows) < 18:
            self.rows.append(row)

    def upstream(self, chunk):
        # The SDK-normalized event, not an independent raw-wire trace.
        choices = chunk.get("choices") or []
        if self.first_terminal is None and any(c.get("finish_reason") for c in choices):
            self.first_terminal = self.elapsed()
        if self.first_text is None and any(
            (c.get("delta") or {}).get("content") for c in choices
        ):
            self.first_text = self.elapsed()
        self.record("upstream", chunk.get("usage"))

    def summary(self):
        return {
            "frame_counts": self.counts,
            "usage_frame_counts": self.usage_counts,
            "first_nonzero_usage": self.first_usage,
            "last_usage": self.last_usage,
            "first_visible_text_seconds": self.first_text,
            "first_terminal_seconds": self.first_terminal,
            "upstream_nonzero_usage_before_terminal": self.nonzero_before_terminal,
            "usage_samples": self.rows,
            "elapsed_seconds": self.elapsed(),
        }


def config_inventory(config):
    result = []
    for entry in config.models:
        fields = entry.model_dump(exclude_none=True)
        model = str(fields.get("model", ""))
        if "glm" in model.lower() or "deepseek" in model.lower():
            result.append(
                {
                    "family": "glm" if "glm" in model.lower() else "deepseek",
                    "stream_usage_configured": fields.get("stream_usage")
                    if type(fields.get("stream_usage")) is bool
                    else None,
                    "cumulative_stream_usage_configured": fields.get(
                        "cumulative_stream_usage"
                    )
                    if type(fields.get("cumulative_stream_usage")) is bool
                    else None,
                }
            )
    return result


async def run_probe(run):
    from deerflow.config import get_app_config
    from deerflow.models.factory import create_chat_model

    config = get_app_config()
    entries = [
        m for m in config.models if m.model_dump().get("model") == "GLM-5.3-Flash"
    ]
    assert len(entries) == 1, "GLM_PROFILE_NOT_UNIQUE"
    entry = entries[0]
    fields = entry.model_dump(exclude_none=True)
    assert fields.get("use") == "deerflow.models.enterprise_glm:EnterpriseGLMChat", (
        "ADAPTER_MISMATCH"
    )
    endpoint = (
        fields.get("base_url")
        or fields.get("openai_api_base")
        or fields.get("api_base")
    )
    assert (
        endpoint
        and str(endpoint).rstrip("/")
        == "https://aiapi.cnooc.com.cn/GLM-5.3-Flash/FCGLM-001/v1"
    ), "ENDPOINT_MISMATCH"
    root = pathlib.Path("/app/backend/packages/harness/deerflow/models")
    sources = {
        name: hashlib.sha256((root / (name + ".py")).read_bytes()).hexdigest()
        for name in ("factory", "robust_openai", "enterprise_glm")
    }
    result = {
        "status": "PREFLIGHT_PASS",
        "configured_usage": config_inventory(config),
        "runtime_sources": sources,
        "provider_requests_attempted": 0,
        "configuration_modified": False,
        "workload_modified": False,
        "credentials_exported": False,
        "content_exported": False,
        "production_code_replaced": False,
    }
    if not run:
        return result
    observer = UsageObserver()
    llm = None
    try:
        # Preserve configured usage mode and routing. Bound only this diagnostic instance.
        llm = create_chat_model(
            entry.name,
            app_config=config,
            attach_tracing=False,
            model_overrides={
                "max_retries": 0,
                "max_tokens": 512,
                "timeout": 60,
                "request_deadline_seconds": 120,
                "callbacks": [],
                "cache": False,
            },
        )
        result["effective_usage"] = {
            "stream_usage": llm.stream_usage,
            "cumulative_stream_usage": llm.cumulative_stream_usage,
        }
        normalize = llm._normalize_stream_chunk
        convert = llm._convert_chunk_to_generation_chunk

        def normalized(chunk):
            value = normalize(chunk)
            observer.upstream(value)
            return value

        def converted(*args, **kwargs):
            value = convert(*args, **kwargs)
            if value is not None:
                observer.record("converted", value.message.usage_metadata)
            return value

        object.__setattr__(llm, "_normalize_stream_chunk", normalized)
        object.__setattr__(llm, "_convert_chunk_to_generation_chunk", converted)
        client = llm.root_async_client._client
        client.follow_redirects = False

        async def request_guard(request):
            url = urlsplit(str(request.url))
            assert (
                request.method == "POST"
                and url.scheme == "https"
                and url.hostname == "aiapi.cnooc.com.cn"
                and url.port in (None, 443)
            )
            assert (
                url.path == "/GLM-5.3-Flash/FCGLM-001/v1/chat/completions"
                and not url.query
            )
            payload = json.loads(request.content)
            limit = payload.get("max_completion_tokens", payload.get("max_tokens"))
            assert (
                type(limit) is int
                and 0 < limit <= 512
                and payload.get("stream") is True
            )
            assert payload.get("model") == "GLM-5.3-Flash" and not payload.get("tools")
            assert result["provider_requests_attempted"] == 0, "REQUEST_LIMIT"
            result["provider_requests_attempted"] += 1

        client.event_hooks["request"] = [request_guard]
        # Existing response hooks are retained; never export request or response bodies.
        import httpx

        class Bounded(httpx.AsyncByteStream):
            def __init__(self, inner):
                self.inner = inner

            async def __aiter__(self):
                size = 0
                async for value in self.inner:
                    size += len(value)
                    if size > 2097152:
                        raise ValueError("RESPONSE_LIMIT")
                    yield value

            async def aclose(self):
                await self.inner.aclose()

        async def bounded(response):
            result["http_status"] = response.status_code
            response.stream = Bounded(response.stream)

        client.event_hooks["response"].append(bounded)
        async with asyncio.timeout(120):
            stream = llm.astream(
                "Explain in three short sentences why plants need sunlight. This is a synthetic transport test. Do not use tools."
            )
            try:
                async for chunk in stream:
                    observer.record("delivered", chunk.usage_metadata)
            finally:
                await stream.aclose()
        result["status"] = "GLM_USAGE_TIMING_OBSERVED"
    except Exception as exc:
        result.update(status="INCOMPLETE", error_class=type(exc).__name__)
    finally:
        result["timing"] = observer.summary()
        result["provider_outcome_may_be_uncertain"] = (
            result["provider_requests_attempted"] > 0
            and result["status"] == "INCOMPLETE"
        )
        if llm is not None:
            await llm.root_async_client.close()
            llm.root_client.close()
    return result
