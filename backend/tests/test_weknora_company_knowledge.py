import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.gateway import weknora_company_knowledge as company

ORG = "36d52e3c-7ab1-4e28-996e-7981f0d12463"
KB = "ca8d0f80-1ef9-41f5-9b07-8a99ec4c689a"
SESSION = SimpleNamespace(token="user-token-not-admin", tenant={"id": 77})
ORG_DATA = {"id": ORG, "my_role": "", "searchable": True, "require_approval": False, "member_limit": 0}
SHARED = [{"knowledge_base": {"id": KB, "tenant_id": 999}, "organization_id": ORG, "permission": "viewer", "source_tenant_id": 999}]


@pytest.fixture(autouse=True)
def configure(monkeypatch):
    monkeypatch.setenv("WEKNORA_COMPANY_KB_ENABLED", "true")
    monkeypatch.setenv("WEKNORA_COMPANY_KB_ORGANIZATION_IDS", json.dumps([ORG]))


def transport(monkeypatch, responses):
    calls = []

    def handle(request):
        calls.append(request)
        # The owner's/source tenant and any global API key are never used.
        assert request.headers["authorization"] == "Bearer user-token-not-admin"
        assert request.headers["x-tenant-id"] == "77"
        assert "x-api-key" not in request.headers
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        status, data = item
        return httpx.Response(status, json={"success": True, "data": data})

    real_client = httpx.AsyncClient

    def client(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return real_client(**kwargs, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(company.httpx, "AsyncClient", client)
    return calls


async def ensure(*, admin=False, session=SESSION):
    await company.ensure_company_knowledge_access(session, lambda path: "http://weknora/api/v1" + path, administrator=admin)


@pytest.mark.asyncio
async def test_disabled_is_noop_even_with_missing_configuration(monkeypatch):
    monkeypatch.delenv("WEKNORA_COMPANY_KB_ENABLED")
    monkeypatch.delenv("WEKNORA_COMPANY_KB_ORGANIZATION_IDS")
    calls = transport(monkeypatch, [])
    await ensure()
    assert calls == []


@pytest.mark.parametrize(
    "name,value",
    [
        ("WEKNORA_COMPANY_KB_ENABLED", "tru"),
        ("WEKNORA_COMPANY_KB_ORGANIZATION_IDS", '["../private"]'),
        ("WEKNORA_COMPANY_KB_ORGANIZATION_IDS", "[]"),
        ("WEKNORA_COMPANY_KB_ORGANIZATION_IDS", json.dumps([ORG, ORG])),
        ("WEKNORA_COMPANY_KB_ORGANIZATION_IDS", "{}"),
    ],
)
@pytest.mark.asyncio
async def test_bad_configuration_never_calls_weknora(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    calls = transport(monkeypatch, [])
    with pytest.raises(HTTPException, match="COMPANY_KB_CONFIGURATION_INVALID"):
        await ensure()
    assert calls == []


@pytest.mark.asyncio
async def test_new_user_joins_once_and_shared_kb_is_verified(monkeypatch):
    responses = [(200, ORG_DATA), (200, ORG_DATA), (200, {**ORG_DATA, "my_role": "viewer"}), (200, SHARED)]
    calls = transport(monkeypatch, responses)
    await ensure()
    assert [(r.method, r.url.path) for r in calls] == [
        ("GET", f"/api/v1/organizations/{ORG}"),
        ("POST", "/api/v1/organizations/join-by-id"),
        ("GET", f"/api/v1/organizations/{ORG}"),
        ("GET", "/api/v1/shared-knowledge-bases"),
    ]
    assert json.loads(calls[1].content) == {"organization_id": ORG, "role": "viewer"}
    assert responses == []


@pytest.mark.asyncio
async def test_existing_viewer_requires_no_join(monkeypatch):
    calls = transport(monkeypatch, [(200, {**ORG_DATA, "my_role": "viewer"}), (200, SHARED)])
    await ensure()
    assert all(r.method == "GET" for r in calls)


@pytest.mark.parametrize("change", [{"searchable": False}, {"require_approval": True}, {"member_limit": 50}, {"member_limit": False}, {"member_limit": None}, {"id": KB}, {"my_role": "owner"}])
@pytest.mark.asyncio
async def test_invalid_org_is_rejected_before_join(monkeypatch, change):
    calls = transport(monkeypatch, [(200, {**ORG_DATA, **change})])
    with pytest.raises(HTTPException):
        await ensure()
    assert len(calls) == 1 and calls[0].method == "GET"


@pytest.mark.parametrize("role", ["", "editor", "admin"])
@pytest.mark.asyncio
async def test_join_success_without_viewer_readback_is_not_accepted(monkeypatch, role):
    calls = transport(monkeypatch, [(200, ORG_DATA), (200, ORG_DATA), (200, {**ORG_DATA, "my_role": role})])
    with pytest.raises(HTTPException, match="COMPANY_KB_VIEWER_MEMBERSHIP_REQUIRED"):
        await ensure()
    assert len([r for r in calls if r.method == "POST"]) == 1


@pytest.mark.parametrize("confirmed", [True, False])
@pytest.mark.asyncio
async def test_lost_join_response_reads_once_without_resubmission(monkeypatch, confirmed):
    responses = [(200, ORG_DATA), httpx.ReadTimeout("DO_NOT_EXPORT_SECRET"), (200, {**ORG_DATA, "my_role": "viewer" if confirmed else ""})]
    if confirmed:
        responses.append((200, SHARED))
    calls = transport(monkeypatch, responses)
    if confirmed:
        await ensure()
    else:
        with pytest.raises(HTTPException) as exc:
            await ensure()
        assert "DO_NOT_EXPORT_SECRET" not in str(exc.value)
    assert len([r for r in calls if r.method == "POST"]) == 1


@pytest.mark.parametrize("status", [302, 401, 403, 404, 500])
@pytest.mark.asyncio
async def test_join_rejection_never_retries(monkeypatch, status):
    calls = transport(monkeypatch, [(200, ORG_DATA), (status, {"private": "not-exported"})])
    with pytest.raises(HTTPException, match="COMPANY_KB_JOIN_REJECTED"):
        await ensure()
    assert len(calls) == 2


@pytest.mark.parametrize("shared", [[{**SHARED[0], "permission": "editor"}], [{**SHARED[0], "permission": "admin"}]])
@pytest.mark.asyncio
async def test_visibility_requires_the_pinned_readonly_share(monkeypatch, shared):
    transport(monkeypatch, [(200, {**ORG_DATA, "my_role": "viewer"}), (200, shared)])
    with pytest.raises(HTTPException, match="COMPANY_KB_READONLY_SHARE_REQUIRED"):
        await ensure()


@pytest.mark.parametrize("admin", [True, False])
@pytest.mark.asyncio
async def test_existing_maintainer_is_never_demoted_or_silently_granted_to_regular_user(monkeypatch, admin):
    responses = [] if admin else [(200, {**ORG_DATA, "my_role": "admin"})]
    calls = transport(monkeypatch, responses)
    if admin:
        await ensure(admin=True)
    else:
        with pytest.raises(HTTPException, match="COMPANY_KB_VIEWER_MEMBERSHIP_REQUIRED"):
            await ensure()
    assert all(r.method == "GET" for r in calls)


@pytest.mark.asyncio
async def test_admin_can_repair_misconfigured_policy(monkeypatch):
    monkeypatch.setenv("WEKNORA_COMPANY_KB_ORGANIZATION_IDS", "invalid")
    calls = transport(monkeypatch, [])
    await ensure(admin=True)
    assert calls == []


@pytest.mark.parametrize("shared", [[], SHARED + [{**SHARED[0], "knowledge_base": {"id": ORG}}]])
@pytest.mark.asyncio
async def test_empty_space_and_future_kbs_do_not_require_configuration_changes(monkeypatch, shared):
    calls = transport(monkeypatch, [(200, {**ORG_DATA, "my_role": "viewer"}), (200, shared)])
    await ensure()
    assert all(r.method == "GET" for r in calls)


@pytest.mark.asyncio
async def test_multiple_opted_in_spaces_and_unrelated_private_shares(monkeypatch):
    second = "ca8d0f80-1ef9-41f5-9b07-8a99ec4c689b"
    monkeypatch.setenv("WEKNORA_COMPANY_KB_ORGANIZATION_IDS", json.dumps([ORG, second]))
    calls = transport(
        monkeypatch,
        [
            (200, {**ORG_DATA, "my_role": "viewer"}),
            (200, {**ORG_DATA, "id": second, "my_role": "viewer"}),
            (200, SHARED + [{"organization_id": "private-not-selected", "permission": "editor"}]),
        ],
    )
    await ensure()
    assert [r.url.path for r in calls] == [f"/api/v1/organizations/{ORG}", f"/api/v1/organizations/{second}", "/api/v1/shared-knowledge-bases"]


@pytest.mark.parametrize("data", ["secret-response", None, [], {"unexpected": "secret-response"}])
@pytest.mark.asyncio
async def test_bad_protocol_does_not_export_raw_data(monkeypatch, data):
    transport(monkeypatch, [(200, data)])
    with pytest.raises(HTTPException) as exc:
        await ensure()
    assert "secret-response" not in str(exc.value)


@pytest.mark.asyncio
async def test_transport_errors_are_sanitized(monkeypatch):
    transport(monkeypatch, [httpx.ConnectError("secret-provider-error")])
    with pytest.raises(HTTPException, match="COMPANY_KB_TRANSPORT_FAILED") as exc:
        await ensure()
    assert "secret-provider-error" not in str(exc.value)


@pytest.mark.asyncio
async def test_oversize_response_is_rejected(monkeypatch):
    monkeypatch.setattr(company, "_MAX_RESPONSE_BYTES", 16)
    transport(monkeypatch, [(200, ORG_DATA)])
    with pytest.raises(HTTPException, match="COMPANY_KB_RESPONSE_TOO_LARGE"):
        await ensure()


@pytest.mark.asyncio
async def test_end_to_end_deadline_cancels_request(monkeypatch):
    import asyncio

    async def request(*args, **kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(company, "_DEADLINE_SECONDS", 0.01)
    monkeypatch.setattr(company, "_request", request)
    with pytest.raises(HTTPException, match="COMPANY_KB_TRANSPORT_FAILED"):
        await ensure()


@pytest.mark.asyncio
async def test_identity_bridge_does_not_cache_failed_grant(monkeypatch):
    from app.gateway import weknora_identity as identity

    client_type = httpx.AsyncClient
    native_calls = []

    def handle(request):
        native_calls.append(request.url.path)
        if request.url.path.endswith("/auth/register"):
            return httpx.Response(400)
        return httpx.Response(
            200,
            json={
                "success": True,
                "token": SESSION.token,
                "refresh_token": "refresh",
                "user": {"id": "native-user"},
                "active_tenant": {"id": 77},
                "memberships": [{"tenant_id": 77, "role": "owner"}],
            },
        )

    monkeypatch.setattr(identity.httpx, "AsyncClient", lambda **kwargs: client_type(**kwargs, transport=httpx.MockTransport(handle)))
    monkeypatch.setattr(identity, "_session_cache", {})
    monkeypatch.setattr(identity, "_sso_secret", lambda: "synthetic-secret")
    monkeypatch.setattr(identity, "weknora_api_url", lambda: "http://weknora")
    monkeypatch.setattr(identity, "weknora_timeout", lambda: 5)
    monkeypatch.setattr(identity, "_session_cache_ttl", lambda: 900)
    grant_attempts = []

    async def grant(session, endpoint, *, administrator):
        assert session.token == SESSION.token
        assert endpoint("/organizations") == "http://weknora/api/v1/organizations"
        assert administrator is False
        grant_attempts.append(session)
        if len(grant_attempts) == 1:
            raise company._unavailable("COMPANY_KB_JOIN_UNCONFIRMED")

    async def synced(user, session):
        return session

    async def noop(*args):
        pass

    monkeypatch.setattr(identity, "sync_weknora_role", synced)
    monkeypatch.setattr(identity, "ensure_company_knowledge_access", grant)
    monkeypatch.setattr(identity, "ensure_weknora_default_models", noop)
    monkeypatch.setattr(identity, "ensure_weknora_default_mineru", noop)
    monkeypatch.setattr(identity, "ensure_weknora_default_web_search", noop)
    user = SimpleNamespace(id="platform-user", email="test@example.com", system_role="user")
    with pytest.raises(HTTPException, match="COMPANY_KB_JOIN_UNCONFIRMED"):
        await identity.ensure_weknora_session_for_deerflow_user(user)
    assert not identity._session_cache
    first = await identity.ensure_weknora_session_for_deerflow_user(user)
    second = await identity.ensure_weknora_session_for_deerflow_user(user)
    assert first is second
    assert len(grant_attempts) == 2
    assert native_calls == ["/api/v1/auth/register", "/api/v1/auth/login"] * 2
