"""Diagnose the separate member inventory, without replaying SSO or joining."""

import asyncio
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys

BASELINE = None  # Packaged r1 classifier, kept unchanged for diagnosis.
IDENTITY_SHA = "af64e8d928d4d2902b9f189c4e890e0dd7f46ba816b31bfad0197534df55f256"
ORG = "b9cfbf7f-7281-4f62-a72b-6a22379efe60"
MAX_USERS = 30000
SAFE_CODES = frozenset({
    "POSTGRES_REQUIRED", "READONLY_REQUIRED", "IDENTITY_SOURCE_CHANGED",
    "INVENTORY_USER_LIMIT_EXCEEDED", "INVENTORY_NATIVE_LIMIT_EXCEEDED",
    "INVENTORY_MEMBERSHIP_LIMIT_EXCEEDED", "PACKAGED_CLASSIFIER_REQUIRED",
    "DUPLICATE_NATIVE_ORGANIZATION_MEMBERSHIP", "UNRECOGNIZED_PLATFORM_ROLE",
    "UNRECOGNIZED_NATIVE_ORGANIZATION_ROLE", "PLATFORM_EMAIL_AMBIGUOUS",
    "COMPANY_SPACE_NOT_FOUND",
})


def require(value, code):
    if not value:
        raise RuntimeError(code)


def role_counts(rows, field, known):
    # An unexpected database value must not become a user-visible field name.
    result = {key: 0 for key in known}
    result["other_or_null"] = 0
    for row in rows:
        value = row[field]
        result[value if value in known else "other_or_null"] += 1
    return result


async def read_only(connection):
    await connection.execute("SET LOCAL statement_timeout = '10s'")
    require(await connection.fetchval("SHOW transaction_read_only") == "on", "READONLY_REQUIRED")


async def collect(report):
    import asyncpg
    from deerflow.config import get_app_config
    from app.gateway.weknora_identity import _connect_weknora_database

    stats = report["aggregate_observations"]
    report["phase"] = "PLATFORM_CONFIGURATION"
    config = get_app_config().database
    require(config.backend == "postgres", "POSTGRES_REQUIRED")
    report["phase"] = "PLATFORM_CONNECT"
    connection = await asyncpg.connect(config.postgres_url.replace("postgresql+asyncpg://", "postgresql://", 1), timeout=5, command_timeout=10)
    try:
        async with connection.transaction(readonly=True, isolation="repeatable_read"):
            report["phase"] = "PLATFORM_READONLY"
            await read_only(connection)
            report["phase"] = "PLATFORM_COUNT"
            stats["active_setup_complete_platform_accounts"] = await connection.fetchval("SELECT count(*) FROM users WHERE is_disabled=false AND needs_setup=false")
            require(stats["active_setup_complete_platform_accounts"] <= MAX_USERS, "INVENTORY_USER_LIMIT_EXCEEDED")
            report["phase"] = "PLATFORM_IDENTITIES"
            platform = [dict(row) for row in await connection.fetch("SELECT id,email,system_role FROM users WHERE is_disabled=false AND needs_setup=false ORDER BY id LIMIT $1", MAX_USERS + 1)]
            stats["platform_rows"] = len(platform)
            require(len(platform) <= MAX_USERS, "INVENTORY_USER_LIMIT_EXCEEDED")
            stats["platform_roles"] = role_counts(platform, "system_role", ("admin", "user"))
            emails = [user["email"].lower() for user in platform]
            stats["duplicate_normalized_platform_emails"] = len(emails) - len(set(emails))
            require(stats["duplicate_normalized_platform_emails"] == 0, "PLATFORM_EMAIL_AMBIGUOUS")
    finally:
        await connection.close()
    report["phase"] = "NATIVE_CONNECT"
    connection = await _connect_weknora_database()
    try:
        async with connection.transaction(readonly=True, isolation="repeatable_read"):
            report["phase"] = "NATIVE_READONLY"
            await read_only(connection)
            report["phase"] = "SPACE_MEMBER_LIMIT"
            spaces = await connection.fetch("SELECT member_limit,searchable,require_approval FROM organizations WHERE id=$1 AND deleted_at IS NULL LIMIT 2", ORG)
            require(len(spaces) == 1, "COMPANY_SPACE_NOT_FOUND")
            report["space_settings"] = {
                "member_limit": spaces[0]["member_limit"],
                "unlimited": spaces[0]["member_limit"] == 0,
                "searchable": spaces[0]["searchable"],
                "require_approval": spaces[0]["require_approval"],
                "member_unit": "NATIVE_TENANT_WORKSPACE",
            }
            report["phase"] = "NATIVE_IDENTITIES"
            native = [dict(row) for row in await connection.fetch("SELECT id,email,tenant_id,is_active,is_system_admin,can_access_all_tenants FROM users WHERE deleted_at IS NULL AND lower(email)=ANY($1::text[]) LIMIT $2", sorted(set(emails)), MAX_USERS * 2 + 1)]
            stats["matched_native_rows"] = len(native)
            require(len(native) <= MAX_USERS * 2, "INVENTORY_NATIVE_LIMIT_EXCEEDED")
            report["phase"] = "OWNED_WORKSPACES"
            owners = [dict(row) for row in await connection.fetch("SELECT user_id,tenant_id FROM tenant_members WHERE deleted_at IS NULL AND status='active' AND role='owner' AND user_id=ANY($1::text[]) LIMIT $2", [row["id"] for row in native], MAX_USERS * 3 + 1)]
            stats["active_owner_rows"] = len(owners)
            require(len(owners) <= MAX_USERS * 3, "INVENTORY_MEMBERSHIP_LIMIT_EXCEEDED")
            report["phase"] = "EXISTING_SPACE_MEMBERS"
            members = [dict(row) for row in await connection.fetch("SELECT tenant_id,role FROM organization_tenant_members WHERE organization_id=$1 LIMIT $2", ORG, MAX_USERS * 3 + 1)]
            stats["existing_space_member_rows"] = len(members)
            stats["space_member_roles"] = role_counts(members, "role", ("admin", "editor", "viewer"))
            stats["duplicate_space_member_tenants"] = len(members) - len({str(row["tenant_id"]) for row in members})
            require(len(members) <= MAX_USERS * 3, "INVENTORY_MEMBERSHIP_LIMIT_EXCEEDED")
    finally:
        await connection.close()
    report["phase"] = "CLASSIFY_MEMBERSHIPS"
    require(isinstance(BASELINE, str), "PACKAGED_CLASSIFIER_REQUIRED")
    scope = {"__name__": "company_members_inventory_baseline"}
    exec(compile(BASELINE, "<inventory-classifier>", "exec"), scope)
    report["inventory"] = scope["classify"](platform, native, owners, members)
    report["phase"] = "COMPLETE"
    report["status"] = "READONLY_MEMBERSHIP_INVENTORY_NOT_ENROLLED"


async def diagnose(report):
    try:
        await asyncio.wait_for(collect(report), timeout=60)
    except Exception as exc:
        # Exception text is discarded unless it exactly matches our fixed guard.
        code = str(exc) if type(exc) is RuntimeError and str(exc) in SAFE_CODES else "READONLY_INVENTORY_QUERY_FAILED"
        if isinstance(exc, asyncio.TimeoutError):
            code = "READONLY_INVENTORY_DEADLINE"
        report.update(status="INCOMPLETE", error_code=code, error_class=type(exc).__name__)


def initial_report():
    return {
        "schema": "portal-weknora-company-members-diagnosis.v2", "status": "INCOMPLETE",
        "phase": "SOURCE_CHECK", "aggregate_observations": {},
        "limits": {"platform_accounts": MAX_USERS, "native_matches": MAX_USERS * 2, "ownership_or_membership_rows": MAX_USERS * 3, "deadline_seconds": 60},
        "membership_grants": 0, "login_requests": 0, "provider_requests": 0,
        "database_modified": False, "files_modified": False, "documents_read": False,
        "credentials_exported": False, "user_list_exported": False, "raw_errors_exported": False,
        "automatic_retry": False, "inventory_is_not_enrollment": True,
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
