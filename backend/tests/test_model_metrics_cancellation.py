"""Real LangChain lifecycle, synthetic provider: no model/Redis/SQL network."""

import asyncio

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import PrivateAttr

from deerflow.observability import model_metrics as telemetry


class SyntheticModel(BaseChatModel):
    wait: bool = False
    fail: bool = False
    _entered: asyncio.Event = PrivateAttr(default_factory=asyncio.Event)
    _release: asyncio.Event = PrivateAttr(default_factory=asyncio.Event)
    _exited: asyncio.Event = PrivateAttr(default_factory=asyncio.Event)

    @property
    def _llm_type(self):
        return "synthetic-lifecycle"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="synthetic"))])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        self._entered.set()
        try:
            if self.wait:
                await self._release.wait()
            if self.fail:
                raise ValueError("synthetic failure")
            return self._generate(messages)
        finally:
            self._exited.set()

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        self._entered.set()
        try:
            if self.wait:
                await self._release.wait()
            yield ChatGenerationChunk(message=AIMessageChunk(content="synthetic"))
        finally:
            self._exited.set()


@pytest.fixture
def metrics(monkeypatch):
    collector = telemetry.ModelMetrics(enabled=True)
    monkeypatch.setattr(telemetry, "metrics", collector)
    return collector


def model(**kwargs):
    return telemetry.instrument_model_class(SyntheticModel)(callbacks=[telemetry.ModelMetricsCallback("synthetic")], **kwargs)


def counts(metrics):
    snapshot = metrics.snapshot()
    rows = [row for bucket in snapshot["minutes"].values() for row in bucket.values()]
    return {field: sum(row[field] for row in rows) for field in ("started", "success", "error", "cancelled")}


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_cancelled_ainvoke_clears_actual_finished_provider(metrics, stream):
    llm = model(wait=True)
    task = asyncio.create_task(llm.ainvoke("synthetic", stream=stream))
    await asyncio.wait_for(llm._entered.wait(), 3)
    token = next(iter(metrics.active))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    assert llm._exited.is_set()
    assert not metrics.active
    assert counts(metrics) == {"started": 1, "success": 0, "error": 0, "cancelled": 1}
    # A late terminal callback must not double-count or change the outcome.
    metrics.finish(token)
    assert counts(metrics)["success"] == 0 and counts(metrics)["cancelled"] == 1


@pytest.mark.asyncio
async def test_cancelling_one_parallel_invocation_does_not_clear_another(metrics):
    first, second = model(wait=True), model(wait=True)
    one = asyncio.create_task(first.ainvoke("first"))
    two = asyncio.create_task(second.ainvoke("second"))
    await asyncio.wait_for(asyncio.gather(first._entered.wait(), second._entered.wait()), 3)
    assert len(metrics.active) == 2
    one.cancel()
    with pytest.raises(asyncio.CancelledError):
        await one
    await asyncio.sleep(0)
    assert len(metrics.active) == 1 and not two.done()
    second._release.set()
    assert (await two).content == "synthetic"
    assert not metrics.active
    assert counts(metrics) == {"started": 2, "success": 1, "error": 0, "cancelled": 1}


@pytest.mark.asyncio
async def test_wait_for_cancellation_is_not_reported_as_success(metrics):
    llm = model(wait=True)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(llm.ainvoke("synthetic"), 0.05)
    await asyncio.sleep(0)
    assert llm._exited.is_set() and not metrics.active
    # The observed model operation was cancelled by its caller; this collector
    # must not invent a provider timeout based on the caller's exception.
    assert counts(metrics)["cancelled"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_regular_callbacks_remove_watchers_and_preserve_results(metrics, fail):
    llm = model(fail=fail)
    if fail:
        with pytest.raises(ValueError, match="synthetic failure"):
            await llm.ainvoke("synthetic")
    else:
        assert (await llm.ainvoke("synthetic")).content == "synthetic"
    assert not metrics.active
    assert counts(metrics) == {"started": 1, "success": int(not fail), "error": int(fail), "cancelled": 0}


@pytest.mark.asyncio
async def test_stream_handoff_between_tasks_is_not_prematurely_finished(metrics):
    llm = model()
    stream = llm.astream("synthetic")
    assert (await asyncio.create_task(anext(stream))).content == "synthetic"
    await asyncio.sleep(0)
    assert len(metrics.active) == 1  # producer task returned one chunk normally
    async for _ in stream:
        pass
    assert not metrics.active
    assert counts(metrics)["success"] == 1 and counts(metrics)["cancelled"] == 0


@pytest.mark.asyncio
async def test_stream_cancellation_and_generator_close_are_counted_once(metrics):
    llm = model()
    stream = llm.astream("synthetic")
    await anext(stream)
    await stream.aclose()
    assert not metrics.active
    assert counts(metrics)["cancelled"] == 1


def test_sync_lifecycle_needs_no_event_loop(metrics):
    assert model().invoke("synthetic").content == "synthetic"
    assert not metrics.active
    assert counts(metrics)["success"] == 1


def test_instrumentation_keeps_provider_identity_and_is_idempotent():
    observed = telemetry.instrument_model_class(SyntheticModel)
    assert observed.lc_id() == SyntheticModel.lc_id()
    assert isinstance(observed(), SyntheticModel)
    assert observed().model_dump() == SyntheticModel().model_dump()
    assert telemetry.instrument_model_class(observed) is observed


@pytest.mark.asyncio
async def test_caught_timeout_then_next_call_in_same_task(metrics):
    first = model(wait=True)
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await first.ainvoke("synthetic", stream=True)
    assert not metrics.active
    assert (await model().ainvoke("next invocation")).content == "synthetic"
    assert counts(metrics) == {"started": 2, "success": 1, "error": 0, "cancelled": 1}


@pytest.mark.asyncio
async def test_batch_cancel_cleans_every_started_call(metrics):
    from langchain_core.messages import HumanMessage

    llm = model(wait=True)
    task = asyncio.create_task(llm.agenerate([[HumanMessage(content="first")], [HumanMessage(content="second")]]))
    await asyncio.wait_for(llm._entered.wait(), 3)
    assert len(metrics.active) == 2
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not metrics.active
    assert counts(metrics)["cancelled"] == 2


def test_real_long_call_is_retained_and_age_uses_monotonic_clock():
    clock = [0.0]
    metrics = telemetry.ModelMetrics(enabled=True, clock=lambda: 120, monotonic=lambda: clock[0])
    token = metrics.start("synthetic")
    clock[0] = 7200
    row = metrics.snapshot()["series"]["text:invoke:synthetic"]
    assert row["active"] == 1 and row["oldest_active_seconds"] == 7200
    metrics.finish(token)
    assert metrics.snapshot()["series"]["text:invoke:synthetic"]["oldest_active_seconds"] == 0
