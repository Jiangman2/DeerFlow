"""v2.1 #4450: admitted pending work stays cancellable before graph construction."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio

from deerflow.runtime.runs.manager import RunManager, RunStartupError
from deerflow.runtime.runs.schemas import RunStatus
from deerflow.runtime.runs.store.memory import MemoryRunStore
from deerflow.runtime.runs.worker import RunContext, run_agent
from deerflow.trace_context import get_current_trace_id


@pytest_asyncio.fixture(params=["memory", "sqlite"])
async def startup_store(request, tmp_path):
    if request.param == "memory":
        yield MemoryRunStore()
        return
    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine
    from deerflow.persistence.run import RunRepository

    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'startup.db'}", sqlite_dir=str(tmp_path))
    try:
        yield RunRepository(get_session_factory())
    finally:
        await close_engine()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "running", "interrupted", "error", "success", "timeout"])
async def test_store_starts_only_pending_once(startup_store, status):
    await startup_store.put("run", thread_id="thread", status=status)
    assert await startup_store.start_run("run") is (status == "pending")
    assert await startup_store.start_run("run") is False
    assert await startup_store.start_run("missing") is False
    assert (await startup_store.get("run"))["status"] == ("running" if status == "pending" else status)


@pytest.mark.asyncio
async def test_local_pending_cancel_never_starts(startup_store):
    manager = RunManager(store=startup_store)
    record = await manager.create_or_reject("thread")
    await manager.cancel(record.run_id)
    assert await manager.try_start(record.run_id) == "cancelled"
    assert record.status == RunStatus.interrupted
    assert (await startup_store.get(record.run_id))["status"] == "interrupted"


@pytest.mark.asyncio
async def test_store_terminal_record_never_resurrected(startup_store):
    manager = RunManager(store=startup_store)
    record = await manager.create_or_reject("thread")
    await startup_store.update_status(record.run_id, "error", error="peer recovered")
    assert await manager.try_start(record.run_id) == "cancelled"
    assert record.abort_event.is_set()
    assert (await startup_store.get(record.run_id))["error"] == "peer recovered"


@pytest.mark.asyncio
async def test_duplicate_start_attempts_are_serialized():
    manager = RunManager(store=MemoryRunStore())
    record = await manager.create_or_reject("thread")
    outcomes = await asyncio.gather(manager.try_start(record.run_id), manager.try_start(record.run_id))
    assert sorted(outcomes) == ["cancelled", "started"]
    assert record.status == RunStatus.running


@pytest.mark.asyncio
async def test_durable_start_has_only_one_winner(startup_store):
    await startup_store.put("run", thread_id="thread", status="pending")
    results = await asyncio.gather(startup_store.start_run("run"), startup_store.start_run("run"))
    assert sorted(results) == [False, True]


@pytest.mark.asyncio
async def test_startup_fails_closed_when_store_is_unavailable():
    class BrokenStore(MemoryRunStore):
        async def start_run(self, run_id):
            raise RuntimeError("store unavailable")

    manager = RunManager(store=BrokenStore())
    record = await manager.create_or_reject("thread")
    with pytest.raises(RunStartupError, match="store unavailable"):
        await manager.try_start(record.run_id)
    assert record.status == RunStatus.pending
    with pytest.raises(RunStartupError, match="unknown run"):
        await manager.try_start("missing")


@pytest.mark.asyncio
async def test_cancel_during_store_start_preserves_cancelled_status():
    entered, release = asyncio.Event(), asyncio.Event()

    class SlowStore(MemoryRunStore):
        async def start_run(self, run_id):
            updated = await super().start_run(run_id)
            entered.set()
            await release.wait()
            return updated

    store = SlowStore()
    manager = RunManager(store=store)
    record = await manager.create_or_reject("thread")
    starter = asyncio.create_task(manager.try_start(record.run_id))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await manager.cancel(record.run_id)
    finally:
        release.set()
    assert await starter == "cancelled"
    assert (await store.get(record.run_id))["status"] == "interrupted"


@pytest.mark.asyncio
async def test_failed_attachment_does_not_overwrite_a_cancelled_run(startup_store):
    manager = RunManager(store=startup_store)
    record = await manager.create_or_reject("thread")
    assert await manager.fail_start_if_pending(record.run_id, error="attach failed") is True
    assert record.abort_event.is_set()
    assert (await startup_store.get(record.run_id))["status"] == "error"
    other = await manager.create_or_reject("other-thread")
    await manager.cancel(other.run_id)
    assert await manager.fail_start_if_pending(other.run_id, error="too late") is False
    assert (await startup_store.get(other.run_id))["status"] == "interrupted"


@pytest.mark.asyncio
async def test_cancelled_pending_worker_publishes_end_without_build_or_checkpoint():
    manager = RunManager(store=MemoryRunStore())
    record = await manager.create_or_reject("thread")
    await manager.cancel(record.run_id)
    bridge = SimpleNamespace(publish=AsyncMock(), publish_end=AsyncMock(), cleanup=AsyncMock())
    checkpointer = SimpleNamespace(aget_tuple=AsyncMock(return_value=None))
    threads = SimpleNamespace(update_status=AsyncMock(), update_display_name=AsyncMock())
    factory, completion = Mock(), AsyncMock()
    await run_agent(
        bridge,
        manager,
        record,
        ctx=RunContext(checkpointer=checkpointer, thread_store=threads, on_run_completed=completion),
        agent_factory=factory,
        graph_input={},
        config={},
    )
    factory.assert_not_called()
    checkpointer.aget_tuple.assert_not_called()
    threads.update_status.assert_not_called()
    completion.assert_awaited_once_with(record)
    bridge.publish_end.assert_awaited_once_with(record.run_id)
    assert record.status == RunStatus.interrupted
    assert get_current_trace_id() is None
    await asyncio.sleep(0)  # Let the mock stream cleanup finish.


@pytest.mark.asyncio
async def test_cancel_can_exit_prior_finalizer_wait():
    manager = RunManager()
    previous = await manager.create_or_reject("thread")
    await manager.cancel(previous.run_id)
    current = await manager.create_or_reject("thread")
    await manager.set_finalizing(previous.run_id, True)
    current.abort_event.set()
    await asyncio.wait_for(manager.wait_for_prior_finalizing("thread", current.run_id, abort_event=current.abort_event), 1)
    assert previous.finalizing  # Waiting must not fake completion of the old owner.


@pytest.fixture
def gateway_startup(monkeypatch):
    import app.gateway.services as services
    from app.gateway.routers.thread_runs import RunCreateRequest
    from deerflow.config.app_config import AppConfig, reset_app_config, set_app_config

    set_app_config(AppConfig.model_validate({"sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"}}))
    manager = RunManager(store=MemoryRunStore())
    threads = SimpleNamespace(get=AsyncMock(return_value=None), create=AsyncMock(), update_status=AsyncMock())
    ctx = RunContext(checkpointer=None, thread_store=threads)
    request = SimpleNamespace(headers={}, state=SimpleNamespace(auth_source=None))
    monkeypatch.setattr(services, "get_stream_bridge", lambda _: object())
    monkeypatch.setattr(services, "get_run_manager", lambda _: manager)
    monkeypatch.setattr(services, "get_run_context", lambda _: ctx)
    monkeypatch.setattr(services, "resolve_agent_factory", lambda _: object())
    worker = AsyncMock()
    monkeypatch.setattr(services, "run_agent", worker)
    body = RunCreateRequest(input={"messages": []}, assistant_id="lead_agent")
    yield SimpleNamespace(services=services, manager=manager, threads=threads, request=request, body=body, worker=worker)
    reset_app_config()


@pytest.mark.asyncio
async def test_admission_attaches_worker_before_waiting_for_metadata(gateway_startup):
    setup = gateway_startup
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked_get(*args, **kwargs):
        entered.set()
        await release.wait()

    setup.threads.get.side_effect = blocked_get
    admission = asyncio.create_task(setup.services.start_run(setup.body, "thread", setup.request))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        # A durable admission must already have an attached worker/response,
        # even when thread-store IO is indefinitely blocked.
        assert admission.done()
        record = admission.result()
        assert record.task is not None
        await setup.manager.cancel(record.run_id)
        await asyncio.wait_for(record.task, 1)
        setup.worker.assert_awaited_once()
        assert setup.worker.call_args.args[2].abort_event.is_set()
    finally:
        release.set()
        record = await admission
        if record.task is not None:
            await record.task
    assert get_current_trace_id() is None


@pytest.mark.asyncio
async def test_metadata_timeout_cannot_prevent_worker_entry(gateway_startup, monkeypatch):
    setup = gateway_startup
    monkeypatch.setattr(setup.services, "_THREAD_METADATA_SETUP_TIMEOUT_SECONDS", 0.01, raising=False)

    async def never_ready(*args, **kwargs):
        await asyncio.Event().wait()

    setup.threads.get.side_effect = never_ready
    record = await asyncio.wait_for(setup.services.start_run(setup.body, "thread", setup.request), 1)
    await asyncio.wait_for(record.task, 1)
    setup.worker.assert_awaited_once()


@pytest.mark.asyncio
async def test_attachment_failure_marks_pending_error(gateway_startup, monkeypatch):
    setup = gateway_startup
    monkeypatch.setattr(asyncio, "create_task", Mock(side_effect=RuntimeError("attach rejected")))
    with pytest.raises(RuntimeError, match="attach rejected"):
        await setup.services.start_run(setup.body, "thread", setup.request)
    records = await setup.manager.list_by_thread("thread")
    assert len(records) == 1
    assert records[0].status == RunStatus.error
    assert records[0].abort_event.is_set()
    assert records[0].task is None
    setup.threads.get.assert_not_awaited()
    assert get_current_trace_id() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["interrupt", "rollback"])
async def test_replacement_of_pending_run_drains_real_worker(gateway_startup, monkeypatch, strategy):
    setup = gateway_startup
    bridge = SimpleNamespace(publish=AsyncMock(), publish_end=AsyncMock(), cleanup=AsyncMock())
    factory = Mock(side_effect=AssertionError("cancelled pending run must not build an agent"))
    monkeypatch.setattr(setup.services, "get_stream_bridge", lambda _: bridge)
    monkeypatch.setattr(setup.services, "resolve_agent_factory", lambda _: factory)
    monkeypatch.setattr(setup.services, "run_agent", run_agent)
    entered = asyncio.Event()

    async def blocked_get(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    setup.threads.get.side_effect = blocked_get
    record = await setup.services.start_run(setup.body, "thread", setup.request)
    try:
        await asyncio.wait_for(entered.wait(), 1)
        replacement = await setup.manager.create_or_reject("thread", multitask_strategy=strategy)
        await asyncio.wait_for(record.task, 1)
        bridge.publish_end.assert_awaited_once_with(record.run_id)
        assert record.status == RunStatus.interrupted
        assert record.abort_action == strategy
        assert record.finalizing is False
        assert replacement.status == RunStatus.pending
        factory.assert_not_called()
        setup.threads.update_status.assert_not_called()
    finally:
        if not record.task.done():
            await setup.manager.cancel(record.run_id)
            await asyncio.wait_for(record.task, 1)
        await asyncio.sleep(0)
