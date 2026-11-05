"""GLM-only SSE termination check before the pinned SDK consumes [DONE].

The SDK 2.32 response-local decoder seam is deliberately narrow. Never patch a
shared client, transport, decoder class or another model's stream. Unknown SDK
shapes fail closed. SSE parsing remains owned by the SDK, including split bytes,
CRLF, multiline data, comments and UTF-8. We retain only terminal-state booleans.
"""

import json
from contextlib import aclosing, closing

from openai import AsyncStream, Stream

from deerflow.models.output_safety import ModelOutputSafetyError


class GLMStreamDecoder:
    def __init__(self, decoder):
        self.decoder = decoder
        self.terminal_seen = False
        self.done_seen = False

    def observe(self, event):
        data = event.data
        if data.startswith("[DONE]"):
            if data != "[DONE]" or not self.terminal_seen:
                raise ModelOutputSafetyError("incomplete_glm_stream")
            self.done_seen = True
            return
        # Let the SDK retain its normal handling of malformed JSON and errors.
        try:
            value = json.loads(data)
        except (ValueError, TypeError):
            return
        if not isinstance(value, dict) or value.get("error"):
            return
        choices = value.get("choices")
        if isinstance(choices, list):
            self.terminal_seen |= any(isinstance(c, dict) and c.get("index") == 0 and c.get("finish_reason") in ("stop", "tool_calls", "length", "content_filter") for c in choices)

    def require_complete(self):
        if not self.done_seen:
            raise ModelOutputSafetyError("incomplete_glm_stream")

    def iter_bytes(self, iterator):
        with closing(self.decoder.iter_bytes(iterator)) as events:
            for event in events:
                self.observe(event)
                yield event
        self.require_complete()

    async def aiter_bytes(self, iterator):
        async with aclosing(self.decoder.aiter_bytes(iterator)) as events:
            async for event in events:
                self.observe(event)
                yield event
        self.require_complete()


def install_glm_stream_guard(response):
    # Structured-output SDK streams wrap the same raw Chat Completions stream.
    stream = getattr(response, "_raw_stream", response)
    if not isinstance(stream, (Stream, AsyncStream)):
        raise ModelOutputSafetyError("incomplete_glm_stream")
    decoder = getattr(stream, "_decoder", None)
    if isinstance(decoder, GLMStreamDecoder):
        return
    if not callable(getattr(decoder, "iter_bytes", None)) or not callable(getattr(decoder, "aiter_bytes", None)):
        raise ModelOutputSafetyError("incomplete_glm_stream")
    stream._decoder = GLMStreamDecoder(decoder)
