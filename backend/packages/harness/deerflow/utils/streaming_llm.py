"""Private model turns: stream upstream, return only a complete merged response.

Never fall back to invoke/ainvoke. TAG_NOSTREAM hides graph token events, not
provider streaming; callbacks and tracing metadata remain intact.
"""

from contextlib import aclosing, closing
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessageChunk
from langchain_core.runnables import RunnableBinding
from langgraph.constants import TAG_NOSTREAM

from deerflow.utils.llm_text import extract_response_text


def _stream_model(model: Any, *, asynchronous: bool = False) -> Any:
    if isinstance(model, RunnableBinding):
        kwargs = dict(model.kwargs)
        if isinstance(kwargs.get("extra_body"), dict) and "stream" in kwargs["extra_body"]:
            kwargs["extra_body"] = {**kwargs["extra_body"], "stream": True}
        return model.model_copy(update={"bound": _stream_model(model.bound, asynchronous=asynchronous), "kwargs": kwargs})
    if isinstance(model, BaseChatModel):
        if type(model)._stream is BaseChatModel._stream and (not asynchronous or type(model)._astream is BaseChatModel._astream):
            raise TypeError("Background model must implement streaming")
        # These models may be shared across threads. Never toggle the original.
        updates = {"disable_streaming": False}
        if "streaming" in type(model).model_fields:
            updates["streaming"] = True
        if isinstance(getattr(model, "extra_body", None), dict) and "stream" in model.extra_body:
            updates["extra_body"] = {**model.extra_body, "stream": True}
        return model.model_copy(update=updates)
    return model  # Duck-typed providers must supply a real stream iterator.


def _private_config(config: dict | None) -> dict:
    result = dict(config or {})
    result["tags"] = list(dict.fromkeys([*(result.get("tags") or []), TAG_NOSTREAM]))
    return result


def _validate(response: AIMessageChunk | None, model: Any) -> AIMessageChunk:
    if response is None or not extract_response_text(response.content).strip():
        raise ValueError("Background model returned an empty stream")
    reason = response.response_metadata.get("finish_reason")
    status = response.response_metadata.get("status")
    while isinstance(model, RunnableBinding):
        model = model.bound
    # OpenAI-compatible streams must carry a normal terminal marker. A clean
    # socket EOF alone is not evidence that an apparently valid JSON is complete.
    if reason is not None and reason != "stop":
        raise ValueError("Background model stream did not finish normally")
    if status is not None and status != "completed":
        raise ValueError("Background model response is incomplete")
    if isinstance(model, BaseChatModel) and model._llm_type == "openai-chat" and reason != "stop" and status != "completed":
        raise ValueError("Background model stream is missing its terminal marker")
    return response


def stream_response(model: Any, prompt: Any, *, config: dict | None = None) -> AIMessageChunk:
    model = _stream_model(model)
    response = None
    with closing(model.stream(prompt, config=_private_config(config), stream=True)) as chunks:
        for chunk in chunks:
            if not isinstance(chunk, AIMessageChunk):
                raise TypeError("Background model must return AIMessageChunk")
            response = chunk if response is None else response + chunk
    return _validate(response, model)


async def astream_response(model: Any, prompt: Any, *, config: dict | None = None) -> AIMessageChunk:
    model = _stream_model(model, asynchronous=True)
    response = None
    async with aclosing(model.astream(prompt, config=_private_config(config), stream=True)) as chunks:
        async for chunk in chunks:
            if not isinstance(chunk, AIMessageChunk):
                raise TypeError("Background model must return AIMessageChunk")
            response = chunk if response is None else response + chunk
    return _validate(response, model)
