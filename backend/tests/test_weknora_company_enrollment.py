"""Native bulk writes, fresh authority, uncertain outcomes and host receipts."""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

DIRECTORY = Path(__file__).resolve().parents[2] / "deploy/enterprise"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, DIRECTORY / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@pytest.fixture
def package():
    raw = load("package-weknora-company-enrollment.py", "bulk_package").build().decode()
    helpers = load("package-weknora-company-members.py", "bulk_helpers")
    return raw, helpers


@pytest.fixture
def child(package, monkeypatch):
    raw, helpers = package
    scope = {"__name__": "bulk_test_child"}
    exec(compile(helpers.literal(raw, "CHILD"), "<bulk-test>", "exec"), scope)
    m = SimpleNamespace(**scope)
    monkeypatch.setitem(scope, "POST_INTERVAL", 0)
    m.scope = scope
    m.classifier = m.module(m.module(m.COLLECTOR, "collector")["CLASSIFIER"], "classifier")
    return m


def target(tenant):
    return {"tenant": tenant, "users": [{"id": "n" + str(tenant), "email": "private" + str(tenant) + "@example.test"}]}


class DB:
    def __init__(self, roles=None):
        self.current = dict(roles or {})
        self.revalidations = 0
        self.rejected = False

    async def revalidate(self, targets, classifier):
        self.revalidations += 1
        if self.rejected:
            raise RuntimeError("synthetic-authority-change")
        return [row for row in targets if row["tenant"] not in self.current], [self.current[row["tenant"]] for row in targets if row["tenant"] in self.current]

    async def roles(self, tenants):
        return {t: self.current[t] for t in tenants if t in self.current}


class Invites:
    def __init__(self, db, fail=None, role="viewer"):
        self.db, self.fail, self.role = db, fail, role
        self.calls = []
        self.active = self.maximum = 0

    async def invite(self, tenant):
        self.calls.append(tenant)
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        try:
            await asyncio.sleep(0)
            if self.fail == "before":
                raise httpx.ReadTimeout("private response secret")
            self.db.current[tenant] = self.role
            if self.fail == "after":
                raise httpx.ReadTimeout("private response secret")
        finally:
            self.active -= 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "after"])
async def test_native_write_readback_without_retry(child, failure):
    db = DB({1: "viewer", 2: "editor", 3: "admin"})
    api = Invites(db, failure)
    report, progress = child.initial_report(), []
    engine = child.Enrollment(api, db, child.classifier, report, progress.append)
    await engine.batch([target(i) for i in range(1, 10)])
    assert api.calls == list(range(4, 10)) and api.maximum <= 2
    assert report["confirmed_viewer_memberships"] == report["membership_post_requests"] == 6
    assert report["concurrent_viewers_preserved"] == 1 and report["concurrent_elevated_preserved"] == 2
    assert report["uncertain_posts_resolved_by_readback"] == (6 if failure else 0)
    assert len(progress) == 1 and "private" not in json.dumps(report)
    assert db.current[2] == "editor" and db.current[3] == "admin"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,role", [("before", "viewer"), (None, "editor")])
async def test_unconfirmed_or_elevated_invitation_stops_next_pair(child, failure, role):
    db = DB()
    api = Invites(db, failure, role)
    report = child.initial_report()
    engine = child.Enrollment(api, db, child.classifier, report, lambda row: None)
    with pytest.raises(child.Refused, match="INVITE_NOT_CONFIRMED"):
        await engine.batch([target(i) for i in range(1, 6)])
    assert api.calls == [1, 2] and report["batches_verified"] == 0


@pytest.mark.asyncio
async def test_no_replay_if_added_member_removed(child):
    db, report = DB(), child.initial_report()
    api = Invites(db)
    engine = child.Enrollment(api, db, child.classifier, report, lambda row: None)
    await engine.batch([target(1)])
    db.current.clear()
    with pytest.raises(child.Refused, match="REPLAY_OF_ATTEMPTED"):
        await engine.batch([target(1)])
    assert api.calls == [1]


@pytest.mark.asyncio
async def test_budget_and_revalidation_fail_before_post(child, monkeypatch):
    db, report = DB(), child.initial_report()
    api = Invites(db)
    engine = child.Enrollment(api, db, child.classifier, report, lambda row: None)
    db.rejected = True
    with pytest.raises(RuntimeError):
        await engine.batch([target(1)])
    assert not api.calls
    db.rejected = False
    monkeypatch.setitem(child.scope, "MAX_POSTS", 0)
    with pytest.raises(child.Refused, match="POST_BUDGET"):
        await engine.batch([target(1)])
    assert not api.calls


def test_packaged_plan_matches_original_and_never_exports_identity(child):
    platform = {"admin@example.test": "admin", "u@example.test": "user", "new@example.test": "user"}
    native = [{"id": "n1", "email": "u@example.test", "tenant_id": 25, "is_active": True, "is_system_admin": False, "can_access_all_tenants": False}]
    owners = [{"user_id": "n1", "tenant_id": 25}]
    inventory = child.classifier["classify"]([{"email": e, "system_role": r} for e, r in platform.items()], native, owners, [])
    targets = child.derive_targets(child.classifier, (platform, native, owners, []), inventory)
    assert targets == [{"tenant": 25, "users": [{"id": "n1", "email": "u@example.test"}]}]
    assert "example.test" not in json.dumps(inventory)
    inventory["candidate_inventory_sha256"] = "wrong"
    with pytest.raises(child.Refused, match="PLAN_MISMATCH"):
        child.derive_targets(child.classifier, (platform, native, owners, []), inventory)


@pytest.mark.asyncio
async def test_native_invite_no_data_envelope_and_exact_viewer_payload(child):
    calls = []

    def transport(request):
        calls.append(request)
        assert request.url.path == "/api/v1/organizations/" + child.ORG + "/invite"
        assert json.loads(request.content) == {"tenant_id": 42, "role": "viewer"}
        assert request.headers["Authorization"] == "Bearer synthetic" and request.headers["X-Tenant-ID"] == "10000"
        return httpx.Response(200, json={"success": True, "message": "Member added successfully"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        api = child.API(client, lambda p: "http://native.invalid/api/v1" + p, child.initial_report())
        api.headers = {"Authorization": "Bearer synthetic", "X-Tenant-ID": "10000"}
        await api.invite(42)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["redirect", "malformed", "false", "oversize"])
async def test_native_errors_do_not_reveal_body(child, mode):
    def transport(request):
        if mode == "redirect":
            return httpx.Response(302, headers={"Location": "https://external.invalid"})
        if mode == "malformed":
            return httpx.Response(200, content=b"not json secret")
        if mode == "oversize":
            return httpx.Response(200, content=b"x" * (4 * 1024 * 1024 + 1))
        return httpx.Response(200, json={"success": False, "message": "secret"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport), follow_redirects=False) as client:
        api = child.API(client, lambda p: "http://native.invalid" + p, child.initial_report())
        with pytest.raises((child.Refused, ValueError)) as error:
            await api.invite(42)
    assert "secret" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["owner_id", "owner_tenant_id", "my_role", "member_limit", "searchable", "require_approval"])
async def test_space_authority_cannot_drift(child, changed):
    data = {"id": child.ORG, "owner_id": "admin", "owner_tenant_id": 10000, "my_role": "admin", "member_limit": 0, "searchable": True, "require_approval": False}
    data[changed] = "wrong"
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"success": True, "data": data}))) as client:
        api = child.API(client, lambda p: "http://native.invalid" + p, child.initial_report())
        with pytest.raises(child.Refused):
            await api.organization("admin")


class ReadConnection:
    def __init__(self, rows):
        self.rows = list(rows)
        self.inside = self.closed = False
        self.sql = []

    def transaction(self, **kwargs):
        assert kwargs == {"readonly": True, "isolation": "repeatable_read"}
        return self

    async def __aenter__(self):
        self.inside = True

    async def __aexit__(self, *args):
        self.inside = False

    async def execute(self, sql):
        assert self.inside and sql == "SET LOCAL statement_timeout = '10s'"

    async def fetchval(self, sql):
        assert self.inside and sql == "SHOW transaction_read_only"
        return "on"

    async def fetch(self, sql, *args, timeout):
        assert self.inside and sql.startswith("SELECT ") and timeout == 10
        self.sql.append(sql)
        return self.rows.pop(0)

    async def close(self, timeout):
        assert not self.inside and timeout == 5
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [None, "disabled", "admin", "wrong_owner", "native_admin", "duplicate_email", "existing_editor"])
async def test_fresh_identity_and_ownership_queries_are_readonly(child, change):
    p_rows = [{"email": "private5@example.test", "system_role": "admin" if change == "admin" else "user"}]
    if change == "disabled":
        p_rows = []
    if change == "duplicate_email":
        p_rows = p_rows * 2
    native = [{"id": "n5", "email": "private5@example.test", "tenant_id": 5, "is_active": True, "is_system_admin": change == "native_admin", "can_access_all_tenants": False}]
    owners = [{"user_id": "n5", "tenant_id": 6 if change == "wrong_owner" else 5}]
    members = [{"tenant_id": 5, "role": "editor"}] if change == "existing_editor" else []
    p, n = ReadConnection([p_rows]), ReadConnection([native, owners, members])

    async def pc():
        return p

    async def nc():
        return n

    db = child.Database(pc, nc)
    if change in {None, "existing_editor"}:
        pending, existing = await db.revalidate([target(5)], child.classifier)
        assert (pending, existing) == (([target(5)], []) if change is None else ([], ["editor"]))
    else:
        with pytest.raises(child.Refused):
            await db.revalidate([target(5)], child.classifier)
    assert p.closed
    if n.sql:
        assert n.closed and "status='active' AND role='owner'" in n.sql[1]


@pytest.mark.parametrize("failure", [False, True])
def test_host_streams_progress_and_retains_final_receipt(package, tmp_path, monkeypatch, failure):
    host = load("enroll-weknora-company-members-host.py", "bulk_host_test")
    monkeypatch.setattr(host, "DIRECTORY", tmp_path)
    schema = "portal-weknora-company-bulk-enrollment.v1"
    code = (
        "import sys,json; assert sys.stdin.buffer.readline()==b'PING\\n'; "
        "print(json.dumps({'phase':'BULK_VIEWER_BATCH_VERIFIED','batches':1,'posts':1,'confirmed_viewers':1}),flush=True); print(json.dumps("
        + repr({"schema": schema, "status": "INCOMPLETE" if failure else host.COMPLETE})
        + "),flush=True); sys.exit("
        + str(int(failure))
        + ")"
    )
    report = {}
    if failure:
        with pytest.raises(host.Refused, match="BULK_INCOMPLETE"):
            host.stream_child([sys.executable, "-I", "-B", "-u", "-c", code], report)
    else:
        host.stream_child([sys.executable, "-I", "-B", "-u", "-c", code], report)
    assert report["last_progress"]["confirmed_viewers"] == 1
    assert report["observation"]["schema"] == schema
    assert (tmp_path / "PROGRESS.jsonl").read_text().count("\n") == 1


def test_package_does_not_modify_historical_sources_or_enable_auto_join(package):
    raw, helpers = package
    child_source = helpers.literal(raw, "CHILD")
    guard_source = helpers.literal(raw, "GUARD")
    assert helpers.literal(guard_source, "CHILD") is None
    assert len(helpers.literal(guard_source, "RECEIPTS")) == 2
    assert "internal_plan=False" not in (DIRECTORY / "inspect-weknora-company-members-child.py").read_text()
    assert 'headers = {"Authorization": "Bearer " + session.token, "X-Tenant-ID": OWNER}' in child_source
    assert "trust_env=False, follow_redirects=False" in child_source
    assert '"auto_enrollment_deployed": False' in child_source
    assert '"POST", "/organizations/" + ORG + "/invite"' in child_source


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["success", "new_workspace", "budget", "admin_revoked", "login_failed"])
async def test_complete_coordinator_and_mutation_boundaries(child, monkeypatch, scenario):
    from app import gateway
    from deerflow import config

    db = DB()
    authority_reads, scans, posts = [], [], []
    original_module = child.module

    async def admin():
        return {"id": "platform-admin"}

    async def collect(report):
        scans.append(True)
        size = 4 if scenario == "new_workspace" and len(scans) > 1 else 3
        p = {"private" + str(i) + "@example.test": "user" for i in range(1, size + 1)}
        n = [{"id": "n" + str(i), "email": "private" + str(i) + "@example.test", "tenant_id": i, "is_active": True, "is_system_admin": False, "can_access_all_tenants": False} for i in range(1, size + 1)]
        o = [{"user_id": row["id"], "tenant_id": row["tenant_id"]} for row in n]
        m = [{"tenant_id": tenant, "role": role} for tenant, role in db.current.items()]
        report["inventory"] = child.classifier["classify"]([{"email": e, "system_role": r} for e, r in p.items()], n, o, m)
        report["space_settings"] = {"member_limit": 0}
        return p, n, o, m

    def modules(source, name):
        result = original_module(source, name)
        if name == "bulk_bootstrap":
            result["platform_admin"] = admin
        if name == "bulk_collector":
            result["collect"] = collect
        return result

    async def authority(platform_id, native_id):
        authority_reads.append((platform_id, native_id))
        if scenario == "admin_revoked":
            raise child.Refused("PLATFORM_ADMIN_CHANGED")

    db.authority = authority
    monkeypatch.setitem(child.scope, "module", modules)
    monkeypatch.setitem(child.scope, "Database", lambda *args: db)
    if scenario == "budget":
        monkeypatch.setitem(child.scope, "MAX_POSTS", 2)
    session = SimpleNamespace(token="synthetic-bearer", user={"id": "native-admin", "email": child.EMAIL, "is_system_admin": True}, tenant={"id": 10000}, memberships=[{"tenant_id": 10000, "role": "owner"}])
    identity = SimpleNamespace(
        _password_candidates=lambda *args: ["synthetic-current", "synthetic-legacy"],
        _normalise_login_response=lambda v: session,
        weknora_endpoint=lambda p: "http://fixture.invalid/api/v1" + p,
        _connect_weknora_database=None,
    )
    monkeypatch.setattr(gateway, "weknora_identity", identity, raising=False)
    monkeypatch.setattr(config, "get_app_config", lambda: SimpleNamespace(database=SimpleNamespace(backend="postgres", postgres_url="synthetic-not-used")))

    def transport(request):
        if request.url.path.endswith("/auth/login"):
            if scenario == "login_failed" or json.loads(request.content)["password"] == "synthetic-current":
                return httpx.Response(401)
            return httpx.Response(200, json={"success": True})
        assert request.headers["X-Tenant-ID"] == "10000"
        if request.method == "GET":
            return httpx.Response(200, json={"success": True, "data": {"id": child.ORG, "owner_id": "native-admin", "owner_tenant_id": 10000, "my_role": "admin", "member_limit": 0, "searchable": True, "require_approval": False}})
        tenant = json.loads(request.content)["tenant_id"]
        assert tenant not in posts
        posts.append(tenant)
        db.current[tenant] = "viewer"
        return httpx.Response(200, json={"success": True, "message": "added"})

    client_class = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_class(transport=httpx.MockTransport(transport), **kwargs))
    report = child.initial_report()
    if scenario in {"success", "new_workspace"}:
        await child.run(report, lambda row: None)
        assert report["status"] == child.COMPLETE
        assert len(posts) == (4 if scenario == "new_workspace" else 3)
        assert report["remaining_workspace_memberships"] == 0
        assert report["auto_enrollment_deployed"] is False
    else:
        with pytest.raises(child.Refused):
            await child.run(report, lambda row: None)
        assert not posts and report["status"] == "INCOMPLETE"
    assert report["login_requests"] == 2
    assert "private" not in json.dumps(report) and "synthetic-bearer" not in json.dumps(report)
