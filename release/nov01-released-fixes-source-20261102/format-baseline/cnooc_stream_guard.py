"""V4.1 response-local SSE completeness check; never retains response bodies.

Use the SDK decoder so split UTF-8, CRLF and SSE multiline rules have one owner.
An EOF or [DONE] without a finish_reason is an incomplete model request, not a
successful empty assistant turn. The existing provider middleware owns retries.
"""

import json
import logging
import uuid
from contextlib import aclosing, closing

from openai import AsyncStream, Stream

from deerflow.llm_timing_context import get_current_llm_timing_context
from deerflow.models.robust_openai import OpenAICompatibleResponseError

logger = logging.getLogger(__name__)


class CnoocStreamDecoder:
    def __init__(self, decoder, *, status_code=0):
        self.decoder = decoder
        self.status_code = status_code if isinstance(status_code, int) else 0
        self.call_id = uuid.uuid4().hex
        self.context = get_current_llm_timing_context()
        self.frames = 0
        self.visible_chars = 0
        self.reasoning_chars = 0
        self.tool_frames = 0
        self.terminal_seen = False
        self.done_seen = False
        self.usage_seen = False

    def require_complete(self, boundary):
        if self.terminal_seen:
            return
        logger.warning(
            "LLM_INCOMPLETE_STREAM thread_id=%s run_id=%s call_id=%s boundary=%s "
            "http_status=%s sse_frames=%s finish_present=%s done_seen=%s "
            "usage_present=%s visible_chars=%s reasoning_chars=%s tool_frames=%s",
            self.context.thread_id if self.context else "-",
            self.context.run_id if self.context else "-",
            self.call_id, boundary, self.status_code, self.frames, self.terminal_seen,
            self.done_seen, self.usage_seen, self.visible_chars, self.reasoning_chars, self.tool_frames,
        )
        raise OpenAICompatibleResponseError(
            code="INCOMPLETE_STREAM", success=False,
            reason="V4.1 stream ended without a terminal finish_reason",
            diagnostic=f"boundary={boundary},frames={self.frames},done={self.done_seen},finish=False",
        )

    def observe(self, event):
        data = event.data
        if data.startswith("[DONE]"):
            self.done_seen = data == "[DONE]"
            self.require_complete("done" if self.done_seen else "invalid_done")
            return
        self.frames += 1
        # Observe only fixed counters; leave parsing/business-error ownership
        # with the SDK and RobustChatOpenAI. Do not log unknown keys or values.
        try:
            value = data
            for _ in range(3):
                if not isinstance(value, str):
                    break
                value = json.loads(value.removeprefix("data:").lstrip())
        except (ValueError, TypeError):
            return
        if not isinstance(value, dict) or value.get("error"):
            return
        if isinstance(value.get("chunk"), dict) and not value.get("choices"):
            value = value["chunk"]
        self.usage_seen |= isinstance(value.get("usage"), dict) and bool(value["usage"])
        choices = value.get("choices")
        if not isinstance(choices, list):
            return
        for choice in choices:
            if not isinstance(choice, dict) or choice.get("index") != 0:
                continue
            finish = choice.get("finish_reason")
            self.terminal_seen |= isinstance(finish, str) and bool(finish.strip())
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue
            self.visible_chars += len(delta["content"]) if isinstance(delta.get("content"), str) else 0
            self.reasoning_chars += len(delta["reasoning_content"]) if isinstance(delta.get("reasoning_content"), str) else 0
            self.tool_frames += bool(delta.get("tool_calls") or delta.get("function_call"))

    def iter_bytes(self, iterator):
        with closing(self.decoder.iter_bytes(iterator)) as events:
            for event in events:
                self.observe(event)
                yield event
        self.require_complete("eof")

    async def aiter_bytes(self, iterator):
        async with aclosing(self.decoder.aiter_bytes(iterator)) as events:
            async for event in events:
                self.observe(event)
                yield event
        self.require_complete("eof")


def install_cnooc_stream_guard(response):
    stream = getattr(response, "_raw_stream", response)
    if not isinstance(stream, (Stream, AsyncStream)):
        raise RuntimeError("Unsupported V4.1 SDK stream shape")
    decoder = getattr(stream, "_decoder", None)
    if isinstance(decoder, CnoocStreamDecoder):
        return
    if not callable(getattr(decoder, "iter_bytes", None)) or not callable(getattr(decoder, "aiter_bytes", None)):
        raise RuntimeError("Unsupported V4.1 SDK decoder shape")
    stream._decoder = CnoocStreamDecoder(decoder, status_code=getattr(stream.response, "status_code", 0))
