"""Real request policy combined with enterprise upload and preview paths."""

import asyncio
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from _router_auth_helpers import call_unwrapped
from fastapi import HTTPException, UploadFile
from langgraph.errors import GraphBubbleUp
from starlette.requests import Request

from app.gateway import sandbox_authz as gate
from app.gateway.routers import artifacts, uploads
from app.gateway.sandbox_requests import SandboxRequestSession
from deerflow.config.authorization_config import AuthorizationConfig, AuthorizationProviderConfig
from deerflow.sandbox.exceptions import SandboxAuthorizationError


def _config(*, allow=False, enabled=True, fail_closed=True):
    return SimpleNamespace(
        authorization=AuthorizationConfig(
            enabled=enabled,
            fail_closed=fail_closed,
            provider=AuthorizationProviderConfig(
                use="deerflow.authz.rbac:RbacAuthorizationProvider",
                config={"roles": {"user": {"sandbox": {"allow": allow}}, "admin": {"sandbox": {"allow": True}}}},
            ),
        )
    )


def _request(*, role="user", source="session", user_id="viewer", headers=()):
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": list(headers), "query_string": b"user_role=admin"})
    request.state.user = SimpleNamespace(id=user_id, system_role=role, is_disabled=False, oauth_provider="local", oauth_id="actor-oauth")
    request.state.auth_source = source
    request.state.authz_attributes = {"admin": True}  # Not a sanctioned producer.
    return request


@pytest.fixture
def policy(monkeypatch):
    config = _config()
    monkeypatch.setattr("deerflow.config.get_app_config", lambda: config)
    return config


@pytest.mark.parametrize("fail_closed", [True, False])
def test_deny_uses_actor_not_internal_header_or_query(policy, fail_closed):
    policy.authorization.fail_closed = fail_closed
    request = _request(headers=((b"x-deerflow-owner-user-id", b"admin-owner"), (b"x-deerflow-internal-token", b"fake")))
    with pytest.raises(SandboxAuthorizationError):
        asyncio.run(gate.authorize_sandbox_for_request(request))


def test_request_context_is_server_owned(monkeypatch, policy):
    capture = AsyncMock()
    monkeypatch.setattr(gate, "authorize_sandbox_execution_async", capture)
    asyncio.run(gate.authorize_sandbox_for_request(_request()))
    assert capture.await_args.kwargs["context"] == {
        "user_id": "viewer",
        "user_role": "user",
        "oauth_provider": "local",
        "oauth_id": "actor-oauth",
        "is_internal": False,
    }


@pytest.mark.parametrize("source", ["session", None, "internal"])
def test_internal_role_string_does_not_establish_internal_trust(monkeypatch, policy, source):
    capture = AsyncMock()
    monkeypatch.setattr(gate, "authorize_sandbox_execution_async", capture)
    request = _request(role="internal", source=source)
    if source is None:
        with pytest.raises(SandboxAuthorizationError):
            asyncio.run(gate.authorize_sandbox_for_request(request))
        capture.assert_not_awaited()
    else:
        asyncio.run(gate.authorize_sandbox_for_request(request))
        context = capture.await_args.kwargs["context"]
        assert context["is_internal"] is (source == "internal")
        assert context["user_role"] == (None if source == "internal" else "internal")


@pytest.mark.parametrize("owner_state", ["active", "disabled", "missing", "other", "error"])
def test_internal_owner_lookup_cannot_fall_back_to_more_privileged_default(monkeypatch, policy, owner_state):
    policy.authorization.default_role = "admin"
    request = _request(role="internal", source="internal", headers=((b"x-deerflow-owner-user-id", b"viewer"),))
    owner = SimpleNamespace(id="other" if owner_state == "other" else "viewer", system_role="user", is_disabled=owner_state == "disabled")
    lookup = AsyncMock(return_value=None if owner_state == "missing" else owner)
    if owner_state == "error":
        lookup.side_effect = RuntimeError("SECRET_DATABASE_VALUE")
    monkeypatch.setattr(gate, "get_local_provider", lambda: SimpleNamespace(get_user=lookup))
    with pytest.raises(SandboxAuthorizationError) as exc:
        asyncio.run(gate.authorize_sandbox_for_request(request))
    assert "SECRET" not in str(exc.value)
    lookup.assert_awaited_once_with("viewer")


@pytest.mark.parametrize("kind", ["none", "missing", "mock", "read_error", "anonymous"])
def test_unknown_config_or_identity_never_becomes_disabled(monkeypatch, policy, kind):
    config = {"none": None, "missing": SimpleNamespace(), "mock": MagicMock()}.get(kind, policy)
    if kind == "read_error":
        monkeypatch.setattr("deerflow.config.get_app_config", MagicMock(side_effect=ValueError("SECRET_CONFIG")))
    else:
        monkeypatch.setattr("deerflow.config.get_app_config", lambda: config)
    request = Request({"type": "http", "headers": []}) if kind == "anonymous" else _request()
    with pytest.raises(SandboxAuthorizationError) as exc:
        asyncio.run(gate.authorize_sandbox_for_request(request))
    assert "SECRET" not in str(exc.value)


def test_disabled_does_not_resolve_identity_or_provider(monkeypatch, policy):
    policy.authorization.enabled = False
    lookup = AsyncMock(side_effect=AssertionError("should not resolve"))
    monkeypatch.setattr(gate, "get_current_user_from_request", lookup)
    asyncio.run(gate.authorize_sandbox_for_request(None))
    lookup.assert_not_awaited()


@pytest.mark.parametrize("fail_closed", [True, False])
def test_provider_failure_obeys_explicit_operator_policy(policy, fail_closed):
    policy.authorization.fail_closed = fail_closed
    policy.authorization.provider.use = "module_does_not_exist:Provider"
    if fail_closed:
        with pytest.raises(SandboxAuthorizationError):
            asyncio.run(gate.authorize_sandbox_for_request(_request()))
    else:
        asyncio.run(gate.authorize_sandbox_for_request(_request()))


@pytest.mark.parametrize("error", [asyncio.CancelledError, GraphBubbleUp])
def test_control_flow_is_not_policy_failure(monkeypatch, policy, error):
    monkeypatch.setattr(gate, "get_current_user_from_request", AsyncMock(side_effect=error))
    with pytest.raises(error):
        asyncio.run(gate.authorize_sandbox_for_request(_request()))


@pytest.fixture
def upload_runtime(monkeypatch, tmp_path, policy):
    provider = MagicMock()
    provider.uses_thread_data_mounts = False
    provider.acquire_async = AsyncMock(return_value="sandbox")
    monkeypatch.setattr(uploads, "ensure_uploads_dir", lambda *a, **k: tmp_path)
    monkeypatch.setattr(uploads, "get_sandbox_provider", lambda: provider)
    monkeypatch.setattr(uploads, "conversation_document_parser_endpoint", lambda: None)
    monkeypatch.setattr(uploads, "_auto_convert_documents_enabled", lambda config: True)
    monkeypatch.setattr(uploads, "_require_parsed_text_enabled", lambda: False)
    monkeypatch.setattr(uploads, "convert_file_to_markdown", AsyncMock(return_value=None))
    return provider, tmp_path


@pytest.mark.parametrize("mounted", [False, True])
@pytest.mark.parametrize("filename", ["note.txt", "legacy.doc"])
def test_denied_upload_persists_without_sync_or_legacy_execution(upload_runtime, policy, mounted, filename):
    provider, directory = upload_runtime
    provider.uses_thread_data_mounts = mounted
    result = asyncio.run(call_unwrapped(uploads.upload_files, "thread", request=_request(), files=[UploadFile(filename=filename, file=BytesIO(b"test-upload"))], config=policy))
    assert result.success
    assert (directory / filename).read_bytes() == b"test-upload"
    provider.acquire_async.assert_not_awaited()
    provider.get.assert_not_called()


def test_denied_legacy_fallback_preserves_required_parse_failure(upload_runtime, policy, monkeypatch):
    provider, directory = upload_runtime
    provider.uses_thread_data_mounts = True
    monkeypatch.setattr(uploads, "_require_parsed_text_enabled", lambda: True)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(call_unwrapped(uploads.upload_files, "thread", request=_request(), files=[UploadFile(filename="legacy.doc", file=BytesIO(b"doc"))], config=policy))
    assert exc.value.status_code == 503
    assert not list(directory.iterdir())
    provider.acquire_async.assert_not_awaited()


def test_allowed_upload_sync_still_uses_scoped_storage_owner(upload_runtime, policy):
    policy.authorization = _config(allow=True).authorization
    provider, _ = upload_runtime
    result = asyncio.run(call_unwrapped(uploads.upload_files, "thread", request=_request(), files=[UploadFile(filename="note.txt", file=BytesIO(b"test"))], config=policy))
    assert result.success
    provider.acquire_async.assert_awaited_once_with("thread", user_id="test-user-autouse")
    provider.get.return_value.update_file.assert_called_once_with("/mnt/user-data/uploads/note.txt", b"test")


def test_direct_legacy_fallback_rechecks_even_existing_client(upload_runtime, policy):
    provider, directory = upload_runtime
    sandbox = MagicMock()
    session = SandboxRequestSession(_request(), provider, "thread", "other-owner")
    session._lease = SimpleNamespace(sandbox=sandbox, release=AsyncMock())

    async def run():
        async with session:
            return await uploads._convert_legacy_doc_via_sandbox(sandbox_session=session, source_path=directory / "legacy.doc")

    assert asyncio.run(run()) is None
    assert not sandbox.mock_calls
    provider.acquire_async.assert_not_awaited()


@pytest.mark.parametrize("runner", [False, True])
@pytest.mark.parametrize("write", [False, True])
def test_preview_mutation_gate_precedes_discovery_and_runner_forward(monkeypatch, policy, runner, write):
    monkeypatch.delenv("DEER_FLOW_RUNTIME_PROFILE", raising=False)
    monkeypatch.setattr(artifacts, "_resolve_artifact_owner_user_id", AsyncMock(return_value="admin-owner"))
    monkeypatch.setattr(artifacts, "_resolve_thread_runtime_profile", AsyncMock(return_value=artifacts.PPT_MASTER_RUNTIME_PROFILE if runner else "default"))
    forwarded = AsyncMock(return_value="runner-response")
    monkeypatch.setattr(artifacts, "_forward_live_preview_to_runner", forwarded)
    provider = MagicMock()
    provider.get_for_thread.return_value = None
    monkeypatch.setattr(artifacts, "get_sandbox_provider", lambda: provider)
    call = artifacts._proxy_live_preview("thread", artifacts.LIVE_PREVIEW_PORT, "", _request(), user_can_write=write)
    if not write and runner:
        assert asyncio.run(call) == "runner-response"
        forwarded.assert_awaited_once()
    else:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(call)
        assert exc.value.status_code == (403 if write else 404)
    if write or runner:
        provider.get_for_thread.assert_not_called()
    else:
        provider.get_for_thread.assert_called_once_with("thread", user_id="admin-owner")
    if write:
        forwarded.assert_not_awaited()
