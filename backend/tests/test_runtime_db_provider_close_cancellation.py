"""Every SQL-backed provider must retain its resource through cancelled exit."""

import asyncio
import sys
from types import ModuleType, SimpleNamespace

import pytest

from deerflow.runtime.checkpointer import async_provider as checkpointer_provider
from deerflow.runtime.store import async_provider as store_provider


class _Resource:
    serde = object()

    def __init__(self):
        self.exit_started = asyncio.Event()
        self.allow_exit = asyncio.Event()
        self.exit_finished = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _tb):
        self.exit_started.set()
        await self.allow_exit.wait()
        self.exit_finished.set()
        return False

    async def setup(self):
        return None


def _install_module(monkeypatch, name, **attributes):
    module = ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)


async def _assert_drained(context, resource):
    entered = asyncio.Event()
    leave = asyncio.Event()

    async def owner():
        async with context:
            entered.set()
            await leave.wait()

    task = asyncio.create_task(owner())
    entered_wait = asyncio.create_task(entered.wait())
    try:
        done, _ = await asyncio.wait({entered_wait, task}, timeout=1, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            # Show a factory error directly instead of disguising it as a
            # teardown timeout and leaving an unobserved task exception.
            await task
        assert entered_wait in done, "provider never entered its resource"
        leave.set()
        await asyncio.wait_for(resource.exit_started.wait(), timeout=1)
        task.cancel()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        resource.allow_exit.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert resource.exit_finished.is_set()
    finally:
        resource.allow_exit.set()
        if not task.done():
            task.cancel()
        entered_wait.cancel()
        await asyncio.gather(task, entered_wait, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("unified", [False, True])
async def test_sqlite_checkpointer_exit_drains(monkeypatch, unified):
    resource = _Resource()

    class _Saver:
        @classmethod
        def from_conn_string(cls, _conn_str):
            return resource

    _install_module(monkeypatch, "langgraph.checkpoint.sqlite.aio", AsyncSqliteSaver=_Saver)
    config = SimpleNamespace(backend="sqlite", checkpointer_sqlite_path=":memory:") if unified else SimpleNamespace(type="sqlite", connection_string=":memory:")
    context = checkpointer_provider._async_checkpointer_from_database(config) if unified else checkpointer_provider._async_checkpointer(config)
    await _assert_drained(context, resource)


@pytest.mark.asyncio
@pytest.mark.parametrize("unified", [False, True])
async def test_postgres_checkpointer_exit_drains(monkeypatch, unified):
    resource = _Resource()

    class _Saver(_Resource):
        def __init__(self, *, conn):
            assert conn is resource

    def build_pool(url, schema):
        assert url == "postgresql://example/db"
        assert schema == ""
        return resource

    monkeypatch.setattr(checkpointer_provider, "_build_postgres_pool", build_pool)
    monkeypatch.setattr(checkpointer_provider, "_ensure_postgres_imports", lambda: (_Saver, object()))
    config = SimpleNamespace(backend="postgres", postgres_url="postgresql://example/db", postgres_schema="") if unified else SimpleNamespace(type="postgres", connection_string="postgresql://example/db", postgres_schema="")
    context = checkpointer_provider._async_checkpointer_from_database(config) if unified else checkpointer_provider._async_checkpointer(config)
    await _assert_drained(context, resource)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_store_exit_drains(monkeypatch, backend):
    resource = _Resource()

    class _Store:
        @classmethod
        def from_conn_string(cls, _conn_str):
            return resource

    if backend == "sqlite":
        _install_module(monkeypatch, "langgraph.store.sqlite.aio", AsyncSqliteStore=_Store)
        config = SimpleNamespace(type="sqlite", connection_string=":memory:")
    else:
        _install_module(monkeypatch, "langgraph.store.postgres.aio", AsyncPostgresStore=_Store)
        config = SimpleNamespace(type="postgres", connection_string="postgresql://example/db", postgres_schema="")
    await _assert_drained(store_provider._async_store(config), resource)
