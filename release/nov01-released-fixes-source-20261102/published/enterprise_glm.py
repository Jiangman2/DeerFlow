"""Enterprise GLM-5.3-Flash Chat Completions contract (2026-10 document)."""

import asyncio
import logging
import time
from contextlib import aclosing
from copy import deepcopy

from pydantic import Field, field_validator

from deerflow.llm_timing_context import get_current_llm_timing_context
from deerflow.models.glm_stream_guard import install_glm_stream_guard
from deerflow.models.output_safety import ModelOutputSafetyError, visible_text
from deerflow.models.robust_openai import RobustChatOpenAI

logger = logging.getLogger(__name__)


class GLMRequestDeadlineError(ModelOutputSafetyError):
    """The operator's total call deadline expired; never automatically replay it."""

    def __init__(self, *, seconds, elapsed_ms, mode, chunks, visible_chunks, reasoning_chunks, tool_chunks):
        super().__init__("model_request_deadline")
        # Fixed names and numeric counters only; safe for durable error summaries.
        self.args = (
            "model_request_deadline: provider=GLM-5.3-Flash "
            f"deadline_seconds={seconds:g} elapsed_ms={elapsed_ms} mode={mode} "
            f"chunks={chunks} visible_chunks={visible_chunks} "
            f"reasoning_chunks={reasoning_chunks} tool_chunks={tool_chunks}",
        )


class EnterpriseGLMChat(RobustChatOpenAI):
    use_responses_api: bool = False
    request_deadline_seconds: float = Field(default=600, gt=0, le=1800)

    def _prepare_provider_stream(self, response):
        install_glm_stream_guard(response)

    @field_validator("use_responses_api", mode="before")
    @classmethod
    def require_chat_completions(cls, value):
        if value is not False:
            raise ValueError("Enterprise GLM supports Chat Completions only")
        return value

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = deepcopy(super()._get_request_payload(input_, stop=stop, **kwargs))
        if payload.get("model") != "GLM-5.3-Flash":
            raise ValueError("Enterprise GLM profile requires GLM-5.3-Flash")
        body = payload.setdefault("extra_body", {})
        body["thinking"] = {"type": "enabled"}
        effort = body.get("reasoning_effort", payload.get("reasoning_effort"))
        if effort not in (None, "high", "max"):
            raise ValueError("GLM reasoning_effort must be omitted, high or max")
        image_count = 0
        for message in payload.get("messages", []):
            content = message.get("content")
            if not isinstance(content, list):
                continue
            images = [block for block in content if isinstance(block, dict) and block.get("type") == "image_url"]
            image_count += len(images)
            for image in images:
                image_url = image.get("image_url")
                url = image_url.get("url", "") if isinstance(image_url, dict) else ""
                if not isinstance(url, str) or not url.startswith("data:image/") or ";base64," not in url:
                    raise ValueError("GLM image input requires an uploaded base64 image")
            message["content"] = images + [block for block in content if block not in images]
        if image_count > 1:
            raise ValueError("GLM-5.3-Flash supports a single image per request; use a new conversation for another image")
        return payload

    async def _astream(self, *args, **kwargs):
        # Empty/reasoning keepalives must not renew the total request budget.
        started = time.monotonic()
        counts = dict(chunks=0, visible_chunks=0, reasoning_chunks=0, tool_chunks=0)
        deadline = asyncio.timeout(self.request_deadline_seconds)
        try:
            async with deadline:
                async with aclosing(super()._astream(*args, **kwargs)) as stream:
                    async for chunk in stream:
                        counts["chunks"] += 1
                        message = chunk.message
                        counts["visible_chunks"] += bool(visible_text(message.content))
                        counts["reasoning_chunks"] += bool(message.additional_kwargs.get("reasoning_content"))
                        counts["tool_chunks"] += bool(getattr(message, "tool_call_chunks", None))
                        yield chunk
        except TimeoutError:
            if not deadline.expired():
                raise  # Preserve a different, inner transport timeout.
            raise self._deadline_failure(started, "stream", counts) from None

    async def _agenerate(self, *args, **kwargs):
        started = time.monotonic()
        deadline = asyncio.timeout(self.request_deadline_seconds)
        try:
            async with deadline:
                return await super()._agenerate(*args, **kwargs)
        except TimeoutError:
            if not deadline.expired():
                raise
            raise self._deadline_failure(started, "nonstream", dict(chunks=0, visible_chunks=0, reasoning_chunks=0, tool_chunks=0)) from None

    def _deadline_failure(self, started, mode, counts):
        error = GLMRequestDeadlineError(seconds=self.request_deadline_seconds, elapsed_ms=round((time.monotonic() - started) * 1000), mode=mode, **counts)
        context = get_current_llm_timing_context()
        logger.warning("LLM_DEADLINE thread=%s run=%s %s", context.thread_id if context else "-", context.run_id if context else "-", error)
        return error
