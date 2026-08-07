"""Tests for thread-global message history pagination."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.routers import thread_runs
from deerflow.runtime import RunRecord
from deerflow.runtime.events.store.memory import MemoryRunEventStore


def _make_app(
    event_store: MemoryRunEventStore,
    *,
    superseded: set[str] | None = None,
    records=None,
    feedback=None,
    user_factory=None,
):
    app = make_authed_test_app(user_factory=user_factory)
    app.include_router(thread_runs.router)
    app.state.run_event_store = event_store
    run_manager = AsyncMock()
    run_manager.list_successful_regenerate_sources.return_value = superseded or set()
    run_manager.get_many_by_thread.return_value = records or {}
    app.state.run_manager = run_manager
    feedback_repo = AsyncMock()
    feedback_repo.list_by_run_ids.return_value = feedback or {}
    app.state.feedback_repo = feedback_repo
    return app


def _admin_user() -> User:
    return User(
        id=UUID("11111111-2222-3333-4444-555555555555"),
        email="admin@example.com",
        password_hash="x",
        system_role="admin",
    )


async def _put_message(store, run_id, message_type, message_id, *, caller="lead_agent"):
    return await store.put(
        thread_id="thread-1",
        run_id=run_id,
        event_type="llm.ai.response" if message_type == "ai" else "llm.human.input",
        category="message",
        content={"type": message_type, "id": message_id, "content": message_id, "additional_kwargs": {}},
        metadata={"caller": caller},
    )


def test_thread_page_orders_across_runs_and_paginates_without_gaps():
    store = MemoryRunEventStore()

    async def seed():
        for index in range(1, 7):
            await _put_message(store, f"run-{(index + 1) // 2}", "human" if index % 2 else "ai", f"m-{index}")

    asyncio.run(seed())
    app = _make_app(store)
    with TestClient(app) as client:
        latest = client.get("/api/threads/thread-1/messages/page?limit=3")
        older = client.get("/api/threads/thread-1/messages/page?limit=3&before_seq=4")

    assert latest.status_code == 200
    assert [row["seq"] for row in latest.json()["data"]] == [4, 5, 6]
    assert latest.json()["has_more"] is True
    assert latest.json()["next_before_seq"] == 4
    assert [row["seq"] for row in older.json()["data"]] == [1, 2, 3]
    assert older.json()["has_more"] is False
    assert older.json()["next_before_seq"] is None


def test_admin_thread_page_uses_authorized_unscoped_read_for_another_users_thread(monkeypatch):
    store = MemoryRunEventStore()

    async def seed():
        await _put_message(store, "run-1", "human", "visible-human")
        await _put_message(store, "run-1", "ai", "visible-answer")

    asyncio.run(seed())
    original_list_messages = store.list_messages
    store.list_messages = AsyncMock(wraps=original_list_messages)
    monkeypatch.setattr(
        thread_runs,
        "get_current_user",
        AsyncMock(return_value="11111111-2222-3333-4444-555555555555"),
    )
    app = _make_app(store, user_factory=_admin_user)
    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/messages/page?limit=10")

    assert response.status_code == 200
    assert [row["content"]["id"] for row in response.json()["data"]] == ["visible-human", "visible-answer"]
    assert store.list_messages.await_args_list
    assert all(call.kwargs["user_id"] is None for call in store.list_messages.await_args_list)
    app.state.run_manager.list_successful_regenerate_sources.assert_awaited_once_with(
        "thread-1",
        user_id=None,
    )
    app.state.run_manager.get_many_by_thread.assert_awaited_once()
    assert app.state.run_manager.get_many_by_thread.await_args.kwargs["user_id"] is None


def test_thread_page_keeps_only_latest_version_of_same_message_id():
    store = MemoryRunEventStore()

    async def seed():
        await store.put(
            thread_id="thread-1",
            run_id="run-1",
            event_type="llm.ai.response",
            category="message",
            content={"type": "ai", "id": "same-id", "content": "虚假的文件链接", "additional_kwargs": {}},
            metadata={"caller": "lead_agent"},
        )
        await store.put(
            thread_id="thread-1",
            run_id="run-1",
            event_type="llm.ai.response",
            category="message",
            content={
                "type": "ai",
                "id": "same-id",
                "content": "文件交付失败",
                "additional_kwargs": {"deerflow_error_fallback": True},
            },
            metadata={"caller": "lead_agent", "reconciled": True},
        )

    asyncio.run(seed())
    app = _make_app(store)
    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/messages/page?limit=10")

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["content"]["content"] == "文件交付失败"


def test_thread_page_scans_past_middleware_chunks_to_fill_visible_page(monkeypatch):
    monkeypatch.setattr(thread_runs, "THREAD_MESSAGE_PAGE_SCAN_BATCH", 3)
    store = MemoryRunEventStore()

    async def seed():
        await _put_message(store, "run-1", "human", "visible-old")
        for index in range(3):
            await _put_message(store, "run-1", "ai", f"middleware-{index}", caller="middleware:title")
        await _put_message(store, "run-2", "human", "visible-new-human")
        await _put_message(store, "run-2", "ai", "visible-new-ai")

    asyncio.run(seed())
    app = _make_app(store)
    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/messages/page?limit=2")

    body = response.json()
    assert [row["seq"] for row in body["data"]] == [5, 6]
    assert body["has_more"] is True
    assert body["next_before_seq"] == 5


def test_thread_page_hides_leaked_internal_compaction_transcript():
    store = MemoryRunEventStore()

    async def seed():
        await _put_message(store, "run-1", "human", "visible-human")
        await store.put(
            thread_id="thread-1",
            run_id="run-1",
            event_type="llm.ai.response",
            category="message",
            content={
                "type": "ai",
                "id": "leaked-summary",
                "content": "<analysis>internal</analysis><summary>compact</summary>",
                "additional_kwargs": {},
            },
            metadata={"caller": "lead_agent"},
        )
        await _put_message(store, "run-1", "ai", "visible-answer")

    asyncio.run(seed())
    app = _make_app(store)
    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/messages/page?limit=10")

    assert response.status_code == 200
    assert [row["content"]["id"] for row in response.json()["data"]] == ["visible-human", "visible-answer"]


def test_thread_page_scans_large_middleware_only_region_with_production_batch_size():
    store = MemoryRunEventStore()

    async def seed():
        await _put_message(store, "run-old", "human", "visible-old")
        for index in range(thread_runs.THREAD_MESSAGE_PAGE_SCAN_BATCH * 2):
            await _put_message(store, "run-middle", "ai", f"middleware-{index}", caller="middleware:title")
        await _put_message(store, "run-new", "human", "visible-new-human")
        await _put_message(store, "run-new", "ai", "visible-new-ai")

    asyncio.run(seed())
    original_list_messages = store.list_messages
    store.list_messages = AsyncMock(wraps=original_list_messages)
    app = _make_app(store)
    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/messages/page?limit=2")

    body = response.json()
    assert response.status_code == 200
    assert [row["seq"] for row in body["data"]] == [404, 405]
    assert body["has_more"] is True
    assert body["next_before_seq"] == 404
    assert store.list_messages.await_count == 3


def test_thread_page_filters_all_successfully_superseded_runs_before_filling():
    store = MemoryRunEventStore()

    async def seed():
        await _put_message(store, "run-a", "ai", "answer-a")
        await _put_message(store, "run-b", "ai", "answer-b")
        await _put_message(store, "run-c", "ai", "answer-c")

    asyncio.run(seed())
    app = _make_app(store, superseded={"run-a", "run-b"})
    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/messages/page?limit=2")

    body = response.json()
    assert [row["run_id"] for row in body["data"]] == ["run-c"]
    assert body["has_more"] is False
    assert body["next_before_seq"] is None


def test_thread_page_logs_rows_missing_sequence_values(caplog):
    store = AsyncMock()
    store.list_messages.return_value = [{"run_id": "run-1", "content": {"type": "human"}}]
    app = _make_app(store)

    with caplog.at_level(logging.ERROR, logger="app.gateway.routers.thread_runs"):
        with TestClient(app) as client, pytest.raises(RuntimeError, match="missing sequence values"):
            client.get("/api/threads/thread-1/messages/page")

    assert "Thread message scan found rows without sequence values" in caplog.text
    assert "thread_id=thread-1" in caplog.text
    assert "scan_before=None" in caplog.text
    assert "row_count=1" in caplog.text


def test_thread_page_logs_when_scan_cursor_does_not_advance(caplog):
    store = AsyncMock()
    store.list_messages.return_value = [{"run_id": "run-1", "seq": 10, "content": {"type": "human"}}]
    app = _make_app(store)

    with caplog.at_level(logging.ERROR, logger="app.gateway.routers.thread_runs"):
        with TestClient(app) as client, pytest.raises(RuntimeError, match="did not advance"):
            client.get("/api/threads/thread-1/messages/page?before_seq=10")

    assert "Thread message scan cursor did not advance" in caplog.text
    assert "thread_id=thread-1" in caplog.text
    assert "scan_before=10" in caplog.text
    assert "next_scan_before=10" in caplog.text
    assert "row_count=1" in caplog.text


def test_thread_page_feedback_only_attaches_to_global_last_ai_row():
    store = MemoryRunEventStore()

    async def seed():
        await _put_message(store, "run-1", "ai", "draft")
        await _put_message(store, "run-1", "human", "follow-up")
        await _put_message(store, "run-1", "ai", "final")

    asyncio.run(seed())
    original_list_messages = store.list_messages
    original_get_last_visible_ai_seq_by_run = store.get_last_visible_ai_seq_by_run
    store.list_messages = AsyncMock(wraps=original_list_messages)
    store.get_last_visible_ai_seq_by_run = AsyncMock(wraps=original_get_last_visible_ai_seq_by_run)
    feedback = {"run-1": {"feedback_id": "fb-1", "rating": 1, "comment": "good"}}
    app = _make_app(store, feedback=feedback)
    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/messages/page?limit=3")

    data = response.json()["data"]
    assert data[0]["feedback"] is None
    assert data[1]["feedback"] is None
    assert data[2]["feedback"] == {"feedback_id": "fb-1", "rating": 1, "comment": "good"}
    scan_user_id = store.list_messages.await_args.kwargs["user_id"]
    enrichment_user_id = store.get_last_visible_ai_seq_by_run.await_args.kwargs["user_id"]
    assert enrichment_user_id == scan_user_id
    feedback_repo = app.state.feedback_repo
    feedback_repo.list_by_run_ids.assert_awaited_once_with("thread-1", {"run-1"}, user_id=scan_user_id)
    feedback_repo.list_by_thread_grouped.assert_not_awaited()


def test_thread_page_helpers_forward_explicit_user_without_request_context():
    event_store = AsyncMock()
    event_store.list_messages.return_value = []
    event_store.get_last_visible_ai_seq_by_run.return_value = {}
    run_manager = AsyncMock()
    run_manager.list_successful_regenerate_sources.return_value = set()
    run_manager.get_many_by_thread.return_value = {}
    request = MagicMock()
    request.app.state.run_event_store = event_store
    request.app.state.run_manager = run_manager
    request.app.state.feedback_repo = AsyncMock()

    async def exercise_helpers():
        await thread_runs._scan_thread_message_page(
            "thread-1",
            limit=10,
            before_seq=None,
            request=request,
            user_id="background-user",
        )
        await thread_runs._enrich_thread_message_page(
            "thread-1",
            [{"run_id": "run-1", "seq": 1, "content": {"type": "human"}}],
            request=request,
            user_id="background-user",
        )

    asyncio.run(exercise_helpers())

    assert event_store.list_messages.await_args.kwargs["user_id"] == "background-user"
    assert event_store.get_last_visible_ai_seq_by_run.await_args.kwargs["user_id"] == "background-user"


def test_thread_page_scan_rejects_any_row_without_sequence():
    event_store = AsyncMock()
    event_store.list_messages.return_value = [
        {"run_id": "run-1", "seq": 1, "content": {"type": "human"}},
        {"run_id": "run-1", "content": {"type": "ai"}},
    ]
    run_manager = AsyncMock()
    run_manager.list_successful_regenerate_sources.return_value = set()
    request = MagicMock()
    request.app.state.run_event_store = event_store
    request.app.state.run_manager = run_manager

    with pytest.raises(RuntimeError, match="missing sequence values"):
        asyncio.run(
            thread_runs._scan_thread_message_page(
                "thread-1",
                limit=1,
                before_seq=None,
                request=request,
                user_id="user-1",
            )
        )


def test_thread_page_batch_hydrates_duration_for_old_runs():
    store = MemoryRunEventStore()
    asyncio.run(_put_message(store, "run-old", "ai", "answer"))
    record = RunRecord(
        run_id="run-old",
        thread_id="thread-1",
        assistant_id=None,
        status="success",
        on_disconnect="cancel",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:07Z",
    )
    app = _make_app(store, records={"run-old": record})
    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/messages/page")

    assert response.json()["data"][0]["content"]["additional_kwargs"]["turn_duration"] == 7


def test_thread_page_preserves_tool_and_subagent_wrapper_metadata():
    store = MemoryRunEventStore()
    asyncio.run(
        store.put(
            thread_id="thread-1",
            run_id="run-tool",
            event_type="tool.result",
            category="message",
            content={
                "type": "tool",
                "id": "tool-message-1",
                "tool_call_id": "call-1",
                "content": "result",
                "artifact": {"kind": "subagent"},
            },
            metadata={"caller": "subagent:research", "task_id": "task-1", "message_index": 3},
        )
    )
    app = _make_app(store)
    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/messages/page")

    row = response.json()["data"][0]
    assert row["run_id"] == "run-tool"
    assert row["content"]["artifact"] == {"kind": "subagent"}
    assert row["metadata"] == {"caller": "subagent:research", "task_id": "task-1", "message_index": 3}


def test_thread_page_empty_and_exact_limit_cursor_contract():
    empty_store = MemoryRunEventStore()
    with TestClient(_make_app(empty_store)) as client:
        empty = client.get("/api/threads/thread-1/messages/page?limit=2")
    assert empty.json() == {"data": [], "has_more": False, "next_before_seq": None}

    store = MemoryRunEventStore()

    async def seed():
        await _put_message(store, "run-1", "human", "one")
        await _put_message(store, "run-1", "ai", "two")

    asyncio.run(seed())
    with TestClient(_make_app(store)) as client:
        exact = client.get("/api/threads/thread-1/messages/page?limit=2")
    assert [row["seq"] for row in exact.json()["data"]] == [1, 2]
    assert exact.json()["has_more"] is False
    assert exact.json()["next_before_seq"] is None


def test_thread_page_rejects_forward_cursor_and_invalid_bounds():
    app = _make_app(MemoryRunEventStore())
    with TestClient(app) as client:
        assert client.get("/api/threads/thread-1/messages/page?after_seq=1").status_code == 422
        assert client.get("/api/threads/thread-1/messages/page?limit=0").status_code == 422
        assert client.get("/api/threads/thread-1/messages/page?limit=201").status_code == 422
        assert client.get("/api/threads/thread-1/messages/page?before_seq=0").status_code == 422
