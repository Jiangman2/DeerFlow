"""No production credentials, native HTTP calls, or DB writes in diagnosis."""

import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

DIRECTORY = Path(__file__).resolve().parents[2] / "deploy/enterprise"


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), DIRECTORY / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def child():
    module = load("inspect-weknora-company-members-r2-child")
    module.BASELINE = (DIRECTORY / "inspect-weknora-company-members-child.py").read_text(encoding="utf-8")
    return module


class Connection:
    def __init__(self, kind, state):
        self.kind, self.state, self.closed = kind, state, False
        self.inside = False

    def transaction(self, **kwargs):
        assert kwargs == {"readonly": True, "isolation": "repeatable_read"}
        return self

    async def __aenter__(self):
        self.inside = True

    async def __aexit__(self, *args):
        self.inside = False

    async def close(self):
        self.closed = True

    async def execute(self, sql):
        assert self.inside and sql == "SET LOCAL statement_timeout = '10s'"

    async def fetchval(self, sql):
        assert self.inside
        if sql == "SHOW transaction_read_only":
            return self.state.get("readonly", "on")
        assert self.kind == "platform" and sql == "SELECT count(*) FROM users WHERE is_disabled=false AND needs_setup=false"
        return self.state.get("platform_count", len(self.state["platform"]))

    async def fetch(self, sql, *args):
        assert self.inside and sql.startswith("SELECT ")
        if self.kind == "platform":
            assert "is_disabled=false AND needs_setup=false" in sql
            return self.state["platform"]
        if "FROM organizations " in sql:
            assert args == ("b9cfbf7f-7281-4f62-a72b-6a22379efe60",)
            return self.state["spaces"]
        if "FROM users " in sql:
            if self.state.get("native_error"):
                raise self.state["native_error"]
            assert "deleted_at IS NULL" in sql and set(args[0]) == {"one@example.test"}
            return self.state["native"]
        if "FROM tenant_members " in sql:
            assert "status='active'" in sql and "deleted_at IS NULL" in sql and "role='owner'" in sql
            return self.state["owners"]
        assert "FROM organization_tenant_members " in sql
        return self.state["members"]


@pytest.fixture
def database(monkeypatch):
    import asyncpg

    import deerflow.config
    from app.gateway import weknora_identity

    state = {
        "platform": [{"id": "p", "email": "one@example.test", "system_role": "user"}],
        "native": [{"id": "n", "email": "one@example.test", "tenant_id": 77, "is_active": True, "is_system_admin": False, "can_access_all_tenants": False}],
        "owners": [{"user_id": "n", "tenant_id": 77}],
        "members": [{"tenant_id": 10000, "role": "admin"}],
        "spaces": [{"member_limit": 0, "searchable": True, "require_approval": False}],
    }
    connections = []

    async def platform(*args, **kwargs):
        assert args == ("postgresql://synthetic",) and kwargs["command_timeout"] == 10
        connection = Connection("platform", state)
        connections.append(connection)
        return connection

    async def native():
        connection = Connection("native", state)
        connections.append(connection)
        return connection

    monkeypatch.setattr(asyncpg, "connect", platform)
    monkeypatch.setattr(weknora_identity, "_connect_weknora_database", native)
    monkeypatch.setattr(deerflow.config, "get_app_config", lambda: SimpleNamespace(database=SimpleNamespace(backend="postgres", postgres_url="postgresql+asyncpg://synthetic")))
    return state, connections


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,code,phase",
    [
        ("success", None, "COMPLETE"),
        ("too_many", "INVENTORY_USER_LIMIT_EXCEEDED", "PLATFORM_COUNT"),
        ("unknown_role", "UNRECOGNIZED_PLATFORM_ROLE", "CLASSIFY_MEMBERSHIPS"),
        ("duplicate_members", "DUPLICATE_NATIVE_ORGANIZATION_MEMBERSHIP", "CLASSIFY_MEMBERSHIPS"),
        ("unknown_member_role", "UNRECOGNIZED_NATIVE_ORGANIZATION_ROLE", "CLASSIFY_MEMBERSHIPS"),
        ("duplicate_platform_email", "PLATFORM_EMAIL_AMBIGUOUS", "PLATFORM_IDENTITIES"),
        ("native_error", "READONLY_INVENTORY_QUERY_FAILED", "NATIVE_IDENTITIES"),
        ("readonly_off", "READONLY_REQUIRED", "PLATFORM_READONLY"),
        ("missing_space", "COMPANY_SPACE_NOT_FOUND", "SPACE_MEMBER_LIMIT"),
    ],
)
async def test_inventory_guards_and_fixed_diagnostics(child, database, case, code, phase):
    state, connections = database
    if case == "too_many":
        state["platform_count"] = child.MAX_USERS + 1
    elif case == "unknown_role":
        state["platform"][0]["system_role"] = "private-value-not-exported"
    elif case == "duplicate_members":
        state["members"] *= 2
    elif case == "unknown_member_role":
        state["members"].append({"tenant_id": 77, "role": "private-value-not-exported"})
    elif case == "duplicate_platform_email":
        state["platform"].append({"id": "p2", "email": "ONE@example.test", "system_role": "user"})
    elif case == "native_error":
        state["native_error"] = RuntimeError("password=private-value-not-exported")
    elif case == "readonly_off":
        state["readonly"] = "off"
    elif case == "missing_space":
        state["spaces"] = []
    report = child.initial_report()
    await child.diagnose(report)
    assert report["phase"] == phase
    assert report.get("error_code") == code
    assert all(c.closed for c in connections)
    assert report["membership_grants"] == report["login_requests"] == report["provider_requests"] == 0
    raw = json.dumps(report)
    assert "private-value-not-exported" not in raw and "one@example.test" not in raw
    if case == "too_many":
        assert len(connections) == 1
    if case == "success":
        assert report["inventory"]["workspace_memberships_to_add"] == 1
        assert report["space_settings"]["unlimited"] is True


@pytest.fixture(params=["r2", "r3"])
def host(monkeypatch, request):
    package = load("package-weknora-company-members" + ("-r3" if request.param == "r3" else ""))
    scope = {"__name__": "packaged_member_host"}
    exec(compile(package.build(), "<host>", "exec"), scope)
    assert scope["RECEIPTS"][1]["sha256"] == "19eea1a4ea765974f3f5053763257119f8c269bfca0ab18f513e3a728ad26307"
    monkeypatch.setattr(scope["socket"], "gethostname", lambda: "datax-01")
    monkeypatch.setattr(scope["os"], "geteuid", lambda: 0, raising=False)
    scope["_test_child_schema"] = "portal-weknora-company-members-inventory.v3" if request.param == "r3" else "portal-weknora-company-members-diagnosis.v2"
    return scope


def test_changed_prior_receipt_stops_before_cluster_read(host, tmp_path):
    p = tmp_path / "report.json"
    p.write_text("{}", encoding="utf-8")
    host["RECEIPTS"] = [{"path": str(p), "bytes": 2, "sha256": "0" * 64}]
    host["query"] = lambda args: pytest.fail("No cluster queries before validating prior success")
    with pytest.raises(host["Refused"], match="PRIOR_RECEIPT_CHANGED"):
        host["execute"]({})


@pytest.mark.parametrize("case", ["success", "child_failure", "pod_restart", "deployment_drift", "rollout"])
def test_host_readonly_child_and_deployment_binding(host, case):
    deployments = {}
    for name in host["PINS"]:
        obj = {"metadata": {"uid": name, "generation": 1}, "spec": {"replicas": 1}, "status": {"observedGeneration": 1, "replicas": 1, "updatedReplicas": 1, "readyReplicas": 1, "availableReplicas": 1}}
        deployments[name] = obj
        host["PINS"][name] = [name, 1, host["digest"](obj["spec"])]
    pod = {
        "metadata": {"name": "sample", "uid": "same"},
        "spec": {"containers": [{"name": "gateway", "image": host["IMAGE"]}]},
        "status": {"conditions": [{"type": "Ready", "status": "True"}], "containerStatuses": [{"containerID": "same"}]},
    }
    host["check_receipts"] = lambda: host["RECEIPTS"]
    calls, processes = [], []

    def query(args):
        calls.append(args)
        assert args[0] == "get"
        if args[1] == "deployment":
            result = copy.deepcopy(deployments[args[2]])
            if case == "rollout":
                result["status"]["updatedReplicas"] = 0
            if case == "deployment_drift" and processes:
                result["metadata"]["generation"] = 2
            return result
        if args[1] == "pods":
            return {"items": [copy.deepcopy(pod)]}
        result = copy.deepcopy(pod)
        if case == "pod_restart" and processes:
            result["status"]["containerStatuses"][0]["containerID"] = "changed"
        return result

    def process(args, **kwargs):
        processes.append(args)
        assert args[len(host["BASE"]) : len(host["BASE"]) + 4] == ["exec", "sample", "-c", "gateway"]
        assert args[-1] == host["CHILD"]
        status = "INCOMPLETE" if case == "child_failure" else "READONLY_MEMBERSHIP_INVENTORY_NOT_ENROLLED"
        return SimpleNamespace(returncode=int(case == "child_failure"), stdout=json.dumps({"schema": host["_test_child_schema"], "status": status}).encode(), stderr=b"")

    host["query"] = query
    host["subprocess"] = SimpleNamespace(run=process, PIPE=-1)
    report = {}
    if case == "success":
        host["execute"](report)
        assert report["status"] == "READONLY_MEMBERSHIP_INVENTORY_NOT_ENROLLED"
    else:
        with pytest.raises(host["Refused"]):
            host["execute"](report)
        assert "status" not in report
    assert len(processes) == (0 if case == "rollout" else 1)
