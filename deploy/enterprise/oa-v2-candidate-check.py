"""Executed only in a short-lived candidate child, with a supplied source bundle."""

import asyncio
import contextlib
import hashlib
import importlib
import io
import json
import pathlib
import re
import sys
import tempfile
import types
from unittest.mock import AsyncMock


async def checks(bundle):
    root = pathlib.Path('/app/backend')
    for relative, expected in bundle['baseline'].items():
        path = root / relative
        if expected is None:
            assert not path.exists(), 'BASELINE_NEW_FILE_EXISTS'
        else:
            assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, 'BASELINE_SOURCE_CHANGED'
    # python -I excludes the Gateway working directory. The harness is an
    # installed package, whereas app.gateway is loaded from this fixed root.
    sys.path.insert(0, str(root))
    for name in ('app.gateway.oa_policy_flow_config', 'app.gateway.services', 'app.gateway.oa_policy_flow_results', 'app.gateway.oa_policy_flow_service'):
        record = bundle['modules'][name]
        source = record['source']
        assert hashlib.sha256(source.encode()).hexdigest() == record['sha256']
        # Import dependencies normally; replace only this child interpreter's module.
        importlib.import_module(name.rpartition('.')[0])
        module = types.ModuleType(name)
        module.__file__ = '<oa-candidate:' + name + '>'
        module.__package__ = name.rpartition('.')[0]
        sys.modules[name] = module
        exec(compile(source, module.__file__, 'exec'), module.__dict__)
    from app.gateway.oa_policy_flow_config import load_oa_policy_flow_config
    from app.gateway.oa_policy_flow_results import collect_result_set, read_result_snapshot
    from app.gateway.oa_policy_flow_service import OAPolicyFlowError, OAPolicyFlowService
    from deerflow.config import get_app_config
    from deerflow.config.paths import Paths

    config = load_oa_policy_flow_config()
    actual = get_app_config()
    assert actual.scheduler.recursion_limit == 300 and actual.max_recursion_limit == 2000, 'BUDGET_BASELINE_CHANGED'
    assert config.recursion_limit == 1000, 'OA_BUDGET_NOT_EXPECTED'
    services = sys.modules['app.gateway.services']
    start = AsyncMock(return_value=types.SimpleNamespace(run_id='candidate-run', thread_id='candidate-thread'))
    services.start_run = start
    args = dict(app=types.SimpleNamespace(), thread_id='candidate-thread', assistant_id='candidate', prompt='synthetic')
    await services.launch_scheduled_thread_run(**args)
    assert start.await_args.args[0].config['recursion_limit'] == 300
    await services.launch_scheduled_thread_run(**args, recursion_limit=config.recursion_limit)
    assert start.await_args.args[0].config['recursion_limit'] == 1000
    accepted = ['scheduler_budget_preserved', 'oa_budget_1000']
    with tempfile.TemporaryDirectory(prefix='oa-candidate-') as directory:
        paths = Paths(base_dir=pathlib.Path(directory))
        for name in ('app.gateway.oa_policy_flow_results', 'app.gateway.oa_policy_flow_service'):
            sys.modules[name].get_paths = lambda: paths
        job = {'id': 'candidate-job', 'request_id': 'synthetic', 'thread_id': 'candidate-thread', 'owner_user_id': 'candidate-owner', 'run_id': 'candidate-run', 'result_protocol': 'v2', 'key_fields': {}, 'status': 'running'}
        outputs = paths.sandbox_outputs_dir(job['thread_id'], user_id=job['owner_user_id'])
        outputs.mkdir(parents=True)
        artifacts = []
        for index in range(3):
            name = 'flow-%s.json' % index
            (outputs / name).write_text(json.dumps({'cells': [], 'index': index}))
            artifacts.append('/mnt/user-data/outputs/' + name)
        results = collect_result_set(job, artifacts, config)
        assert len(results) == 3
        assert results == collect_result_set(job, artifacts, config)
        (outputs / 'flow-0.json').write_text('changed')
        assert json.loads(read_result_snapshot(job, results[0], max_result_bytes=config.max_result_bytes))['index'] == 0
        accepted.extend(['three_file_snapshot', 'snapshot_retry_stable', 'agent_edit_does_not_change_delivery'])
        try:
            collect_result_set(job, artifacts, config)
        except OAPolicyFlowError as exc:
            assert exc.code == 'invalid_json'
        else:
            raise AssertionError('INVALID_BATCH_ACCEPTED')
        accepted.append('invalid_batch_rejected')
        patches = []

        class Repo:
            async def patch(self, job_id, **values):
                patches.append(values)
                return True

        runs = types.SimpleNamespace(get=AsyncMock(return_value={'status': 'error', 'stop_reason': 'recursion_capped'}))
        service = OAPolicyFlowService(app=types.SimpleNamespace(state=types.SimpleNamespace(run_store=runs)), repository=Repo(), config=config)
        await service._poll_run(job)
        assert patches[-1]['status'] == 'failed' and patches[-1]['result_files'] is None
        accepted.append('errored_run_not_promoted')
    return {'status': 'CANDIDATE_OFFLINE_RUNTIME_ACCEPTED', 'checks': accepted, 'provider_requests': 0, 'oa_callback_requests': 0, 'database_access': False, 'candidate_only_in_child': True, 'temporary_files_cleaned': True, 'business_values_verified': False, 'ready_for_rollout': False}


def run(bundle):
    sink = io.StringIO()
    try:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            result = asyncio.run(checks(bundle))
    except Exception as exc:
        result = {'status': 'INCOMPLETE', 'error_class': type(exc).__name__, 'raw_error_exported': False, 'ready_for_rollout': False}
        if isinstance(exc, ModuleNotFoundError) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.]{0,159}', exc.name or ''):
            result['missing_python_module'] = exc.name
    print(json.dumps(result, sort_keys=True))
