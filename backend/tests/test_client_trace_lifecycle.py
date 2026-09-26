"""Embedded generator steps and cleanup own one bounded, canonical trace."""

import asyncio
import threading
from contextvars import Context, ContextVar
from types import SimpleNamespace

import pytest

from deerflow.client import DeerFlowClient, StreamEvent, _run_async_from_sync
from deerflow.trace_context import get_current_trace_id, request_trace_context


def make_client(monkeypatch, observed, *, enhance, fail=None):
    client = DeerFlowClient.__new__(DeerFlowClient)
    client._app_config = SimpleNamespace(logging=SimpleNamespace(enhance=SimpleNamespace(enabled=enhance)))

    def turn(message, **kwargs):
        try:
            observed.append((message, "step", get_current_trace_id()))
            yield StreamEvent(type="values")
            observed.append((message, "step", get_current_trace_id()))
            if fail == "step":
                raise RuntimeError("step failed")
            yield StreamEvent(type="end")
        finally:
            observed.append((message, "close", get_current_trace_id()))
            if fail == "close":
                raise RuntimeError("cleanup failed")

    monkeypatch.setattr(client, "_stream_without_trace_context", turn)
    return client


@pytest.mark.parametrize("enhance", [False, True])
def test_interleaved_turns_keep_distinct_ids_without_polluting_caller(monkeypatch, enhance):
    observed = []
    client = make_client(monkeypatch, observed, enhance=enhance)
    first, second = client.stream("first"), client.stream("second")
    try:
        next(first)
        assert get_current_trace_id() is None
        next(second)
        assert get_current_trace_id() is None
        with request_trace_context("unrelated-caller"):
            next(first)
            assert get_current_trace_id() == "unrelated-caller"
            next(second)
            assert get_current_trace_id() == "unrelated-caller"
    finally:
        first.close()
        second.close()
    ids = {name: {trace for item, _, trace in observed if item == name} for name in ("first", "second")}
    assert len(ids["first"]) == len(ids["second"]) == 1
    assert None not in ids["first"] | ids["second"]
    assert ids["first"].isdisjoint(ids["second"])
    assert get_current_trace_id() is None


@pytest.mark.parametrize("enhance", [False, True])
@pytest.mark.parametrize("fail", [None, "close"])
def test_abandoned_stream_cleanup_keeps_turn_id_in_another_context(monkeypatch, enhance, fail):
    observed = []
    client = make_client(monkeypatch, observed, enhance=enhance, fail=fail)
    stream = client.stream("turn")
    with request_trace_context("turn-owner"):
        next(stream)
        assert get_current_trace_id() == "turn-owner"

    def close_elsewhere():
        with request_trace_context("cleanup-caller"):
            if fail:
                with pytest.raises(RuntimeError, match="cleanup failed"):
                    stream.close()
            else:
                stream.close()
            assert get_current_trace_id() == "cleanup-caller"

    Context().run(close_elsewhere)
    assert observed == [("turn", "step", "turn-owner"), ("turn", "close", "turn-owner")]
    assert get_current_trace_id() is None


@pytest.mark.parametrize("enhance", [False, True])
def test_step_failure_restores_caller_and_correlates_cleanup(monkeypatch, enhance):
    observed = []
    client = make_client(monkeypatch, observed, enhance=enhance, fail="step")
    stream = client.stream("turn")
    next(stream)
    with request_trace_context("caller-after-yield"):
        with pytest.raises(RuntimeError, match="step failed"):
            next(stream)
        assert get_current_trace_id() == "caller-after-yield"
    ids = {trace for _, _, trace in observed}
    assert len(ids) == 1 and None not in ids
    assert observed[-1][1] == "close"
    assert get_current_trace_id() is None


def test_closing_unstarted_stream_does_not_start_work(monkeypatch):
    observed = []
    client = make_client(monkeypatch, observed, enhance=False)
    stream = client.stream("never-started")
    stream.close()
    assert observed == []
    assert get_current_trace_id() is None


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
async def test_sync_helper_thread_hop_preserves_context_without_writeback(outcome):
    caller_thread = threading.get_ident()
    caller_marker = ContextVar("embedded-caller-marker", default=None)
    token = caller_marker.set("caller")
    observed = []

    async def work():
        await asyncio.sleep(0)
        observed.append((threading.get_ident(), get_current_trace_id(), caller_marker.get()))
        caller_marker.set("worker-only")
        if outcome == "error":
            raise RuntimeError("helper failed")
        if outcome == "cancel":
            raise asyncio.CancelledError
        return "done"

    try:
        with request_trace_context("embedded-helper-parent"):
            if outcome == "success":
                assert _run_async_from_sync(work()) == "done"
            else:
                exception = RuntimeError if outcome == "error" else asyncio.CancelledError
                with pytest.raises(exception):
                    _run_async_from_sync(work())
            assert get_current_trace_id() == "embedded-helper-parent"
            assert caller_marker.get() == "caller"
        assert observed == [(observed[0][0], "embedded-helper-parent", "caller")]
        assert observed[0][0] != caller_thread
    finally:
        caller_marker.reset(token)
