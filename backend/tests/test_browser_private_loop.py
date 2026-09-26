"""Browser subprocess support must not depend on the Gateway's loop policy."""

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from deerflow.community.browser_automation import session


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_private_loop_factory_does_not_change_process_policy(monkeypatch, platform):
    default_loop = object()
    subprocess_loop = object()
    default_factory = Mock(return_value=default_loop)
    proactor_factory = Mock(return_value=subprocess_loop)
    monkeypatch.setattr(session, "sys", SimpleNamespace(platform=platform), raising=False)
    # No process-global set_event_loop_policy is available to this fake.
    monkeypatch.setattr(session, "asyncio", SimpleNamespace(new_event_loop=default_factory, ProactorEventLoop=proactor_factory))
    monkeypatch.setattr(session, "threading", SimpleNamespace(Thread=lambda **kwargs: SimpleNamespace(start=lambda: None)))
    private = session._PlaywrightLoopThread()
    assert private._loop is (subprocess_loop if platform == "win32" else default_loop)
    assert proactor_factory.call_count == (1 if platform == "win32" else 0)
    assert default_factory.call_count == (0 if platform == "win32" else 1)


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "win32", reason="real Windows selector/subprocess boundary")
async def test_windows_private_loop_launches_child_even_with_selector_factory(monkeypatch):
    monkeypatch.setattr(session.asyncio, "new_event_loop", asyncio.SelectorEventLoop)
    private = session._PlaywrightLoopThread()

    async def child():
        process = await asyncio.create_subprocess_exec(sys.executable, "-c", "print(1+1)", stdout=asyncio.subprocess.PIPE)
        stdout, _ = await process.communicate()
        return process.returncode, stdout.strip()

    try:
        assert await asyncio.wait_for(private.run(child()), timeout=5) == (0, b"2")
    finally:
        private._loop.call_soon_threadsafe(private._loop.stop)
        await asyncio.to_thread(private._thread.join, 5)
        assert not private._thread.is_alive()
        private._loop.close()
