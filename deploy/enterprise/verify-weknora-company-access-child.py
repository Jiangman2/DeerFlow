"""One authorized ordinary-user canary; candidate code lives only in this process."""

import asyncio
import contextlib
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import types

HELPERS = None  # Packaged bootstrap helpers; never run their main.
POLICY = None  # Packaged candidate enrollment source.
INVENTORY = None  # Read-only all-user inventory; no grants.
EMAIL = "bianqy@cnooc.com.cn"
ORG = "b9cfbf7f-7281-4f62-a72b-6a22379efe60"
KB = "bd83e659-9d9c-429a-acf1-ead01037cfa1"
OWNER_TENANT = "10000"
IDENTITY_SHA = "af64e8d928d4d2902b9f189c4e890e0dd7f46ba816b31bfad0197534df55f256"


class Refused(Exception):
    pass


def require(condition, code):
    if not condition:
        raise Refused(code)


async def verification_user():
    import asyncpg
    from deerflow.config import get_app_config

    config = get_app_config().database
    require(config.backend == "postgres", "POSTGRES_REQUIRED")
    connection = await asyncpg.connect(config.postgres_url.replace("postgresql+asyncpg://", "postgresql://", 1), timeout=5, command_timeout=5)
    try:
        async with connection.transaction(readonly=True):
            rows = await connection.fetch("SELECT id,email,system_role,is_disabled,needs_setup FROM users WHERE lower(email)=$1 LIMIT 2", EMAIL)
            require(len(rows) == 1, "VERIFICATION_ACCOUNT_NOT_UNIQUE")
            user = dict(rows[0])
            require(user["system_role"] == "user" and user["is_disabled"] is False and user["needs_setup"] is False, "ACTIVE_ORDINARY_USER_REQUIRED")
            return user
    finally:
        await connection.close()


def load_module(name, source):
    require(isinstance(source, str), "PACKAGED_SOURCE_REQUIRED")
    module = types.ModuleType(name)
    sys.modules[name] = module
    exec(compile(source, "<" + name + ">", "exec"), module.__dict__)
    return module


async def exercise_access(policy, helpers, api, session, report):
    tenant_id = helpers.tenant_for_owner(session)
    require(tenant_id != OWNER_TENANT, "VERIFICATION_MUST_USE_CONSUMER_WORKSPACE")
    session = replace(session, tenant={**session.tenant, "id": tenant_id})
    api.headers = {"Authorization": "Bearer " + session.token, "X-Tenant-ID": tenant_id}
    original_request = policy._request

    async def limited_request(client, method, url, *, payload=None):
        require(method in {"GET", "POST"}, "UNEXPECTED_POLICY_REQUEST")
        if method == "POST":
            require(url == api.endpoint("/organizations/join-by-id") and payload == {"organization_id": ORG, "role": "viewer"}, "UNEXPECTED_POLICY_MUTATION")
            require(report["join_requests"] == 0, "JOIN_BUDGET_EXCEEDED")
            report["join_requests"] += 1
            report["membership_mutation_may_have_applied"] = True
        return await original_request(client, method, url, payload=payload)

    policy._request = limited_request
    os.environ["WEKNORA_COMPANY_KB_ENABLED"] = "true"
    os.environ["WEKNORA_COMPANY_KB_ORGANIZATION_IDS"] = json.dumps([ORG])
    await policy.ensure_company_knowledge_access(session, api.endpoint, administrator=False)
    first_join_count = report["join_requests"]
    await policy.ensure_company_knowledge_access(session, api.endpoint, administrator=False)
    require(report["join_requests"] == first_join_count, "REPEATED_ACCESS_JOINED_AGAIN")
    org = await api.request("GET", "/organizations/" + ORG, phase="VERIFY_VIEWER_MEMBERSHIP")
    require(isinstance(org, dict) and org.get("id") == ORG and org.get("my_role") == "viewer", "VIEWER_MEMBERSHIP_NOT_CONFIRMED")
    shares = await api.request("GET", "/organizations/" + ORG + "/shares", phase="VERIFY_EFFECTIVE_VIEWER")
    require(isinstance(shares, dict) and isinstance(shares.get("shares"), list), "SHARES_PROTOCOL_INVALID")
    matching = [row for row in shares["shares"] if isinstance(row, dict) and row.get("knowledge_base_id") == KB and row.get("organization_id") == ORG]
    require(len(matching) == 1 and matching[0].get("permission") == "viewer" and matching[0].get("my_role_in_org") == "viewer" and matching[0].get("my_permission") == "viewer", "EFFECTIVE_VIEWER_NOT_CONFIRMED")
    shared = await api.request("GET", "/shared-knowledge-bases", phase="VERIFY_SELECTOR_RESOURCE")
    require(isinstance(shared, list), "SHARED_LIST_PROTOCOL_INVALID")
    visible = [row for row in shared if isinstance(row, dict) and isinstance(row.get("knowledge_base"), dict) and row["knowledge_base"].get("id") == KB and row.get("organization_id") == ORG]
    require(len(visible) == 1 and visible[0].get("permission") == "viewer", "KNOWLEDGE_BASE_NOT_VISIBLE_READONLY")
    knowledge = await api.request("GET", "/knowledge-bases/" + KB, phase="VERIFY_KNOWLEDGE_BASE_READ")
    require(isinstance(knowledge, dict) and knowledge.get("id") == KB and str(knowledge.get("tenant_id")) == OWNER_TENANT and knowledge.get("my_permission") == "viewer", "KNOWLEDGE_BASE_READ_NOT_VIEWER")
    report.update({
        "status": "ORDINARY_VIEWER_CANDIDATE_ACCEPTED_NOT_DEPLOYED", "knowledge_base_id": KB,
        "organization_id": ORG, "native_role": "viewer", "effective_permission": "viewer",
        "consumer_workspace_differs_from_owner": True, "native_shared_list_visible": True,
        "repeated_access_no_additional_join": True, "knowledge_base_metadata_read": True,
        "write_denial_basis": "NATIVE_EFFECTIVE_PERMISSION_METADATA_NO_WRITE_PROBES",
        "browser_acceptance_pending": True, "knowledge_qa_business_acceptance_pending": True,
        "checks": ["existing_ordinary_identity", "consumer_workspace", "viewer_join_or_existing_membership", "repeat_without_rejoin", "effective_viewer_permission", "shared_list_visibility", "knowledge_base_metadata_read"],
    })


async def run(report, emit):
    import httpx
    from app.gateway import weknora_identity as identity

    helpers = load_module("company_bootstrap_helpers", HELPERS)
    policy = load_module("company_knowledge_candidate", POLICY)
    user = await verification_user()
    async with httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=False) as client:
        api = helpers.NativeAPI(client, identity.weknora_endpoint, report, emit)
        session = None
        for password in identity._password_candidates(user["id"], EMAIL):
            report["login_requests"] += 1
            data = await api.request("POST", "/auth/login", {"email": EMAIL, "password": password}, phase="LOGIN_AUTHORIZED_VERIFICATION_USER", login=True)
            if data is not None:
                session = identity._normalise_login_response(data)
                break
        require(session is not None, "EXISTING_USER_SSO_REQUIRED_NO_ACCOUNT_CHANGE")
        require(str(session.user.get("email", "")).lower() == EMAIL and session.user.get("is_system_admin") is False and session.user.get("can_access_all_tenants") is not True, "ORDINARY_NATIVE_IDENTITY_REQUIRED")
        fresh = await verification_user()
        require(fresh["id"] == user["id"], "PLATFORM_IDENTITY_CHANGED")
        await exercise_access(policy, helpers, api, session, report)
    # Independent metadata inventory requested by the administrator. Keep a
    # successful viewer canary visible even when this additional schema differs.
    try:
        inventory = load_module("company_membership_inventory", INVENTORY)
        report["all_user_inventory"] = await asyncio.wait_for(inventory.collect(), 45)
    except Exception as exc:
        report["all_user_inventory"] = {"status": "INCOMPLETE", "error_class": type(exc).__name__, "raw_errors_exported": False, "membership_grants": 0}


def main():
    report = {
        "schema": "portal-weknora-company-viewer-canary.v1", "status": "INCOMPLETE",
        "verification_email": EMAIL, "resource_write_requests": 0, "join_requests": 0,
        "membership_mutation_may_have_applied": False, "login_requests": 0,
        "maximum_join_requests": 1, "account_created": False, "account_roles_changed": False,
        "credentials_exported": False, "raw_errors_exported": False, "documents_read": False,
        "provider_requests": 0, "database_direct_writes": 0, "automatic_retry": False,
        "candidate_only_in_child": True, "production_configuration_modified": False,
        "auto_enrollment_enabled_for_all_users": False, "gateway_restarted": False,
    }
    output = sys.stdout

    def emit(value):
        print(json.dumps(value, ensure_ascii=True, sort_keys=True), file=output, flush=True)

    try:
        require(hashlib.sha256(Path("/app/backend/app/gateway/weknora_identity.py").read_bytes()).hexdigest() == IDENTITY_SHA, "IDENTITY_SOURCE_CHANGED")
        sys.path.insert(0, "/app/backend")
        report["candidate_policy_sha256"] = hashlib.sha256(POLICY.encode()).hexdigest()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            asyncio.run(asyncio.wait_for(run(report, emit), 150))
    except Exception as exc:
        # Candidate policy emits fixed codes; never print native HTTP text.
        detail = getattr(exc, "detail", None)
        code = detail.get("code") if isinstance(detail, dict) else None
        if not isinstance(code, str) or not code.startswith("COMPANY_KB_") or not code.replace("_", "").isalnum():
            code = str(exc) if isinstance(exc, Refused) else "VIEWER_CANARY_INCOMPLETE"
        report.update({"error_class": type(exc).__name__, "error_code": code})
    emit(report)
    return 0 if report["status"] == "ORDINARY_VIEWER_CANDIDATE_ACCEPTED_NOT_DEPLOYED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
