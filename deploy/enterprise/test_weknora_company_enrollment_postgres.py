"""Disposable PostgreSQL/HTTP fixture; never imports production configuration."""

import argparse
import asyncio
import importlib.util
import json
from pathlib import Path

import asyncpg
import httpx

DIRECTORY = Path(__file__).resolve().parent


def load(filename):
    spec = importlib.util.spec_from_file_location(filename.replace("-", "_"), DIRECTORY / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def exercise(port):
    async def connect(schema="public"):
        return await asyncpg.connect(host="127.0.0.1", port=port, user="postgres", password="synthetic-test-only", database="company_enrollment_synthetic", server_settings={"search_path": schema}, timeout=5, command_timeout=10)

    seed = await connect()
    try:
        assert await seed.fetchval("SELECT current_database()") == "company_enrollment_synthetic"
        assert await seed.fetchval("SELECT count(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema')") == 0
        await seed.execute("""
            CREATE SCHEMA platform;
            CREATE SCHEMA native;
            CREATE TABLE platform.users (id uuid PRIMARY KEY, email text NOT NULL, system_role text, is_disabled boolean, needs_setup boolean);
            INSERT INTO platform.users SELECT md5(i::text)::uuid, 'u'||i||'@example.test', 'user', false, false FROM generate_series(1,55) i;
            CREATE TABLE native.users (id text PRIMARY KEY, email text, tenant_id bigint, is_active boolean, is_system_admin boolean, can_access_all_tenants boolean, deleted_at timestamp);
            INSERT INTO native.users SELECT 'n'||i, 'u'||i||'@example.test', i, true, false, false, NULL FROM generate_series(1,50) i;
            CREATE TABLE native.tenant_members (user_id text, tenant_id bigint, role text, status text, deleted_at timestamp);
            INSERT INTO native.tenant_members SELECT 'n'||i, i, 'owner', 'active', NULL FROM generate_series(1,50) i;
            CREATE TABLE native.organizations (id text PRIMARY KEY, owner_id text, owner_tenant_id bigint, member_limit int, searchable boolean, require_approval boolean, deleted_at timestamp);
            CREATE TABLE native.organization_tenant_members (organization_id text, tenant_id bigint, role text, PRIMARY KEY (organization_id,tenant_id));
            CREATE TABLE native.kb_shares (organization_id text, knowledge_base_id text, permission text);
        """)
        helpers = load("package-weknora-company-members.py")
        raw = load("package-weknora-company-enrollment.py").build().decode()
        child = {"__name__": "synthetic_bulk_child"}
        exec(compile(helpers.literal(raw, "CHILD"), "<synthetic-bulk-child>", "exec"), child)
        child["POST_INTERVAL"] = 0
        org, kb, email = child["ORG"], child["KB"], child["EMAIL"]
        admin_id = await seed.fetchval("INSERT INTO platform.users VALUES (md5('admin')::uuid,$1,'admin',false,false) RETURNING id", email)
        await seed.execute("INSERT INTO native.users VALUES ('admin',$1,10000,true,true,true,NULL)", email)
        await seed.execute("INSERT INTO native.tenant_members VALUES ('admin',10000,'owner','active',NULL)")
        await seed.execute("INSERT INTO native.organizations VALUES ($1,'admin',10000,0,true,false,NULL)", org)
        await seed.execute("INSERT INTO native.kb_shares VALUES ($1,$2,'viewer')", org, kb)
        await seed.executemany("INSERT INTO native.organization_tenant_members VALUES ($1,$2,$3)", [(org, 10000, "admin"), (org, 1, "viewer"), (org, 2, "editor")])
        collector = child["module"](child["COLLECTOR"], "synthetic_collector")
        classifier = child["module"](collector["CLASSIFIER"], "synthetic_classifier")
        scan = collector["initial_report"]()
        snapshot = await collector["collect"](scan, lambda: connect("platform"), lambda: connect("native"))
        targets = child["derive_targets"](classifier, snapshot, scan["inventory"])
        assert len(targets) == 48 and scan["inventory"]["counts"]["not_yet_provisioned"] == 5
        db = child["Database"](lambda: connect("platform"), lambda: connect("native"))
        await db.authority(admin_id, "admin")
        await seed.execute("UPDATE platform.users SET is_disabled=true WHERE email='u3@example.test'")
        try:
            await db.revalidate([next(row for row in targets if row["tenant"] == 3)], classifier)
        except child["Refused"]:
            pass
        else:
            raise AssertionError("disabled account must not be granted")
        await seed.execute("UPDATE platform.users SET is_disabled=false WHERE email='u3@example.test'")
        # The HTTP fake owns all fixture writes through a separate connection;
        # all Database queries must remain readonly, with no open transaction
        # spanning the write request.
        posts = []

        async def transport(request):
            assert request.method == "POST" and request.url.path == "/api/v1/organizations/" + org + "/invite"
            value = json.loads(request.content)
            assert value["role"] == "viewer" and set(value) == {"tenant_id", "role"}
            assert request.headers["X-Tenant-ID"] == "10000"
            tenant = value["tenant_id"]
            posts.append(tenant)
            writer = await connect("native")
            try:
                await writer.execute("INSERT INTO organization_tenant_members VALUES ($1,$2,'viewer')", org, tenant)
            finally:
                await writer.close(timeout=5)
            if tenant == 4:
                raise httpx.ReadTimeout("synthetic private upstream failure after commit")
            return httpx.Response(200, json={"success": True, "message": "Member added successfully"})

        report = child["initial_report"]()
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport), trust_env=False, follow_redirects=False) as client:
            api = child["API"](client, lambda p: "http://fixture.invalid/api/v1" + p, report)
            api.headers = {"Authorization": "Bearer synthetic-only", "X-Tenant-ID": "10000"}
            engine = child["Enrollment"](api, db, classifier, report, lambda row: None)
            for index in range(0, len(targets), 40):
                await engine.batch(targets[index:index + 40])
            await engine.batch(targets)  # Already members: no POSTs repeated.
        assert len(posts) == len(set(posts)) == 48
        assert report["confirmed_viewer_memberships"] == 48 and report["uncertain_posts_resolved_by_readback"] == 1
        assert await seed.fetchval("SELECT count(*) FROM native.organization_tenant_members WHERE organization_id=$1 AND role='viewer'", org) == 49
        assert await seed.fetchval("SELECT role FROM native.organization_tenant_members WHERE organization_id=$1 AND tenant_id=2", org) == "editor"
        after = collector["initial_report"]()
        await collector["collect"](after, lambda: connect("platform"), lambda: connect("native"))
        assert after["inventory"]["workspace_memberships_to_add"] == 0
        assert after["inventory"]["counts"]["not_yet_provisioned"] == 5
        assert "example.test" not in json.dumps(report) and "synthetic-only" not in json.dumps(report)
        return {"status": "SYNTHETIC_POSTGRES_NATIVE_INVITE_ACCEPTED", "confirmed_synthetic_viewers": 48, "post_retries": 0, "lost_response_readback_accepted": True, "disabled_account_refused": True, "elevated_member_preserved": True, "unprovisioned_accounts_unchanged": 5, "production_database_access": False}
    finally:
        await seed.close(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    assert 1024 < args.port < 65536
    print(json.dumps(asyncio.run(exercise(args.port)), sort_keys=True))


if __name__ == "__main__":
    main()
