"""Current-batch image injection, including durable graph replay."""

import asyncio
import base64

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, messages_from_dict, messages_to_dict
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from deerflow.agents.middlewares.view_image_middleware import ViewImageMiddleware
from deerflow.agents.thread_state import ThreadState


def image(tmp_path, name, data=None):
    data = data or b"\x89PNG\r\n\x1a\n" + name.encode()
    actual = tmp_path / name
    actual.write_bytes(data)
    return {"mime_type": "image/png", "actual_path": str(actual), "size": len(data)}


def call(key, path):
    return {"name": "view_image", "id": key, "args": {"image_path": path}}


def batch(*calls, results=None):
    return [AIMessage(content="", tool_calls=list(calls)), *(results if results is not None else [ToolMessage(content="Successfully read image", tool_call_id=c["id"]) for c in calls])]


def urls(update):
    if update is None:
        return []
    return [b["image_url"]["url"] for m in update["messages"] for b in m.content if isinstance(b, dict) and b.get("type") == "image_url"]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_sequential_views_add_one_image_each(tmp_path, monkeypatch, asynchronous):
    mw = ViewImageMiddleware()
    state = {"messages": [], "viewed_images": {}}
    read_paths = []
    read = mw._read_image_as_data_url
    monkeypatch.setattr(mw, "_read_image_as_data_url", lambda p, m, s: (read_paths.append(p), read(p, m, s))[1])
    updates = []
    for i in range(6):
        path = f"/mnt/user-data/workspace/{i}.png"
        state["viewed_images"][path] = image(tmp_path, f"{i}.png")
        state["messages"].extend(batch(call(f"c{i}", path)))
        update = asyncio.run(mw.abefore_model(state, None)) if asynchronous else mw.before_model(state, None)
        assert len(urls(update)) == 1
        assert len(read_paths) == i + 1
        assert read_paths[-1] == state["viewed_images"][path]["actual_path"]
        updates.append(update)
        state["messages"].extend(update["messages"])
    assert sum(len(urls(u)) for u in updates) == 6  # Previously 1+2+3+4+5+6 = 21.


def test_mixed_parallel_batch_waits_and_deduplicates_paths(tmp_path):
    mw = ViewImageMiddleware()
    calls = [call("one", "/a.png"), call("same", "/a.png"), call("two", "/b.png"), {"name": "bash", "id": "bash", "args": {"command": "true"}}]
    state = {"messages": batch(*calls), "viewed_images": {"/a.png": image(tmp_path, "a.png"), "/b.png": image(tmp_path, "b.png"), "/old.png": image(tmp_path, "old.png")}}
    state["messages"].pop()
    assert mw.before_model(state, None) is None
    state["messages"].append(ToolMessage(content="ok", tool_call_id="bash"))
    assert len(urls(mw.before_model(state, None))) == 2


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("content,status", [("Error: Image file not found", "success"), ("Successfully read image", "error"), ("unexpected receipt", "success")])
def test_failed_or_unrecognized_receipt_cannot_reuse_old_metadata(tmp_path, content, status, asynchronous):
    mw = ViewImageMiddleware()
    state = {"messages": batch(call("new", "/old.png"), results=[ToolMessage(content=content, tool_call_id="new", status=status)]), "viewed_images": {"/old.png": image(tmp_path, "old.png")}}
    result = asyncio.run(mw.abefore_model(state, None)) if asynchronous else mw.before_model(state, None)
    assert result is None


def test_successful_member_of_failed_batch_is_still_visible(tmp_path):
    state = {
        "messages": batch(call("a", "/a.png"), call("b", "/b.png"), results=[ToolMessage(content="Successfully read image", tool_call_id="a"), ToolMessage(content="Error: failed", tool_call_id="b")]),
        "viewed_images": {"/a.png": image(tmp_path, "a.png"), "/b.png": image(tmp_path, "b.png")},
    }
    assert len(urls(ViewImageMiddleware().before_model(state, None))) == 1


def test_restored_messages_do_not_reinject_completed_batch(tmp_path):
    mw = ViewImageMiddleware()
    state = {"messages": batch(call("a", "/a.png")), "viewed_images": {"/a.png": image(tmp_path, "a.png")}}
    result = mw.before_model(state, None)
    state["messages"].extend(result["messages"])
    state["messages"] = messages_from_dict(messages_to_dict(state["messages"]))
    assert mw.before_model(state, None) is None
    assert asyncio.run(mw.abefore_model(state, None)) is None


def test_explicit_review_reads_updated_file_without_changing_history(tmp_path):
    mw = ViewImageMiddleware()
    state = {"messages": batch(call("old", "/a.png")), "viewed_images": {"/a.png": image(tmp_path, "a.png")}}
    previous = mw.before_model(state, None)
    state["messages"].extend(previous["messages"])
    state["messages"].extend(batch(call("new", "/a.png")))
    state["viewed_images"]["/a.png"] = image(tmp_path, "a.png", b"\x89PNG\r\n\x1a\nupdated")
    current = mw.before_model(state, None)
    assert len(urls(current)) == 1
    assert urls(current) != urls(previous)
    assert base64.b64decode(urls(current)[0].split(",")[1]).endswith(b"updated")


def test_path_without_validated_metadata_does_not_read_disk(tmp_path, monkeypatch):
    mw = ViewImageMiddleware()
    monkeypatch.setattr(mw, "_read_image_as_data_url", lambda *args: pytest.fail("unvalidated read"))
    state = {"messages": batch(call("x", "/etc/private.png")), "viewed_images": {"/old.png": image(tmp_path, "old.png")}}
    assert mw.before_model(state, None) is None


def test_stale_receipt_before_current_ai_does_not_satisfy_new_call(tmp_path):
    state = {"messages": [ToolMessage(content="Successfully read image", tool_call_id="same"), AIMessage(content="", tool_calls=[call("same", "/a.png")])], "viewed_images": {"/a.png": image(tmp_path, "a.png")}}
    assert ViewImageMiddleware().before_model(state, None) is None


def test_missing_file_does_not_fall_back_to_history(tmp_path):
    meta = image(tmp_path, "gone.png")
    (tmp_path / "gone.png").unlink()
    state = {"messages": batch(call("a", "/gone.png")), "viewed_images": {"/gone.png": meta, "/old.png": image(tmp_path, "old.png")}}
    update = ViewImageMiddleware().before_model(state, None)
    assert urls(update) == []
    assert "unavailable" in str(update["messages"][0].content)
    assert "old.png" not in str(update)


def test_real_graph_checkpoint_continuation_keeps_images_linear(tmp_path):
    mw = ViewImageMiddleware()
    graph = StateGraph(ThreadState)
    graph.add_node("inject", lambda state: mw.before_model(state, None) or {})
    graph.add_edge(START, "inject")
    graph.add_edge("inject", END)
    app = graph.compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "batch-images"}}
    first = app.invoke({"messages": batch(call("a", "/a.png")), "viewed_images": {"/a.png": image(tmp_path, "a.png")}}, cfg)
    assert len(urls({"messages": [m for m in first["messages"] if isinstance(m, HumanMessage)]})) == 1
    second = app.invoke({"messages": batch(call("b", "/b.png")), "viewed_images": {"/b.png": image(tmp_path, "b.png")}}, cfg)
    images = [m for m in second["messages"] if isinstance(m, HumanMessage)]
    assert len(urls({"messages": images})) == 2
    app.invoke({}, cfg)
    replay = app.get_state(cfg).values
    assert len(replay["messages"]) == len(second["messages"])
