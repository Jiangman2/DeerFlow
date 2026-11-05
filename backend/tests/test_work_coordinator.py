"""Recovery must not depend on a browser request or resurrect disabled Work."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.channels.runtime_config_store import ChannelRuntimeConfigStore
from app.channels.service import ChannelService


@pytest.fixture
def service(monkeypatch, tmp_path):
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr("deerflow.config.paths._paths", None)
    monkeypatch.delenv("DEER_FLOW_WORK_PRODUCTION_APP_ID", raising=False)
    monkeypatch.delenv("DEER_FLOW_WORK_PRODUCTION_APP_SECRET", raising=False)
    cfg = {"enabled": True, "app_id": "cli_synthetic", "app_secret": "synthetic-one", "domain": "https://open.w.cnooc.com.cn"}
    connections = SimpleNamespace(enabled=True, feishu=SimpleNamespace(enabled=True))
    monkeypatch.setattr("deerflow.config.app_config.get_app_config", lambda: SimpleNamespace(model_extra={}, channel_connections=connections))
    svc = ChannelService(channels_config={"feishu": cfg})
    svc._running = True
    return svc


def test_explicit_shared_disable_never_falls_back_to_enabled_cache(service):
    ChannelRuntimeConfigStore().set_provider_disconnected("feishu")
    assert not service._load_channel_config("feishu").get("enabled")


def test_bootstrap_credentials_do_not_override_shared_disable(service, monkeypatch):
    monkeypatch.setenv("DEER_FLOW_WORK_PRODUCTION_APP_ID", "cli_env")
    monkeypatch.setenv("DEER_FLOW_WORK_PRODUCTION_APP_SECRET", "synthetic-env")
    ChannelRuntimeConfigStore().set_provider_disconnected("feishu")
    assert not service._load_channel_config("feishu").get("enabled")


@pytest.mark.asyncio
async def test_shared_disable_then_enable_is_reconciled_without_existing_receiver(service):
    old = SimpleNamespace(is_running=True, stop=AsyncMock())
    service._channels["feishu"] = old
    service._start_channel = AsyncMock(return_value=True)
    ChannelRuntimeConfigStore().set_provider_disconnected("feishu")
    assert await service._reconcile_work_channel()
    old.stop.assert_awaited_once()
    assert "feishu" not in service._channels
    service._start_channel.assert_not_awaited()
    cfg = {"enabled": True, "app_id": "cli_ui", "app_secret": "synthetic-new", "domain": "https://open.w.cnooc.com.cn"}
    ChannelRuntimeConfigStore().set_provider_config("feishu", cfg)
    assert await service._reconcile_work_channel()
    service._start_channel.assert_awaited_once_with("feishu", cfg)


@pytest.mark.asyncio
async def test_config_removed_stops_cached_worker(service):
    service._channels["feishu"] = SimpleNamespace(is_running=True, stop=AsyncMock())
    service._start_channel = AsyncMock(return_value=True)
    assert await service._reconcile_work_channel()
    assert "feishu" not in service._channels
    service._start_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_unreadable_config_does_not_start_a_cached_enabled_channel(service, monkeypatch):
    service._start_channel = AsyncMock(return_value=True)

    def unavailable():
        raise OSError("synthetic-config-unavailable")

    monkeypatch.setattr("deerflow.config.app_config.get_app_config", unavailable)
    with pytest.raises(OSError):
        await service._reconcile_work_channel()
    service._start_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_dead_receiver_monitor_requires_restart_even_when_transport_reports_running(service):
    cfg = dict(service._config["feishu"])
    ChannelRuntimeConfigStore().set_provider_config("feishu", cfg)
    monitor = asyncio.create_task(asyncio.sleep(0))
    await monitor
    service._channels["feishu"] = SimpleNamespace(is_running=True, stop=AsyncMock())
    service._work_receivers["feishu"] = SimpleNamespace(config=cfg, task=monitor, closed=False, close=AsyncMock())
    service._start_channel = AsyncMock(return_value=True)
    assert await service._reconcile_work_channel()
    service._start_channel.assert_awaited_once()


@pytest.mark.asyncio
async def test_shared_secret_change_closes_old_worker_before_replacing_it(service):
    old = dict(service._config["feishu"])
    cfg = {**old, "app_secret": "synthetic-two"}
    ChannelRuntimeConfigStore().set_provider_config("feishu", cfg)
    stopped = False

    async def stop():
        nonlocal stopped
        stopped = True

    async def start(name, values):
        assert stopped and values == cfg
        return True

    service._channels["feishu"] = SimpleNamespace(is_running=True, stop=stop)
    service._start_channel = AsyncMock(side_effect=start)
    assert await service._reconcile_work_channel()
    service._start_channel.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_socket_stop_retains_worker_and_blocks_replacement(service):
    cfg = {**service._config["feishu"], "app_secret": "synthetic-two"}
    ChannelRuntimeConfigStore().set_provider_config("feishu", cfg)
    old = SimpleNamespace(is_running=True, stop=AsyncMock(side_effect=RuntimeError("synthetic-join-timeout")))
    service._channels["feishu"] = old
    service._start_channel = AsyncMock(return_value=True)
    assert not await service._reconcile_work_channel()
    assert service._channels["feishu"] is old
    service._start_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_db_release_failure_does_not_interrupt_local_cleanup(service):
    from app.integrations.work.receiver import WorkReceiver

    cfg = service._config["feishu"]
    receiver = WorkReceiver(service, SimpleNamespace(release=AsyncMock(side_effect=ConnectionError("synthetic-db-down"))), cfg)
    receiver.task = asyncio.create_task(asyncio.sleep(100))
    service._work_receivers["feishu"] = receiver
    channel = SimpleNamespace(stop=AsyncMock())
    await service._stop_channel("feishu", channel)
    channel.stop.assert_awaited_once()
    assert receiver.closed and receiver.task.done()
    assert "feishu" not in service._work_receivers


@pytest.mark.asyncio
async def test_coordinator_retries_after_db_recovery_without_browser(service):
    from app.integrations.work.coordinator import WorkChannelCoordinator

    recovered = asyncio.Event()
    attempts = 0

    async def reconcile():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionError("synthetic-db-down")
        recovered.set()
        return True

    service._reconcile_work_channel = reconcile
    coordinator = WorkChannelCoordinator(service, interval=0.01, max_delay=0.04)
    coordinator.start()
    try:
        await asyncio.wait_for(recovered.wait(), 2)
        assert attempts >= 3
    finally:
        await coordinator.close()
    assert coordinator.task.done()


@pytest.mark.asyncio
async def test_coordinator_stop_waits_for_inflight_cleanup_and_does_not_restart(service):
    from app.integrations.work.coordinator import WorkChannelCoordinator

    entered, release = asyncio.Event(), asyncio.Event()

    async def reconcile():
        entered.set()
        await release.wait()
        return True

    service._reconcile_work_channel = AsyncMock(side_effect=reconcile)
    coordinator = WorkChannelCoordinator(service, interval=0.01, max_delay=0.04)
    coordinator.start()
    await entered.wait()
    stopping = asyncio.create_task(coordinator.close())
    await asyncio.sleep(0)
    assert not stopping.done()
    release.set()
    await stopping
    service._reconcile_work_channel.assert_awaited_once()


def test_retry_backoff_is_bounded_and_resets_after_success(service):
    from app.integrations.work.coordinator import WorkChannelCoordinator

    coordinator = WorkChannelCoordinator(service)
    delays = [coordinator.retry_delay(False) for _ in range(10)]
    assert all(0 < x <= 60 for x in delays)
    assert delays[-1] >= delays[0]
    assert 0 < coordinator.retry_delay(True) <= 5


@pytest.mark.asyncio
async def test_concurrent_reconciliation_never_replaces_worker_twice(service):
    cfg = dict(service._config["feishu"])
    ChannelRuntimeConfigStore().set_provider_config("feishu", cfg)

    async def start(name, values):
        await asyncio.sleep(0.01)
        service._channels[name] = SimpleNamespace(is_running=True, stop=AsyncMock())
        monitor = asyncio.create_task(asyncio.sleep(100))
        service._work_receivers[name] = SimpleNamespace(config=values, task=monitor, closed=False, close=AsyncMock())
        return True

    service._start_channel = AsyncMock(side_effect=start)
    try:
        assert await asyncio.gather(service._reconcile_work_channel(), service._reconcile_work_channel()) == [True, True]
        service._start_channel.assert_awaited_once()
    finally:
        task = service._work_receivers["feishu"].task
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_inflight_old_config_cannot_override_newer_ui_disable(service):
    entered, release = threading.Event(), threading.Event()
    stale = dict(service._config["feishu"])

    def read(name):
        entered.set()
        assert release.wait(2)
        return stale

    service._read_channel_config = read
    service._start_channel = AsyncMock(return_value=True)
    reconciling = asyncio.create_task(service._reconcile_work_channel())
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        await service.configure_channel("feishu", {"enabled": False, "_runtime_disabled": True})
    finally:
        release.set()
    assert await reconciling
    assert service._config["feishu"]["enabled"] is False
    service._start_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_initial_channel_start_still_starts_background_recovery(service, monkeypatch):
    from app.integrations.work import coordinator as module

    coordinator_cls = module.WorkChannelCoordinator
    monkeypatch.setattr(module, "WorkChannelCoordinator", lambda svc: coordinator_cls(svc, interval=0.01, max_delay=0.04))
    monkeypatch.setattr("app.integrations.work.config.WorkSettings.from_env", lambda: SimpleNamespace(enabled=True, persistent=True))
    service._running = False
    service.manager.start, service.manager.stop = AsyncMock(), AsyncMock()
    service.ensure_ready_channels = AsyncMock(return_value={"feishu": False})
    ready = asyncio.Event()
    attempts = 0

    async def reconcile():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return False
        ready.set()
        return True

    service._reconcile_work_channel = reconcile
    await service.start()
    try:
        await asyncio.wait_for(ready.wait(), 2)
        assert attempts >= 3
    finally:
        await service.stop()
    assert service._work_coordinator is None
