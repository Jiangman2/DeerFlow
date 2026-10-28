"""Read-only aggregate inventory for an authorized all-user viewer enrollment."""

import hashlib
import json

ORG = "b9cfbf7f-7281-4f62-a72b-6a22379efe60"
MAX_USERS = 30000


def classify(platform, native, owners, members):
    counts = {key: 0 for key in (
        "eligible_platform_users", "platform_admins_preserved", "not_yet_provisioned",
        "native_identity_not_ordinary_active", "ambiguous_native_identity",
        "no_owned_workspace", "multiple_owned_workspaces", "existing_viewer_users",
        "existing_elevated_membership_users_preserved", "users_to_enroll",
    )}
    by_email, owned, roles, planned = {}, {}, {}, {}
    for user in native:
        by_email.setdefault(user["email"].lower(), []).append(user)
    for member in owners:
        owned.setdefault(member["user_id"], set()).add(str(member["tenant_id"]))
    for member in members:
        tenant = str(member["tenant_id"])
        if tenant in roles:
            raise RuntimeError("DUPLICATE_NATIVE_ORGANIZATION_MEMBERSHIP")
        roles[tenant] = member["role"]
    for user in platform:
        if user["system_role"] == "admin":
            counts["platform_admins_preserved"] += 1
            continue
        if user["system_role"] != "user":
            raise RuntimeError("UNRECOGNIZED_PLATFORM_ROLE")
        counts["eligible_platform_users"] += 1
        matches = by_email.get(user["email"].lower(), [])
        if not matches:
            counts["not_yet_provisioned"] += 1
            continue
        if len(matches) != 1:
            counts["ambiguous_native_identity"] += 1
            continue
        native_user = matches[0]
        if native_user["is_active"] is not True or native_user["is_system_admin"] is not False or native_user["can_access_all_tenants"] is not False:
            counts["native_identity_not_ordinary_active"] += 1
            continue
        tenants = owned.get(native_user["id"], set())
        if not tenants:
            counts["no_owned_workspace"] += 1
            continue
        default = str(native_user["tenant_id"])
        if default in tenants:
            selected = default
        elif len(tenants) == 1:
            selected = next(iter(tenants))
        else:
            counts["multiple_owned_workspaces"] += 1
            continue
        role = roles.get(selected)
        if role == "viewer":
            counts["existing_viewer_users"] += 1
        elif role in {"admin", "editor"}:
            counts["existing_elevated_membership_users_preserved"] += 1
        elif role is None:
            counts["users_to_enroll"] += 1
            planned.setdefault(selected, []).append(native_user["id"])
        else:
            raise RuntimeError("UNRECOGNIZED_NATIVE_ORGANIZATION_ROLE")
    plan = [[tenant, sorted(set(users))] for tenant, users in sorted(planned.items())]
    return {
        "status": "READONLY_MEMBERSHIP_INVENTORY_NOT_ENROLLED", "counts": counts,
        "workspace_memberships_to_add": len(plan),
        "candidate_inventory_sha256": hashlib.sha256(json.dumps(plan, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest(),
        "membership_grants": 0, "user_list_exported": False, "credentials_exported": False,
        "database_readonly": True, "scope": "ACTIVE_SETUP_COMPLETE_PLATFORM_ACCOUNTS_AND_OWNED_DEFAULT_WORKSPACES",
    }


async def collect():
    import asyncpg
    from deerflow.config import get_app_config
    from app.gateway.weknora_identity import _connect_weknora_database

    config = get_app_config().database
    if config.backend != "postgres":
        raise RuntimeError("POSTGRES_REQUIRED")
    connection = await asyncpg.connect(config.postgres_url.replace("postgresql+asyncpg://", "postgresql://", 1), timeout=5, command_timeout=10)
    try:
        async with connection.transaction(readonly=True, isolation="repeatable_read"):
            platform = [dict(row) for row in await connection.fetch("SELECT id,email,system_role FROM users WHERE is_disabled=false AND needs_setup=false ORDER BY id LIMIT $1", MAX_USERS + 1)]
            if len(platform) > MAX_USERS:
                raise RuntimeError("INVENTORY_USER_LIMIT_EXCEEDED")
    finally:
        await connection.close()
    connection = await _connect_weknora_database()
    try:
        async with connection.transaction(readonly=True, isolation="repeatable_read"):
            await connection.execute("SET LOCAL statement_timeout = '10s'")
            emails = sorted({user["email"].lower() for user in platform})
            native = [dict(row) for row in await connection.fetch("SELECT id,email,tenant_id,is_active,is_system_admin,can_access_all_tenants FROM users WHERE deleted_at IS NULL AND lower(email)=ANY($1::text[]) LIMIT $2", emails, MAX_USERS * 2 + 1)]
            if len(native) > MAX_USERS * 2:
                raise RuntimeError("INVENTORY_NATIVE_LIMIT_EXCEEDED")
            owners = [dict(row) for row in await connection.fetch("SELECT user_id,tenant_id FROM tenant_members WHERE deleted_at IS NULL AND status='active' AND role='owner' AND user_id=ANY($1::text[]) LIMIT $2", [row["id"] for row in native], MAX_USERS * 3 + 1)]
            members = [dict(row) for row in await connection.fetch("SELECT tenant_id,role FROM organization_tenant_members WHERE organization_id=$1 LIMIT $2", ORG, MAX_USERS * 3 + 1)]
            if len(owners) > MAX_USERS * 3 or len(members) > MAX_USERS * 3:
                raise RuntimeError("INVENTORY_MEMBERSHIP_LIMIT_EXCEEDED")
    finally:
        await connection.close()
    return classify(platform, native, owners, members)
