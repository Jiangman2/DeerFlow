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
            jobs = await conn.fetchval("SELECT count(*) FROM oa_policy_flow_jobs WHERE status IN ('queued','running')")
        return {'status': 'PASS', 'revisions': revisions, 'active_runs': runs, 'active_oa_jobs': jobs,
                'database_readonly': True, 'schema_migrated': False}
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
        result = asyncio.run(database()) if args['action'] == 'database' else runtime(args['pin'])
except Exception as exc:
    result = {'status': 'INCOMPLETE', 'error_class': type(exc).__name__, 'raw_errors_exported': False}
print(json.dumps(result, sort_keys=True))
