"""Real asyncpg cursor qualification against a disposable localhost fixture.

Run manually with the ephemeral Docker port; never uses application config.
Creates only synthetic users in an empty company_members_synthetic database.
"""

import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
import time

import asyncpg

DIRECTORY = Path(__file__).resolve().parent


async def exercise(port):
    async def connect(schema="public"):
        return await asyncpg.connect(host="127.0.0.1", port=port, user="postgres", password="synthetic-test-only", database="company_members_synthetic", server_settings={"search_path": schema}, timeout=5, command_timeout=10)

    seed = await connect()
    try:
        assert await seed.fetchval("SELECT current_database()") == "company_members_synthetic"
        assert await seed.fetchval("SELECT count(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema')") == 0
        await seed.execute("""
            CREATE SCHEMA platform;
            CREATE SCHEMA native;
            CREATE TABLE platform.users (id uuid PRIMARY KEY, email text NOT NULL, system_role text, is_disabled boolean, needs_setup boolean);
            INSERT INTO platform.users SELECT md5(i::text)::uuid, 'u'||i||'@example.test', CASE WHEN i=1 THEN 'admin' ELSE 'user' END, false, false FROM generate_series(1,84748) i;
            INSERT INTO platform.users VALUES (md5('disabled')::uuid, 'disabled@example.test', 'user', true, false), (md5('setup')::uuid, 'setup@example.test', 'user', false, true);
            CREATE TABLE native.users (id text PRIMARY KEY, email text, tenant_id bigint, is_active boolean, is_system_admin boolean, can_access_all_tenants boolean, deleted_at timestamp);
            INSERT INTO native.users SELECT 'n'||i, 'u'||i||'@example.test', i, i<>10, false, false, CASE WHEN i=11 THEN now() ELSE NULL END FROM generate_series(1,1200) i;
            INSERT INTO native.users VALUES ('unrelated-native', 'unrelated@example.test', 999999, true, false, false, NULL);
            CREATE TABLE native.tenant_members (user_id text, tenant_id bigint, role text, status text, deleted_at timestamp);
            INSERT INTO native.tenant_members SELECT 'n'||i, i, 'owner', CASE WHEN i=12 THEN 'suspended' ELSE 'active' END, NULL FROM generate_series(1,1200) i;
            CREATE TABLE native.organizations (id text PRIMARY KEY, member_limit int, searchable boolean, require_approval boolean, deleted_at timestamp);
            CREATE TABLE native.organization_tenant_members (organization_id text, tenant_id bigint, role text);
        """)
        org = "b9cfbf7f-7281-4f62-a72b-6a22379efe60"
        await seed.execute("INSERT INTO native.organizations VALUES ($1,0,true,false,NULL)", org)
        await seed.executemany("INSERT INTO native.organization_tenant_members VALUES ($1,$2,$3)", [(org, 1, "admin"), (org, 2, "viewer"), (org, 3, "editor")])
        spec = importlib.util.spec_from_file_location("company_paged_postgres_fixture", DIRECTORY / "inspect-weknora-company-members-r3-child.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.CLASSIFIER = (DIRECTORY / "inspect-weknora-company-members-child.py").read_text(encoding="utf-8")
        report = module.initial_report()
        started = time.monotonic()
        await module.diagnose(report, lambda: connect("platform"), lambda: connect("native"))
        assert report["status"] == "READONLY_MEMBERSHIP_INVENTORY_NOT_ENROLLED", report
        assert report["aggregate_observations"]["platform_rows"] == 84748
        assert report["aggregate_observations"]["platform_pages"] == 85
        assert report["aggregate_observations"]["native_rows"] == 1199
        assert report["aggregate_observations"]["native_pages"] == 2
        counts = report["inventory"]["counts"]
        assert counts["platform_admins_preserved"] == 1 and counts["eligible_platform_users"] == 84747
        assert counts["not_yet_provisioned"] == 83549
        assert counts["native_identity_not_ordinary_active"] == counts["no_owned_workspace"] == 1
        assert counts["existing_viewer_users"] == counts["existing_elevated_membership_users_preserved"] == 1
        assert counts["users_to_enroll"] == report["inventory"]["workspace_memberships_to_add"] == 1194
        assert await seed.fetchval("SELECT count(*) FROM native.organization_tenant_members") == 3
        assert await seed.fetchval("SELECT count(*) FROM platform.users") == 84750
        assert "example.test" not in json.dumps(report)
        return {"status": "PAGED_POSTGRES_FIXTURE_ACCEPTED", "platform_accounts": 84748, "platform_pages": 85, "native_cursor_pages": 2, "classified_workspaces_to_add": 1194, "membership_grants": 0, "production_database_access": False, "elapsed_seconds": round(time.monotonic() - started, 3)}
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
