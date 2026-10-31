import json

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from deerflow.persistence.models.run_event import RunEventRow
from deerflow.persistence.observability.model import OperationDispositionRow
from deerflow.persistence.observability.sql import ObservabilityRepository
from deerflow.persistence.run.model import RunRow


@pytest.mark.asyncio
async def test_large_diagnostics_are_bounded_in_sql_and_keep_tail():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sf = async_sessionmaker(engine, expire_on_commit=False)
        async with sf.begin() as session:
            session.add(RunRow(run_id="large", thread_id="thread", status="error", error="E" * 100_000, first_human_message="Q" * 100_000, kwargs_json={"never_load": "X" * 1_000_000}, token_usage_by_model={"large": "X" * 100_000}))
            for i in range(50):
                session.add(RunEventRow(thread_id="thread", run_id="large", seq=i, event_type="llm.delta", category="trace", content="X" * 100_000, event_metadata={"oversized": "Y" * 100_000}))
            for i in range(23):
                session.add(OperationDispositionRow(run_id="large", thread_id="thread", action="note", status="handled" if i == 0 else None, note="N" * 100_000, details_json={"oversized": "Z" * 100_000}))
        statements = []
        event.listen(engine.sync_engine, "before_cursor_execute", lambda c, u, s, p, x, m: statements.append(s))
        result = await ObservabilityRepository(sf).run_diagnostics("large", bounded=True)
        assert len(json.dumps(result).encode()) < 300_000
        assert result["run"]["event_count"] == 50
        assert [e["seq"] for e in result["events"]] == list(range(20, 50))
        assert all(len(e["content"]) == 4096 and e["metadata"] == {} for e in result["events"])
        assert len(result["error_stack"]) == 4096
        assert result["tokens"]["by_model"] == {}
        assert result["preview"]["bounded"]
        assert len(result["dispositions"]) == 20
        assert len(result["dispositions"][0]["note"]) == 4096
        assert result["dispositions"][0]["details"] == {}
        assert result["disposition_status"] == "handled"
        assert all("kwargs_json" not in s and "last_ai_message" not in s for s in statements)
        assert any("substr(run_events.content" in s for s in statements)
        assert await ObservabilityRepository(sf).run_diagnostics("missing", bounded=True) is None
    finally:
        await engine.dispose()


def test_tool_preview_caps_entries_and_handles_malformed_shapes():
    from types import SimpleNamespace

    from deerflow.persistence.observability.sql import _extract_tool_calls

    def event(seq, payload):
        return SimpleNamespace(seq=seq, event_type="llm.ai.response", created_at=None, content=json.dumps(payload))

    events = [event(0, {"tool_calls": 7}), event(1, {"tool_calls": [None, {"function": "invalid"}]})]
    events += [event(i + 2, {"tool_calls": [{"id": str(i), "name": "test", "args": {}}]}) for i in range(60)]
    result = _extract_tool_calls(events, limit=30)
    assert len(result) == 30
    assert all(isinstance(row["name"], str) for row in result)


@pytest.mark.asyncio
async def test_small_diagnostics_preserve_error_tool_and_node():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sf = async_sessionmaker(engine, expire_on_commit=False)
        async with sf.begin() as session:
            session.add(RunRow(run_id="small", thread_id="t", status="error", error="tool failed"))
            session.add(
                RunEventRow(
                    thread_id="t",
                    run_id="small",
                    seq=1,
                    category="trace",
                    event_type="llm.tool.result",
                    content=json.dumps({"tool_call_id": "call-1", "name": "generate_image", "status": "error", "content": "HTTP 500"}),
                    event_metadata={"caller": "tools", "status": "error"},
                )
            )
        result = await ObservabilityRepository(sf).run_diagnostics("small", bounded=True)
        assert result["failed_node"] == "tools"
        assert result["tool_calls"][0]["name"] == "generate_image"
        assert result["tool_calls"][0]["error"] == "HTTP 500"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_diagnostic_route_preserves_admin_boundary_and_requires_preview(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from fastapi import HTTPException
    from starlette.requests import Request

    from app.gateway.routers import observability

    read = AsyncMock(return_value={"preview": {"bounded": True}})
    monkeypatch.setattr(observability, "_repo", lambda: SimpleNamespace(run_diagnostics=read))
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    request.state.user = SimpleNamespace(system_role="user")
    with pytest.raises(HTTPException) as exc:
        await observability.get_run_diagnostics(request, "synthetic")
    assert exc.value.status_code == 403
    read.assert_not_awaited()
    request.state.user = SimpleNamespace(system_role="admin")
    assert (await observability.get_run_diagnostics(request, "synthetic"))["preview"]["bounded"]
    read.assert_awaited_once_with("synthetic", bounded=True)
    read.return_value = None
    with pytest.raises(HTTPException) as exc:
        await observability.get_run_diagnostics(request, "missing")
    assert exc.value.status_code == 404
