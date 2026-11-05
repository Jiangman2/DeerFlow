"""Disposable local PostgreSQL fixture with simulated native API writes only."""
import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import httpx

HERE = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), HERE / name)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


async def exercise(port):
    async def connect(schema='public'):
        return await asyncpg.connect(host='127.0.0.1', port=port, user='postgres', password='synthetic-test-only',
            database='company_admin_enrollment_synthetic', server_settings={'search_path': schema}, timeout=5, command_timeout=10)

    helper = load('package-weknora-company-members.py')
    raw = load('package-weknora-company-admin-enrollment.py').build().decode()
    scope = {'__name__': 'synthetic_admin_child'}
    exec(helper.literal(raw, 'CHILD'), scope)
    child = SimpleNamespace(**scope)
    common = child.module(child.COMMON, 'synthetic_db_api')
    seed = await connect()
    try:
        assert await seed.fetchval('SELECT current_database()') == 'company_admin_enrollment_synthetic'
        assert await seed.fetchval("SELECT count(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema')") == 0
        await seed.execute('''
            CREATE SCHEMA platform;
            CREATE SCHEMA native;
            CREATE TABLE platform.users (id uuid PRIMARY KEY, email text, system_role text, is_disabled boolean, needs_setup boolean);
            CREATE TABLE native.users (id text PRIMARY KEY, email text, tenant_id bigint, is_active boolean, is_system_admin boolean, can_access_all_tenants boolean, deleted_at timestamp);
            CREATE TABLE native.tenant_members (user_id text, tenant_id bigint, role text, status text, deleted_at timestamp);
            CREATE TABLE native.organizations (id text PRIMARY KEY, owner_id text, owner_tenant_id bigint, member_limit int, searchable boolean, require_approval boolean, deleted_at timestamp);
            CREATE TABLE native.organization_tenant_members (organization_id text, tenant_id bigint, role text, PRIMARY KEY(organization_id,tenant_id));
            CREATE TABLE native.kb_shares (organization_id text, knowledge_base_id text, permission text);
        ''')
        for tenant in (10000, 1, 2, 3, 4, 5):
            email = child.EMAIL if tenant == 10000 else f'synthetic{tenant}@example.test'
            await seed.execute("INSERT INTO platform.users VALUES(md5($1)::uuid,$2,'admin',false,false)", str(tenant), email)
            await seed.execute('INSERT INTO native.users VALUES($1,$2,$3,true,true,$4,NULL)', f'n{tenant}', email, tenant, tenant != 2)
            await seed.execute("INSERT INTO native.tenant_members VALUES($1,$2,'owner','active',NULL)", f'n{tenant}', tenant)
        await seed.execute("INSERT INTO platform.users VALUES(md5('ordinary')::uuid,'ordinary@example.test','user',false,false), (md5('disabled')::uuid,'disabled@example.test','admin',true,false), (md5('setup')::uuid,'setup@example.test','admin',false,true)")
        await seed.execute("INSERT INTO native.organizations VALUES($1,'n10000',10000,0,true,false,NULL)", child.ORG)
        await seed.execute("INSERT INTO native.kb_shares VALUES($1,$2,'viewer')", child.ORG, child.KB)
        await seed.executemany('INSERT INTO native.organization_tenant_members VALUES($1,$2,$3)', [(child.ORG, t, r) for t, r in ((10000, 'admin'), (1, 'viewer'), (2, 'editor'), (3, 'admin'))])
        flags_before = [dict(r) for r in await seed.fetch('SELECT * FROM native.users ORDER BY id')]
        platform_before = [dict(r) for r in await seed.fetch('SELECT * FROM platform.users ORDER BY id')]
        admin_id = await seed.fetchval("SELECT id FROM platform.users WHERE email=$1", child.EMAIL)
        base = common['Database'](lambda: connect('platform'), lambda: connect('native'))
        db, report, posts = child.AdminDatabase(base), child.initial_report(), []
        initial = await db.snapshot()
        assert initial['counts']['platform_admin_accounts'] == 6 and initial['missing'] == [4, 5]

        async def transport(request):
            assert request.headers['X-Tenant-ID'] == '10000'
            if request.method == 'GET':
                assert request.url.path == '/api/v1/organizations/' + child.ORG
                return httpx.Response(200, json={'success': True, 'data': {'id': child.ORG, 'owner_id': 'n10000',
                    'owner_tenant_id': 10000, 'my_role': 'admin', 'member_limit': 0, 'searchable': True, 'require_approval': False}})
            assert request.method == 'POST' and request.url.path == '/api/v1/organizations/' + child.ORG + '/invite'
            payload = json.loads(request.content)
            assert set(payload) == {'tenant_id', 'role'} and payload['role'] == 'viewer'
            tenant = payload['tenant_id']
            posts.append(tenant)
            writer = await connect('native')
            try:
                # A simultaneous manual editor membership wins for tenant 4.
                # Tenant 5's insert succeeds but its HTTP response is lost.
                await writer.execute('INSERT INTO organization_tenant_members VALUES($1,$2,$3)', child.ORG, tenant, 'editor' if tenant == 4 else 'viewer')
            finally:
                await writer.close()
            if tenant == 4:
                return httpx.Response(400, json={'success': False, 'message': 'already member'})
            raise httpx.ReadTimeout('synthetic lost response')

        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            api = common['API'](client, lambda path: 'http://synthetic.test/api/v1' + path, report)
            api.headers = {'X-Tenant-ID': '10000', 'Authorization': 'Bearer synthetic-fixture'}

            async def authority():
                await base.authority(admin_id, 'n10000')
                await api.organization('n10000')

            await child.complete_memberships(db, api, authority, report, lambda row: None)
            assert posts == [4, 5] and report['status'] == child.COMPLETE
            assert report['confirmed_after_post'] == {'viewer': 1, 'editor': 1, 'admin': 0}
            assert report['post_responses_unconfirmed'] == report['uncertain_posts_resolved_by_readback'] == 2
            final = await db.snapshot()
            assert final['roles'] == {10000: 'admin', 1: 'viewer', 2: 'editor', 3: 'admin', 4: 'editor', 5: 'viewer'}
            second = child.initial_report()
            await child.complete_memberships(db, api, authority, second, lambda row: None)
            assert second['membership_post_requests'] == 0 and posts == [4, 5]
        assert flags_before == [dict(r) for r in await seed.fetch('SELECT * FROM native.users ORDER BY id')]
        assert platform_before == [dict(r) for r in await seed.fetch('SELECT * FROM platform.users ORDER BY id')]
        assert not any(x in json.dumps(report) for x in ('@', 'synthetic-fixture', 'n10000'))
        print(json.dumps({'status': 'SYNTHETIC_POSTGRES_ADMIN_COMPLETION_ACCEPTED', 'selected_admins': 6,
            'simulated_membership_posts': len(posts), 'manual_editor_preserved': True, 'lost_reply_readback_accepted': True,
            'repeat_extra_posts': 0, 'platform_and_native_flags_unchanged': True, 'production_access': False}))
    finally:
        await seed.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    asyncio.run(exercise(parser.parse_args().port))
