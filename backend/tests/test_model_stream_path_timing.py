"""Content-free diagnostics for asynchronous model stream backpressure."""

from contextlib import aclosing

import pytest

from deerflow.models.robust_openai import _ModelStreamTiming, _timed_async_stream_chunks


def _timing() -> _ModelStreamTiming:
    return _ModelStreamTiming(
        enabled=True,
        model="DeepSeek-V4-Flash",
        endpoint="https://model.example/v1",
        langchain_run_id="langchain-run",
        mode="async",
        deerflow_thread_id="thread-1",
        deerflow_run_id="run-1",
    )


@pytest.mark.asyncio
async def test_provider_wait_excludes_time_spent_in_downstream_consumer() -> None:
    now = [0.0]

    async def source():
        now[0] += 0.1
        yield "first"
        now[0] += 2.0
        yield "second"

    timing = _timing()
    async with aclosing(_timed_async_stream_chunks(source(), timing, clock=lambda: now[0])) as stream:
        assert await anext(stream) == "first"
        now[0] += 30.0  # The consumer pauses before requesting another chunk.
        assert await anext(stream) == "second"

    assert timing.upstream_next_count == 2
    assert timing.upstream_next_total_s == pytest.approx(2.1)
    assert timing.upstream_next_max_s == pytest.approx(2.0)
    assert timing.upstream_next_over_1s == 1


@pytest.mark.asyncio
async def test_failed_provider_wait_is_retained_for_error_summary(caplog) -> None:
    now = [0.0]

    async def source():
        now[0] += 240.0
        raise TimeoutError("provider stopped producing chunks")
        yield  # pragma: no cover - keep this an async generator

    timing = _timing()
    with pytest.raises(TimeoutError):
        async with aclosing(_timed_async_stream_chunks(source(), timing, clock=lambda: now[0])) as stream:
            await anext(stream)

    timing.observe_callback(0.125)
    timing.observe_downstream_resume(0.250)
    caplog.set_level("INFO", logger="deerflow.models.robust_openai")
    timing.finish(outcome="error", error=TimeoutError())

    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert "LLM_STREAM_PATH event=end" in rendered
    assert f"llm_call_id={timing.llm_call_id}" in rendered
    assert "upstream_next_max_ms=240000" in rendered
    assert "upstream_error_wait_ms=240000" in rendered
    assert "callback_max_ms=125" in rendered
    assert "downstream_resume_max_ms=250" in rendered
    assert "provider stopped producing chunks" not in rendered


@pytest.mark.asyncio
async def test_timed_chunks_accept_iterator_without_aclose() -> None:
    class PlainAsyncIterator:
        def __aiter__(self):
            return self

        async def __anext__(self):
            if getattr(self, "sent", False):
                raise StopAsyncIteration
            self.sent = True
            return "chunk"

    timing = _timing()
    async with aclosing(_timed_async_stream_chunks(PlainAsyncIterator(), timing)) as stream:
        assert [chunk async for chunk in stream] == ["chunk"]

    assert timing.upstream_next_count == 1


@pytest.mark.asyncio
async def test_timed_chunks_close_source_when_consumer_stops_early() -> None:
    closed = []

    async def source():
        try:
            yield "first"
            yield "second"
        finally:
            closed.append(True)

    async with aclosing(_timed_async_stream_chunks(source(), _timing())) as stream:
        assert await anext(stream) == "first"

    assert closed == [True]


def test_disabled_timing_does_not_emit_path_metrics(caplog) -> None:
    timing = _timing()
    timing.enabled = False
    timing.observe_upstream_next(240.0, failed=True)
    timing.observe_callback(1.0)
    timing.observe_downstream_resume(1.0)
    timing.finish(outcome="error")

    assert timing.upstream_next_count == 0
    assert timing.callback_count == 0
    assert timing.downstream_resume_count == 0
    assert not caplog.records
