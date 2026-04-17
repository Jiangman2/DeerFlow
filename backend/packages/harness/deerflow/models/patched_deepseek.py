"""Patched ChatDeepSeek that preserves reasoning_content in multi-turn conversations.

This module provides a patched version of ChatDeepSeek that properly handles
reasoning_content when sending messages back to the API. The original implementation
stores reasoning_content in additional_kwargs but doesn't include it when making
subsequent API calls, which causes errors with APIs that require reasoning_content
on all assistant messages when thinking mode is enabled.
"""

from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage
from langchain_deepseek import ChatDeepSeek


class PatchedChatDeepSeek(ChatDeepSeek):
    """ChatDeepSeek with proper reasoning_content preservation.

    When using thinking/reasoning enabled models, the API expects reasoning_content
    to be present on ALL assistant messages in multi-turn conversations. This patched
    version ensures reasoning_content from additional_kwargs is included in the
    request payload.
    """

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """Get request payload with reasoning_content preserved.

        Overrides the parent method to inject reasoning_content from
        additional_kwargs into assistant messages in the payload.
        """
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if not isinstance(payload, dict):
            return payload

        payload_messages = payload.get("messages")
        if not isinstance(payload_messages, list):
            return payload

        try:
            original_messages = self._convert_input(input_).to_messages()
            if len(payload_messages) == len(original_messages):
                for payload_msg, orig_msg in zip(payload_messages, original_messages):
                    if payload_msg.get("role") == "assistant" and isinstance(orig_msg, AIMessage):
                        reasoning_content = orig_msg.additional_kwargs.get("reasoning_content")
                        if reasoning_content is not None:
                            payload_msg["reasoning_content"] = reasoning_content
            else:
                ai_messages = [m for m in original_messages if isinstance(m, AIMessage)]
                assistant_payloads = [(i, m) for i, m in enumerate(payload_messages) if m.get("role") == "assistant"]

                for (idx, _payload_msg), ai_msg in zip(assistant_payloads, ai_messages):
                    reasoning_content = ai_msg.additional_kwargs.get("reasoning_content")
                    if reasoning_content is not None:
                        payload_messages[idx]["reasoning_content"] = reasoning_content
        except Exception:
            return payload

        return payload
