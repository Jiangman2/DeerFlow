import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.gateway.weknora_identity import WeKnoraSession

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "deploy/enterprise" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def canary():
    return load("verify-weknora-company-access-child")


@pytest.mark.parametrize("case", ["new", "existing", "elevated", "not_visible", "detail_editor", "owner_workspace", "post_timeout"])
@pytest.mark.asyncio
async def test_real_candidate_and_frozen_session_contract(canary, monkeypatch, case):
    policy = canary.load_module("canary_test_policy", (ROOT / "backend/app/gateway/weknora_company_knowledge.py").read_text(encoding="utf-8"))
    helpers = load("bootstrap-weknora-company-knowledge-child")
    # Exercise the actual frozen dataclass returned by production normalization.
    session = WeKnoraSession(token="ordinary-secret", refresh_token="refresh-secret", user={"id": "ordinary-user"}, tenant={"id": 77}, memberships=[{"tenant_id": 77, "role": "owner"}])
    if case == "owner_workspace":
        session = WeKnoraSession(token=session.token, refresh_token=session.refresh_token, user=session.user, tenant={"id": 10000}, memberships=[{"tenant_id": 10000, "role": "owner"}])
    role = "viewer" if case == "existing" else "editor" if case == "elevated" else ""
    calls = []

    def response(request):
        nonlocal role
        assert request.headers["authorization"] == "Bearer ordinary-secret"
        assert request.headers["x-tenant-id"] == "77"
        calls.append(request)
        path = request.url.path.removeprefix("/api/v1")
        if request.method == "POST":
            assert path == "/organizations/join-by-id"
            assert json.loads(request.content) == {"organization_id": canary.ORG, "role": "viewer"}
            role = "viewer"
            if case == "post_timeout":
                raise httpx.ReadTimeout("DO_NOT_EXPORT")
            data = {}
        elif path == "/organizations/" + canary.ORG:
            data = {"id": canary.ORG, "my_role": role, "searchable": True, "require_approval": False, "member_limit": 0}
        elif path == "/organizations/" + canary.ORG + "/shares":
            data = {"shares": [{"knowledge_base_id": canary.KB, "organization_id": canary.ORG, "permission": "viewer", "my_role_in_org": role, "my_permission": role}]}
        elif path == "/shared-knowledge-bases":
            data = [] if case == "not_visible" else [{"knowledge_base": {"id": canary.KB}, "organization_id": canary.ORG, "permission": "viewer"}]
        elif path == "/knowledge-bases/" + canary.KB:
            data = {"id": canary.KB, "tenant_id": 10000, "my_permission": "editor" if case == "detail_editor" else "viewer"}
        else:
            pytest.fail("Unexpected native path")
        return httpx.Response(200, json={"success": True, "data": data})

    real_client = httpx.AsyncClient

    def client(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return real_client(**kwargs, transport=httpx.MockTransport(response))

    monkeypatch.setattr(policy.httpx, "AsyncClient", client)
    monkeypatch.setenv("WEKNORA_COMPANY_KB_ENABLED", "false")
    monkeypatch.setenv("WEKNORA_COMPANY_KB_ORGANIZATION_IDS", "[]")
    report, progress = {"join_requests": 0, "resource_write_requests": 0}, []
    async with client(trust_env=False, follow_redirects=False, timeout=5) as connection:
        api = helpers.NativeAPI(connection, lambda path: "http://weknora/api/v1" + path, report, progress.append)
        if case in {"new", "existing", "post_timeout"}:
            await canary.exercise_access(policy, helpers, api, session, report)
            assert report["effective_permission"] == "viewer"
            assert report["repeated_access_no_additional_join"] is True
        else:
            with pytest.raises(Exception):
                await canary.exercise_access(policy, helpers, api, session, report)
            assert report.get("status") != "ORDINARY_VIEWER_CANDIDATE_ACCEPTED_NOT_DEPLOYED"
    assert sum(request.method == "POST" for request in calls) <= 1
    assert "ordinary-secret" not in json.dumps([report, progress])
    assert session.tenant["id"] == (10000 if case == "owner_workspace" else 77)


def test_inventory_groups_existing_unprovisioned_and_elevated_accounts():
    module = load("inspect-weknora-company-members-child")
    users = [{"id": str(n), "email": f"u{n}@example.com", "system_role": "admin" if n == 0 else "user"} for n in range(8)]
    native = [{"id": f"native-{n}", "email": f"u{n}@example.com", "tenant_id": n, "is_active": n != 4, "is_system_admin": False, "can_access_all_tenants": False} for n in range(7)]
    owners = [{"user_id": f"native-{n}", "tenant_id": n} for n in range(6)]
    roles = [{"tenant_id": 1, "role": "viewer"}, {"tenant_id": 2, "role": "editor"}]
    result = module.classify(users, native, owners, roles)
    assert result["counts"] == {
        "eligible_platform_users": 7,
        "platform_admins_preserved": 1,
        "not_yet_provisioned": 1,
        "native_identity_not_ordinary_active": 1,
        "ambiguous_native_identity": 0,
        "no_owned_workspace": 1,
        "multiple_owned_workspaces": 0,
        "existing_viewer_users": 1,
        "existing_elevated_membership_users_preserved": 1,
        "users_to_enroll": 2,
    }
    assert result["workspace_memberships_to_add"] == 2
    assert result["candidate_inventory_sha256"] == module.classify(users[::-1], native[::-1], owners[::-1], roles[::-1])["candidate_inventory_sha256"]
    assert "@example.com" not in json.dumps(result)


def test_inventory_ambiguous_account_or_workspace_cannot_be_granted():
    module = load("inspect-weknora-company-members-child")
    user = {"id": "platform", "email": "one@example.com", "system_role": "user"}
    native = {"id": "native", "email": user["email"], "tenant_id": 1, "is_active": True, "is_system_admin": False, "can_access_all_tenants": False}
    result = module.classify([user], [native, native], [], [])
    assert result["counts"]["ambiguous_native_identity"] == 1
    result = module.classify([user], [native], [{"user_id": "native", "tenant_id": 2}, {"user_id": "native", "tenant_id": 3}], [])
    assert result["counts"]["multiple_owned_workspaces"] == 1
    assert result["workspace_memberships_to_add"] == 0


def test_packaged_host_binds_creation_receipt_and_never_claims_on_change(tmp_path, monkeypatch):
    package = load("package-weknora-company-access")
    raw, evidence = package.build()
    namespace = {"__name__": "viewer_host_test"}
    exec(compile(raw, "<packaged>", "exec"), namespace)
    assert namespace["DIRECTORY"].name == "portal-weknora-company-viewer-canary-20261024-r1"
    assert evidence["creation_receipt"]["report_sha256"] == "876963431324e6fc146aad8de65fbb19eadeabf93a69fcf1a5f6f7b9a6151879"
    child_namespace = {"__name__": "viewer_child_test"}
    exec(compile(namespace["CHILD"], "<child>", "exec"), child_namespace)
    assert child_namespace["EMAIL"] == "bianqy@cnooc.com.cn"
    assert child_namespace["ORG"] == evidence["creation_receipt"]["organization_id"]
    monkeypatch.setattr(namespace["socket"], "gethostname", lambda: "datax-01")
    monkeypatch.setattr(namespace["os"], "geteuid", lambda: 0, raising=False)
    namespace["Path"] = lambda path: tmp_path / "missing-report"
    with pytest.raises(namespace["Refused"], match="CREATION_RECEIPT_REQUIRED"):
        namespace["execute"]({})
    assert not (tmp_path / "missing-report").exists()


@pytest.mark.asyncio
async def test_canary_rejects_platform_admin_before_native_login(canary, monkeypatch):
    import asyncpg

    import deerflow.config

    class Connection:
        def transaction(self, *, readonly):
            assert readonly
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def fetch(self, sql, email):
            assert email == "bianqy@cnooc.com.cn"
            return [{"id": "wrong-admin", "system_role": "admin", "is_disabled": False, "needs_setup": False}]

        async def close(self):
            pass

    async def connect(*args, **kwargs):
        return Connection()

    monkeypatch.setattr(asyncpg, "connect", connect)
    monkeypatch.setattr(deerflow.config, "get_app_config", lambda: SimpleNamespace(database=SimpleNamespace(backend="postgres", postgres_url="postgresql://synthetic")))
    with pytest.raises(canary.Refused, match="ACTIVE_ORDINARY_USER_REQUIRED"):
        await canary.verification_user()
