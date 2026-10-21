"""Enterprise GLM-5.3-Flash Chat Completions contract (2026-10 document)."""

import asyncio
from contextlib import aclosing
from copy import deepcopy

from pydantic import Field, field_validator

from deerflow.models.glm_stream_guard import install_glm_stream_guard
from deerflow.models.robust_openai import RobustChatOpenAI


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
        async with asyncio.timeout(self.request_deadline_seconds):
            async with aclosing(super()._astream(*args, **kwargs)) as stream:
                async for chunk in stream:
                    yield chunk

    async def _agenerate(self, *args, **kwargs):
        async with asyncio.timeout(self.request_deadline_seconds):
            return await super()._agenerate(*args, **kwargs)
