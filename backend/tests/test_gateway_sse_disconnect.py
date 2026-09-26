"""Regression coverage for SSE disconnect cleanup in the Gateway."""

from types import SimpleNamespace

import pytest

from app.gateway.services import sse_consumer
from deerflow.runtime.runs.schemas import DisconnectMode, RunStatus


class _Bridge:
    async def subscribe(self, _run_id, *, last_event_id=None):
        assert last_event_id is None
        yield object()


class _Request:
    headers = {}

    async def is_disconnected(self):
        return True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("on_disconnect", "store_only", "expected_cancels"),
    [
        (DisconnectMode.cancel, False, ["run-1"]),
        (DisconnectMode.continue_, False, []),
        (DisconnectMode.cancel, True, []),
    ],
)
async def test_sse_disconnect_cleanup(on_disconnect, store_only, expected_cancels):
    cancelled = []

    async def cancel(run_id):
        cancelled.append(run_id)

    record = SimpleNamespace(
        run_id="run-1",
        status=RunStatus.running,
        on_disconnect=on_disconnect,
        store_only=store_only,
    )
    run_manager = SimpleNamespace(cancel=cancel)

    assert [frame async for frame in sse_consumer(_Bridge(), record, _Request(), run_manager)] == []
    assert cancelled == expected_cancels
