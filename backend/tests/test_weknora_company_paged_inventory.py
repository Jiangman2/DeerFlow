"""Bounded cursor reads, full-set accounting and privacy under batch failures."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

DIRECTORY = Path(__file__).resolve().parents[2] / "deploy/enterprise"


@pytest.fixture
def module():
    spec = importlib.util.spec_from_file_location("company_paged_inventory", DIRECTORY / "inspect-weknora-company-members-r3-child.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.CLASSIFIER = (DIRECTORY / "inspect-weknora-company-members-child.py").read_text(encoding="utf-8")
    return m


class Cursor:
    def __init__(self, rows, connection):
        self.rows, self.index, self.connection = rows, 0, connection

    async def fetch(self, size, timeout):
        assert size == 1000 and timeout == 10 and self.connection.inside
        self.connection.fetches += 1
        if self.connection.state.get("midstream_failure") and self.index >= 1000:
            raise RuntimeError("private-data-must-not-appear")
        result = self.rows[self.index : self.index + size]
        self.index += len(result)
        return result


class Connection:
    def __init__(self, kind, state):
        self.kind, self.state = kind, state
        self.inside, self.closed, self.fetches = False, False, 0
        self.cursors = []

    def transaction(self, **kwargs):
        assert kwargs == {"readonly": True, "isolation": "repeatable_read"}
        return self

    async def __aenter__(self):
        self.inside = True

    async def __aexit__(self, *args):
        self.inside = False

    async def close(self, timeout):
        assert timeout == 5
        self.closed = True

    async def execute(self, sql):
        assert sql == "SET LOCAL statement_timeout = '10s'" and self.inside

    async def fetchval(self, sql):
        assert self.inside
        if sql == "SHOW transaction_read_only":
            return self.state.get("readonly", "on")
        assert self.kind == "platform" and sql == "SELECT count(*) FROM users WHERE is_disabled=false AND needs_setup=false"
        return self.state.get("platform_count", len(self.state["platform"]))

    async def fetch(self, sql, organization):
        assert self.kind == "native" and self.inside and "FROM organizations " in sql
        assert organization == "b9cfbf7f-7281-4f62-a72b-6a22379efe60"
        return [{"member_limit": 0, "searchable": True, "require_approval": False}]

    async def cursor(self, sql, *args, timeout):
        assert self.inside and timeout == 10 and sql.startswith("SELECT ") and "ORDER BY" in sql
        assert "OFFSET" not in sql and " LIMIT " not in sql
        self.cursors.append(sql)
        if self.kind == "platform":
            assert "is_disabled=false AND needs_setup=false" in sql and not args
            key = "platform"
        elif "FROM users " in sql:
            assert "deleted_at IS NULL" in sql and "lower(email)=ANY($1::text[])" in sql
            assert set(args[0]) == {x["email"].lower() for x in self.state["platform"]}
            key = "native"
        elif "FROM tenant_members " in sql:
            assert "deleted_at IS NULL AND status='active' AND role='owner'" in sql
            assert set(args[0]) == {x["id"] for x in self.state["native"]}
            key = "owners"
        else:
            assert "FROM organization_tenant_members " in sql
            key = "members"
        return Cursor(self.state[key], self)


def fixture_state(count):
    return {
        "platform": [{"email": f"u{i}@example.test", "system_role": "admin" if i == 0 else "user"} for i in range(count)],
        "native": [{"id": f"n{i}", "email": f"u{i}@example.test", "tenant_id": i + 1, "is_active": True, "is_system_admin": False, "can_access_all_tenants": False} for i in range(count)],
        "owners": [{"user_id": f"n{i}", "tenant_id": i + 1} for i in range(count)],
        "members": [{"tenant_id": 2, "role": "viewer"}, {"tenant_id": 3, "role": "editor"}],
    }


async def exercise(module, state):
    platform, native = Connection("platform", state), Connection("native", state)

    async def p():
        return platform

    async def n():
        return native

    report = module.initial_report()
    await module.diagnose(report, p, n)
    assert platform.closed
    if native.inside or native.cursors:
        assert native.closed
    assert "example.test" not in json.dumps(report)
    assert report["membership_grants"] == report["login_requests"] == report["provider_requests"] == 0
    return report, platform, native


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1000, 1001, 30001, 84748])
async def test_complete_cursor_accounting_beyond_old_limit(module, count):
    report, p, n = await exercise(module, fixture_state(count))
    assert report["status"] == "READONLY_MEMBERSHIP_INVENTORY_NOT_ENROLLED"
    assert report["aggregate_observations"]["platform_rows"] == count
    assert report["aggregate_observations"]["platform_pages"] == (count + 999) // 1000
    assert report["inventory"]["counts"]["eligible_platform_users"] == max(0, count - 1)
    assert report["inventory"]["workspace_memberships_to_add"] == max(0, count - 3)
    assert len(p.cursors) == 1 and len(n.cursors) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,code",
    [
        ("cap", "INVENTORY_USER_LIMIT_EXCEEDED"),
        ("native_cap", "INVENTORY_NATIVE_LIMIT_EXCEEDED"),
        ("owner_cap", "INVENTORY_MEMBERSHIP_LIMIT_EXCEEDED"),
        ("member_cap", "INVENTORY_MEMBERSHIP_LIMIT_EXCEEDED"),
        ("metadata_cap", "METADATA_BYTE_LIMIT_EXCEEDED"),
        ("count_mismatch", "PLATFORM_COUNT_MISMATCH"),
        ("duplicate_email_across_pages", "PLATFORM_EMAIL_AMBIGUOUS"),
        ("unknown_role", "UNRECOGNIZED_PLATFORM_ROLE"),
        ("readonly_off", "READONLY_REQUIRED"),
        ("partial_cursor", "READONLY_INVENTORY_QUERY_FAILED"),
        ("duplicate_members", "DUPLICATE_NATIVE_ORGANIZATION_MEMBERSHIP"),
    ],
)
async def test_no_success_on_partial_or_ambiguous_inventory(module, case, code):
    state = fixture_state(1001)
    if case == "cap":
        state["platform_count"] = module.MAX_PLATFORM + 1
    elif case == "native_cap":
        module.MAX_NATIVE = 1000
    elif case == "owner_cap":
        module.MAX_OWNERS = 1000
    elif case == "member_cap":
        module.MAX_MEMBERS = 1
    elif case == "metadata_cap":
        module.MAX_METADATA_BYTES = 1
    elif case == "count_mismatch":
        state["platform_count"] = 1002
    elif case == "duplicate_email_across_pages":
        state["platform"][-1]["email"] = state["platform"][0]["email"].upper()
    elif case == "unknown_role":
        state["platform"][0]["system_role"] = "private-data-must-not-appear"
    elif case == "readonly_off":
        state["readonly"] = "off"
    elif case == "partial_cursor":
        state["midstream_failure"] = True
    elif case == "duplicate_members":
        state["members"] *= 2
    report, p, n = await exercise(module, state)
    assert report["status"] == "INCOMPLETE" and report["error_code"] == code
    assert "inventory" not in report
    assert "private-data-must-not-appear" not in json.dumps(report)
    if case == "cap":
        assert not p.cursors and not n.closed


@pytest.mark.asyncio
async def test_workspace_dedup_and_existing_permission_classification(module):
    state = fixture_state(1001)
    state["native"] = state["native"][:6]
    state["native"][3]["tenant_id"] = 5
    state["owners"][3]["tenant_id"] = 5
    state["native"][5]["is_active"] = False
    report, _, _ = await exercise(module, copy.deepcopy(state))
    counts = report["inventory"]["counts"]
    assert counts["users_to_enroll"] == 2
    assert report["inventory"]["workspace_memberships_to_add"] == 1
    assert counts["not_yet_provisioned"] == 995
    assert counts["existing_viewer_users"] == counts["existing_elevated_membership_users_preserved"] == 1
    state["platform"].reverse()
    state["native"].reverse()
    state["owners"].reverse()
    again, _, _ = await exercise(module, state)
    assert again["inventory"]["candidate_inventory_sha256"] == report["inventory"]["candidate_inventory_sha256"]
