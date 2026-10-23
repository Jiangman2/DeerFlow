"""Read-only live checks, without application startup or schema bootstrap."""
import asyncio
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import urllib.request


async def database():
    import asyncpg
    from deerflow.config import get_app_config
    c = get_app_config().database
    assert c.backend == 'postgres'
    conn = await asyncpg.connect(c.postgres_url.replace('postgresql+asyncpg://', 'postgresql://', 1), timeout=10, command_timeout=15)
    try:
        async with conn.transaction(readonly=True):
            revisions = [r[0] for r in await conn.fetch('SELECT version_num FROM alembic_version LIMIT 8')]
            runs = await conn.fetchval("SELECT count(*) FROM runs WHERE status IN ('pending','running')")
            jobs = await conn.fetchval("SELECT count(*) FROM oa_policy_flow_jobs WHERE status IN ('queued','launching','running')")
            callbacks = await conn.fetchval("SELECT count(*) FROM oa_policy_flow_jobs WHERE status IN ('succeeded','failed') AND callback_status = 'pending' AND lease_expires_at > CURRENT_TIMESTAMP")
        return {'status': 'PASS', 'revisions': revisions, 'active_runs': runs, 'active_oa_jobs': jobs,
                'active_callback_leases': callbacks,
                'database_readonly': True, 'schema_migrated': False}
    finally:
        await conn.close()


async def task_inventory():
    """Only aggregate lifecycle metadata; never select identities or content."""
    import asyncpg
    from deerflow.config import get_app_config
    c = get_app_config().database
    assert c.backend == 'postgres'
    conn = await asyncpg.connect(c.postgres_url.replace('postgresql+asyncpg://', 'postgresql://', 1),
                                 timeout=10, command_timeout=15)
    try:
        async with conn.transaction(readonly=True, isolation='repeatable_read'):
            revisions = [r[0] for r in await conn.fetch('SELECT version_num FROM alembic_version LIMIT 8')]
            sampled_at = await conn.fetchval('SELECT CURRENT_TIMESTAMP')
            groups = {}
            for table, statuses, key in (
                    ('runs', ['pending', 'running'], 'run_groups'),
                    ('oa_policy_flow_jobs', ['queued', 'launching', 'running'], 'oa_groups'),
                    ('oa_policy_flow_jobs', ['succeeded', 'failed'], 'callback_groups')):
                # table comes exclusively from the fixed tuple above; statuses
                # are bound parameters. This transaction never calls a service.
                rows = await conn.fetch('''SELECT status, count(*) AS count,
                    min(created_at) AS oldest_created_at, min(updated_at) AS oldest_updated_at,
                    max(updated_at) AS latest_updated_at,
                    count(*) FILTER (WHERE lease_expires_at IS NULL) AS lease_missing,
                    count(*) FILTER (WHERE lease_expires_at > CURRENT_TIMESTAMP) AS lease_unexpired,
                    count(*) FILTER (WHERE lease_expires_at <= CURRENT_TIMESTAMP) AS lease_expired,
                    count(*) FILTER (WHERE updated_at < CURRENT_TIMESTAMP - INTERVAL '1 hour') AS not_updated_for_one_hour
                    FROM ''' + table + ''' WHERE status = ANY($1::text[])'''
                    + (" AND callback_status = 'pending'" if key == 'callback_groups' else '')
                    + ' GROUP BY status ORDER BY status', statuses)
                groups[key] = []
                for row in rows:
                    item = dict(row)
                    assert item['status'] in statuses
                    for k in ('oldest_created_at', 'oldest_updated_at', 'latest_updated_at'):
                        item[k] = item[k].isoformat() if item[k] is not None else None
                    groups[key].append(item)
        return dict(groups, status='PASS', revisions=revisions, sampled_at_utc=sampled_at.isoformat(),
                    active_runs=sum(row['count'] for row in groups['run_groups']),
                    active_oa_jobs=sum(row['count'] for row in groups['oa_groups']),
                    active_callback_leases=sum(row['lease_unexpired'] for row in groups['callback_groups']),
                    database_readonly=True, schema_migrated=False,
                    task_content_or_id_exported=False, expired_lease_does_not_authorize_rollout=True)
    finally:
        await conn.close()


def runtime(pin):
    from deerflow.persistence import bootstrap, oa_schema_compatibility
    assert hashlib.sha256(Path(bootstrap.__file__).read_bytes()).hexdigest() == pin['sources']['bootstrap']
    assert hashlib.sha256(Path(oa_schema_compatibility.__file__).read_bytes()).hexdigest() == pin['sources']['helper']
    assert bootstrap._get_head_revision() == '0020_ppt_confirmation'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:%d/health' % pin['port'], timeout=5) as response:
        assert response.status == 200
        data = response.read(16385)
        assert len(data) <= 16384 and isinstance(json.loads(data), dict)
    return {'status': 'PASS', 'sources': pin['sources'], 'migration_head': '0020_ppt_confirmation',
            'local_health_http_status': 200, 'provider_requests': 0, 'schema_migrated': False}


try:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        args = json.loads(sys.argv[1])
        if args['action'] == 'database':
            result = asyncio.run(database())
        elif args['action'] == 'tasks':
            result = asyncio.run(task_inventory())
        elif args['action'] == 'runtime':
            result = runtime(args['pin'])
        else:
            raise ValueError('UNSUPPORTED_CHECK_ACTION')
except Exception as exc:
    result = {'status': 'INCOMPLETE', 'error_class': type(exc).__name__, 'raw_errors_exported': False}
print(json.dumps(result, sort_keys=True))
