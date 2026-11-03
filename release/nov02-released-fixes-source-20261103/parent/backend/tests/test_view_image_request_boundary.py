"""A real graph/checkpointer must not retain the model-only image payload."""

import base64
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from deerflow.agents.middlewares.view_image_middleware import ViewImageMiddleware


def state(tmp_path):
    image = tmp_path / "image.png"
    image.write_bytes(b"synthetic image bytes")
    return {
        "messages": [
            HumanMessage(content="inspect this image"),
            AIMessage(content="", tool_calls=[{"id": "view-1", "name": "view_image", "args": {"image_path": "/image.png"}}]),
            ToolMessage(content="Successfully read image", tool_call_id="view-1"),
        ],
        "viewed_images": {"/image.png": {"actual_path": str(image), "size": image.stat().st_size, "mime_type": "image/png"}},
    }


def request(current):
    return SimpleNamespace(state=current, messages=current["messages"], override=lambda **kw: kw)


def inline(messages):
    return [b["image_url"]["url"] for m in messages if isinstance(m.content, list) for b in m.content if isinstance(b, dict) and b.get("type") == "image_url"]


def test_receipt_does_not_read_image_and_only_model_copy_expands(tmp_path):
    mw = ViewImageMiddleware()
    current = state(tmp_path)
    with patch.object(mw, "_read_image_as_data_url", side_effect=AssertionError("must not read while writing state")):
        update = mw.before_model(current, None)
    current["messages"].extend(update["messages"])
    output = mw.wrap_model_call(request(current), lambda r: r)
    assert base64.b64decode(inline(output["messages"])[0].split(",")[1]) == b"synthetic image bytes"
    assert inline(current["messages"]) == []
    assert "actual_path" not in str(update)


@pytest.mark.anyio
async def test_async_wrapper_runs_same_request_boundary(tmp_path):
    mw = ViewImageMiddleware()
    current = state(tmp_path)
    current["messages"].extend((await mw.abefore_model(current, None))["messages"])

    async def handler(r):
        return r

    output = await mw.awrap_model_call(request(current), handler)
    assert len(inline(output["messages"])) == 1
    assert inline(current["messages"]) == []


def test_real_agent_checkpoint_has_no_base64_and_next_turn_does_not_reinject(tmp_path):
    seen = []

    class InspectModel(FakeMessagesListChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            seen.append(messages)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    model = InspectModel(responses=[AIMessage(content="image inspected"), AIMessage(content="next answer")])
    saver = InMemorySaver()
    graph = create_agent(model, tools=[], middleware=[ViewImageMiddleware()], checkpointer=saver)
    config = {"configurable": {"thread_id": "synthetic-image-boundary"}}
    result = graph.invoke(state(tmp_path), config)
    assert len(inline(seen[0])) == 1
    assert inline(result["messages"]) == []
    assert inline(graph.get_state(config).values["messages"]) == []
    assert all(b"data:image/" not in value[1] for value in saver.blobs.values())
    graph.invoke({"messages": [HumanMessage(content="thank you")]}, config)
    assert inline(seen[-1]) == []


def test_legacy_hidden_image_removed_only_from_request_copy(tmp_path):
    mw = ViewImageMiddleware()
    current = state(tmp_path)
    large = "data:image/png;base64," + "A" * 75_000_000
    legacy = HumanMessage(id="legacy", content=[{"type": "text", "text": "Here are the images you've viewed:"}, {"type": "image_url", "image_url": {"url": large}}], additional_kwargs={"hide_from_ui": True})
    current["messages"] = [legacy, AIMessage(content="previous answer"), HumanMessage(content="next question")]
    output = mw.wrap_model_call(request(current), lambda r: r)["messages"]
    assert inline(output) == []
    assert output[0].id == "legacy"
    assert current["messages"][0] is legacy and inline([legacy]) == [large]
    assert output[0].content[0] == legacy.content[0]
    assert output[1:] == current["messages"][1:]


def test_user_supplied_image_and_similar_text_remain_intact(tmp_path):
    current = state(tmp_path)
    content = [{"type": "text", "text": "Here are the images you've viewed:"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,USER"}}]
    visible = HumanMessage(content=content)
    different = HumanMessage(content=[{"type": "text", "text": "Other internal context"}, content[1]], additional_kwargs={"hide_from_ui": True})
    current["messages"] = [visible, different]
    result = ViewImageMiddleware()._model_messages(request(current))
    assert result == [visible, different]
    assert len(inline(result)) == 2


def test_old_viewed_images_not_resent_with_new_view_call(tmp_path):
    current = state(tmp_path)
    current["viewed_images"]["/old.png"] = dict(current["viewed_images"]["/image.png"])
    mw = ViewImageMiddleware()
    current["messages"].extend(mw.before_model(current, None)["messages"])
    output = mw._model_messages(request(current))
    assert len(inline(output)) == 1
    assert "/old.png" not in str(output[-1].content)


def test_total_image_budget_has_explicit_omission_and_no_extra_read(tmp_path, monkeypatch):
    import deerflow.agents.middlewares.view_image_middleware as module

    current = state(tmp_path)
    meta = current["viewed_images"]["/image.png"]
    current["viewed_images"]["/second.png"] = dict(meta)
    monkeypatch.setattr(module, "_MAX_REQUEST_IMAGE_BYTES", meta["size"])
    mw = ViewImageMiddleware()
    with patch.object(mw, "_read_image_as_data_url", wraps=mw._read_image_as_data_url) as read:
        blocks = mw._create_image_details_message(current)
    assert read.call_count == 1
    assert sum(b["type"] == "image_url" for b in blocks) == 1
    assert any("omitted" in b.get("text", "") for b in blocks)


@pytest.mark.parametrize("error_result", [ToolMessage(content="Error: Image file not found", tool_call_id="view-1"), ToolMessage(content="failed", tool_call_id="view-1", status="error")])
def test_failed_repeat_view_does_not_reuse_previous_metadata(tmp_path, error_result):
    current = state(tmp_path)
    current["messages"][-1] = error_result
    mw = ViewImageMiddleware()
    assert mw.before_model(current, None) is None
    with patch.object(mw, "_read_image_as_data_url", side_effect=AssertionError("failed view must not read old image")):
        assert inline(mw._model_messages(request(current))) == []


def test_new_user_turn_after_interrupted_view_does_not_reinject(tmp_path):
    current = state(tmp_path)
    current["messages"].append(HumanMessage(content="different request"))
    mw = ViewImageMiddleware()
    assert mw.before_model(current, None) is None
    assert inline(mw._model_messages(request(current))) == []
