"""Bounded cursor inventory for the observed 84,748 platform accounts.

The scan never logs in, creates accounts, grants membership or writes reports.
Each DB is read in its own repeatable-read snapshot; this is not a distributed
snapshot or permission to apply the plan later without fresh revalidation.
"""

import asyncio
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys

CLASSIFIER = None  # Packaged original membership classification; no mutation.
ORG = "b9cfbf7f-7281-4f62-a72b-6a22379efe60"
IDENTITY_SHA = "af64e8d928d4d2902b9f189c4e890e0dd7f46ba816b31bfad0197534df55f256"
BATCH = 1000
MAX_PLATFORM = 200000
MAX_NATIVE = 400000
MAX_OWNERS = 800000
MAX_MEMBERS = 200000
MAX_METADATA_BYTES = 128 * 1024 * 1024
DEADLINE = 180
SAFE_CODES = frozenset({
    "POSTGRES_REQUIRED", "READONLY_REQUIRED", "IDENTITY_SOURCE_CHANGED",
    "INVENTORY_USER_LIMIT_EXCEEDED", "INVENTORY_NATIVE_LIMIT_EXCEEDED",
    "INVENTORY_MEMBERSHIP_LIMIT_EXCEEDED", "PACKAGED_CLASSIFIER_REQUIRED",
    "DUPLICATE_NATIVE_ORGANIZATION_MEMBERSHIP", "UNRECOGNIZED_PLATFORM_ROLE",
    "UNRECOGNIZED_NATIVE_ORGANIZATION_ROLE", "PLATFORM_EMAIL_AMBIGUOUS",
    "COMPANY_SPACE_NOT_FOUND", "PLATFORM_COUNT_MISMATCH", "IDENTITY_METADATA_INVALID",
    "METADATA_BYTE_LIMIT_EXCEEDED", "CURSOR_BATCH_LIMIT_EXCEEDED",
})


def require(value, code):
    if not value:
        raise RuntimeError(code)


async def readonly(connection):
    await connection.execute("SET LOCAL statement_timeout = '10s'")
    require(await connection.fetchval("SHOW transaction_read_only") == "on", "READONLY_REQUIRED")


async def pages(connection, sql, arguments, name, limit, code, report):
    """One bounded server-side cursor; no OFFSET scans or per-user queries."""
    stats = report["aggregate_observations"]
    stats[name + "_rows"] = 0
    stats[name + "_pages"] = 0
    cursor = await connection.cursor(sql, *arguments, timeout=10)
    while True:
        rows = await cursor.fetch(BATCH, timeout=10)
        require(len(rows) <= BATCH, "CURSOR_BATCH_LIMIT_EXCEEDED")
        if not rows:
            break
        stats[name + "_pages"] += 1
        stats[name + "_rows"] += len(rows)
        require(stats[name + "_rows"] <= limit, code)
        for row in rows:
            report["metadata_bytes_scanned"] += sum(len(str(v).encode("utf-8")) for v in row.values())
        require(report["metadata_bytes_scanned"] <= MAX_METADATA_BYTES, "METADATA_BYTE_LIMIT_EXCEEDED")
        yield rows


async def collect(report, platform_connect=None, native_connect=None):
    if platform_connect is None or native_connect is None:
        import asyncpg
        from deerflow.config import get_app_config
        from app.gateway.weknora_identity import _connect_weknora_database

        report["phase"] = "PLATFORM_CONFIGURATION"
        config = get_app_config().database
        require(config.backend == "postgres", "POSTGRES_REQUIRED")

        async def platform_connect():
            return await asyncpg.connect(config.postgres_url.replace("postgresql+asyncpg://", "postgresql://", 1), timeout=5, command_timeout=10)

        native_connect = _connect_weknora_database

    stats = report["aggregate_observations"]
    platform = {}
    report["phase"] = "PLATFORM_CONNECT"
    connection = await platform_connect()
    try:
        async with connection.transaction(readonly=True, isolation="repeatable_read"):
            report["phase"] = "PLATFORM_READONLY"
            await readonly(connection)
            report["phase"] = "PLATFORM_COUNT"
            expected = await connection.fetchval("SELECT count(*) FROM users WHERE is_disabled=false AND needs_setup=false")
            stats["active_setup_complete_platform_accounts"] = expected
            stats["exceeds_old_30000_scan_cap"] = expected > 30000
            require(expected <= MAX_PLATFORM, "INVENTORY_USER_LIMIT_EXCEEDED")
            report["phase"] = "PLATFORM_IDENTITIES"
            stats["platform_roles"] = {"admin": 0, "user": 0, "other_or_null": 0}
            stats["duplicate_normalized_platform_emails"] = 0
            async for rows in pages(connection, "SELECT email,system_role FROM users WHERE is_disabled=false AND needs_setup=false ORDER BY id", (), "platform", MAX_PLATFORM, "INVENTORY_USER_LIMIT_EXCEEDED", report):
                for row in rows:
                    email = row["email"]
                    require(isinstance(email, str) and 0 < len(email) <= 255, "IDENTITY_METADATA_INVALID")
                    email = email.lower()
                    role = row["system_role"]
                    stats["platform_roles"][role if role in {"admin", "user"} else "other_or_null"] += 1
                    if email in platform:
                        stats["duplicate_normalized_platform_emails"] += 1
                    platform[email] = role
            require(stats["platform_rows"] == expected, "PLATFORM_COUNT_MISMATCH")
            require(stats["duplicate_normalized_platform_emails"] == 0, "PLATFORM_EMAIL_AMBIGUOUS")
            require(stats["platform_roles"]["other_or_null"] == 0, "UNRECOGNIZED_PLATFORM_ROLE")
    finally:
        await connection.close(timeout=5)

    report["phase"] = "NATIVE_CONNECT"
    connection = await native_connect()
    native, owners, members = [], [], []
    try:
        async with connection.transaction(readonly=True, isolation="repeatable_read"):
            report["phase"] = "NATIVE_READONLY"
            await readonly(connection)
            report["phase"] = "SPACE_MEMBER_LIMIT"
            spaces = await connection.fetch("SELECT member_limit,searchable,require_approval FROM organizations WHERE id=$1 AND deleted_at IS NULL LIMIT 2", ORG)
            require(len(spaces) == 1, "COMPANY_SPACE_NOT_FOUND")
            report["space_settings"] = {
                "member_limit": spaces[0]["member_limit"], "unlimited": spaces[0]["member_limit"] == 0,
                "searchable": spaces[0]["searchable"], "require_approval": spaces[0]["require_approval"],
                "member_unit": "NATIVE_TENANT_WORKSPACE",
            }
            report["phase"] = "NATIVE_IDENTITIES"
            # One array parameter, not 84,748 queries or repeated full-table scans.
            async for rows in pages(connection, "SELECT id,email,tenant_id,is_active,is_system_admin,can_access_all_tenants FROM users WHERE deleted_at IS NULL AND lower(email)=ANY($1::text[]) ORDER BY id", (sorted(platform),), "native", MAX_NATIVE, "INVENTORY_NATIVE_LIMIT_EXCEEDED", report):
                native.extend(dict(row) for row in rows)
            report["phase"] = "OWNED_WORKSPACES"
            async for rows in pages(connection, "SELECT user_id,tenant_id FROM tenant_members WHERE deleted_at IS NULL AND status='active' AND role='owner' AND user_id=ANY($1::text[]) ORDER BY user_id,tenant_id", ([row["id"] for row in native],), "ownership", MAX_OWNERS, "INVENTORY_MEMBERSHIP_LIMIT_EXCEEDED", report):
                owners.extend(dict(row) for row in rows)
            report["phase"] = "EXISTING_SPACE_MEMBERS"
            async for rows in pages(connection, "SELECT tenant_id,role FROM organization_tenant_members WHERE organization_id=$1 ORDER BY tenant_id", (ORG,), "space_members", MAX_MEMBERS, "INVENTORY_MEMBERSHIP_LIMIT_EXCEEDED", report):
                members.extend(dict(row) for row in rows)
            stats["space_member_roles"] = {"admin": 0, "editor": 0, "viewer": 0, "other_or_null": 0}
            for member in members:
                role = member["role"]
                stats["space_member_roles"][role if role in {"admin", "editor", "viewer"} else "other_or_null"] += 1
            stats["duplicate_space_member_tenants"] = len(members) - len({str(row["tenant_id"]) for row in members})
    finally:
        await connection.close(timeout=5)
    report["phase"] = "CLASSIFY_MEMBERSHIPS"
    require(isinstance(CLASSIFIER, str), "PACKAGED_CLASSIFIER_REQUIRED")
    scope = {"__name__": "company_members_inventory_classifier"}
    exec(compile(CLASSIFIER, "<inventory-classifier>", "exec"), scope)
    report["inventory"] = scope["classify"](({"email": email, "system_role": role} for email, role in platform.items()), native, owners, members)
    report["phase"] = "COMPLETE"
    report["status"] = "READONLY_MEMBERSHIP_INVENTORY_NOT_ENROLLED"


async def diagnose(report, platform_connect=None, native_connect=None):
    try:
        await asyncio.wait_for(collect(report, platform_connect, native_connect), timeout=DEADLINE)
    except Exception as exc:
        code = str(exc) if type(exc) is RuntimeError and str(exc) in SAFE_CODES else "READONLY_INVENTORY_QUERY_FAILED"
        if isinstance(exc, asyncio.TimeoutError):
            code = "READONLY_INVENTORY_DEADLINE"
        report.update(status="INCOMPLETE", error_code=code, error_class=type(exc).__name__)


def initial_report():
    return {
        "schema": "portal-weknora-company-members-inventory.v3", "status": "INCOMPLETE",
        "phase": "SOURCE_CHECK", "aggregate_observations": {}, "metadata_bytes_scanned": 0,
        "limits": {"rows_per_fetch": BATCH, "platform_accounts": MAX_PLATFORM, "native_matches": MAX_NATIVE, "ownership_rows": MAX_OWNERS, "space_members": MAX_MEMBERS, "metadata_bytes": MAX_METADATA_BYTES, "deadline_seconds": DEADLINE},
        "membership_grants": 0, "login_requests": 0, "provider_requests": 0,
        "database_modified": False, "files_modified": False, "documents_read": False,
        "credentials_exported": False, "user_list_exported": False, "raw_errors_exported": False,
        "automatic_retry": False, "inventory_is_not_enrollment": True,
        "snapshot_scope": "REPEATABLE_READ_PER_DATABASE_NOT_DISTRIBUTED_SNAPSHOT",
        "fresh_account_and_membership_revalidation_required_for_enrollment": True,
    }


def main():
    report = initial_report()
    try:
        require(hashlib.sha256(Path("/app/backend/app/gateway/weknora_identity.py").read_bytes()).hexdigest() == IDENTITY_SHA, "IDENTITY_SOURCE_CHANGED")
        sys.path.insert(0, "/app/backend")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            asyncio.run(diagnose(report))
    except Exception as exc:
        report.update(error_class=type(exc).__name__, error_code=str(exc) if type(exc) is RuntimeError and str(exc) in SAFE_CODES else "INVENTORY_BOOTSTRAP_FAILED")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "READONLY_MEMBERSHIP_INVENTORY_NOT_ENROLLED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
