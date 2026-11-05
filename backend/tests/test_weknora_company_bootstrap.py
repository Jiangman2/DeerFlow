"""Offline contract and mutation-boundary checks; no production connections."""

import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "deploy" / "enterprise" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def child(monkeypatch):
    module = load("bootstrap-weknora-company-knowledge-child")
    monkeypatch.delenv("WEKNORA_DEFAULT_EMBEDDING_MODEL_ID", raising=False)
    monkeypatch.delenv("WEKNORA_DEFAULT_SUMMARY_MODEL_ID", raising=False)
    from app.gateway.routers import weknora

    monkeypatch.setattr(weknora, "_presentation_parser_rules", lambda: [{"file_types": ["pptx"], "engine": "docreader"}])
    return module


@pytest.fixture
def session():
    return SimpleNamespace(token="synthetic-admin-token", user={"id": "native-admin"}, tenant={"id": 77}, memberships=[{"tenant_id": 77, "role": "owner"}])


KB = "ca8d0f80-1ef9-41f5-9b07-8a99ec4c689a"
ORG = "36d52e3c-7ab1-4e28-996e-7981f0d12463"
MODELS = [{"id": "embedding", "type": "Embedding", "status": "active", "is_default": True}, {"id": "summary", "type": "KnowledgeQA", "status": "active", "is_default": True}]


def resource_replies(child):
    kb = {"id": KB, "name": child.KB_NAME, "tenant_id": 77, "creator_id": "native-admin", "embedding_model_id": "embedding", "summary_model_id": "summary"}
    org = {"id": ORG, "name": child.ORG_NAME, "owner_id": "native-admin", "owner_tenant_id": 77, "my_role": "admin", "searchable": True, "require_approval": False, "member_limit": 0}
    share = {"knowledge_base_id": KB, "organization_id": ORG, "permission": "viewer"}
    return [MODELS, {"organizations": []}, kb, org, share, org, org, {"shares": [share]}, kb]


async def creation(child, session, replies, calls, report, progress):
    def handle(request):
        assert request.headers["authorization"] == "Bearer synthetic-admin-token"
        assert request.headers["x-tenant-id"] == "77"
        calls.append(request)
        result = replies.pop(0)
        if isinstance(result, Exception):
            raise result
        return httpx.Response(200, json={"success": True, "data": result})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        api = child.NativeAPI(client, lambda path: "http://weknora/api/v1" + path, report, progress.append)
        await child.create_resources(api, session, report)


@pytest.mark.asyncio
async def test_create_owned_resources_share_viewer_and_read_back(child, session):
    calls, progress, report = [], [], {"resource_write_requests": 0}
    await creation(child, session, resource_replies(child), calls, report, progress)
    writes = [r for r in calls if r.method != "GET"]
    assert [(r.method, r.url.path) for r in writes] == [
        ("POST", "/api/v1/knowledge-bases"),
        ("POST", "/api/v1/organizations"),
        ("POST", f"/api/v1/knowledge-bases/{KB}/shares"),
        ("PUT", f"/api/v1/organizations/{ORG}"),
    ]
    assert json.loads(writes[0].content)["name"] == child.KB_NAME
    assert json.loads(writes[1].content)["name"] == child.ORG_NAME
    assert json.loads(writes[2].content) == {"organization_id": ORG, "permission": "viewer"}
    assert json.loads(writes[3].content) == {"searchable": True, "require_approval": False, "member_limit": 0}
    assert report["resource_write_requests"] == 4
    assert report["viewer_share_verified"] is True
    assert report["knowledge_base_id"] == KB and report["organization_id"] == ORG
    assert report["status"] == "ADMIN_RESOURCES_CREATED_AUTO_ENROLLMENT_NOT_DEPLOYED"
    assert "synthetic-admin-token" not in json.dumps([progress, report])


@pytest.mark.parametrize(
    "position,change,error,writes",
    [
        (0, MODELS + [MODELS[0]], "DEFAULT_MODEL_NOT_UNIQUE", 0),
        (1, [], "ORGANIZATION_API_CONTRACT_CHANGED", 0),
        (2, {"id": KB, "creator_id": "somebody-else"}, "KB_OWNERSHIP_NOT_CONFIRMED", 1),
        (3, {"id": ORG, "owner_id": "somebody-else"}, "ORGANIZATION_OWNERSHIP_NOT_CONFIRMED", 2),
        (7, {"shares": [{"knowledge_base_id": KB, "organization_id": ORG, "permission": "editor"}]}, "VIEWER_SHARE_NOT_CONFIRMED", 4),
    ],
)
@pytest.mark.asyncio
async def test_contract_failures_bound_writes(child, session, position, change, error, writes):
    replies = resource_replies(child)
    replies[position] = change
    calls, progress, report = [], [], {"resource_write_requests": 0}
    with pytest.raises(child.Refused, match=error):
        await creation(child, session, replies, calls, report, progress)
    assert report["resource_write_requests"] == writes


@pytest.mark.asyncio
async def test_uncertain_create_is_never_replayed(child, session):
    replies = resource_replies(child)
    replies[2] = httpx.ReadTimeout("synthetic-sensitive-error")
    calls, progress, report = [], [], {"resource_write_requests": 0}
    with pytest.raises(httpx.ReadTimeout):
        await creation(child, session, replies, calls, report, progress)
    assert len(calls) == 3
    assert report["resource_write_requests"] == 1 and report["resource_write_may_have_applied"] is True
    assert "synthetic-sensitive-error" not in json.dumps([progress, report])


@pytest.mark.parametrize(
    "memberships,active,expected",
    [
        ([{"tenant_id": 77, "role": "admin"}], 77, None),
        ([{"tenant_id": 77, "role": "owner"}, {"tenant_id": 88, "role": "owner"}], 99, None),
        ([{"tenant_id": 77, "role": "owner"}], 99, "77"),
        ([{"tenant_id": 77, "role": "owner"}, {"tenant_id": 88, "role": "owner"}], 88, "88"),
    ],
)
def test_only_owned_unambiguous_workspace_used(child, session, memberships, active, expected):
    session.memberships, session.tenant = memberships, {"id": active}
    if expected is None:
        with pytest.raises(child.Refused):
            child.tenant_for_owner(session)
    else:
        assert child.tenant_for_owner(session) == expected


class ReadonlyTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class Connection:
    closed = False

    def __init__(self, rows=None, counts=None):
        self.rows, self.counts = rows, counts

    def transaction(self, *, readonly):
        assert readonly is True
        return ReadonlyTransaction()

    async def fetch(self, sql, value):
        assert sql.startswith("SELECT ") and value == "jiangman2@cnooc.com.cn"
        return self.rows

    async def fetchval(self, sql, value):
        assert sql.startswith("SELECT count(*)")
        return self.counts.pop(0)

    async def close(self):
        self.closed = True


@pytest.mark.parametrize("invalid", [False, "disabled", "setup", "ordinary", "duplicate"])
@pytest.mark.asyncio
async def test_existing_platform_admin_checked_readonly(child, monkeypatch, invalid):
    import asyncpg

    import deerflow.config

    row = {"id": "platform-admin", "email": child.EMAIL, "system_role": "user" if invalid == "ordinary" else "admin", "is_disabled": invalid == "disabled", "needs_setup": invalid == "setup"}
    conn = Connection(rows=[row, row] if invalid == "duplicate" else [row])

    async def connect(*args, **kwargs):
        return conn

    monkeypatch.setattr(asyncpg, "connect", connect)
    monkeypatch.setattr(deerflow.config, "get_app_config", lambda: SimpleNamespace(database=SimpleNamespace(backend="postgres", postgres_url="postgresql://synthetic")))
    if invalid:
        with pytest.raises(child.Refused):
            await child.platform_admin()
    else:
        assert (await child.platform_admin())["id"] == "platform-admin"
    assert conn.closed


@pytest.mark.parametrize("counts", [[1], [0, 1], [0, 0]])
@pytest.mark.asyncio
async def test_existing_named_resources_are_never_adopted_or_overwritten(child, monkeypatch, counts):
    from app.gateway import weknora_identity

    exists = any(counts)
    conn = Connection(counts=list(counts))

    async def connect():
        return conn

    monkeypatch.setattr(weknora_identity, "_connect_weknora_database", connect)
    if exists:
        with pytest.raises(child.Refused, match="ALREADY_EXISTS"):
            await child.check_names_unused()
    else:
        await child.check_names_unused()
    assert conn.closed


@pytest.fixture
def host(tmp_path, monkeypatch):
    module = load("bootstrap-weknora-company-knowledge-host")
    monkeypatch.setattr(module, "DIRECTORY", tmp_path / "once")
    monkeypatch.setattr(module, "CHILD", "synthetic-child")
    monkeypatch.setattr(module.socket, "gethostname", lambda: "datax-01")
    monkeypatch.setattr(module.os, "geteuid", lambda: 0, raising=False)
    return module


def test_host_once_boundary_preserves_prior_receipt(host):
    host.claim()
    before = (host.DIRECTORY / "STARTED.json").read_bytes()
    with pytest.raises(host.Refused, match="PRIOR_ATTEMPT_EXISTS"):
        host.claim()
    assert (host.DIRECTORY / "STARTED.json").read_bytes() == before


def test_host_deployment_change_stops_before_claim_or_exec(host, monkeypatch):
    monkeypatch.setattr(host, "query", lambda args: {"metadata": {"uid": "changed", "generation": 1}, "spec": {}})
    with pytest.raises(host.Refused, match="DEPLOYMENT_BASELINE_CHANGED"):
        host.execute({})
    assert not host.DIRECTORY.exists()


@pytest.mark.parametrize("timeout", [False, True])
def test_host_single_child_execution_and_uncertain_timeout(host, monkeypatch, timeout):
    pod = {"metadata": {"name": "gateway-synthetic", "uid": "pod-uid"}, "spec": {"containers": [{"name": "gateway", "image": host.IMAGE}]}, "status": {"conditions": [{"type": "Ready", "status": "True"}], "containerStatuses": []}}
    monkeypatch.setattr(host, "pin", lambda *args: None)
    monkeypatch.setattr(host, "query", lambda args: {"items": [pod]} if args[1] == "pods" else pod if args[1] == "pod" else {})
    executions = []

    def execute(args, **kwargs):
        executions.append(args)
        if timeout:
            raise subprocess.TimeoutExpired("kubectl", 240)
        result = {"schema": "portal-weknora-company-bootstrap.v1", "resource_write_may_have_applied": True, "status": "ADMIN_RESOURCES_CREATED_AUTO_ENROLLMENT_NOT_DEPLOYED"}
        return subprocess.CompletedProcess(args, 0, json.dumps(result).encode(), b"")

    monkeypatch.setattr(host.subprocess, "run", execute)
    report = {}
    if timeout:
        with pytest.raises(host.Refused, match="CHILD_TIMEOUT_DO_NOT_REPLAY"):
            host.execute(report)
    else:
        host.execute(report)
        assert report["status"] == "ADMIN_RESOURCES_CREATED_AUTO_ENROLLMENT_NOT_DEPLOYED"
    assert report["once_claimed"] is True and report["operation_may_have_applied"] is True
    assert len(executions) == 1
