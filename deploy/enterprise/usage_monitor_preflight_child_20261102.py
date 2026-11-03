"""Bounded source, scalar SQL and Redis readiness probe. No business bodies."""
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import time

SOURCES = [
    'app/gateway/app.py', 'app/gateway/routers/console.py',
    'app/gateway/dashboard_cache.py', 'app/gateway/codex_responses_adapter.py',
    'app/gateway/routers/oa_policy_flow.py', 'app/gateway/oa_policy_flow_service.py',
    'packages/harness/deerflow/models/factory.py',
    'packages/harness/deerflow/community/media_generation/tools.py',
    'packages/harness/deerflow/persistence/run/model.py',
    'packages/harness/deerflow/sandbox/tools.py',
    'packages/harness/deerflow/community/aio_sandbox/aio_sandbox.py',
    'packages/harness/deerflow/persistence/migrations/versions/0022_work_integration_state.py',
]
NEW_PATHS = ['app/gateway/account_activity.py', 'app/gateway/model_monitor.py',
    'app/gateway/oa_policy_flow_metrics.py',
    'app/gateway/routers/model_monitor.py', 'packages/harness/deerflow/observability/model_metrics.py',
    'packages/harness/deerflow/sandbox/text_read_policy.py',
    'packages/harness/deerflow/persistence/migrations/versions/0023_data_lake_work_grants.py']
CONFIG_SHA = 'bf39d16f673d2259a0430357e7a9a435482add27194aa947a0e34cdd594f1fb7'
INSPECT_SERVICES = INSPECT_SERVICES_PLACEHOLDER
ACTIVITY_SQL = """SELECT to_char(timezone('Asia/Shanghai',created_at),'YYYY-MM-DD') AS day,
sum(CASE WHEN total_tokens>0 THEN total_tokens ELSE 0 END),
sum(CASE WHEN total_input_tokens>0 THEN total_input_tokens ELSE 0 END),
sum(CASE WHEN total_output_tokens>0 THEN total_output_tokens ELSE 0 END), count(*),
max(CASE WHEN status IN ('success','error','timeout','interrupted') THEN
CASE WHEN extract(epoch FROM updated_at-created_at)>0 THEN extract(epoch FROM updated_at-created_at) ELSE 0 END ELSE 0 END)
FROM runs WHERE user_id=$1 AND created_at<=CURRENT_TIMESTAMP
GROUP BY day ORDER BY day LIMIT 20001"""


def plan_summary(plan):
    result, stack = [], [plan[0]['Plan']]
    while stack and len(result) < 32:
        node = stack.pop()
        result.append({key: node[key] for key in ('Node Type', 'Parallel Aware', 'Index Name', 'Plan Rows', 'Plan Width') if key in node})
        stack.extend(node.get('Plans', []))
    return {'nodes': result, 'node_limit_reached': bool(stack), 'raw_plan_exported': False}


async def database_probe(cfg):
    import asyncpg
    db = cfg.get('database', {})
    url = db.get('postgres_url', '')
    if url.startswith('$'):
        url = os.environ.get(url[1:], '')
    if db.get('backend') != 'postgres' or not url:
        raise ValueError('DATABASE_BINDING')
    con = await asyncpg.connect(url.replace('postgresql+asyncpg://', 'postgresql://', 1), timeout=5, command_timeout=3,
        server_settings={'default_transaction_read_only': 'on', 'statement_timeout': '2000', 'lock_timeout': '250',
            'max_parallel_workers_per_gather': '0', 'work_mem': '4MB', 'application_name': 'portal-usage-monitor-preflight'})
    try:
        async with con.transaction(readonly=True):
            if await con.fetchval('SHOW transaction_read_only') != 'on':
                raise ValueError('READONLY_REQUIRED')
            revisions = await con.fetch('SELECT version_num FROM alembic_version LIMIT 3')
            if [r[0] for r in revisions] != ['0022_work_integration_state']:
                raise ValueError('SCHEMA_VERSION_CHANGED')
            indexes = await con.fetch("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' AND tablename='runs' LIMIT 32")
            owner_index = any('(user_id' in r['indexdef'] for r in indexes)
            date_index = any('(created_at' in r['indexdef'] for r in indexes)
            if not owner_index or not date_index:
                raise ValueError('OWNER_AND_DATE_INDEX_REQUIRED')
            if not await con.fetchval('SELECT pg_try_advisory_xact_lock(732492018)'):
                return {'status': 'BUSY_RETRY_LATER', 'transaction_read_only': True}
            # Date index limits selection; do not fetch usernames, prompts or errors.
            owner = await con.fetchval('SELECT user_id FROM runs WHERE user_id IS NOT NULL ORDER BY created_at DESC LIMIT 1')
            if not owner:
                return {'status': 'NO_OWNER_SAMPLE', 'transaction_read_only': True}
            plan = json.loads(await con.fetchval('EXPLAIN (FORMAT JSON) ' + ACTIVITY_SQL, owner))
            start = time.monotonic()
            rows = await con.fetch(ACTIVITY_SQL, owner)
            return {'status': 'PASS', 'transaction_read_only': True, 'alembic_revision': revisions[0][0],
                'owner_index_present': owner_index, 'created_index_present': date_index,
                'query_count': 7, 'aggregate_days': len(rows), 'elapsed_ms': round((time.monotonic()-start)*1000),
                'owner_exported': False, 'usage_values_exported': False, 'query_plan': plan_summary(plan)}
    finally:
        await con.close(timeout=2)


async def redis_probe():
    from redis.asyncio import Redis
    names = ('DEER_FLOW_MODEL_METRICS_REDIS_URL', 'DEER_FLOW_DASHBOARD_REDIS_URL', 'DEER_FLOW_STREAM_BRIDGE_REDIS_URL')
    selected = next((name for name in names if os.getenv(name)), None)
    result = {'configured': selected is not None, 'selected_variable': selected, 'values_exported': False,
        'writes': 0, 'metrics_enabled': os.getenv('DEER_FLOW_MODEL_METRICS_ENABLED', '').lower() == 'true',
        'namespace_present': bool(os.getenv('DEER_FLOW_MODEL_METRICS_NAMESPACE'))}
    if not selected:
        return dict(result, status='NOT_CONFIGURED')
    client = Redis.from_url(os.environ[selected], socket_connect_timeout=0.5, socket_timeout=0.5, decode_responses=True, max_connections=1)
    try:
        async with asyncio.timeout(2):
            if not await client.ping():
                raise ValueError('PING_FAILED')
            info = await client.info('server')
            # No keys are supplied and this fixed script has no Redis commands.
            if await client.eval('return 1', 0) != 1:
                raise ValueError('EVAL_UNAVAILABLE')
            mode = info.get('redis_mode')
            version = info.get('redis_version', '')
            return dict(result, status='PASS' if mode == 'standalone' else 'UNSUPPORTED_TOPOLOGY',
                mode=mode if mode in ('standalone','cluster','sentinel') else 'other',
                major_version=int(version.split('.')[0]) if version.split('.')[0].isdigit() else None,
                ping=True, fixed_readonly_eval=True)
    except Exception as exc:
        return dict(result, status='UNAVAILABLE', error_class=type(exc).__name__)
    finally:
        await client.aclose()


async def main():
    logging.disable(logging.CRITICAL)
    out = {'status': 'INCOMPLETE', 'schema': 'portal-usage-monitor-preflight-child.v1',
        'application_started': False, 'provider_requests': 0, 'database_modified': False,
        'files_modified': False, 'credential_values_exported': False, 'business_text_exported': False}
    step = 'sources'
    try:
        root = Path('/app/backend')
        observed = {}
        for rel in SOURCES:
            p = root/rel
            if not p.is_file() or p.is_symlink() or p.stat().st_size > 2*1024*1024:
                raise ValueError('SOURCE_BOUND')
            observed[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        for rel in NEW_PATHS:
            if (root/rel).exists() or (root/rel).is_symlink():
                raise ValueError('UNEXPECTED_NEW_SOURCE')
            observed[rel] = None
        out['sources'] = observed
        step = 'configuration'
        import yaml
        p = root/'config.yaml'
        if p.stat().st_size > 2*1024*1024:
            raise ValueError('CONFIG_BOUND')
        raw = p.read_bytes()
        if hashlib.sha256(raw).hexdigest() != CONFIG_SHA:
            raise ValueError('CONFIG_CHANGED')
        cfg = yaml.safe_load(raw)
        out['configuration'] = {'sha256': CONFIG_SHA, 'max_recursion_limit': cfg.get('max_recursion_limit')}
        if INSPECT_SERVICES:
            step = 'readonly-owner-scalar-query'
            out['database_access_may_have_occurred'] = True
            out['database'] = await database_probe(cfg)
            step = 'readonly-redis-readiness'
            out['redis'] = await redis_probe()
        out['status'] = 'PASS'
    except Exception as exc:
        out.update(error_class=type(exc).__name__, failed_step=step)
    print(json.dumps(out, sort_keys=True))


if __name__ == '__main__':
    asyncio.run(main())
