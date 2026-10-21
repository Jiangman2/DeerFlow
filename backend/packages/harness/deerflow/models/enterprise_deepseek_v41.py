"""Documented FCV-41 Chat Completions payload; scoped to the new V4.1 profile."""

from copy import deepcopy

from pydantic import field_validator

from deerflow.models.robust_openai import RobustChatOpenAI


class EnterpriseDeepSeekV41Chat(RobustChatOpenAI):
    use_responses_api: bool = False

    @field_validator("use_responses_api", mode="before")
    @classmethod
    def require_chat_completions(cls, value):
        if value is not False:
            raise ValueError("Enterprise DeepSeek V4.1 requires Chat Completions")
        return value

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = deepcopy(super()._get_request_payload(input_, stop=stop, **kwargs))
        if payload.get("model") != "DeepSeek-V4.1-Flash":
            raise ValueError("Enterprise DeepSeek V4.1 requires its dedicated profile")
        # LangChain normalizes max_tokens to max_completion_tokens. FCV-41's
        # supplied contract documents only max_tokens. Normalize after the
        # parent serializer, for both streaming and non-streaming requests.
        body = payload.get("extra_body") or {}
        limits = [mapping[name] for mapping in (payload, body) for name in ("max_tokens", "max_completion_tokens") if mapping.get(name) is not None]
        if limits:
            if any(type(value) is not int or value <= 0 for value in limits) or len(set(limits)) != 1:
                raise ValueError("DeepSeek V4.1 token limits must be positive and consistent")
            payload["max_tokens"] = limits[0]
        payload.pop("max_completion_tokens", None)
        if body:
            body.pop("max_tokens", None)
            body.pop("max_completion_tokens", None)
            payload["extra_body"] = body
        return payload
