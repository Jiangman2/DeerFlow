"""Bounded, read-only deployed Gateway/edge checks for the sealed combined batch.

Reads one JSON value on stdin. Never imports app.gateway.app, creates an
application, submits a task, calls a model/search provider or writes files.
"""
import asyncio
import contextlib
from datetime import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import signal
import ssl
import stat
import sys
import time

CONFIG_SHA = 'dbde51e71421be4ee4544684faa77baf18a522ecd87db663b2bcb4280589c36b'
CA_SHA = '84c58afec9e48e1bd7b41d832ff6986b74a6565b4473f7cc77b868900b4d76af'
ORG = 'b9cfbf7f-7281-4f62-a72b-6a22379efe60'
ROOT = Path('/app/backend')
PROTECTED = ('/api/admin/dashboard/employee-clicks', '/api/admin/dashboard/feedback-actors',
             '/api/admin/competition/display', '/api/admin/competition/employees', '/api/console/token-summary')


class Refused(RuntimeError):
    pass


def require(value, code):
    if not value:
        raise Refused(code)


class Sink:
    def write(self, value): return len(value)
    def flush(self): pass


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def bounded(path, limit=2097152, nofollow=False):
    p = Path(path)
    flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0)
    if nofollow:
        require(p.resolve() == p.absolute(), 'SOURCE_SYMLINK')
        flags |= getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(str(p), flags)
    with os.fdopen(fd, 'rb') as f:
        before = os.fstat(f.fileno())
        require(stat.S_ISREG(before.st_mode) and 0 <= before.st_size <= limit, 'FILE_BOUND')
        raw = f.read(limit + 1)
        after = os.fstat(f.fileno())
    require(len(raw) == before.st_size and len(raw) <= limit
            and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'FILE_CHANGED')
    return raw


def sources(expected, root=ROOT):
    require(isinstance(expected, dict) and 0 < len(expected) <= 2000, 'SOURCE_PINS_SHAPE')
    require(all(isinstance(k, str) and re.fullmatch(r'backend/(?:app|packages/harness)/[A-Za-z0-9_./-]+\.py', k)
                and '..' not in k.split('/') and isinstance(v, str) and re.fullmatch(r'[a-f0-9]{64}', v)
                for k, v in expected.items()), 'SOURCE_PINS_SHAPE')
    started = time.monotonic(); rows = {}; total = 0
    for base in (root / 'app', root / 'packages/harness'):
        require(base.is_dir() and base.resolve() == base.absolute(), 'SOURCE_ROOT_CHANGED')
        for directory, dirs, names in os.walk(str(base), followlinks=False):
            dirs[:] = [d for d in dirs if d not in ('__pycache__', '.git', '.venv')]
            require(not any(Path(directory, d).is_symlink() for d in dirs), 'SOURCE_DIRECTORY_SYMLINK')
            for name in sorted(names):
                if not name.endswith('.py'): continue
                require(len(rows) < 2000 and time.monotonic() - started < 45, 'SOURCE_SCAN_BOUND')
                p = Path(directory, name)
                raw = bounded(p, nofollow=True); total += len(raw)
                require(total <= 33554432, 'SOURCE_TOTAL_BOUND')
                rows['backend/' + p.relative_to(root).as_posix()] = sha(raw)
    require(rows == expected, 'INSTALLED_SOURCES_MISMATCH')
    return dict(checked=len(rows), matches_sealed_gateway=True,
                source_set_sha256=sha(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()))


def configuration_content(raw, env, tavily_key_sha256=None):
    import yaml
    require(sha(raw) == CONFIG_SHA, 'CONFIG_CHANGED')
    data = yaml.safe_load(raw)
    models = data.get('models')
    require(isinstance(models, list) and len(models) == 2 and all(isinstance(m, dict) for m in models), 'MODEL_CONFIG_CHANGED')
    require(models[0].get('name') == 'deepseek-v4-flash'
            and not any(m.get('name') == 'deepseek-v4.1-flash-1017' for m in models), 'DEFAULT_OR_V41_CHANGED')
    require(bool(env.get('DeepSeek_V4_API_KEY')), 'CURRENT_MODEL_CREDENTIAL_ABSENT')
    require(env.get('OA_POLICY_FLOW_MULTI_RESULT_ENABLED') == 'true'
            and env.get('OA_POLICY_FLOW_CALLBACK_ENABLED') == 'false'
            and env.get('WEKNORA_COMPANY_KB_ENABLED') == 'true'
            and json.loads(env.get('WEKNORA_COMPANY_KB_ORGANIZATION_IDS', 'null')) == [ORG], 'EXISTING_FEATURE_FLAGS_CHANGED')
    require(env.get('DEER_FLOW_AUTH_DISABLED') != '1', 'AUTH_DISABLED_REQUESTED')
    tools = [t for t in data.get('tools', []) if t.get('name') == 'web_search']
    require(len(tools) == 1 and tools[0].get('use') == 'deerflow.community.tavily.tools:web_search_tool'
            and tools[0].get('api_key') in (None, '', '$TAVILY_API_KEY'), 'SEARCH_BINDING_CHANGED')
    tavily_matches = None
    if tavily_key_sha256 is not None:
        require(isinstance(tavily_key_sha256, str) and re.fullmatch(r'[a-f0-9]{64}', tavily_key_sha256), 'KEY_PIN_SHAPE')
        value = env.get('TAVILY_API_KEY', '')
        tavily_matches = bool(value) and hmac.compare_digest(sha(value.encode()), tavily_key_sha256)
        require(tavily_matches, 'TAVILY_PROCESS_KEY_MISMATCH')
    return tavily_matches


def configuration(tavily_key_sha256=None):
    require(os.getenv('DEER_FLOW_CONFIG_PATH') in (None, '', str(ROOT / 'config.yaml')), 'CONFIG_PATH_CHANGED')
    raw = bounded(ROOT / 'config.yaml')
    tavily_matches = configuration_content(raw, os.environ, tavily_key_sha256)
    certificates = []
    for key in ('SSL_CERT_FILE', 'REQUESTS_CA_BUNDLE'):
        value = os.getenv(key)
        require(bool(value), 'CA_ENV_ABSENT')
        require(sha(bounded(value, 8388608)) == CA_SHA, 'CA_CHANGED')
        ctx = ssl.create_default_context(cafile=value)
        require(ctx.check_hostname and ctx.verify_mode == ssl.CERT_REQUIRED, 'CA_VERIFICATION_DISABLED')
        certificates.append(dict(source=key, sha256=CA_SHA, hostname_verification=True, ca_count=len(ctx.get_ca_certs())))
    home = Path(os.getenv('DEER_FLOW_HOME') or (Path(os.getenv('DEER_FLOW_PROJECT_ROOT') or os.getcwd()) / '.deer-flow')).resolve()
    require(str(home) == '/app/backend/.deer-flow' and home.is_dir(), 'USERDATA_ROOT_CHANGED')
    display = Path(os.getenv('COMPETITION_DISPLAY_PATH') or (home / 'competition/display.json'))
    require(display == home / 'competition/display.json', 'COMPETITION_STORAGE_CHANGED')
    return dict(file_sha256=CONFIG_SHA, default_model='deepseek-v4-flash', models_count=2,
                v41_enabled=False, oa_multi_enabled=True, oa_callback_enabled=False, company_kb_enabled=True,
                company_organization_id=ORG, certificate_bundles=certificates, userdata_path=str(home),
                competition_display_path=str(display), competition_display_exists=display.exists(),
                tavily_environment_matches_expected=tavily_matches, credential_values_exported=False)


async def database():
    import asyncpg
    from deerflow.config import get_app_config
    c = get_app_config().database
    require(c.backend == 'postgres', 'POSTGRES_REQUIRED')
    conn = await asyncpg.connect(c.postgres_url.replace('postgresql+asyncpg://', 'postgresql://', 1),
        timeout=8, command_timeout=8, server_settings={'default_transaction_read_only': 'on',
        'statement_timeout': '5000', 'lock_timeout': '1000', 'application_name': 'portal-combined-activation-readonly'})
    try:
        async with conn.transaction(readonly=True, isolation='repeatable_read'):
            require(await conn.fetchval('SHOW transaction_read_only') == 'on', 'READONLY_REQUIRED')
            revisions = [r[0] for r in await conn.fetch('SELECT version_num FROM alembic_version LIMIT 8')]
            require(revisions == ['0021_oa_result_sets'], 'DATABASE_REVISION_CHANGED')
            counts = {}
            for key, sql in (
                ('active_runs', "SELECT count(*) FROM runs WHERE status IN ('pending','running')"),
                ('active_oa_jobs', "SELECT count(*) FROM oa_policy_flow_jobs WHERE status IN ('queued','launching','running')"),
                ('active_callback_leases', "SELECT count(*) FROM oa_policy_flow_jobs WHERE status IN ('succeeded','failed') AND callback_status='pending' AND lease_expires_at>CURRENT_TIMESTAMP")):
                value = await conn.fetchval(sql)
                require(type(value) is int and value >= 0, 'TASK_COUNT_SHAPE')
                counts[key] = value
            return dict(counts, status='PASS', revisions=revisions, database_readonly=True,
                        business_rows_exported=False, schema_migrated=False)
    finally:
        await conn.close()


def validate_public_display(status, raw):
    body = json.loads(raw)
    if status == 404:
        require(body == {'detail': 'competition_display_not_published'}, 'PUBLIC_DISPLAY_NOT_INSTALLED')
        return dict(http_status=404, published=False, aggregate_only_contract=True)
    require(status == 200 and isinstance(body, dict)
            and set(body) == {'title', 'items', 'updated_at', 'unavailable_count', 'refresh_seconds'}, 'PUBLIC_DISPLAY_SHAPE')
    require(isinstance(body['title'], str) and 1 <= len(body['title']) <= 60
            and isinstance(body['items'], list) and len(body['items']) <= 100
            and type(body['unavailable_count']) is int and body['unavailable_count'] >= 0
            and body['refresh_seconds'] == 10 and isinstance(body['updated_at'], str), 'PUBLIC_DISPLAY_SHAPE')
    require(datetime.fromisoformat(body['updated_at'].replace('Z', '+00:00')).utcoffset() is not None, 'PUBLIC_DISPLAY_TIMESTAMP')
    identities = set()
    for row in body['items']:
        require(isinstance(row, dict) and set(row) == {'identity', 'name', 'total_clicks', 'today_clicks'}, 'PUBLIC_DISPLAY_EXTRA_FIELDS')
        require(isinstance(row['identity'], str) and re.fullmatch(r'[a-f0-9]{64}', row['identity'])
                and row['identity'] not in identities and isinstance(row['name'], str) and 0 < len(row['name']) <= 256
                and type(row['total_clicks']) is int and type(row['today_clicks']) is int
                and 0 <= row['today_clicks'] <= row['total_clicks'], 'PUBLIC_DISPLAY_ITEM_SHAPE')
        identities.add(row['identity'])
    return dict(http_status=200, published=True, items_count=len(body['items']), aggregate_only_contract=True,
                actor_identifiers_exported=False, names_exported=False)


async def http_checks(edge=False, check_api=True):
    import httpx
    base = 'http://nginx:2026' if edge else 'http://127.0.0.1:8001'
    paths = ['/competition'] if edge else ['/health']
    if check_api:
        paths += list(PROTECTED) + ['/api/competition/display']
    rows = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5), trust_env=False, follow_redirects=False,
                                headers={'Host': 'cnoocclaw.cnooc.com.cn', 'Accept-Encoding': 'identity'}) as client:
        for path in paths:
            client.cookies.clear()
            limit = 1048576 if path == '/competition' else 65536
            async with client.stream('GET', base + path) as response:
                raw = bytearray()
                async for chunk in response.aiter_bytes(4096):
                    require(len(raw) + len(chunk) <= limit, 'HTTP_BODY_BOUND')
                    raw.extend(chunk)
                kind = response.headers.get('content-type', '').split(';', 1)[0].strip().lower()
                if path == '/competition':
                    require(response.status_code == 200 and kind == 'text/html'
                            and b'data-testid="competition-display"' in raw, 'EDGE_PAGE_NOT_ACCEPTED')
                    row = dict(path=path, http_status=200, competition_page_marker=True)
                elif path == '/health':
                    require(response.status_code == 200 and kind == 'application/json'
                            and json.loads(raw) == {'status': 'healthy', 'service': 'deer-flow-gateway'}, 'LOCAL_HEALTH_NOT_ACCEPTED')
                    row = dict(path=path, http_status=200)
                elif path in PROTECTED:
                    require(response.status_code == 401 and kind == 'application/json', 'UNAUTHENTICATED_ROUTE_NOT_DENIED')
                    row = dict(path=path, http_status=401, unauthenticated_denied=True)
                else:
                    require(kind == 'application/json', 'PUBLIC_DISPLAY_NOT_JSON')
                    row = dict(path=path, **validate_public_display(response.status_code, raw))
                rows.append(row)
    return dict(status='PASS', scope='SERVICE_NGINX_HTTP_NOT_EXTERNAL_TLS' if edge else 'GATEWAY_LOOPBACK',
                requests=len(rows), checks=rows, credentials_sent=False, raw_bodies_exported=False)


async def inspect(args):
    require(isinstance(args, dict) and set(args) <= {'mode', 'expected', 'tavily_key_sha256', 'database', 'check_api'}, 'INPUT_SHAPE')
    mode = args.get('mode')
    require(mode in ('preflight', 'runtime', 'edge'), 'MODE_INVALID')
    require(type(args.get('check_api', True)) is bool and type(args.get('database', False)) is bool, 'INPUT_FLAGS')
    result = dict(schema='portal-combined-live-child.v1', mode=mode, status='INCOMPLETE', provider_requests=0,
                  application_started=False, files_modified=False, credential_values_exported=False,
                  raw_errors_exported=False, database_readonly=False, schema_migrated=False)
    if mode == 'edge':
        require(not args.get('database') and 'expected' not in args and 'tavily_key_sha256' not in args, 'EDGE_INPUT_SCOPE')
        result['http'] = await http_checks(edge=True)
        result['status'] = 'COMBINED_EDGE_HTTP_ACCEPTED'
        return result
    require(isinstance(args.get('expected'), dict) and len(args['expected']) == (583 if mode == 'preflight' else 588), 'SOURCE_SET_SIZE')
    if mode == 'runtime': require(args.get('tavily_key_sha256') is not None, 'RUNTIME_KEY_PIN_REQUIRED')
    result['sources'] = sources(args['expected'])
    result['configuration'] = configuration(args.get('tavily_key_sha256') if mode == 'runtime' else None)
    if mode == 'preflight' or args.get('database'):
        result['database'] = await database(); result['database_readonly'] = True
    result['http'] = await http_checks(check_api=args.get('check_api', True) if mode == 'runtime' else False)
    result['status'] = 'COMBINED_PREFLIGHT_ACCEPTED' if mode == 'preflight' else 'COMBINED_RUNTIME_ACCEPTED'
    return result


def main():
    sys.path.insert(0, str(ROOT))
    try:
        raw = sys.stdin.buffer.read(524289)
        require(0 < len(raw) <= 524288, 'INPUT_BOUND')
        if hasattr(signal, 'SIGALRM'):
            def deadline(signum, frame): raise Refused('PROCESS_DEADLINE')
            signal.signal(signal.SIGALRM, deadline); signal.alarm(100)
        with contextlib.redirect_stdout(Sink()), contextlib.redirect_stderr(Sink()):
            result = asyncio.run(asyncio.wait_for(inspect(json.loads(raw)), timeout=95))
    except BaseException as exc:
        result = dict(schema='portal-combined-live-child.v1', status='INCOMPLETE', error_class=type(exc).__name__,
                      error_code=str(exc) if isinstance(exc, Refused) else 'LIVE_CHECK_INCOMPLETE',
                      raw_errors_exported=False, credential_values_exported=False, provider_requests=0,
                      application_started=False, files_modified=False)
    finally:
        if hasattr(signal, 'SIGALRM'): signal.alarm(0)
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] != 'INCOMPLETE' else 1


if __name__ == '__main__': raise SystemExit(main())
