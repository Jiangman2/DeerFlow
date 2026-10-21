"""Explicit synthetic acceptance through the installed model factory; no business tools.

The caller supplies the reviewed profile and an HTTP client. Never run on import,
retry requests, or export response text, reasoning, credentials or raw exceptions.
"""

import asyncio
import base64
import copy
import json
import struct
import time
import zlib
from contextlib import aclosing

from langchain_core.messages import HumanMessage, ToolMessage, message_chunk_to_message

from deerflow.config.app_config import AppConfig
from deerflow.config.model_config import ModelConfig
from deerflow.config.sandbox_config import SandboxConfig
from deerflow.models.factory import create_chat_model

MARKER = "V41_PROBE_OK"
BASE_URL = "https://aiapi.cnooc.com.cn/DeepSeek-V4.1-Flash/FCV-41/v1"
TOOL = {
    "type": "function",
    "function": {
        "name": "probe_echo",
        "description": "Synthetic acceptance only; echo value, no external actions.",
        "parameters": {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False},
    },
}


class ProbeRejected(RuntimeError):
    pass


def color_image(rgb):
    def block(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = b"\x89PNG\r\n\x1a\n" + block(b"IHDR", struct.pack("!2I5B", 32, 32, 8, 2, 0, 0, 0))
    raw += block(b"IDAT", zlib.compress((b"\x00" + bytes(rgb) * 32) * 32)) + block(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


async def run_probe(key, http, profile, *, request_deadline=180):
    stages = ("flash", "thinking", "tool_call", "tool_resume", "multi_image")
    receipt = dict(
        status="INCOMPLETE",
        stage="setup",
        stage_results=dict.fromkeys(stages, "NOT_ATTEMPTED"),
        provider_requests_attempted=0,
        maximum_provider_calls=5,
        automatic_retry=False,
        business_tools_executed=0,
        credentials_exported=False,
        raw_output_exported=False,
        configuration_modified=False,
        checks=[],
    )
    try:
        row = copy.deepcopy(profile)
        if row.get("base_url") != BASE_URL or row.get("model") != "DeepSeek-V4.1-Flash" or not key:
            raise ProbeRejected("profile_identity")
        row.update(api_key=key, max_tokens=1000, max_retries=0)
        config = AppConfig(models=[ModelConfig(**row)], sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"))

        async def invoke(stage, messages, **kwargs):
            receipt["stage"] = stage
            receipt["stage_results"][stage] = "RUNNING"
            receipt["provider_requests_attempted"] += 1
            if receipt["provider_requests_attempted"] > 5:
                raise ProbeRejected("call_limit")
            model = create_chat_model(row["name"], thinking_enabled=stage in ("thinking", "multi_image"), app_config=config, attach_tracing=False, http_async_client=http)
            start = time.monotonic()
            merged, size, frames = None, 0, 0
            async with asyncio.timeout(request_deadline):
                async with aclosing(model.astream(messages, config={"callbacks": []}, **kwargs)) as stream:
                    async for chunk in stream:
                        frames += 1
                        size += len(json.dumps(chunk.model_dump(), default=str))
                        if size > 512000 or frames > 4096:
                            raise ProbeRejected("output_budget")
                        merged = chunk if merged is None else merged + chunk
            if merged is None:
                raise ProbeRejected("empty_stream")
            message = message_chunk_to_message(merged)
            finish = message.response_metadata.get("finish_reason")
            reasoning = bool(message.additional_kwargs.get("reasoning_content"))
            check = dict(stage=stage, elapsed_seconds=round(time.monotonic() - start, 2), reasoning_present=reasoning, finish_reason=finish if finish in ("stop", "length", "tool_calls", "content_filter") else "OTHER")
            check["usage"] = {k: v for k, v in (message.usage_metadata or {}).items() if k in ("input_tokens", "output_tokens", "total_tokens") and type(v) is int and v >= 0}
            receipt["checks"].append(check)
            if finish != ("tool_calls" if stage == "tool_call" else "stop") or message.invalid_tool_calls:
                raise ProbeRejected("finish_or_tool_contract")
            if stage == "flash" and reasoning:
                raise ProbeRejected("flash_did_not_disable_thinking")
            if stage == "thinking" and not reasoning:
                raise ProbeRejected("thinking_not_observed")
            return message

        for stage in ("flash", "thinking"):
            value = await invoke(stage, [HumanMessage("Reply exactly V41_PROBE_OK and nothing else.")])
            if value.content.strip() != MARKER or value.tool_calls:
                raise ProbeRejected("text_marker")
            receipt["stage_results"][stage] = "PASS"
        prompt = [HumanMessage("Call probe_echo with value V41_PROBE_OK. After receiving the tool result reply with that exact value, without calling again.")]
        call = await invoke("tool_call", prompt, tools=[TOOL], tool_choice="auto")
        if len(call.tool_calls) != 1:
            raise ProbeRejected("one_tool_required")
        tool = call.tool_calls[0]
        if tool["name"] != "probe_echo" or tool["args"] != {"value": MARKER} or not tool.get("id"):
            raise ProbeRejected("tool_arguments")
        receipt["stage_results"]["tool_call"] = "PASS"
        value = await invoke("tool_resume", prompt + [call, ToolMessage(MARKER, tool_call_id=tool["id"])], tools=[TOOL], tool_choice="auto")
        if value.content.strip() != MARKER or value.tool_calls:
            raise ProbeRejected("tool_resume")
        receipt["stage_results"]["tool_resume"] = "PASS"
        images = [{"type": "image_url", "image_url": {"url": color_image(rgb), "detail": "low"}} for rgb in ((255, 0, 0), (0, 0, 255))]
        value = await invoke("multi_image", [HumanMessage(images + [{"type": "text", "text": "Name the solid color in each image, in order. Reply with just two English color words separated by a space."}])])
        if value.content.strip().upper() != "RED BLUE" or value.tool_calls:
            raise ProbeRejected("image_colors")
        receipt["stage_results"]["multi_image"] = "PASS"
        receipt.update(status="PROVIDER_ACCEPTED_NOT_DEPLOYED", stage="complete")
    except Exception as exc:
        receipt["error_class"] = type(exc).__name__
        receipt["error_code"] = str(exc) if isinstance(exc, ProbeRejected) else "PROVIDER_OR_TRANSPORT_ERROR"
        status = getattr(exc, "status_code", None)
        if type(status) is int and 100 <= status <= 599:
            receipt["http_status"] = status
        if receipt["stage"] in stages:
            receipt["stage_results"][receipt["stage"]] = "FAIL"
    return receipt
