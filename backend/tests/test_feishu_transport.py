"""Offline WS lifecycle regression: no Work messages or real sockets."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.channels.feishu import FeishuChannel
from app.channels.feishu_transport import handshake_failure, observed_client
from app.channels.message_bus import MessageBus


def test_work_quota_code_in_message_header_is_not_hidden_by_http_500():
    error = RuntimeError("synthetic")
    error.response = SimpleNamespace(status_code=500, headers={"handshake-status": "514", "handshake-msg": "1000040350"})
    failure = handshake_failure(error)
    assert failure.http_status == 500
    assert failure.provider_status == 514
    assert failure.business_code == 1000040350
    assert "1000040350" in str(failure)


@pytest.mark.asyncio
async def test_stop_fences_sdk_tasks_created_after_handshake_gap(monkeypatch):
    import threading

    import lark_oapi as lark
    import lark_oapi.ws.client as ws_module

    entered, proceed = threading.Event(), threading.Event()

    class Client:
        def __init__(self, **kwargs):
            self._conn, self._auto_reconnect = None, True

        async def _disconnect(self):
            pass

        def start(self):
            entered.set()
            if not proceed.wait(5):
                raise RuntimeError("synthetic test barrier timed out")
            # Mimic SDK creating its select task after stop took its snapshot.
            ws_module.loop.run_until_complete(asyncio.Event().wait())

    original_loop = ws_module.loop
    monkeypatch.setattr(lark.ws, "Client", Client)
    channel = FeishuChannel(MessageBus(), {"app_id": "synthetic", "app_secret": "synthetic"})
    try:
        await channel.start()
        assert await asyncio.to_thread(entered.wait, 5)
        stopped = asyncio.create_task(channel.stop())
        await asyncio.sleep(0)
        assert channel._ws_stop_requested.is_set()
        proceed.set()
        await asyncio.wait_for(stopped, 5)
        assert channel._thread is None
    finally:
        proceed.set()
        await channel.stop()
        ws_module.loop = original_loop


@pytest.mark.asyncio
async def test_observed_client_tracks_real_handshake_and_redacts_ticket_urls():
    class Client:
        async def _connect(self):
            self._conn = object()

        async def _disconnect(self):
            self._conn = None

        def _fmt_log(self, fmt, *args):
            return fmt.format(*args)

    owner = FeishuChannel(MessageBus(), {})
    client = observed_client(Client, owner)()
    await client._connect()
    assert owner.transport_connected
    assert "secret-ticket" not in client._fmt_log("connected to {}", "wss://example.test/ws?ticket=secret-ticket&access_key=secret-access")
    await client._disconnect()
    assert not owner.transport_connected


@pytest.mark.asyncio
@pytest.mark.parametrize("connected", [True, False])
async def test_stopping_worker_closes_socket_cancels_tasks_and_joins_thread(monkeypatch, connected):
    import lark_oapi as lark
    import lark_oapi.ws.client as ws_module

    disconnected = []

    class FakeClient:
        def __init__(self, **kwargs):
            self._conn = None
            self._auto_reconnect = True

        async def _connect(self):
            self._conn = object()

        async def _disconnect(self):
            disconnected.append(True)
            self._conn = None

        def _fmt_log(self, fmt, *args):
            return fmt.format(*args)

        def start(self):
            if connected:
                ws_module.loop.run_until_complete(self._connect())
            ws_module.loop.run_until_complete(asyncio.Event().wait())

    monkeypatch.setattr(lark.ws, "Client", FakeClient)
    original_loop = ws_module.loop
    channel = FeishuChannel(MessageBus(), {"app_id": "synthetic", "app_secret": "synthetic"})
    try:
        for _ in range(2):
            await channel.start()
            for _ in range(100):
                if channel.transport_connected or (not connected and channel._ws_loop is not None and channel._ws_loop.is_running()):
                    break
                await asyncio.sleep(0.01)
            assert channel.transport_connected is connected
            worker, worker_loop = channel._thread, channel._ws_loop
            await channel.stop()
            assert not worker.is_alive()
            assert worker_loop.is_closed()
            assert not channel.transport_connected
        assert len(disconnected) >= 2
    finally:
        await channel.stop()
        ws_module.loop = original_loop


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["restart", "ensure", "remove"])
async def test_failed_stop_keeps_old_worker_and_does_not_create_duplicate(monkeypatch, tmp_path, operation):
    from app.channels.service import ChannelService
    from app.channels.store import ChannelStore

    monkeypatch.setattr("app.channels.service.ChannelStore", lambda: ChannelStore(path=tmp_path / "channels.json"))
    service = ChannelService(channels_config={"feishu": {"enabled": True}})
    service._running = True
    old = SimpleNamespace(is_running=False, stop=AsyncMock(side_effect=RuntimeError("synthetic stop timeout")))
    service._channels["feishu"] = old
    service._start_channel = AsyncMock(return_value=True)
    if operation == "restart":
        result = await service.restart_channel("feishu", reload_config=False)
    elif operation == "ensure":
        result = await service.ensure_channel_ready("feishu")
    else:
        result = await service.remove_channel("feishu")
    assert not result
    assert service._channels["feishu"] is old
    service._start_channel.assert_not_awaited()


def test_idle_retry_thread_is_not_reported_as_a_connected_transport():
    channel = FeishuChannel(MessageBus(), {})
    channel._running = True
    channel._thread = MagicMock()
    channel._thread.is_alive.return_value = True
    assert channel.is_running
    assert not channel.transport_connected


@pytest.mark.asyncio
async def test_concurrent_restart_requests_never_stop_the_same_worker_twice(monkeypatch, tmp_path):
    from app.channels.service import ChannelService
    from app.channels.store import ChannelStore

    monkeypatch.setattr("app.channels.service.ChannelStore", lambda: ChannelStore(path=tmp_path / "channels.json"))
    service = ChannelService(channels_config={"feishu": {"enabled": True}})
    entered, release = asyncio.Event(), asyncio.Event()

    async def stop():
        entered.set()
        await release.wait()

    old = SimpleNamespace(stop=AsyncMock(side_effect=stop))
    service._channels["feishu"] = old
    service._start_channel = AsyncMock(return_value=True)
    first = asyncio.create_task(service.restart_channel("feishu", reload_config=False))
    await entered.wait()
    second = asyncio.create_task(service.restart_channel("feishu", reload_config=False))
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert results == [True, True]
    assert old.stop.await_count == 1


def test_provider_health_and_quota_hint_do_not_confuse_running_thread_with_connection(monkeypatch):
    from app.gateway.routers import channel_connections

    state = {"service_running": True, "channels": {"feishu": {"running": True, "connected": False, "transport_business_code": 1000040350}}}
    monkeypatch.setattr("app.channels.service.get_channel_service", lambda: SimpleNamespace(get_status=lambda: state))
    assert channel_connections._runtime_channel_running("feishu") is False
    assert "1000040350" in channel_connections._runtime_not_running_reason("feishu")
    state["channels"]["feishu"].update(connected=True, transport_business_code=None)
    assert channel_connections._runtime_channel_running("feishu") is True


@pytest.mark.asyncio
async def test_failed_start_cleanup_must_finish_before_retry(monkeypatch, tmp_path):
    from app.channels.service import ChannelService
    from app.channels.store import ChannelStore

    monkeypatch.setattr("app.channels.service.ChannelStore", lambda: ChannelStore(path=tmp_path / "channels.json"))
    created = []

    class FailedChannel:
        is_running = False

        def __init__(self, **kwargs):
            created.append(self)

        async def start(self):
            pass

        async def stop(self):
            raise RuntimeError("synthetic shutdown timeout")

    monkeypatch.setattr("deerflow.reflection.resolve_class", lambda *args, **kwargs: FailedChannel)
    service = ChannelService(channels_config={"feishu": {"enabled": True}})
    service._running = True
    assert not await service.ensure_channel_ready("feishu", attempts=2)
    assert len(created) == 1
    assert service._channels["feishu"] is created[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("handshake_ok", [True, False])
async def test_installed_sdk_socket_and_retry_loops_stop_without_real_network(monkeypatch, handshake_ok):
    import lark_oapi.ws.client as ws_module

    attempted = asyncio.Event()
    main_loop = asyncio.get_running_loop()
    closed = []

    class Socket:
        transport = SimpleNamespace(abort=lambda: None)

        async def recv(self):
            await asyncio.Event().wait()

        async def send(self, message):
            pass

        async def close(self):
            closed.append(True)

    async def connect(*args, **kwargs):
        main_loop.call_soon_threadsafe(attempted.set)
        if not handshake_ok:
            raise RuntimeError("synthetic handshake failure")
        return Socket()

    original_loop = ws_module.loop
    monkeypatch.setattr(ws_module.Client, "_get_conn_url", lambda self: "wss://example.test/ws?device_id=synthetic&service_id=1&ticket=synthetic-secret")
    monkeypatch.setattr(ws_module.websockets, "connect", connect)
    channel = FeishuChannel(MessageBus(), {"app_id": "synthetic", "app_secret": "synthetic"})
    try:
        await channel.start()
        await asyncio.wait_for(attempted.wait(), timeout=5)
        worker = channel._thread
        await channel.stop()
        assert not worker.is_alive()
        assert channel._ws_loop.is_closed()
        assert not channel.transport_connected
        assert bool(closed) is handshake_ok
    finally:
        await channel.stop()
        ws_module.loop = original_loop
