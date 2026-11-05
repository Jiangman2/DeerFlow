"""Qualify installed OA/Data QA code using synthetic files and SQLite only."""
import asyncio
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


CHECKS = [
    'installed_sources_and_0021_head', 'v2_disabled_by_default',
    'scheduler_budget_300', 'oa_budget_1000', 'three_file_snapshot',
    'snapshot_retry_stable', 'agent_edit_does_not_change_delivery',
    'invalid_batch_rejected', 'errored_run_not_promoted',
    'data_qa_progress_before_stream_end', 'progress_redacted_and_terminal',
    'additive_migration_preserves_legacy_row',
]


def no_network(*args, **kwargs):
    raise RuntimeError('CHECK_NETWORK_FORBIDDEN')


async def check(sources, root=Path('/app/backend')):
    # No replacement modules: these are exactly the files shipped in the image.
    for name, expected in sources.items():
        assert name.startswith('app/backend/') and '..' not in Path(name).parts
        raw = (root / name[len('app/backend/'):]).read_bytes()
        assert len(raw) == expected['bytes'] and hashlib.sha256(raw).hexdigest() == expected['sha256'], 'INSTALLED_SOURCE_CHANGED'
    sys.path.insert(0, str(root))
    from app.gateway import oa_policy_flow_results as results_module
    from app.gateway import oa_policy_flow_service as service_module
    from app.gateway import services
    from app.gateway.oa_policy_flow_config import load_oa_policy_flow_config
    from deerflow.config.paths import Paths
    from deerflow.persistence import bootstrap
    from deerflow.community.appforge.data_qa_progress import DataQAProgress
    from deerflow.community.appforge.data_qa_stream import collect_data_qa_stream
    import httpx
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    assert bootstrap._get_head_revision() == '0021_oa_result_sets', 'MIGRATION_HEAD_CHANGED'
    accepted = [CHECKS[0]]
    config = load_oa_policy_flow_config()
    assert config.multi_result_enabled is False and config.recursion_limit == 1000, 'OA_DEFAULT_CHANGED'
    accepted.append(CHECKS[1])
    synthetic_config = SimpleNamespace(scheduler=SimpleNamespace(recursion_limit=300), max_recursion_limit=2000)
    start = AsyncMock(return_value=SimpleNamespace(run_id='synthetic-run', thread_id='synthetic-thread'))
    args = dict(app=SimpleNamespace(), thread_id='synthetic-thread', assistant_id='synthetic', prompt='synthetic')
    with patch.object(services, 'get_app_config', return_value=synthetic_config), patch.object(services, 'start_run', start):
        await services.launch_scheduled_thread_run(**args)
        assert start.await_args.args[0].config['recursion_limit'] == 300
        accepted.append(CHECKS[2])
        await services.launch_scheduled_thread_run(**args, recursion_limit=config.recursion_limit)
        assert start.await_args.args[0].config['recursion_limit'] == 1000
        accepted.append(CHECKS[3])

    with tempfile.TemporaryDirectory(prefix='oa-installed-') as directory:
        paths = Paths(base_dir=Path(directory))
        with patch.object(results_module, 'get_paths', return_value=paths), patch.object(service_module, 'get_paths', return_value=paths):
            job = dict(id='synthetic-job', request_id='synthetic', thread_id='synthetic-thread', owner_user_id='synthetic-owner',
                       run_id='synthetic-run', result_protocol='v2', key_fields={}, status='running')
            outputs = paths.sandbox_outputs_dir(job['thread_id'], user_id=job['owner_user_id'])
            outputs.mkdir(parents=True)
            artifacts = []
            for index in range(3):
                name = 'flow-%s.json' % index
                (outputs / name).write_text(json.dumps({'cells': [], 'index': index}))
                artifacts.append('/mnt/user-data/outputs/' + name)
            results = results_module.collect_result_set(job, artifacts, config)
            assert len(results) == 3
            accepted.append(CHECKS[4])
            assert results == results_module.collect_result_set(job, artifacts, config)
            accepted.append(CHECKS[5])
            (outputs / 'flow-0.json').write_text('changed')
            assert json.loads(results_module.read_result_snapshot(job, results[0], max_result_bytes=config.max_result_bytes))['index'] == 0
            accepted.append(CHECKS[6])
            try:
                results_module.collect_result_set(job, artifacts, config)
            except service_module.OAPolicyFlowError as exc:
                assert exc.code == 'invalid_json'
            else:
                raise AssertionError('INVALID_BATCH_ACCEPTED')
            accepted.append(CHECKS[7])
            repository = SimpleNamespace(patch=AsyncMock(return_value=True))
            runs = SimpleNamespace(get=AsyncMock(return_value={'status': 'error', 'stop_reason': 'recursion_capped'}))
            service = service_module.OAPolicyFlowService(app=SimpleNamespace(state=SimpleNamespace(run_store=runs)), repository=repository, config=config)
            await service._poll_run(job)
            assert repository.patch.await_args.kwargs['status'] == 'failed'
            assert repository.patch.await_args.kwargs['result_files'] is None
            accepted.append(CHECKS[8])

        events = []
        progress = DataQAProgress(SimpleNamespace(context={'thread_id': 'synthetic-thread', 'run_id': 'synthetic-run'},
                                                  config={}, tool_call_id='synthetic-call', stream_writer=events.append))
        progress.emit('waiting')

        class Stream(httpx.SyncByteStream):
            def __iter__(self):
                yield b'event: answer\ndata: {"data":{"node_type":"end","text":"synthetic"}}\n\n'
                assert [e['stage'] for e in events] == ['waiting', 'answering'], 'PROGRESS_BUFFERED_UNTIL_EOF'
                yield b'event: workflow_finished\ndata: {"data":{"status":"succeeded","outputs":{"output":"synthetic"}}}\n\n'

        response = httpx.Response(200, headers={'Content-Type': 'text/event-stream'}, stream=Stream())
        try:
            result = collect_data_qa_stream(response, parse_knowledge=lambda value: [], on_progress=progress.emit)
        finally:
            response.close()
        assert result['status'] == 'done' and result['answer'] == 'synthetic'
        accepted.append(CHECKS[9])
        progress.emit('answering')
        progress.emit('raw synthetic private text')
        progress.emit('completed')
        progress.emit('failed')
        assert [e['stage'] for e in events] == ['waiting', 'answering', 'completed']
        assert [e['sequence'] for e in events] == [1, 2, 3]
        assert all(set(e) == {'type', 'thread_id', 'run_id', 'tool_call_id', 'tool', 'stage', 'sequence'} for e in events)
        accepted.append(CHECKS[10])

        migration_path = root / 'packages/harness/deerflow/persistence/migrations/versions/0021_oa_result_sets.py'
        spec = importlib.util.spec_from_file_location('audited_oa_0021', migration_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        assert migration.revision == '0021_oa_result_sets' and migration.down_revision == '0020_ppt_confirmation'
        engine = sa.create_engine('sqlite:///' + str(Path(directory) / 'synthetic.db'))
        try:
            with engine.begin() as conn:
                conn.execute(sa.text('CREATE TABLE oa_policy_flow_jobs (id VARCHAR(64) PRIMARY KEY, payload TEXT NOT NULL)'))
                conn.execute(sa.text("INSERT INTO oa_policy_flow_jobs VALUES ('legacy','retained')"))
                with Operations.context(MigrationContext.configure(conn)):
                    migration.upgrade()
                    migration.upgrade()
                columns = {c['name']: c for c in sa.inspect(conn).get_columns('oa_policy_flow_jobs')}
                assert len(columns) == 4 and columns['result_protocol']['nullable'] is False
                assert isinstance(columns['result_protocol']['type'], sa.String) and columns['result_protocol']['type'].length == 8
                assert isinstance(columns['result_files']['type'], sa.JSON) and columns['result_files']['nullable'] is True
                row = conn.execute(sa.text('SELECT id,payload,result_protocol,result_files FROM oa_policy_flow_jobs')).one()
                assert tuple(row) == ('legacy', 'retained', 'v1', None)
        finally:
            engine.dispose()
        accepted.append(CHECKS[11])
    assert accepted == CHECKS
    return dict(status='INSTALLED_OA_DATA_QA_IMAGE_ACCEPTED', checks=accepted, production_database_access=False,
                schema_migrated=False, synthetic_database='TEMPORARY_SQLITE', temporary_files_cleaned=True,
                provider_requests=0, oa_callback_requests=0, business_values_verified=False,
                production_configuration_used=False, ready_for_rollout=False)


def main():
    socket.socket.connect = no_network
    socket.socket.connect_ex = no_network
    socket.create_connection = no_network
    socket.getaddrinfo = no_network
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = asyncio.run(check(json.loads(sys.argv[1])))
        code = 0
    except Exception as exc:
        result = dict(status='INCOMPLETE', error_class=type(exc).__name__, raw_errors_exported=False,
                      production_database_access=False, provider_requests=0, schema_migrated=False)
        if isinstance(exc, AssertionError) and str(exc).replace('_', '').isupper():
            result['check_code'] = str(exc)[:80]
        if isinstance(exc, ModuleNotFoundError):
            result['missing_python_module'] = exc.name
        code = 1
    print(json.dumps(result, sort_keys=True), flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
