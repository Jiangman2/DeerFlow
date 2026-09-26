"""Official #5112 runtime/callback detachment, including enterprise wait state."""

import asyncio
import weakref
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from deerflow.runtime.context_keys import CURRENT_RUN_PRE_EXISTING_MESSAGE_IDS_KEY
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal
from deerflow.runtime.runs import RunContext, RunManager, worker


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [None, "hook", "end"])
async def test_detaches_only_worker_owned_references_even_on_interrupted_finalization(failure):
    manager = RunManager()
    record = await manager.create("detach-terminal")
    stream = SimpleNamespace(publish=AsyncMock(), publish_end=AsyncMock(), cleanup=AsyncMock())
    caller_callback = object()
    config = {"callbacks": [caller_callback], "context": {"agent_name": "企业员工", "enterprise_field": {"keep": True}}, "configurable": {"custom": "keep"}}
    captured = {}

    class Agent:
        async def astream(self, *args, config=None, **kwargs):
            captured["config"] = config
            captured["runtime"] = config["configurable"]["__pregel_runtime"]
            journal = next(callback for callback in config["callbacks"] if isinstance(callback, RunJournal))
            captured["journal"] = weakref.ref(journal)
            yield {"messages": []}

    hook = AsyncMock()
    if failure == "hook":
        hook.side_effect = asyncio.CancelledError("hook cancel")
    if failure == "end":
        stream.publish_end.side_effect = RuntimeError("END unavailable")
    try:
        await worker.run_agent(stream, manager, record, ctx=RunContext(checkpointer=None, event_store=MemoryRunEventStore(), on_run_completed=hook), agent_factory=lambda **kw: Agent(), graph_input={}, config=config)
    except (RuntimeError, asyncio.CancelledError):
        assert failure is not None
    await asyncio.sleep(0)

    for runnable_config in (config, captured["config"]):
        assert "__pregel_runtime" not in runnable_config["configurable"]
        assert runnable_config["callbacks"] == [caller_callback]
        assert runnable_config["configurable"]["custom"] == "keep"
        assert runnable_config["context"]["agent_name"] == "企业员工"
        assert runnable_config["context"]["enterprise_field"] == {"keep": True}
        assert CURRENT_RUN_PRE_EXISTING_MESSAGE_IDS_KEY not in runnable_config["context"]
    runtime_context = captured["runtime"].context
    assert "__run_journal" not in runtime_context
    assert "__clarification_event" not in runtime_context
    assert CURRENT_RUN_PRE_EXISTING_MESSAGE_IDS_KEY not in runtime_context
    assert captured["journal"]() is None


@pytest.mark.anyio
async def test_goal_continuation_configs_are_all_detached(monkeypatch):
    manager = RunManager()
    record = await manager.create("detach-goal")
    stream = SimpleNamespace(publish=AsyncMock(), publish_end=AsyncMock(), cleanup=AsyncMock())
    configs = []

    class Agent:
        async def astream(self, *args, config=None, **kwargs):
            assert "__pregel_runtime" in config["configurable"]
            assert any(isinstance(callback, RunJournal) for callback in config["callbacks"])
            configs.append(config)
            yield {"messages": []}

    monkeypatch.setattr(worker, "_prepare_goal_continuation_input", AsyncMock(side_effect=[{"messages": []}, None]))
    await worker.run_agent(stream, manager, record, ctx=RunContext(checkpointer=None, event_store=MemoryRunEventStore()), agent_factory=lambda **kw: Agent(), graph_input={}, config={})
    assert len(configs) == 2
    for config in configs:
        assert "__pregel_runtime" not in config["configurable"]
        assert "__run_journal" not in config["context"]
        assert config["callbacks"] == []


def test_callback_manager_removes_only_this_run_journal():
    journal = object()
    other = object()

    class Manager:
        def __init__(self):
            self.handlers = [other, journal]

        def remove_handler(self, handler):
            self.handlers.remove(handler)

    callbacks = Manager()
    config = {"callbacks": callbacks}
    worker._release_run_scoped_references([config, config], {}, journal)
    assert callbacks.handlers == [other]


def test_uncooperative_callback_manager_does_not_skip_other_configs():
    class Manager:
        def remove_handler(self, handler):
            raise RuntimeError("synthetic caller handler")

    journal = object()
    later = {"callbacks": [journal], "configurable": {"__pregel_runtime": object()}, "context": {"__run_journal": journal}}
    runtime_context = {"__run_journal": journal, "__clarification_event": object(), "keep": "enterprise"}
    worker._release_run_scoped_references([{"callbacks": Manager()}, later], runtime_context, journal)
    assert later["callbacks"] == []
    assert later["context"] == {}
    assert later["configurable"] == {}
    assert runtime_context == {"keep": "enterprise"}
