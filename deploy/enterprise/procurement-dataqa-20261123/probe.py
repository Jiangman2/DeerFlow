"""Gateway-local read-only proof and isolated candidate transport smoke tests."""
import asyncio
import hashlib
import json
import os
import sys
import tempfile
import time
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.chdir('/app/backend')
sys.path[:0] = ['/app/backend', '/app/backend/packages/harness']

import httpx
from app.gateway.procurement_review.config import ReviewConfig
from deerflow.config.app_config import get_app_config

data = json.load(sys.stdin)
cfg = get_app_config()
review = ReviewConfig.from_env()
assert review is not None and review.job_timeout == 3600, 'ENABLED_REVIEW_TIMEOUT_CHANGED'
assert review.owner_user_id == '3676d214-2c4e-4644-b295-ac5483831f5d' and review.assistant_id == '技术委托要求审查助手', 'REVIEW_BINDING_CHANGED'
loop = cfg.loop_detection
override = loop.tool_freq_overrides.get('bash')
assert loop.enabled and override and (override.warn, override.hard_limit) == (80, 100), 'BASH_80_100_NOT_CONFIGURED'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_sha(path):
    path = Path(path)
    return sha(path.read_bytes()) if path.is_file() else None


modules = ('markdown_report.py',)
proof = {
    'source_sha256': {n: file_sha('/app/backend/app/gateway/procurement_review/' + n) for n in modules},
    'config_yaml_sha256': file_sha('/app/backend/config.yaml'),
    'job_timeout': review.job_timeout, 'bash_warn': override.warn, 'bash_limit': override.hard_limit,
    'loop_sha256': sha(json.dumps(loop.model_dump(), sort_keys=True).encode()),
    'owner_user_id': review.owner_user_id, 'assistant_id': review.assistant_id,
    'other_code_sha256': {p: file_sha(p) for p in data.get('other_paths', [])},
}
from app.gateway.procurement_review.files import reference_inventory
proof['reference_sha256'] = reference_inventory(review.reference_root)['manifest_sha256']
from app.gateway.routers.uploads import _auto_convert_documents_enabled, _markdown_companion_has_content, _require_parsed_text_enabled
from deerflow.utils.file_conversion import convert_file_to_markdown
proof['ordinary_upload_excel_conversion_enabled'] = _auto_convert_documents_enabled(cfg)
proof['ordinary_upload_requires_parsed_text'] = _require_parsed_text_enabled()

if data.get('database'):
    from sqlalchemy import create_engine, text
    assert cfg.database.backend == 'postgres', 'POSTGRES_REQUIRED'
    engine = create_engine(cfg.database.app_sync_sqlalchemy_url, connect_args={'connect_timeout': 5}, echo=False)
    try:
        with engine.connect() as conn:
            with conn.begin():
                conn.execute(text('SET TRANSACTION READ ONLY'))
                conn.execute(text("SET LOCAL statement_timeout='10000'"))
                proof['schema_versions'] = list(conn.execute(text('SELECT version_num FROM alembic_version')).scalars())
                proof['active_jobs'] = conn.execute(text("SELECT count(*) FROM procurement_review_jobs WHERE state IN ('queued','launching','running','callback')")).scalar_one()
                proof['active_review_runs'] = conn.execute(text("SELECT count(*) FROM runs WHERE status IN ('pending','running') AND metadata_json->>'source'='procurement_review'")).scalar_one()
    finally:
        engine.dispose()

if data.get('candidate'):
    # Distinct package, no replacement of production imports and no production
    # worker start. All SQL queue and model operations below are synthetic.
    sources = {n: Path('/app/backend/app/gateway/procurement_review/' + n).read_text(encoding='utf-8') for n in ('config.py', 'rules.py', 'files.py', 'markdown_report.py', 'stream.py', 'service.py', 'router.py')}
    assert set(data['candidate']) == {'markdown_report.py'}, 'SINGLE_MODULE_ONLY'
    sources.update(data['candidate'])
    root = 'app.gateway._procurement_stream_candidate'
    package = types.ModuleType(root)
    package.__path__ = []
    sys.modules[root] = package
    loaded = {}
    for filename in ('config.py', 'rules.py', 'files.py', 'markdown_report.py', 'stream.py', 'service.py', 'router.py'):
        name = root + '.' + filename[:-3]
        module = types.ModuleType(name)
        module.__package__ = root
        module.__file__ = name
        sys.modules[name] = module
        exec(compile(sources[filename], name, 'exec'), module.__dict__)
        loaded[filename] = module
    candidate_config = loaded['config.py'].ReviewConfig.from_env()
    assert {'.json', '.txt', '.xlsx', '.xls', '.csv'} <= loaded['router.py'].ALLOWED, 'NEW_MATERIAL_TYPES_NOT_ENABLED'
    assert candidate_config.owner_user_id == review.owner_user_id and candidate_config.job_timeout == 3600
    # Explicitly demonstrate startup configuration has no callback dependency.
    preserved = {k: os.environ.pop(k, None) for k in ('PROCUREMENT_REVIEW_CALLBACK_URL', 'PROCUREMENT_REVIEW_CALLBACK_KEY')}
    try:
        assert loaded['config.py'].ReviewConfig.from_env().callback_url == ''
    finally:
        for k, v in preserved.items():
            if v is not None:
                os.environ[k] = v

    async def synthetic():
        from fastapi import FastAPI
        rule = loaded['rules.py']
        validator = loaded['markdown_report.py']
        service_module = loaded['service.py']
        stream_module = loaded['stream.py']
        app = FastAPI()
        app.include_router(loaded['router.py'].router)
        app.state.procurement_review_service = SimpleNamespace(config=candidate_config, is_running=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://synthetic') as client:
            endpoint = loaded['config.py'].ENDPOINT
            assert (await client.post(endpoint, content=b'bad')).status_code == 401
            headers = {'Authorization': 'Bearer ' + candidate_config.submit_key}
            assert (await client.post(endpoint, headers=headers, content=b'bad')).status_code == 415
            assert (await client.post(endpoint, headers=headers, files={'wrong': (None, 'value')})).status_code == 422
        j = {'id': 'synthetic-job', 'request_id': 'synthetic-request', 'state': 'running', 'payload': {
            'delivery_mode': 'stream', 'rule_version': rule.RULE_VERSION, 'owner_user_id': review.owner_user_id,
            'assistant_id': review.assistant_id, 'thread_id': 'synthetic-thread', 'run_id': 'synthetic-run', 'deadline': time.time() + 60,
        }}
        run = {'status': 'error', 'thread_id': 'synthetic-thread', 'metadata': {'procurement_review_job_id': j['id']}}
        app.state.run_store = SimpleNamespace(get=AsyncMock(return_value=run))
        repo = SimpleNamespace(save=AsyncMock(return_value=True), get=AsyncMock(side_effect=lambda _: j))
        service = service_module.ReviewService(app, repo, candidate_config)
        service.deliver = AsyncMock(side_effect=AssertionError('callback forbidden'))
        await service.step(j)
        assert j['state'] == 'result_ready' and j['payload']['result_status'] == 'failed'
        service.deliver.assert_not_awaited()
        events = [e async for e in stream_module.stream_result(service, j)]
        assert len(events) == 2 and b'event: result' in events[-1] and b'"status": "failed"' in events[-1]
        raw = '# 自由格式报告\n\n服务/供货内容明确性：无法判断。\n没有固定表格或结论字段。'.encode()
        assert validator.validate_markdown_report(raw, ['missing-input-name.pdf']) == {'scope': 'MARKDOWN_TRANSPORT_ONLY', 'content_validation': False}
        assert validator.validate_markdown_report(b'```unclosed', [])['content_validation'] is False
        for invalid in (b'', b' ', b'\x00', b'\xff', b'x' * (2 * 1024 * 1024 + 1)):
            try:
                validator.validate_markdown_report(invalid, [])
            except (ValueError, UnicodeError):
                pass
            else:
                raise AssertionError('Invalid transport text accepted')
        with tempfile.TemporaryDirectory(prefix='procurement-stream-synthetic-') as temp:
            report = Path(temp) / 'procurement-delivery/report.md'
            report.parent.mkdir()
            report.write_bytes(raw)
            stream_module.get_paths = lambda: SimpleNamespace(thread_dir=lambda *a, **kw: Path(temp))
            j['payload'].update(result_status='succeeded', report_sha256=sha(raw))
            events = [e async for e in stream_module.stream_result(service, j)]
            result = json.loads(events[-1].decode().split('data: ', 1)[1])
            assert result['report_markdown'].encode() == raw
            assert result['report_bytes'] == len(raw) and result['report_sha256'] == sha(raw)
            report.write_bytes(b'tampered')
            events = [e async for e in stream_module.stream_result(service, j)]
            assert b'event: error' in events[-1] and b'report_markdown' not in events[-1]
    asyncio.run(synthetic())
    proof['candidate_synthetic_passed'] = True

if data.get('http'):
    # These requests cannot create a review job; the final request is rejected
    # by Content-Type validation before any upload or queue operation.
    with httpx.Client(trust_env=False, timeout=10) as client:
        base = 'http://127.0.0.1:' + os.getenv('GATEWAY_PORT', '8001')
        health = client.get(base + '/health').status_code
        auth = client.post(base + '/api/v1/integrations/procurement-review', content=b'probe').status_code
        worker = client.post(base + '/api/v1/integrations/procurement-review', content=b'probe', headers={'Authorization': 'Bearer ' + review.submit_key}).status_code
    assert (health, auth, worker) == (200, 401, 415), 'GATEWAY_HTTP_OR_WORKER_NOT_READY'
    proof['http_status'] = [health, auth, worker]

# Read resolved configuration only. Never invoke the remote workflow here.
from deerflow.community.appforge import tools as dq
assert file_sha(dq.__file__) == '2cc6a990353127739c0a8f12028bd09a7e75ab3aca89a21118579a2ecbba621d', 'DATAQA_SOURCE_CHANGED'
overrides = dq._tool_extra('datalake_data_qa')
# Configuration can intentionally override these values. The release host
# clones and updates the mounted config, then verifies resolved values on every Pod.
assert dq._get_inputs('datalake_data_qa') == {'hytoken': '', 'hyaccount': ''}, 'DATAQA_INPUT_CONTRACT_CHANGED'
proof['data_qa'] = {
    'source_sha256': file_sha(dq.__file__),
    'api_url': dq._api_url('datalake_data_qa'),
    'app_id': dq._app_id('datalake_data_qa'),
    'conversation_prefix': dq._conversation_prefix('datalake_data_qa'),
    'key_sha256': sha((dq._api_key('datalake_data_qa') or '').encode()),
    'inputs': dq._get_inputs('datalake_data_qa'),
    'retrieve_timeout': dq._retrieve_timeout('datalake_smart_qa'),
    'dataqa_timeout': dq._get_timeout('datalake_data_qa'),
    'trust_env': dq._get_bool('datalake_data_qa', 'trust_env', 'APPFORGE_TRUST_ENV', False),
}

print(json.dumps(proof, ensure_ascii=False))
