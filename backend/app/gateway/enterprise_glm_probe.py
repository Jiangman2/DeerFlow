"""Opt-in enterprise GLM acceptance: at most four synthetic provider requests.

Uses the candidate adapter, no database/workspace/business tool access. Never
prints response text, reasoning, credentials, request headers or raw exceptions.
Not run during imports/startup/tests; tests supply an offline HTTP transport.
"""

import argparse
import asyncio
import base64
import json
import logging
import os
import ssl
import struct
import time
import zlib
from contextlib import aclosing

import httpx
from langchain_core.messages import HumanMessage, ToolMessage, message_chunk_to_message

from deerflow.models.enterprise_glm import EnterpriseGLMChat

BASE_URL = "https://aiapi.cnooc.com.cn/GLM-5.3-Flash/FCGLM-001/v1"
MARKER = "GLM_PROBE_OK"
PROBE_MAX_TOKENS = 1000
TOOL = {
    "type": "function",
    "function": {
        "name": "probe_echo",
        "description": "Acceptance fixture only. Echo the provided value; no external actions.",
        "parameters": {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False},
    },
}


def red_image():
    def block(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = b"\x89PNG\r\n\x1a\n" + block(b"IHDR", struct.pack("!2I5B", 32, 32, 8, 2, 0, 0, 0))
    raw += block(b"IDAT", zlib.compress((b"\x00" + b"\xff\x00\x00" * 32) * 32)) + block(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


class ProbeRejected(RuntimeError):
    pass


class ProbeOutputLimit(ProbeRejected):
    pass


def error_category(exc):
    """Export a fixed category, never provider messages/headers/body/URLs."""
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return "AUTHENTICATION_OR_AUTHORIZATION"
    if status == 429:
        return "RATE_OR_QUOTA_LIMIT_NOT_DISTINGUISHED"
    if type(status) is int and 500 <= status <= 599:
        return "UPSTREAM_SERVER_ERROR"
    if isinstance(exc, ProbeOutputLimit):
        return "OUTPUT_LIMIT_REACHED_NOT_CAPABILITY_VERDICT"
    if isinstance(exc, ProbeRejected):
        return "EXPECTED_OUTPUT_OR_TOOL_CONTRACT_NOT_MET"
    seen = set()
    current = exc
    for _ in range(8):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        text = str(current).lower()[:8192]
        for label, tokens in (
            ("TLS_WEAK_KEY_OR_SIGNATURE", ("key too weak", "key_too_weak", "digest too weak")),
            ("TLS_CERTIFICATE_VERIFICATION", ("certificate verify", "certificate_verify")),
            ("DNS", ("name or service", "name resolution")),
        ):
            if any(token in text for token in tokens):
                return label
        if isinstance(current, (TimeoutError, httpx.TimeoutException)):
            return "TIMEOUT"
        current = current.__cause__ or current.__context__
    return "OTHER_UNCONFIRMED"


async def run_probe(key, http, *, request_deadline=120):
    receipt = {
        "glm_canary": "RUNNING",
        "stage": "setup",
        "provider_requests_attempted": 0,
        "business_tools_executed": 0,
        "automatic_retry": False,
        "raw_output_printed": False,
        "credentials_printed": False,
        "configuration_modified": False,
        "usage": "NOT_REPORTED",
        "requested_max_tokens": PROBE_MAX_TOKENS,
        "request_deadline_seconds": request_deadline,
        "stage_results": {stage: "NOT_ATTEMPTED" for stage in ("text", "tool_call", "tool_resume", "image")},
        "checks": [],
    }
    reported_usage = []
    try:
        model = EnterpriseGLMChat(
            model="GLM-5.3-Flash",
            api_key=key,
            base_url=BASE_URL,
            http_async_client=http,
            use_responses_api=False,
            streaming=True,
            stream_usage=True,
            max_tokens=PROBE_MAX_TOKENS,
            max_retries=0,
            timeout=30,
            stream_chunk_timeout=30,
            request_deadline_seconds=request_deadline,
        )

        async def invoke(stage, messages, **kwargs):
            receipt["stage"] = stage
            receipt["stage_results"][stage] = "RUNNING"
            receipt["provider_requests_attempted"] += 1
            if receipt["provider_requests_attempted"] > 4:
                raise ProbeRejected()
            start = time.monotonic()
            merged = None
            size = 0
            frames = 0
            async with aclosing(model.astream(messages, **kwargs)) as stream:
                async for part in stream:
                    frames += 1
                    size += len(json.dumps(part.model_dump(), default=str))
                    if size > 512000 or frames > 4096:
                        raise ProbeRejected()
                    merged = part if merged is None else merged + part
            if merged is None:
                raise ProbeRejected()
            message = message_chunk_to_message(merged)
            usage = message.usage_metadata or {}
            clean_usage = {k: v for k, v in usage.items() if k in ("input_tokens", "output_tokens", "total_tokens") and type(v) is int and v >= 0}
            if clean_usage:
                reported_usage.append({"stage": stage, **clean_usage})
            check = {"stage": stage, "transport": "PASS", "elapsed_seconds": round(time.monotonic() - start, 2)}
            check["visible_content_present"] = bool(message.content)
            check["reasoning_present"] = bool(message.additional_kwargs.get("reasoning_content"))
            finish = message.response_metadata.get("finish_reason")
            if finish in ("stop", "length", "tool_calls", "content_filter"):
                check["finish_reason"] = finish
            receipt["checks"].append(check)
            if finish == "length":
                raise ProbeOutputLimit()
            if finish != ("tool_calls" if stage == "tool_call" else "stop"):
                raise ProbeRejected()
            return message

        first = await invoke("text", [HumanMessage("请只回复 GLM_PROBE_OK，不调用工具。")])
        if not isinstance(first.content, str) or first.content.strip() != MARKER or first.tool_calls:
            raise ProbeRejected()
        receipt["stage_results"]["text"] = "PASS"
        prompt = [HumanMessage("接口验收：必须调用 probe_echo，value 严格等于 GLM_PROBE_OK。拿到工具结果后只输出该值。")]
        call = await invoke("tool_call", prompt, tools=[TOOL], tool_choice="auto")
        if len(call.tool_calls) != 1 or call.invalid_tool_calls:
            raise ProbeRejected()
        tool = call.tool_calls[0]
        if tool["name"] != "probe_echo" or tool["args"] != {"value": MARKER} or not tool.get("id"):
            raise ProbeRejected()
        receipt["stage_results"]["tool_call"] = "PASS"
        final = await invoke("tool_resume", prompt + [call, ToolMessage(MARKER, tool_call_id=tool["id"])], tools=[TOOL], tool_choice="auto")
        if not isinstance(final.content, str) or final.content.strip() != MARKER or final.tool_calls:
            raise ProbeRejected()
        receipt["stage_results"]["tool_resume"] = "PASS"
        vision = await invoke("image", [HumanMessage([{"type": "text", "text": "图片的纯色背景是什么颜色？只回答颜色。"}, {"type": "image_url", "image_url": {"url": red_image()}}])])
        if not isinstance(vision.content, str) or not ("红" in vision.content or "red" in vision.content.lower()) or vision.tool_calls:
            raise ProbeRejected()
        receipt["stage_results"]["image"] = "PASS"
        receipt["glm_canary"] = "PASS"
        receipt["stage"] = "complete"
    except Exception as exc:
        receipt.update(glm_canary="FAIL", error_type=type(exc).__name__, error_class=error_category(exc))
        if receipt["stage"] in receipt["stage_results"]:
            receipt["stage_results"][receipt["stage"]] = "INCOMPLETE" if isinstance(exc, ProbeOutputLimit) else "FAIL"
        status = getattr(exc, "status_code", None)
        if type(status) is int and 100 <= status <= 599:
            receipt["http_status"] = status
    if reported_usage:
        receipt["usage"] = "PROVIDER_REPORTED"
        receipt["reported_usage"] = reported_usage
    return receipt


async def _live(key, deadline):
    # Internal API only: no outbound proxy inheritance or credential-bearing
    # redirect. An approved CA bundle is optional; verification stays enabled.
    context = ssl.create_default_context(cafile=os.environ.get("GLM_5_3_FLASH_CA_BUNDLE") or None)
    async with httpx.AsyncClient(verify=context, trust_env=False, follow_redirects=False, timeout=30) as http:
        return await run_probe(key, http, request_deadline=deadline)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.add_argument("--request-deadline", type=int, default=120)
    args = parser.parse_args(argv)
    # Standalone diagnostic only, suppress third-party debug log configuration.
    previous_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        key = os.environ.get("GLM_5_3_FLASH_API_KEY", "")
        if not key or key.strip() != key or not 10 <= args.request_deadline <= 300:
            raise ProbeRejected()
        receipt = asyncio.run(_live(key, args.request_deadline))
    except Exception as exc:
        receipt = {"glm_canary": "FAIL", "stage": "configuration", "error_type": type(exc).__name__, "provider_requests_attempted": 0, "credentials_printed": False, "raw_output_printed": False, "automatic_retry": False}
    finally:
        logging.disable(previous_disable)
    print(json.dumps(receipt, ensure_ascii=False))
    return 0 if receipt["glm_canary"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
