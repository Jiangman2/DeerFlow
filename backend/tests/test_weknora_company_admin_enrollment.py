"""Admin-only discovery, insert-only completion and preservation of existing roles."""

import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

DIRECTORY = Path(__file__).resolve().parents[2] / "deploy/enterprise"


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), DIRECTORY / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def child():
    helpers = load("package-weknora-company-members.py")
    raw = load("package-weknora-company-admin-enrollment.py").build().decode()
    namespace = {"__name__": "admin_test"}
    exec(compile(helpers.literal(raw, "CHILD"), "<synthetic-admin>", "exec"), namespace)
    return SimpleNamespace(**namespace)


def fixture(child):
    p, n, owners = [], [], []
    for t in (10000, 1, 2, 3, 4, 5):
        email = child.EMAIL if t == 10000 else f"private{t}@example.test"
        p.append(dict(id=f"p{t}", email=email, system_role="admin", is_disabled=False, needs_setup=False))
        n.append(dict(id=f"n{t}", email=email, tenant_id=t, is_active=True, is_system_admin=True, can_access_all_tenants=t != 2))
        owners.append(dict(user_id=f"n{t}", tenant_id=t))
    members = [dict(tenant_id=t, role=r) for t, r in ((10000, "admin"), (1, "viewer"), (2, "editor"), (3, "admin"))]
    return [p, n, owners, members]


def test_existing_roles_preserved_and_only_missing_targets(child):
    data = fixture(child)
    result = child.classify(*data)
    assert result["missing"] == [4, 5]
    assert result["roles"] == {10000: "admin", 1: "viewer", 2: "editor", 3: "admin"}
    assert result["counts"]["platform_admin_accounts"] == 6
    assert result["identities"][2][6] is False  # No forced global-tenant privilege change.


@pytest.mark.parametrize("kind", ["duplicate-platform", "duplicate-native", "ordinary", "disabled", "setup", "inactive-native", "not-native-admin", "ambiguous-workspace", "missing-owner", "duplicate-member", "invalid-role"])
def test_unsafe_identity_or_membership_refused(child, kind):
    p, n, owners, members = fixture(child)
    if kind == "duplicate-platform":
        p.append(dict(p[1]))
    elif kind == "duplicate-native":
        n.append(dict(n[1]))
    elif kind == "ordinary":
        p[1]["system_role"] = "user"
    elif kind == "disabled":
        p[1]["is_disabled"] = True
    elif kind == "setup":
        p[1]["needs_setup"] = True
    elif kind == "inactive-native":
        n[1]["is_active"] = False
    elif kind == "not-native-admin":
        n[1]["is_system_admin"] = False
    elif kind == "ambiguous-workspace":
        n[1]["tenant_id"] = 999
        owners.append(dict(user_id=n[1]["id"], tenant_id=77))
    elif kind == "missing-owner":
        members[0]["role"] = "viewer"
    elif kind == "duplicate-member":
        members.append(dict(members[1]))
    else:
        members[1]["role"] = "unknown"
    with pytest.raises(child.Refused):
        child.classify(p, n, owners, members)


def test_missing_native_identity_is_not_created(child):
    p, n, owners, members = fixture(child)
    n.pop()
    result = child.classify(p, n, owners, members)
    assert result["counts"]["not_yet_provisioned"] == 1 and result["missing"] == [4]


def test_multiple_workspaces_selects_owned_default_only(child):
    p, n, owners, members = fixture(child)
    owners.append(dict(user_id=n[1]["id"], tenant_id=77))
    assert 77 not in child.classify(p, n, owners, members)["workspaces"]


class FakeDB:
    def __init__(self, child):
        self.child, self.data, self.reads, self.change = child, fixture(child), 0, None

    async def snapshot(self):
        self.reads += 1
        if self.change:
            self.change(self)
        return self.child.classify(*copy.deepcopy(self.data))


class FakeAPI:
    def __init__(self, db, mode):
        self.db, self.mode, self.calls = db, mode, []

    async def invite(self, tenant):
        assert tenant not in (10000, 1, 2, 3)
        self.calls.append(tenant)
        if self.mode == "missing":
            raise httpx.ReadTimeout("private response secret")
        role = "admin" if self.mode == "concurrent-admin" else "viewer"
        self.db.data[3].append(dict(tenant_id=tenant, role=role))
        if self.mode in ("lost", "concurrent-admin"):
            raise httpx.ReadTimeout("private response secret")


async def authority():
    return None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["success", "lost", "concurrent-admin"])
async def test_completion_and_lost_response_never_repost_or_demote(child, mode):
    db, report, progress = FakeDB(child), child.initial_report(), []
    api = FakeAPI(db, mode)
    await child.complete_memberships(db, api, authority, report, progress.append)
    assert api.calls == [4, 5]
    assert report["status"] == child.COMPLETE and report["admin_flags_preserved"]
    assert report["existing_membership_permissions_preserved"] and report["membership_post_requests"] == 2
    assert report["uncertain_posts_resolved_by_readback"] == (0 if mode == "success" else 2)
    assert "private" not in json.dumps(report) and len(progress) == 6
    repeat = child.initial_report()
    await child.complete_memberships(db, api, authority, repeat, lambda row: None)
    assert api.calls == [4, 5] and repeat["membership_post_requests"] == 0
    assert {r["tenant_id"]: r["role"] for r in db.data[3]}[2] == "editor"


@pytest.mark.asyncio
async def test_unconfirmed_post_stops_without_next_target(child):
    db, report = FakeDB(child), child.initial_report()
    api = FakeAPI(db, "missing")
    with pytest.raises(child.Refused, match="INVITE_NOT_CONFIRMED"):
        await child.complete_memberships(db, api, authority, report, lambda row: None)
    assert api.calls == [4] and report["membership_post_requests"] == 1


@pytest.mark.asyncio
async def test_manual_join_before_write_skips_post_and_retains_editor(child):
    db, report = FakeDB(child), child.initial_report()
    api = FakeAPI(db, "success")

    def manual_join(current):
        if current.reads == 2:
            current.data[3].append(dict(tenant_id=4, role="editor"))

    db.change = manual_join
    await child.complete_memberships(db, api, authority, report, lambda row: None)
    assert api.calls == [5] and report["memberships_preserved_without_post"] == 5


@pytest.mark.asyncio
async def test_identity_drift_blocks_writes(child):
    db, report = FakeDB(child), child.initial_report()
    api = FakeAPI(db, "success")

    def changed(current):
        if current.reads == 2:
            current.data[1][1]["can_access_all_tenants"] = False

    db.change = changed
    with pytest.raises(child.Refused, match="ADMIN_IDENTITY_OR_OWNERSHIP_CHANGED"):
        await child.complete_memberships(db, api, authority, report, lambda row: None)
    assert not api.calls


def test_role_reduction_refused_but_manual_promotion_preserved(child):
    before = child.classify(*fixture(child))
    after = copy.deepcopy(before)
    after["roles"][1] = "editor"
    child.preserve(before, after)
    after["roles"][2] = "viewer"
    with pytest.raises(child.Refused, match="PERMISSION_REDUCED"):
        child.preserve(before, after)


@pytest.mark.asyncio
async def test_provisioning_pending_is_not_claimed_all_complete(child):
    db, report = FakeDB(child), child.initial_report()
    db.data[1].pop()
    api = FakeAPI(db, "success")
    with pytest.raises(child.Refused, match="ADMIN_FIRST_KNOWLEDGE_USE_REQUIRED"):
        await child.complete_memberships(db, api, authority, report, lambda row: None)
    assert api.calls == [4] and report["accounts_created"] == 0
    assert report["final_counts"]["not_yet_provisioned"] == 1 and report["status"] == "INCOMPLETE"


@pytest.mark.asyncio
async def test_native_invite_contract_only_inserts_viewer(child):
    common, seen = child.module(child.COMMON, "native_contract"), []

    def handler(request):
        seen.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"success": True, "message": "added"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        api = common["API"](client, lambda p: "http://native.test/api/v1" + p, child.initial_report())
        await api.invite(4)
    assert seen == [("POST", "/api/v1/organizations/" + child.ORG + "/invite", {"tenant_id": 4, "role": "viewer"})]


def test_host_is_bound_to_successful_gateway387_receipt_and_new_once_path():
    helper = load("package-weknora-company-members.py")
    raw = load("package-weknora-company-admin-enrollment.py").build().decode()
    guard = helper.literal(raw, "GUARD")
    pins = helper.literal(guard, "PINS")
    assert pins["gateway"][1] == 387
    assert helper.literal(guard, "RECEIPTS")[0]["sha256"] == "ab4b4c72b4b132d8588853f52c23e04171a52e27b1256ea727d840c4432602a1"
    assert "portal-weknora-company-admin-enrollment-20261025-r1" in raw
    assert "--enroll-company-admins-once" in raw and "--tasks-drained" not in raw
