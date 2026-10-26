"""Fill missing native workspace memberships for current platform admins.

Existing roles and both identity systems' admin flags are read, never written.
Native invites are insert-only; a concurrent manual join is read back, not edited.
"""
import asyncio
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys

COMMON = None
BOOTSTRAP = None
ORG = 'b9cfbf7f-7281-4f62-a72b-6a22379efe60'
KB = 'bd83e659-9d9c-429a-acf1-ead01037cfa1'
OWNER = '10000'
EMAIL = 'jiangman2@cnooc.com.cn'
MAX_ADMINS = 32
DEADLINE = 600
COMPLETE = 'EXISTING_ADMIN_MEMBERSHIPS_ACCEPTED_ROLES_PRESERVED'
SOURCES = None


class Refused(Exception):
    pass


def require(ok, code):
    if not ok:
        raise Refused(code)


def module(source, name):
    result = {'__name__': name}
    exec(compile(source, '<' + name + '>', 'exec'), result)
    return result


def classify(platform, native, owners, members):
    require(0 < len(platform) <= MAX_ADMINS, 'ADMIN_INVENTORY_LIMIT')
    emails = [str(r['email']).lower() for r in platform]
    require(len(set(emails)) == len(emails) and all(emails), 'PLATFORM_ADMIN_IDENTITY_AMBIGUOUS')
    identities, workspaces, unprovisioned = [], {}, 0
    roles = {}
    for row in members:
        tenant, role = int(row['tenant_id']), row['role']
        require(tenant not in roles and role in ('admin', 'editor', 'viewer'), 'MEMBERSHIP_AMBIGUOUS')
        roles[tenant] = role
    for p in platform:
        email = str(p['email']).lower()
        require(p['system_role'] == 'admin' and p['is_disabled'] is False and p['needs_setup'] is False, 'INELIGIBLE_PLATFORM_ADMIN')
        matches = [n for n in native if str(n['email']).lower() == email]
        require(len(matches) <= 1, 'NATIVE_ADMIN_IDENTITY_AMBIGUOUS')
        if not matches:
            unprovisioned += 1
            identities.append((str(p['id']), email, None))
            continue
        n = matches[0]
        require(n['is_active'] is True and n['is_system_admin'] is True and type(n['can_access_all_tenants']) is bool, 'NATIVE_ADMIN_FLAGS_REQUIRE_REVIEW')
        own = {int(o['tenant_id']) for o in owners if o['user_id'] == n['id']}
        require(bool(own) and all(0 < t <= 9223372036854775807 for t in own), 'ADMIN_OWNED_WORKSPACE_REQUIRED')
        default = int(n['tenant_id'])
        require(default in own or len(own) == 1, 'ADMIN_WORKSPACE_AMBIGUOUS')
        tenant = default if default in own else next(iter(own))
        workspaces.setdefault(tenant, []).append(str(n['id']))
        identities.append((str(p['id']), email, str(n['id']), default, n['is_active'], n['is_system_admin'], n['can_access_all_tenants'], tuple(sorted(own))))
    require(int(OWNER) in workspaces and roles.get(int(OWNER)) == 'admin', 'SPACE_OWNER_MEMBERSHIP_CHANGED')
    current = {t: roles[t] for t in workspaces if t in roles}
    return {'identities': sorted(identities), 'workspaces': {t: sorted(v) for t, v in workspaces.items()},
            'roles': current, 'missing': sorted(set(workspaces) - set(current)),
            'counts': {'platform_admin_accounts': len(platform), 'provisioned_admin_accounts': len(platform) - unprovisioned,
                       'not_yet_provisioned': unprovisioned, 'owned_workspaces': len(workspaces),
                       'missing_memberships': len(workspaces) - len(current),
                       'existing_roles': {r: list(current.values()).count(r) for r in ('admin', 'editor', 'viewer')}}}


class AdminDatabase:
    def __init__(self, base):
        self.base = base

    async def snapshot(self):
        p = (await self.base.read(self.base.platform_connect, [(
            "SELECT id,email,system_role,is_disabled,needs_setup FROM users WHERE system_role='admin' AND is_disabled=false AND needs_setup=false ORDER BY id LIMIT $1",
            (MAX_ADMINS + 1,))]))[0]
        require(0 < len(p) <= MAX_ADMINS, 'ADMIN_INVENTORY_LIMIT')
        emails = [str(r['email']).lower() for r in p]
        n = (await self.base.read(self.base.native_connect, [(
            'SELECT id,email,tenant_id,is_active,is_system_admin,can_access_all_tenants FROM users WHERE deleted_at IS NULL AND lower(email)=ANY($1::text[]) ORDER BY id LIMIT $2',
            (emails, MAX_ADMINS * 2 + 1))]))[0]
        require(len(n) <= MAX_ADMINS * 2, 'NATIVE_ADMIN_INVENTORY_LIMIT')
        owners = (await self.base.read(self.base.native_connect, [(
            "SELECT user_id,tenant_id FROM tenant_members WHERE deleted_at IS NULL AND status='active' AND role='owner' AND user_id=ANY($1::text[]) ORDER BY user_id,tenant_id LIMIT $2",
            ([r['id'] for r in n], MAX_ADMINS * 8 + 1))]))[0]
        require(len(owners) <= MAX_ADMINS * 8, 'ADMIN_OWNERSHIP_LIMIT')
        tenants = sorted({int(r['tenant_id']) for r in owners})
        members = (await self.base.read(self.base.native_connect, [(
            'SELECT tenant_id,role FROM organization_tenant_members WHERE organization_id=$1 AND tenant_id=ANY($2::bigint[]) ORDER BY tenant_id LIMIT $3',
            (ORG, tenants, len(tenants) + 1))]))[0]
        return classify(p, n, owners, members)


def preserve(before, current):
    require(before['identities'] == current['identities'] and before['workspaces'] == current['workspaces'], 'ADMIN_IDENTITY_OR_OWNERSHIP_CHANGED')
    rank = {'viewer': 0, 'editor': 1, 'admin': 2}
    for tenant, role in before['roles'].items():
        require(tenant in current['roles'] and rank[current['roles'][tenant]] >= rank[role], 'EXISTING_MEMBER_PERMISSION_REDUCED')


async def complete_memberships(db, api, authority, report, emit):
    baseline = await db.snapshot()
    report['initial_counts'] = baseline['counts']
    report['phase'] = 'VERIFY_EXISTING_ADMIN_MEMBERSHIPS'
    checked = 0
    for tenant in sorted(baseline['workspaces']):
        # End every readonly transaction before network I/O. Revalidate the
        # entire small admin set before each single, non-retried native POST.
        await authority()
        current = await db.snapshot()
        preserve(baseline, current)
        if tenant in current['roles']:
            report['memberships_preserved_without_post'] += 1
            baseline['roles'][tenant] = current['roles'][tenant]
        else:
            require(tenant != int(OWNER), 'OWNER_INVITE_FORBIDDEN')
            require(report['membership_post_requests'] < MAX_ADMINS, 'POST_BUDGET_EXCEEDED')
            report['phase'] = 'INVITE_MISSING_ADMIN_WORKSPACE'
            report['membership_post_requests'] += 1
            report['membership_mutation_may_have_applied'] = True
            acknowledged = False
            try:
                await asyncio.wait_for(api.invite(tenant), timeout=20)
                acknowledged = True
            except Exception:
                report['post_responses_unconfirmed'] += 1
            after = await db.snapshot()
            preserve(baseline, after)
            require(tenant in after['roles'], 'INVITE_NOT_CONFIRMED_DO_NOT_REPLAY')
            role = after['roles'][tenant]
            report['confirmed_after_post'][role] += 1
            if not acknowledged:
                # Includes a manual membership that won the insert race, or
                # an invite that committed before its response was lost.
                report['uncertain_posts_resolved_by_readback'] += 1
            # Protect this newly observed role on every subsequent read too.
            baseline['roles'][tenant] = role
        checked += 1
        emit({'phase': 'ADMIN_MEMBERSHIP_PROGRESS', 'checked': checked, 'posts': report['membership_post_requests'],
              'preserved': report['memberships_preserved_without_post']})
    await authority()
    final = await db.snapshot()
    preserve(baseline, final)
    report['final_counts'] = final['counts']
    require(not final['missing'], 'ADMIN_MEMBERSHIPS_REMAIN_DO_NOT_REPLAY')
    report['admin_flags_preserved'] = True
    report['existing_membership_permissions_preserved'] = True
    require(not final['counts']['not_yet_provisioned'], 'ADMIN_FIRST_KNOWLEDGE_USE_REQUIRED')
    report.update(status=COMPLETE, phase='COMPLETE')


async def run(report, emit):
    import asyncpg
    import httpx
    from deerflow.config import get_app_config
    from app.gateway import weknora_identity as identity

    common, bootstrap = module(COMMON, 'admin_enrollment_helpers'), module(BOOTSTRAP, 'admin_login_helpers')
    config = get_app_config().database
    require(config.backend == 'postgres', 'POSTGRES_REQUIRED')

    async def platform_connect():
        return await asyncpg.connect(config.postgres_url.replace('postgresql+asyncpg://', 'postgresql://', 1), timeout=5, command_timeout=10)

    base = common['Database'](platform_connect, identity._connect_weknora_database)
    db = AdminDatabase(base)
    # Full discovery runs before even logging in or issuing membership writes.
    report['initial_counts'] = (await db.snapshot())['counts']
    administrator = await bootstrap['platform_admin']()
    async with httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=False) as client:
        api = common['API'](client, identity.weknora_endpoint, report)
        session = None
        report['phase'] = 'LOGIN_EXISTING_SPACE_OWNER'
        for password in identity._password_candidates(administrator['id'], EMAIL):
            report['login_requests'] += 1
            login = await api.request('POST', '/auth/login', {'email': EMAIL, 'password': password}, login=True)
            if login is not None:
                session = identity._normalise_login_response(login)
                break
        require(session is not None, 'EXISTING_OWNER_LOGIN_REQUIRED')
        require(str(session.user.get('email', '')).lower() == EMAIL and session.user.get('is_system_admin') is True
                and bootstrap['tenant_for_owner'](session) == OWNER, 'OWNER_SESSION_MISMATCH')
        api.headers = {'Authorization': 'Bearer ' + session.token, 'X-Tenant-ID': OWNER}

        async def authority():
            await base.authority(administrator['id'], session.user['id'])
            await api.organization(session.user['id'])

        await complete_memberships(db, api, authority, report, emit)


def initial_report():
    return {'schema': 'portal-weknora-company-admin-enrollment.v1', 'status': 'INCOMPLETE', 'phase': 'SOURCE_CHECK',
            'organization_id': ORG, 'knowledge_base_id': KB, 'maximum_admin_accounts': MAX_ADMINS,
            'maximum_membership_posts': MAX_ADMINS, 'membership_post_requests': 0, 'memberships_preserved_without_post': 0,
            'confirmed_after_post': {'viewer': 0, 'editor': 0, 'admin': 0}, 'post_responses_unconfirmed': 0,
            'uncertain_posts_resolved_by_readback': 0, 'membership_mutation_may_have_applied': False, 'login_requests': 0,
            'accounts_created': 0, 'account_roles_changed': False, 'admin_flags_preserved': None,
            'existing_membership_permissions_preserved': None, 'database_direct_writes': 0,
            'credentials_exported': False, 'user_list_exported': False, 'documents_read': False,
            'provider_requests': 0, 'automatic_retry': False, 'workload_modified': False, 'gateway_restarted': False,
            'scope': 'CURRENT_ACTIVE_SETUP_COMPLETE_PLATFORM_ADMINS_OWNED_DEFAULT_WORKSPACES',
            'browser_acceptance_pending': True}


def main():
    report, output = initial_report(), sys.stdout

    def emit(value):
        print(json.dumps(value, sort_keys=True), file=output, flush=True)

    try:
        require(COMMON and BOOTSTRAP and SOURCES, 'PACKAGED_SCRIPT_REQUIRED')
        for relative, digest in SOURCES.items():
            path = Path('/app/backend') / relative
            require(path.is_file() and not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest() == digest, 'INSTALLED_SOURCE_CHANGED')
        sys.path.insert(0, '/app/backend')
        common = module(COMMON, 'admin_heartbeat')
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            asyncio.run(asyncio.wait_for(common['with_heartbeat'](run(report, emit)), timeout=DEADLINE))
    except BaseException as exc:
        report.update(error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, Refused) else 'ADMIN_OPERATION_STOPPED_INSPECT_REPORT', raw_errors_exported=False)
    emit(report)
    return 0 if report['status'] == COMPLETE else 1


if __name__ == '__main__':
    raise SystemExit(main())
