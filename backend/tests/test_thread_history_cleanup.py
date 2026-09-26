"""Owner-scoped cleanup primitives from #5535, before Gateway wiring."""

from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError
from test_run_event_singletons import anyio_backend as _anyio_backend
from test_run_event_singletons import stores as _stores
from test_thread_operation_store import store as _runstore

from deerflow.persistence.feedback.model import FeedbackRow
from deerflow.persistence.feedback.sql import FeedbackRepository
from deerflow.runtime.runs.manager import ConflictError
from deerflow.runtime.user_context import reset_current_user, set_current_user

anyio_backend = _anyio_backend
runstore = _runstore
stores = _stores


@pytest.mark.anyio
async def test_history_cleanup_preserves_owner_thread_and_live_reservation(runstore):
    await runstore.put("alice", thread_id="thread", user_id="alice", status="success")
    await runstore.put("bob", thread_id="thread", user_id="bob", status="success")
    await runstore.put("other", thread_id="other", user_id="alice", status="success")
    await runstore.create_thread_operation_atomic("reservation", thread_id="thread", operation_kind="checkpoint_write", user_id="alice", owner_worker_id="holder", lease_expires_at=None)
    assert await runstore.delete_by_thread("thread", user_id="alice") == 1
    assert await runstore.delete_by_thread("thread", user_id="alice") == 0
    assert await runstore.get("alice", user_id="alice") is None
    assert await runstore.get("bob", user_id="bob") is not None
    assert await runstore.get("other", user_id="alice") is not None
    assert await runstore.get("reservation", user_id="alice") is not None
    assert await runstore.list_by_thread("thread", user_id="alice") == []
    with pytest.raises((ConflictError, IntegrityError)):
        await runstore.create_thread_operation_atomic("new", thread_id="thread", operation_kind="run", user_id="alice", owner_worker_id="peer", lease_expires_at=None)
    await runstore.delete_thread_operation("reservation", user_id="alice")
    await runstore.create_thread_operation_atomic("new", thread_id="thread", operation_kind="run", user_id="alice", owner_worker_id="peer", lease_expires_at=None)
    if hasattr(runstore, "_runs_by_thread"):
        assert "alice" not in runstore._runs_by_thread["thread"]


@pytest.mark.anyio
@pytest.mark.parametrize("runstore", ["sqlite", "postgres"], indirect=True)
async def test_sql_run_cleanup_resolves_auto_and_requires_explicit_unscoped_access(runstore):
    await runstore.put("alice", thread_id="thread", user_id="alice", status="success")
    await runstore.put("bob", thread_id="thread", user_id="bob", status="success")
    token = set_current_user(SimpleNamespace(id="alice"))
    try:
        assert await runstore.delete_by_thread("thread") == 1
    finally:
        reset_current_user(token)
    token = set_current_user(None)
    try:
        with pytest.raises(RuntimeError):
            await runstore.delete_by_thread("thread")
        assert await runstore.delete_by_thread("thread", user_id=None) == 1
    finally:
        reset_current_user(token)


@pytest.mark.anyio
@pytest.mark.parametrize("stores", ["sqlite", "postgres"], indirect=True)
async def test_feedback_cleanup_preserves_other_owner_and_thread(stores):
    event_store, _ = stores
    engine = event_store._sf.kw["bind"]
    async with engine.begin() as conn:
        await conn.run_sync(FeedbackRow.__table__.create)
    repo = FeedbackRepository(event_store._sf)
    await repo.create(run_id="run", thread_id="thread", rating=1, user_id="alice")
    await repo.create(run_id="run", thread_id="thread", rating=-1, user_id="bob")
    await repo.create(run_id="run", thread_id="other", rating=1, user_id="alice")
    assert await repo.delete_by_thread("thread") == 1
    assert await repo.delete_by_thread("thread", user_id="alice") == 0
    assert await repo.list_by_thread("thread", user_id="alice") == []
    assert len(await repo.list_by_thread("thread", user_id="bob")) == 1
    assert len(await repo.list_by_thread("other", user_id="alice")) == 1
    token = set_current_user(None)
    try:
        with pytest.raises(RuntimeError):
            await repo.delete_by_thread("thread")
        assert await repo.delete_by_thread("thread", user_id=None) == 1
    finally:
        reset_current_user(token)
