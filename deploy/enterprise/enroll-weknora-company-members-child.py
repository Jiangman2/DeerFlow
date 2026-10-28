"""Bounded native viewer enrollment, never direct database membership writes.

The wrapper pins installed code/deployments and supplies a stdin heartbeat. Raw
identity metadata stays in this short-lived process. No account provisioning,
password reset, role demotion, document read or model call is performed.
"""

import asyncio
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time

COLLECTOR = None
BOOTSTRAP = None
ORG = "b9cfbf7f-7281-4f62-a72b-6a22379efe60"
KB = "bd83e659-9d9c-429a-acf1-ead01037cfa1"
OWNER = "10000"
EMAIL = "jiangman2@cnooc.com.cn"
OBSERVED_PLAN_SHA = "c357a39c3422b253c4010d8770ef1088cf9a62f537cf7bbed554dcc06c67d100"
BATCH = 40
MAX_POSTS = 10000
DEADLINE = 7200
POST_INTERVAL = 0.25
COMPLETE = "BULK_VIEWER_ENROLLMENT_ACCEPTED_AUTO_ENROLLMENT_NOT_DEPLOYED"


class Refused(Exception):
    pass


def require(value, code):
    if not value:
        raise Refused(code)


def module(source, name):
    require(isinstance(source, str), "PACKAGED_SOURCES_REQUIRED")
    scope = {"__name__": name}
    exec(compile(source, "<" + name + ">", "exec"), scope)
    return scope


def derive_targets(classifier, snapshot, inventory):
    platform, native, owners, members = snapshot
    rows = [{"email": email, "system_role": role} for email, role in platform.items()]
    plan = classifier["classify"](rows, native, owners, members, internal_plan=True)
    digest = hashlib.sha256(json.dumps(plan, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    require(digest == inventory["candidate_inventory_sha256"], "CLASSIFICATION_PLAN_MISMATCH")
    identities = {row["id"]: row for row in native}
    result = []
    for tenant, user_ids in plan:
        require(tenant.isdigit() and 0 < int(tenant) <= 9223372036854775807 and tenant != OWNER, "TARGET_WORKSPACE_INVALID")
        result.append({"tenant": int(tenant), "users": [{"id": uid, "email": identities[uid]["email"].lower()} for uid in user_ids]})
    return result


class API:
    def __init__(self, client, endpoint, report):
        self.client, self.endpoint, self.report = client, endpoint, report
        self.headers = {}

    async def request(self, method, path, payload=None, login=False):
        async with self.client.stream(method, self.endpoint(path), headers=self.headers, json=payload) as response:
            self.report["last_http_status"] = response.status_code
            if login and response.status_code == 401:
                return None
            require(response.status_code in (200, 201), "NATIVE_HTTP_REJECTED")
            data = bytearray()
            async for block in response.aiter_bytes(chunk_size=65536):
                data.extend(block)
                require(len(data) <= 4 * 1024 * 1024, "NATIVE_RESPONSE_TOO_LARGE")
            value = json.loads(data)
            require(isinstance(value, dict) and value.get("success") is True, "NATIVE_PROTOCOL_INVALID")
            return value

    async def organization(self, user_id):
        data = (await self.request("GET", "/organizations/" + ORG)).get("data")
        require(isinstance(data, dict) and data.get("id") == ORG and data.get("owner_id") == user_id and str(data.get("owner_tenant_id")) == OWNER and data.get("my_role") == "admin", "SPACE_AUTHORITY_CHANGED")
        require(type(data.get("member_limit")) is int and data["member_limit"] == 0 and data.get("searchable") is True and data.get("require_approval") is False, "SPACE_SETTINGS_CHANGED")

    async def invite(self, tenant):
        # The native invite contract returns success + message, WITHOUT data.
        # It directly adds a tenant member; it sends no invitation email.
        await self.request("POST", "/organizations/" + ORG + "/invite", {"tenant_id": tenant, "role": "viewer"})


class Database:
    def __init__(self, platform_connect, native_connect):
        self.platform_connect, self.native_connect = platform_connect, native_connect

    async def read(self, connect, statements):
        connection = await connect()
        try:
            async with connection.transaction(readonly=True, isolation="repeatable_read"):
                await connection.execute("SET LOCAL statement_timeout = '10s'")
                require(await connection.fetchval("SHOW transaction_read_only") == "on", "READONLY_REQUIRED")
                return [await connection.fetch(sql, *args, timeout=10) for sql, args in statements]
        finally:
            await connection.close(timeout=5)

    async def authority(self, platform_id, native_id):
        p = (await self.read(self.platform_connect, [("SELECT id FROM users WHERE lower(email)=$1 AND system_role='admin' AND is_disabled=false AND needs_setup=false LIMIT 2", (EMAIL,))]))[0]
        require(len(p) == 1 and p[0]["id"] == platform_id, "PLATFORM_ADMIN_CHANGED")
        user, space, shares = await self.read(self.native_connect, [
            ("SELECT id FROM users WHERE lower(email)=$1 AND deleted_at IS NULL AND is_active=true AND is_system_admin=true LIMIT 2", (EMAIL,)),
            ("SELECT owner_id,owner_tenant_id,member_limit,searchable,require_approval FROM organizations WHERE id=$1 AND deleted_at IS NULL LIMIT 2", (ORG,)),
            ("SELECT knowledge_base_id,permission FROM kb_shares WHERE organization_id=$1 LIMIT 10001", (ORG,)),
        ])
        require(len(user) == 1 and user[0]["id"] == native_id, "NATIVE_ADMIN_CHANGED")
        require(len(space) == 1 and space[0]["owner_id"] == native_id and str(space[0]["owner_tenant_id"]) == OWNER and space[0]["member_limit"] == 0 and space[0]["searchable"] is True and space[0]["require_approval"] is False, "SPACE_SETTINGS_CHANGED")
        require(0 < len(shares) <= 10000 and all(row["permission"] == "viewer" for row in shares) and any(str(row["knowledge_base_id"]) == KB for row in shares), "READONLY_KB_SHARES_CHANGED")

    async def revalidate(self, targets, classifier):
        emails = sorted({u["email"] for row in targets for u in row["users"]})
        user_ids = sorted({u["id"] for row in targets for u in row["users"]})
        tenants = [row["tenant"] for row in targets]
        platform = (await self.read(self.platform_connect, [("SELECT email,system_role FROM users WHERE lower(email)=ANY($1::text[]) AND is_disabled=false AND needs_setup=false LIMIT $2", (emails, 2 * len(emails) + 1))]))[0]
        require(len(platform) <= len(emails) and len({r["email"].lower() for r in platform}) == len(platform), "PLATFORM_IDENTITIES_AMBIGUOUS")
        native, owners, members = await self.read(self.native_connect, [
            ("SELECT id,email,tenant_id,is_active,is_system_admin,can_access_all_tenants FROM users WHERE deleted_at IS NULL AND (id=ANY($1::text[]) OR lower(email)=ANY($2::text[])) LIMIT $3", (user_ids, emails, 2 * len(user_ids) + 1)),
            ("SELECT user_id,tenant_id FROM tenant_members WHERE deleted_at IS NULL AND status='active' AND role='owner' AND user_id=ANY($1::text[]) LIMIT $2", (user_ids, 4 * len(user_ids) + 1)),
            ("SELECT tenant_id,role FROM organization_tenant_members WHERE organization_id=$1 AND tenant_id=ANY($2::bigint[]) LIMIT $3", (ORG, tenants, len(tenants) + 1)),
        ])
        require(len(native) <= 2 * len(user_ids) and len(owners) <= 4 * len(user_ids), "IDENTITY_RECHECK_LIMIT")
        fresh = classifier["classify"](platform, native, owners, members, internal_plan=True)
        fresh = {int(tenant): set(ids) for tenant, ids in fresh}
        roles = role_map(members)
        pending, existing = [], []
        for target in targets:
            tenant = target["tenant"]
            if tenant in roles:
                existing.append(roles[tenant])  # Preserve concurrent viewer/admin/editor grants.
            else:
                require(fresh.get(tenant) == {u["id"] for u in target["users"]}, "TARGET_AUTHORITY_CHANGED")
                pending.append(target)
        return pending, existing

    async def roles(self, tenants):
        rows = (await self.read(self.native_connect, [("SELECT tenant_id,role FROM organization_tenant_members WHERE organization_id=$1 AND tenant_id=ANY($2::bigint[]) LIMIT $3", (ORG, tenants, len(tenants) + 1))]))[0]
        return role_map(rows)


def role_map(rows):
    result = {}
    for row in rows:
        tenant = int(row["tenant_id"])
        require(tenant not in result and row["role"] in {"viewer", "editor", "admin"}, "MEMBER_READBACK_INVALID")
        result[tenant] = row["role"]
    return result


class Enrollment:
    def __init__(self, api, db, classifier, report, emit):
        self.api, self.db, self.classifier, self.report, self.emit = api, db, classifier, report, emit
        self.attempted = set()
        self.rate_lock = asyncio.Lock()
        self.next_post = 0.0

    async def post_once(self, target):
        tenant = target["tenant"]
        async with self.rate_lock:
            await asyncio.sleep(max(0, self.next_post - time.monotonic()))
            require(tenant not in self.attempted, "REPLAY_OF_ATTEMPTED_WORKSPACE_REFUSED")
            require(self.report["membership_post_requests"] < MAX_POSTS, "POST_BUDGET_EXCEEDED")
            self.attempted.add(tenant)
            self.next_post = time.monotonic() + POST_INTERVAL
            self.report["membership_post_requests"] += 1
            self.report["membership_mutation_may_have_applied"] = True
        try:
            await asyncio.wait_for(self.api.invite(tenant), timeout=20)
            return tenant, True
        except Exception:
            # Readback handles a committed request with a lost/error response.
            # Never retry the POST; no raw response or exception leaves the child.
            self.report["post_responses_unconfirmed"] += 1
            return tenant, False

    async def batch(self, targets):
        pending, existing = await self.db.revalidate(targets, self.classifier)
        checked_at = time.monotonic()
        self.report["concurrent_viewers_preserved"] += existing.count("viewer")
        self.report["concurrent_elevated_preserved"] += len(existing) - existing.count("viewer")
        # Two requests at most in flight. Check every pair before another POST;
        # a rejected or ambiguous first native invite stops the run immediately.
        for index in range(0, len(pending), 2):
            pair = pending[index:index + 2]
            if time.monotonic() - checked_at > 15:
                # Slow responses must not turn a batch snapshot into stale
                # authorization for later requests.
                pair, current_roles = await self.db.revalidate(pair, self.classifier)
                self.report["concurrent_viewers_preserved"] += current_roles.count("viewer")
                self.report["concurrent_elevated_preserved"] += len(current_roles) - current_roles.count("viewer")
            if not pair:
                continue
            outcomes = await asyncio.gather(*(self.post_once(row) for row in pair))
            roles = await self.db.roles([tenant for tenant, _ in outcomes])
            for tenant, acknowledged in outcomes:
                require(roles.get(tenant) == "viewer", "INVITE_NOT_CONFIRMED_AS_VIEWER_DO_NOT_REPLAY")
                self.report["confirmed_viewer_memberships"] += 1
                if not acknowledged:
                    self.report["uncertain_posts_resolved_by_readback"] += 1
        self.report["batches_verified"] += 1
        self.emit({"phase": "BULK_VIEWER_BATCH_VERIFIED", "batches": self.report["batches_verified"], "posts": self.report["membership_post_requests"], "confirmed_viewers": self.report["confirmed_viewer_memberships"]})


async def run(report, emit):
    import asyncpg
    import httpx
    from deerflow.config import get_app_config
    from app.gateway import weknora_identity as identity

    collector, bootstrap = module(COLLECTOR, "bulk_collector"), module(BOOTSTRAP, "bulk_bootstrap")
    classifier = module(collector["CLASSIFIER"], "bulk_classifier")
    config = get_app_config().database
    require(config.backend == "postgres", "POSTGRES_REQUIRED")

    async def platform_connect():
        return await asyncpg.connect(config.postgres_url.replace("postgresql+asyncpg://", "postgresql://", 1), timeout=5, command_timeout=10)

    db = Database(platform_connect, identity._connect_weknora_database)
    administrator = await bootstrap["platform_admin"]()
    async with httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=False) as client:
        api = API(client, identity.weknora_endpoint, report)
        session = None
        report["phase"] = "LOGIN_EXISTING_ADMIN"
        for password in identity._password_candidates(administrator["id"], EMAIL):
            report["login_requests"] += 1
            login = await api.request("POST", "/auth/login", {"email": EMAIL, "password": password}, login=True)
            if login is not None:
                session = identity._normalise_login_response(login)
                break
        require(session is not None, "EXISTING_ADMIN_LOGIN_REQUIRED_NO_ACCOUNT_CHANGES")
        require(str(session.user.get("email", "")).lower() == EMAIL and session.user.get("is_system_admin") is True and bootstrap["tenant_for_owner"](session) == OWNER, "ADMIN_OWNER_SESSION_MISMATCH")
        api.headers = {"Authorization": "Bearer " + session.token, "X-Tenant-ID": OWNER}
        native_id = session.user["id"]
        engine = Enrollment(api, db, classifier, report, emit)
        # Up to three complete inventory passes cover workspaces provisioned
        # during the first pass, bounded by the same 10,000 total POST budget.
        for pass_index in range(4):
            report["phase"] = "FRESH_PAGED_INVENTORY"
            emit({"phase": report["phase"], "pass": pass_index + 1})
            scan = collector["initial_report"]()
            snapshot = await asyncio.wait_for(collector["collect"](scan), timeout=180)
            inventory = scan["inventory"]
            report["latest_inventory"] = inventory
            report["latest_space_settings"] = scan["space_settings"]
            targets = derive_targets(classifier, snapshot, inventory)
            if pass_index == 0:
                report["initial_inventory"] = inventory
                report["matches_prior_readonly_inventory"] = inventory["candidate_inventory_sha256"] == OBSERVED_PLAN_SHA
            await db.authority(administrator["id"], native_id)
            await api.organization(native_id)
            require(len(targets) + report["membership_post_requests"] <= MAX_POSTS, "FRESH_TARGET_COUNT_EXCEEDS_WRITE_BUDGET")
            require(not any(row["tenant"] in engine.attempted for row in targets), "PREVIOUSLY_ADDED_MEMBERSHIP_REMOVED_DO_NOT_REPLAY")
            report["remaining_workspace_memberships"] = len(targets)
            if not targets:
                report["status"] = COMPLETE
                report["phase"] = "COMPLETE"
                return
            require(pass_index < 3, "NEW_WORKSPACES_REMAIN_AFTER_BOUNDED_PASSES")
            # Accept one real native invitation before starting the bulk pairs.
            batches = [targets[:1]] + [targets[i:i + BATCH] for i in range(1, len(targets), BATCH)]
            for batch in batches:
                report["phase"] = "REVALIDATE_BATCH_AUTHORITY"
                await db.authority(administrator["id"], native_id)
                await api.organization(native_id)
                report["phase"] = "INVITE_AND_READBACK"
                await engine.batch(batch)


async def with_heartbeat(operation):
    """No unattended batch continues after the local wrapper disappears."""
    loop = asyncio.get_running_loop()
    last_seen = [time.monotonic()]
    closed = [False]
    buffer = bytearray()
    fd = sys.stdin.fileno()

    def read():
        data = os.read(fd, 4096)
        if not data:
            closed[0] = True
            loop.remove_reader(fd)
        else:
            buffer.extend(data)
            if len(buffer) > 4096:
                closed[0] = True
            while b"\n" in buffer:
                line, _, rest = buffer.partition(b"\n")
                buffer[:] = rest
                if line == b"PING":
                    last_seen[0] = time.monotonic()
                else:
                    closed[0] = True

    loop.add_reader(fd, read)

    async def watch():
        while not closed[0] and time.monotonic() - last_seen[0] <= 30:
            await asyncio.sleep(1)
        raise Refused("HOST_HEARTBEAT_LOST_DO_NOT_REPLAY")

    worker, watchdog = asyncio.ensure_future(operation), asyncio.ensure_future(watch())
    try:
        done, _ = await asyncio.wait([worker, watchdog], return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            await task
    finally:
        loop.remove_reader(fd)
        for task in (worker, watchdog):
            if not task.done():
                task.cancel()
        await asyncio.gather(worker, watchdog, return_exceptions=True)


def initial_report():
    return {"schema": "portal-weknora-company-bulk-enrollment.v1", "status": "INCOMPLETE", "phase": "SOURCE_CHECK", "organization_id": ORG, "knowledge_base_id": KB,
            "membership_post_requests": 0, "confirmed_viewer_memberships": 0, "membership_mutation_may_have_applied": False,
            "post_responses_unconfirmed": 0, "uncertain_posts_resolved_by_readback": 0, "batches_verified": 0,
            "concurrent_viewers_preserved": 0, "concurrent_elevated_preserved": 0, "login_requests": 0,
            "maximum_membership_posts": MAX_POSTS, "maximum_in_flight": 2, "maximum_post_starts_per_second": 4, "deadline_seconds": DEADLINE,
            "database_direct_writes": 0, "provider_requests": 0, "accounts_created": 0, "account_roles_changed": False,
            "credentials_exported": False, "user_list_exported": False, "documents_read": False, "automatic_retry": False,
            "auto_enrollment_deployed": False, "workload_modified": False, "business_qa_acceptance_pending": True}


def main():
    report, output = initial_report(), sys.stdout

    def emit(value):
        print(json.dumps(value, sort_keys=True, ensure_ascii=True), file=output, flush=True)

    try:
        require(hashlib.sha256(Path("/app/backend/app/gateway/weknora_identity.py").read_bytes()).hexdigest() == "af64e8d928d4d2902b9f189c4e890e0dd7f46ba816b31bfad0197534df55f256", "IDENTITY_SOURCE_CHANGED")
        sys.path.insert(0, "/app/backend")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            asyncio.run(asyncio.wait_for(with_heartbeat(run(report, emit)), timeout=DEADLINE))
    except BaseException as exc:
        report.update(error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, Refused) else "BULK_ENROLLMENT_STOPPED_DO_NOT_REPLAY", raw_errors_exported=False)
    emit(report)
    return 0 if report["status"] == COMPLETE else 1


if __name__ == "__main__":
    raise SystemExit(main())
