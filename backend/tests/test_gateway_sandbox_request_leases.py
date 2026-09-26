"""Request + real lease manager cancellation/lifecycle integration."""

import asyncio
import threading
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from _router_auth_helpers import call_unwrapped
from fastapi import HTTPException, UploadFile
from langgraph.errors import GraphBubbleUp

from app.gateway import sandbox_requests
from app.gateway.routers import uploads
from app.gateway.sandbox_requests import SandboxRequestSession
from deerflow.sandbox.exceptions import SandboxAuthorizationError
from deerflow.sandbox.lease import discard_sandbox_lease_manager, get_sandbox_lease_manager

pytestmark = pytest.mark.usefixtures("disabled_sandbox_authorization")


@pytest.fixture
def provider():
    provider = MagicMock()
    provider.uses_thread_data_mounts = False
    provider.acquire_async = AsyncMock(return_value="shared")
    provider.get.return_value = MagicMock(id="shared")
    try:
        yield provider
    finally:
        discard_sandbox_lease_manager(provider)


def test_lazy_and_borrowed_request_does_not_park_client(provider):
    async def run():
        async with SandboxRequestSession(None, provider, "thread", "owner"):
            pass
        provider.acquire_async.assert_not_awaited()
        session = SandboxRequestSession(None, provider, "thread", "owner")
        async with session:
            first = await session.acquire()
            assert await session.acquire() is first
            owner = session._lease.owner_id
            assert get_sandbox_lease_manager(provider).binding_for(owner) == "shared"
        assert get_sandbox_lease_manager(provider).binding_for(owner) is None
        provider.acquire_async.assert_awaited_once_with("thread", user_id="owner")
        provider.release.assert_not_called()
        first.release_command_scope.assert_called_once_with(owner)
        with pytest.raises(RuntimeError, match="closed"):
            await session.acquire()
        with pytest.raises(RuntimeError, match="no client lease"):
            await session.run_sync(lambda: None)

    asyncio.run(run())


def test_denied_fallback_reuse_keeps_holder_until_request_cleanup(provider, monkeypatch):
    check = AsyncMock(side_effect=[None, SandboxAuthorizationError()])
    monkeypatch.setattr(sandbox_requests, "authorize_sandbox_for_request", check)

    async def run():
        session = SandboxRequestSession(None, provider, "thread", "owner")
        async with session:
            await session.acquire()
            owner = session._lease.owner_id
            with pytest.raises(SandboxAuthorizationError):
                await session.acquire()
            assert get_sandbox_lease_manager(provider).binding_for(owner) == "shared"
        assert get_sandbox_lease_manager(provider).binding_for(owner) is None

    asyncio.run(run())
    provider.acquire_async.assert_awaited_once()


@pytest.mark.parametrize("late_failure", [False, True])
def test_real_upload_worker_drains_before_concurrent_agent_release(provider, monkeypatch, tmp_path, late_failure):
    entered, finish, ended = threading.Event(), threading.Event(), threading.Event()
    manager = get_sandbox_lease_manager(provider)
    manager.retain("agent", "shared", thread_id="thread", user_id="owner")

    def update(*_):
        entered.set()
        try:
            assert finish.wait(5), "test did not finish the bounded worker"
            if late_failure:
                raise ValueError("late worker failure")
        finally:
            ended.set()

    provider.get.return_value.update_file.side_effect = update
    monkeypatch.setattr(uploads, "get_sandbox_provider", lambda: provider)
    monkeypatch.setattr(uploads, "get_effective_user_id", lambda: "owner")
    monkeypatch.setattr(uploads, "ensure_uploads_dir", lambda *a, **k: tmp_path)
    monkeypatch.setattr(uploads, "_auto_convert_documents_enabled", lambda config: False)

    async def run():
        task = asyncio.create_task(call_unwrapped(uploads.upload_files, "thread", request=None, files=[UploadFile(filename="note.txt", file=BytesIO(b"hello"))], config=SimpleNamespace()))
        try:
            assert await asyncio.to_thread(entered.wait, 4)
            await manager.release_async("agent")
            provider.release.assert_not_called()
            task.cancel("original-upload-cancel")
            await asyncio.sleep(0.01)
            task.cancel("repeated-upload-cancel")
            await asyncio.sleep(0.01)
            assert not task.done()
            assert not ended.is_set()
            provider.release.assert_not_called()
        finally:
            finish.set()
            with pytest.raises(asyncio.CancelledError, match="original-upload-cancel"):
                await asyncio.wait_for(task, 5)
        assert ended.is_set()
        provider.release.assert_called_once_with("shared")

    asyncio.run(run())


@pytest.mark.parametrize("legacy", [False, True])
def test_upload_failure_releases_request_holder_without_parking(provider, monkeypatch, tmp_path, legacy):
    provider.uses_thread_data_mounts = legacy
    sandbox = provider.get.return_value
    if legacy:
        sandbox.execute_command.side_effect = RuntimeError("office failed")
    else:
        sandbox.update_file.side_effect = RuntimeError("copy failed")
    monkeypatch.setattr(uploads, "get_sandbox_provider", lambda: provider)
    monkeypatch.setattr(uploads, "ensure_uploads_dir", lambda *a, **k: tmp_path)
    monkeypatch.setattr(uploads, "_auto_convert_documents_enabled", lambda config: legacy)
    monkeypatch.setattr(uploads, "_require_parsed_text_enabled", lambda: True)
    monkeypatch.setattr(uploads, "conversation_document_parser_endpoint", lambda: None)
    monkeypatch.setattr(uploads, "convert_file_to_markdown", AsyncMock(return_value=None))
    file = UploadFile(filename="old.doc" if legacy else "note.txt", file=BytesIO(b"hello"))
    with pytest.raises(HTTPException if legacy else RuntimeError):
        asyncio.run(call_unwrapped(uploads.upload_files, "thread", request=None, files=[file], config=SimpleNamespace()))
    sandbox.release_command_scope.assert_called_once()
    provider.release.assert_not_called()
    assert not list(tmp_path.iterdir())


def test_lost_client_still_releases_bound_request(provider, monkeypatch, tmp_path):
    provider.get.return_value = None
    monkeypatch.setattr(uploads, "get_sandbox_provider", lambda: provider)
    monkeypatch.setattr(uploads, "ensure_uploads_dir", lambda *a, **k: tmp_path)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(call_unwrapped(uploads.upload_files, "thread", request=None, files=[UploadFile(filename="note.txt", file=BytesIO(b"hello"))], config=SimpleNamespace()))
    assert exc.value.status_code == 500
    assert not get_sandbox_lease_manager(provider)._bindings_by_owner
    provider.release.assert_not_called()


@pytest.mark.parametrize("error", [RuntimeError, GraphBubbleUp, asyncio.CancelledError])
def test_legacy_acquisition_failure_keeps_optional_fallback_and_control_flow(provider, tmp_path, error):
    provider.acquire_async.side_effect = error("acquire-failed")

    async def run():
        async with SandboxRequestSession(None, provider, "thread", "owner") as session:
            return await uploads._convert_legacy_doc_via_sandbox(sandbox_session=session, source_path=tmp_path / "old.doc")

    if error is RuntimeError:
        assert asyncio.run(run()) is None
    else:
        with pytest.raises(error):
            asyncio.run(run())
    assert not get_sandbox_lease_manager(provider)._bindings_by_owner


def test_legacy_cleanup_drains_without_replacing_first_cancel(provider, tmp_path):
    command_started, command_finish = threading.Event(), threading.Event()
    cleanup_started, cleanup_finish, cleanup_ended = threading.Event(), threading.Event(), threading.Event()
    manager = get_sandbox_lease_manager(provider)
    manager.retain("agent", "shared", thread_id="thread", user_id="owner")

    def command(text, **_):
        if text.startswith("rm -rf -- "):
            cleanup_started.set()
            try:
                assert cleanup_finish.wait(5)
            finally:
                cleanup_ended.set()
        else:
            command_started.set()
            assert command_finish.wait(5)

    provider.get.return_value.execute_command.side_effect = command

    async def convert():
        async with SandboxRequestSession(None, provider, "thread", "owner") as session:
            await uploads._convert_legacy_doc_via_sandbox(sandbox_session=session, source_path=tmp_path / "old.doc")

    async def run():
        task = asyncio.create_task(convert())
        try:
            assert await asyncio.to_thread(command_started.wait, 4)
            await manager.release_async("agent")
            task.cancel("first-conversion-cancel")
            await asyncio.sleep(0.01)
            command_finish.set()
            assert await asyncio.to_thread(cleanup_started.wait, 4)
            task.cancel("second-cleanup-cancel")
            await asyncio.sleep(0.01)
            provider.release.assert_not_called()
            assert not task.done()
            assert not cleanup_ended.is_set()
        finally:
            command_finish.set()
            cleanup_finish.set()
            with pytest.raises(asyncio.CancelledError, match="first-conversion-cancel"):
                await asyncio.wait_for(task, 5)
        assert cleanup_ended.is_set()
        provider.release.assert_called_once_with("shared")

    asyncio.run(run())
