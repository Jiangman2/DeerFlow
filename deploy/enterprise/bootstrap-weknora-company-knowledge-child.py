"""Create two approved empty native resources as the explicitly named admin.

Executed only by the pinned host wrapper. DB access is read-only; native HTTP
owns resource creation and its audit records. Never invokes full SSO provisioning,
changes accounts, uploads documents, or enables the company enrollment policy.
"""
import asyncio
import contextlib
import hashlib
import io
import json
import os
import pathlib
import sys
from uuid import UUID

EMAIL = "jiangman2@cnooc.com.cn"
KB_NAME = "中国海油制度知识库"
ORG_NAME = "中国海油全员制度共享空间"
SOURCE_SHA = "af64e8d928d4d2902b9f189c4e890e0dd7f46ba816b31bfad0197534df55f256"
MAX_BODY = 4 * 1024 * 1024


class Refused(Exception):
    pass


def require(condition, code):
    if not condition:
        raise Refused(code)


def uuid_value(value):
    require(isinstance(value, str), "RESOURCE_ID_INVALID")
    try:
        parsed = str(UUID(value))
    except ValueError:
        raise Refused("RESOURCE_ID_INVALID") from None
    require(value == parsed, "RESOURCE_ID_INVALID")
    return parsed


def tenant_for_owner(session):
    memberships = session.memberships
    owners = {str(m.get("tenant_id")) for m in memberships if isinstance(m, dict) and m.get("role") == "owner" and str(m.get("tenant_id", "")).isdigit()}
    require(bool(owners), "NO_OWNER_WORKSPACE")
    active = str(session.tenant.get("id"))
    if active in owners:
        return active
    require(len(owners) == 1, "AMBIGUOUS_OWNER_WORKSPACE")
    return next(iter(owners))


def select_model(models, kind, configured):
    require(isinstance(models, list), "MODELS_PROTOCOL_INVALID")
    eligible = [m for m in models if isinstance(m, dict) and m.get("type") == kind and m.get("status") == "active"]
    candidates = [m for m in eligible if m.get("id") == configured] if configured else [m for m in eligible if m.get("is_default") is True]
    require(len(candidates) == 1, "DEFAULT_MODEL_NOT_UNIQUE_OR_UNAVAILABLE_" + kind.upper())
    return candidates[0]["id"]


async def platform_admin():
    import asyncpg
    from deerflow.config import get_app_config

    config = get_app_config().database
    require(config.backend == "postgres", "POSTGRES_REQUIRED")
    connection = await asyncpg.connect(config.postgres_url.replace("postgresql+asyncpg://", "postgresql://", 1), timeout=5, command_timeout=5)
    try:
        async with connection.transaction(readonly=True):
            rows = await connection.fetch("SELECT id,email,system_role,is_disabled,needs_setup FROM users WHERE lower(email)=$1 LIMIT 2", EMAIL)
            require(len(rows) == 1, "ADMIN_ACCOUNT_NOT_UNIQUE")
            user = dict(rows[0])
            require(user["system_role"] == "admin" and user["is_disabled"] is False and user["needs_setup"] is False, "ACTIVE_PLATFORM_ADMIN_REQUIRED")
            return user
    finally:
        await connection.close()


async def check_names_unused():
    from app.gateway.weknora_identity import _connect_weknora_database

    connection = await _connect_weknora_database()
    try:
        async with connection.transaction(readonly=True):
            require(await connection.fetchval("SELECT count(*) FROM knowledge_bases WHERE name=$1 AND deleted_at IS NULL", KB_NAME) == 0, "NAMED_KB_ALREADY_EXISTS_INSPECT_FIRST")
            require(await connection.fetchval("SELECT count(*) FROM organizations WHERE name=$1 AND deleted_at IS NULL", ORG_NAME) == 0, "NAMED_ORGANIZATION_ALREADY_EXISTS_INSPECT_FIRST")
    finally:
        await connection.close()


class NativeAPI:
    def __init__(self, client, endpoint, report, emit):
        self.client, self.endpoint, self.report, self.emit = client, endpoint, report, emit
        self.headers = {}

    async def request(self, method, path, payload=None, *, phase, login=False):
        if method != "GET" and not login:
            require(self.report["resource_write_requests"] < 4, "WRITE_BUDGET_EXCEEDED")
            self.report["resource_write_requests"] += 1
            self.report["resource_write_may_have_applied"] = True
        self.report["phase"] = phase
        self.emit({"phase": phase, "resource_write_requests": self.report["resource_write_requests"]})
        async with self.client.stream(method, self.endpoint(path), headers=self.headers, json=payload) as response:
            if login and response.status_code == 401:
                return None
            require(response.status_code in (200, 201), "NATIVE_HTTP_" + str(response.status_code) + "_" + phase)
            data = bytearray()
            async for block in response.aiter_bytes(chunk_size=65536):
                data.extend(block)
                require(len(data) <= MAX_BODY, "NATIVE_RESPONSE_TOO_LARGE")
            envelope = json.loads(data)
            require(isinstance(envelope, dict) and envelope.get("success") is True, "NATIVE_PROTOCOL_INVALID_" + phase)
            if login:
                return envelope
            require("data" in envelope, "NATIVE_DATA_MISSING_" + phase)
            return envelope["data"]


async def create_resources(api, session, report):
    native_user_id = session.user["id"]
    tenant_id = tenant_for_owner(session)
    api.headers = {"Authorization": "Bearer " + session.token, "X-Tenant-ID": tenant_id}
    models = await api.request("GET", "/models", phase="READ_EXISTING_MODELS")
    embedding = select_model(models, "Embedding", os.getenv("WEKNORA_DEFAULT_EMBEDDING_MODEL_ID", ""))
    summary = select_model(models, "KnowledgeQA", os.getenv("WEKNORA_DEFAULT_SUMMARY_MODEL_ID", ""))
    # Check the native organization list shape before any creation.
    organizations = await api.request("GET", "/organizations", phase="CHECK_ORGANIZATION_API")
    require(isinstance(organizations, dict) and isinstance(organizations.get("organizations"), list), "ORGANIZATION_API_CONTRACT_CHANGED")
    from app.gateway.routers.weknora import _presentation_parser_rules

    kb = await api.request("POST", "/knowledge-bases", {
        "name": KB_NAME,
        "description": "汇集中国海油现行规章制度，提供制度检索、条款查询和制度问答，由管理员统一维护。",
        "type": "document", "embedding_model_id": embedding, "summary_model_id": summary,
        "chunking_config": {"chunk_size": 512, "chunk_overlap": 50, "separators": ["\n\n", "\n", ". "], "parser_engine_rules": _presentation_parser_rules()},
        "image_processing_config": {}, "extract_config": {"enabled": False},
    }, phase="CREATE_KNOWLEDGE_BASE")
    require(isinstance(kb, dict), "KB_RESPONSE_INVALID")
    kb_id = uuid_value(kb.get("id"))
    report["knowledge_base_id"] = kb_id
    api.emit({"phase": "KNOWLEDGE_BASE_CREATED", "knowledge_base_id": kb_id})
    require(kb.get("name") == KB_NAME and str(kb.get("tenant_id")) == tenant_id and kb.get("creator_id") == native_user_id, "KB_OWNERSHIP_NOT_CONFIRMED")
    org = await api.request("POST", "/organizations", {
        "name": ORG_NAME, "description": "中国海油全员只读制度共享空间，由平台管理员统一发布和维护。",
        "member_limit": 0,
    }, phase="CREATE_SHARED_SPACE")
    require(isinstance(org, dict), "ORGANIZATION_RESPONSE_INVALID")
    org_id = uuid_value(org.get("id"))
    report["organization_id"] = org_id
    api.emit({"phase": "SHARED_SPACE_CREATED", "organization_id": org_id})
    require(org.get("owner_id") == native_user_id and str(org.get("owner_tenant_id")) == tenant_id and org.get("my_role") == "admin", "ORGANIZATION_OWNERSHIP_NOT_CONFIRMED")
    await api.request("POST", "/knowledge-bases/" + kb_id + "/shares", {"organization_id": org_id, "permission": "viewer"}, phase="SHARE_AS_VIEWER")
    await api.request("PUT", "/organizations/" + org_id, {"searchable": True, "require_approval": False, "member_limit": 0}, phase="CONFIGURE_GLOBAL_SPACE")
    actual_org = await api.request("GET", "/organizations/" + org_id, phase="VERIFY_SHARED_SPACE")
    require(isinstance(actual_org, dict) and actual_org.get("id") == org_id and actual_org.get("owner_id") == native_user_id and str(actual_org.get("owner_tenant_id")) == tenant_id and actual_org.get("my_role") == "admin", "ORGANIZATION_READBACK_FAILED")
    require(actual_org.get("searchable") is True and actual_org.get("require_approval") is False and type(actual_org.get("member_limit")) is int and actual_org["member_limit"] == 0, "ORGANIZATION_SETTINGS_NOT_ACCEPTED")
    shares = await api.request("GET", "/knowledge-bases/" + kb_id + "/shares", phase="VERIFY_VIEWER_SHARE")
    require(isinstance(shares, dict) and isinstance(shares.get("shares"), list), "SHARES_PROTOCOL_INVALID")
    matching = [s for s in shares["shares"] if isinstance(s, dict) and s.get("organization_id") == org_id and s.get("knowledge_base_id") == kb_id]
    require(len(matching) == 1 and matching[0].get("permission") == "viewer", "VIEWER_SHARE_NOT_CONFIRMED")
    actual_kb = await api.request("GET", "/knowledge-bases/" + kb_id, phase="VERIFY_KNOWLEDGE_BASE")
    require(isinstance(actual_kb, dict) and actual_kb.get("id") == kb_id and actual_kb.get("creator_id") == native_user_id and str(actual_kb.get("tenant_id")) == tenant_id and actual_kb.get("embedding_model_id") == embedding and actual_kb.get("summary_model_id") == summary, "KB_READBACK_FAILED")
    report.update({
        "status": "ADMIN_RESOURCES_CREATED_AUTO_ENROLLMENT_NOT_DEPLOYED", "owner_tenant_id": tenant_id,
        "knowledge_base_page_path": "/platform/knowledge-bases/" + kb_id,
        "shared_spaces_page_path": "/platform/organizations", "viewer_share_verified": True,
        "recommended_gateway_configuration": {"WEKNORA_COMPANY_KB_ENABLED": "true", "WEKNORA_COMPANY_KB_ORGANIZATION_IDS": json.dumps([org_id])},
        "ordinary_user_acceptance_pending": True,
    })


async def run(report, emit):
    import httpx
    from app.gateway import weknora_identity as identity

    user = await platform_admin()
    await check_names_unused()
    async with httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=False) as client:
        api = NativeAPI(client, identity.weknora_endpoint, report, emit)
        session = None
        for password in identity._password_candidates(user["id"], EMAIL):
            report["login_requests"] += 1
            login = await api.request("POST", "/auth/login", {"email": EMAIL, "password": password}, phase="LOGIN_NAMED_ADMIN", login=True)
            if login is not None:
                session = identity._normalise_login_response(login)
                break
        require(session is not None, "EXISTING_SSO_LOGIN_REQUIRED_NO_PASSWORD_RESET")
        require(str(session.user.get("email", "")).lower() == EMAIL and session.user.get("is_system_admin") is True, "NATIVE_ADMIN_IDENTITY_NOT_CONFIRMED")
        # Re-read platform authority after login, before resource creation.
        fresh = await platform_admin()
        require(fresh["id"] == user["id"], "PLATFORM_IDENTITY_CHANGED")
        await create_resources(api, session, report)


def main():
    report = {"schema": "portal-weknora-company-bootstrap.v1", "status": "INCOMPLETE", "administrator_email": EMAIL, "resource_write_requests": 0, "resource_write_may_have_applied": False, "login_requests": 0, "database_direct_writes": 0, "provider_requests": 0, "credentials_exported": False, "documents_uploaded": 0, "automatic_retry": False, "gateway_restarted": False, "auto_enrollment_enabled": False}
    output = sys.stdout

    def emit(value):
        print(json.dumps(value, ensure_ascii=True, sort_keys=True), file=output, flush=True)

    try:
        require(hashlib.sha256(pathlib.Path("/app/backend/app/gateway/weknora_identity.py").read_bytes()).hexdigest() == SOURCE_SHA, "IDENTITY_SOURCE_CHANGED")
        require(hashlib.sha256(pathlib.Path("/app/backend/app/gateway/routers/weknora.py").read_bytes()).hexdigest() == "3486e1fdf1cdf3a98fdb80fcaccc3828fd2a3549b211e3e63f5f7b0400ccb0a9", "ROUTER_SOURCE_CHANGED")
        sys.path.insert(0, "/app/backend")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            asyncio.run(asyncio.wait_for(run(report, emit), 180))
    except Exception as exc:
        report.update({"error_class": type(exc).__name__, "error_code": str(exc) if isinstance(exc, Refused) else "NATIVE_OPERATION_INCOMPLETE", "raw_errors_exported": False})
    emit(report)
    return 0 if report["status"] == "ADMIN_RESOURCES_CREATED_AUTO_ENROLLMENT_NOT_DEPLOYED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
