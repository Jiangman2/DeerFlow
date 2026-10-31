import json
import tracemalloc
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal
from deerflow.runtime.lifecycle_preview import lifecycle_preview


def test_small_plain_state_preserved_as_independent_copy():
    state = {"messages": [{"type": "ai", "content": "完成"}], "count": 1}
    preview, truncated = lifecycle_preview(state)
    assert preview == state
    assert not truncated
    preview["messages"][0]["content"] = "changed"
    assert state["messages"][0]["content"] == "完成"


def test_seventy_five_mb_state_never_dumped_before_bounding():
    image = "data:image/png;base64," + "A" * 75_000_000
    state = {"messages": [HumanMessage(content=[{"type": "image_url", "image_url": {"url": image}}])]}
    tracemalloc.start()
    try:
        preview, truncated = lifecycle_preview(state)
        serialized = json.dumps(preview, ensure_ascii=False).encode()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert truncated
    assert len(serialized) < 256_000
    assert peak < 2_000_000
    assert state["messages"][0].content[0]["image_url"]["url"] is image


def test_global_limits_cycles_and_unsafe_repr():
    class Opaque:
        def __str__(self):
            raise AssertionError("must not stringify state")

        def __repr__(self):
            raise AssertionError("must not repr state")

    cycle = []
    cycle.append(cycle)
    for state in ({"opaque": Opaque()}, cycle, [{"x" * 1000: "中\x00" * 5000}] * 10000, {"large_int": 10**10000}):
        preview, truncated = lifecycle_preview(state)
        assert truncated
        assert len(json.dumps(preview, ensure_ascii=True).encode()) < 300_000


@pytest.mark.anyio
async def test_only_run_end_snapshot_is_bounded_error_message_retained():
    store = MemoryRunEventStore()
    journal = RunJournal("run", "thread", store)
    answer = AIMessage(content="模型服务返回错误", additional_kwargs={"deerflow_error_fallback": True})
    state = {"messages": [HumanMessage(content="A" * 1_000_000), answer]}
    journal.on_chain_end(state, run_id=uuid4())
    await journal.flush()
    events = await store.list_events("thread", "run")
    terminal = next(e for e in events if e["event_type"] == "run.end")
    assert terminal["metadata"]["content_truncated"]
    assert len(json.dumps(terminal, ensure_ascii=False)) < 50_000
    message = next(e for e in events if e["event_type"] == "llm.ai.response")
    assert message["content"] == answer.model_dump()
    assert state["messages"][1] is answer
    assert len(state["messages"][0].content) == 1_000_000
