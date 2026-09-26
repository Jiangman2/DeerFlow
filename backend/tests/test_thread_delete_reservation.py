"""#5535 deletion admission, captured owner and enterprise cleanup boundaries."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from _router_auth_helpers import call_unwrapped, make_authed_test_app
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from test_thread_operation_manager import manager
from test_thread_operation_store import anyio_backend as _anyio_backend
from test_thread_operation_store import store as _store
from test_threads_router import _build_thread_app

from app.gateway.routers import threads
from deerflow.config.paths import Paths
from deerflow.runtime import ConflictError, RunManager
from deerflow.runtime.user_context import get_effective_user_id

anyio_backend = _anyio_backend
store = _store


def request_for(app):
    return Request({"type": "http", "app": app, "headers": []})


@pytest.fixture
def cleanup_app(tmp_path, monkeypatch):
    app, _, _ = _build_thread_app()
    app.state.run_store = SimpleNamespace(delete_by_thread=AsyncMock(return_value=1))
    app.state.run_event_store = SimpleNamespace(delete_by_thread=AsyncMock(return_value=1))
    app.state.feedback_repo = SimpleNamespace(delete_by_thread=AsyncMock(return_value=1))
    monkeypatch.setattr(threads, "get_paths", lambda: Paths(tmp_path))
    browser = SimpleNamespace(close_session=AsyncMock())
    monkeypatch.setattr("deerflow.community.browser_automation.get_browser_session_manager", lambda: browser)
    return app, browser


@pytest.mark.anyio
async def test_delete_rejects_peer_run_before_any_cleanup(store, cleanup_app, monkeypatch):
    app, browser = cleanup_app
    owner = manager(store, "owner")
    app.state.run_manager = manager(store, "deleter")
    active = await owner.create_or_reject("thread", user_id="alice")
    filesystem = Mock(side_effect=AssertionError("must not delete active workspace"))
    monkeypatch.setattr(threads, "_delete_thread_data", filesystem)
    app.state.thread_store.check_access = AsyncMock(return_value=True)
    app.state.thread_store.delete = AsyncMock()
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.delete("/api/threads/thread")
    assert response.status_code == 409, response.text
    filesystem.assert_not_called()
    app.state.thread_store.delete.assert_not_awaited()
    app.state.run_event_store.delete_by_thread.assert_not_awaited()
    browser.close_session.assert_not_awaited()
    assert await store.get(active.run_id, user_id="alice") is not None


@pytest.mark.anyio
async def test_delete_holds_reservation_through_all_cleanup_stages(store, cleanup_app, monkeypatch):
    app, browser = cleanup_app
    writer, peer = manager(store, "deleter"), manager(store, "peer")
    app.state.run_manager, app.state.run_store = writer, store
    owner = get_effective_user_id()
    await store.put("historical", thread_id="thread", user_id=owner, status="success")
    await store.put("other-user", thread_id="thread", user_id="bob", status="success")
    boundaries = []

    async def fence(stage, *args, **kwargs):
        boundaries.append(stage)
        record = next(iter(writer._runs.values()))
        assert record.operation_kind.value == "delete"
        assert record.user_id == owner
        assert await store.get(record.run_id, user_id=owner) is not None
        for strategy in ("reject", "interrupt", "rollback"):
            with pytest.raises(ConflictError):
                await peer.create_or_reject("thread", user_id=owner, multitask_strategy=strategy)

    async def checkpoint(*args, **kwargs):
        await fence("checkpoint")

    async def history(thread_id, *, user_id):
        assert user_id == owner
        await fence("history")
        return await delete_history(thread_id, user_id=user_id)

    async def events(thread_id, *, user_id):
        assert user_id == owner
        await fence("events")

    async def feedback(thread_id, *, user_id):
        assert user_id == owner
        await fence("feedback")

    async def metadata(thread_id, *, user_id):
        assert user_id == owner
        await fence("metadata")

    async def close_browser(thread_id):
        await fence("browser")

    delete_history = store.delete_by_thread
    monkeypatch.setattr(store, "delete_by_thread", history)
    app.state.checkpointer.adelete_thread = checkpoint
    app.state.run_event_store.delete_by_thread = events
    app.state.feedback_repo.delete_by_thread = feedback
    app.state.thread_store.delete = metadata
    browser.close_session = close_browser
    result = await call_unwrapped(threads.delete_thread_data, "thread", request_for(app))
    assert result.success
    assert boundaries == ["checkpoint", "history", "events", "feedback", "metadata", "browser"]
    assert await store.get("historical", user_id=owner) is None
    assert await store.get("other-user", user_id="bob") is not None
    assert await store.list_inflight() == []
    assert writer._runs == {}
    assert (await peer.create_or_reject("thread", user_id=owner)).status.value == "pending"


@pytest.mark.anyio
async def test_one_captured_owner_is_used_for_every_cleanup(cleanup_app, monkeypatch):
    app, _ = cleanup_app
    resolve_owner = Mock(side_effect=["captured-owner", "wrong-owner"])
    monkeypatch.setattr(threads, "get_effective_user_id", resolve_owner)
    filesystem = Mock(return_value=threads.ThreadDeleteResponse(success=True, message="done"))
    monkeypatch.setattr(threads, "_delete_thread_data", filesystem)
    app.state.thread_store.delete = AsyncMock()
    await call_unwrapped(threads.delete_thread_data, "thread", request_for(app))
    resolve_owner.assert_called_once_with()
    filesystem.assert_called_once_with("thread", user_id="captured-owner")
    for repo in (app.state.run_store, app.state.run_event_store, app.state.feedback_repo):
        repo.delete_by_thread.assert_awaited_once_with("thread", user_id="captured-owner")
    app.state.thread_store.delete.assert_awaited_once_with("thread", user_id="captured-owner")


@pytest.mark.anyio
@pytest.mark.parametrize("failing", ["checkpointer", "run_store", "run_event_store", "feedback_repo", "thread_store"])
async def test_cleanup_failure_does_not_retry_or_skip_remaining_stores(cleanup_app, failing):
    app, browser = cleanup_app
    app.state.checkpointer.adelete_thread = AsyncMock()
    app.state.thread_store.delete = AsyncMock()
    name = {"checkpointer": "adelete_thread", "thread_store": "delete"}.get(failing, "delete_by_thread")
    callback = getattr(getattr(app.state, failing), name)
    callback.side_effect = TypeError("backend failure, not a signature mismatch")
    assert (await call_unwrapped(threads.delete_thread_data, "thread", request_for(app))).success
    callback.assert_awaited_once()
    browser.close_session.assert_awaited_once_with("thread")
    for repo in (app.state.run_store, app.state.run_event_store, app.state.feedback_repo):
        repo.delete_by_thread.assert_awaited_once()
    app.state.thread_store.delete.assert_awaited_once()
    assert app.state.run_manager._runs == {}


@pytest.mark.anyio
async def test_legacy_event_store_and_optional_history_backends(cleanup_app):
    app, _ = cleanup_app
    calls = []

    async def delete_legacy(thread_id):
        calls.append(thread_id)

    app.state.run_event_store.delete_by_thread = delete_legacy
    app.state.run_store = SimpleNamespace()
    app.state.feedback_repo = None
    assert (await call_unwrapped(threads.delete_thread_data, "thread", request_for(app))).success
    assert calls == ["thread"]


@pytest.mark.anyio
async def test_legacy_id_never_reaches_filesystem(cleanup_app, monkeypatch):
    app, _ = cleanup_app
    filesystem = Mock(side_effect=AssertionError("invalid id reached filesystem"))
    monkeypatch.setattr(threads, "_delete_thread_data", filesystem)
    app.state.thread_store.delete = AsyncMock()
    result = await call_unwrapped(threads.delete_thread_data, "legacy.thread", request_for(app))
    assert result.success and "Skipped local data cleanup" in result.message
    filesystem.assert_not_called()
    app.state.thread_store.delete.assert_awaited_once_with("legacy.thread", user_id=get_effective_user_id())


def test_unauthorized_delete_never_enters_reservation(monkeypatch):
    app = make_authed_test_app(owner_check_passes=False)
    app.state.run_manager = RunManager()
    app.include_router(threads.router)
    reservation = Mock(side_effect=AssertionError("unauthorized admission"))
    monkeypatch.setattr(app.state.run_manager, "reserve_thread_operation", reservation)
    with TestClient(app) as client:
        assert client.delete("/api/threads/thread").status_code == 404
    reservation.assert_not_called()


@pytest.mark.anyio
async def test_filesystem_failure_keeps_metadata_and_releases_reservation(cleanup_app, monkeypatch):
    app, browser = cleanup_app
    app.state.thread_store.delete = AsyncMock()
    monkeypatch.setattr(threads, "_delete_thread_data", Mock(side_effect=HTTPException(status_code=500, detail="filesystem failed")))
    with pytest.raises(HTTPException) as failure:
        await call_unwrapped(threads.delete_thread_data, "thread", request_for(app))
    assert failure.value.status_code == 500
    app.state.thread_store.delete.assert_not_awaited()
    app.state.run_event_store.delete_by_thread.assert_not_awaited()
    browser.close_session.assert_not_awaited()
    assert app.state.run_manager._runs == {}


@pytest.mark.anyio
async def test_missing_run_manager_fails_closed_before_cleanup(cleanup_app, monkeypatch):
    app, _ = cleanup_app
    app.state.run_manager = None
    filesystem = Mock(side_effect=AssertionError("unfenced deletion"))
    monkeypatch.setattr(threads, "_delete_thread_data", filesystem)
    with pytest.raises(HTTPException) as failure:
        await call_unwrapped(threads.delete_thread_data, "thread", request_for(app))
    assert failure.value.status_code == 503
    filesystem.assert_not_called()


def test_event_owner_signature_compatibility(monkeypatch):
    def keyword_only(thread_id, *, user_id): ...

    def positional(thread_id, user_id, /): ...

    def variadic(thread_id, **kwargs): ...

    assert threads._event_delete_owner_kwargs(keyword_only, "owner") == {"user_id": "owner"}
    assert threads._event_delete_owner_kwargs(variadic, "owner") == {"user_id": "owner"}
    assert threads._event_delete_owner_kwargs(positional, "owner") == {}
    monkeypatch.setattr(threads.inspect, "signature", Mock(side_effect=ValueError("uninspectable")))
    assert threads._event_delete_owner_kwargs(keyword_only, "owner") == {}


@pytest.mark.anyio
async def test_cancelled_filesystem_cleanup_is_drained_before_admission_reopens(cleanup_app, monkeypatch):
    app, _ = cleanup_app
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    loop_thread = threading.get_ident()

    def remove(thread_id, **kwargs):
        assert threading.get_ident() != loop_thread
        entered.set()
        try:
            assert release.wait(3), "test did not release filesystem worker"
            return threads.ThreadDeleteResponse(success=True, message="done")
        finally:
            finished.set()

    monkeypatch.setattr(threads, "_delete_thread_data", remove)
    task = asyncio.create_task(call_unwrapped(threads.delete_thread_data, "thread", request_for(app)))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        with pytest.raises(ConflictError):
            await app.state.run_manager.create_or_reject("thread", user_id="alice")
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled() and finished.is_set()
    assert app.state.run_manager._runs == {}
    assert (await app.state.run_manager.create_or_reject("thread", user_id="alice")).status.value == "pending"
