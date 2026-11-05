"""Run installed compatibility code against a temporary SQLite database only."""
import asyncio
import contextlib
import hashlib
import io
import json
from pathlib import Path
import socket
import sys
import tempfile


def no_network(*args, **kwargs):
    raise RuntimeError('CANDIDATE_NETWORK_FORBIDDEN')


socket.socket.connect = no_network
socket.socket.connect_ex = no_network
socket.create_connection = no_network
socket.getaddrinfo = no_network


async def check(expected):
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine
    from deerflow.persistence import bootstrap, oa_schema_compatibility

    assert hashlib.sha256(Path(bootstrap.__file__).read_bytes()).hexdigest() == expected['bootstrap'], 'BOOTSTRAP_HASH'
    assert hashlib.sha256(Path(oa_schema_compatibility.__file__).read_bytes()).hexdigest() == expected['helper'], 'HELPER_HASH'
    assert bootstrap._get_head_revision() == '0020_ppt_confirmation', 'HEAD_CHANGED'
    checks = ['installed_sources_and_migration_head']
    with tempfile.TemporaryDirectory(prefix='oa-image-check-') as directory:
        engine = create_async_engine('sqlite+aiosqlite:///' + str(Path(directory) / 'synthetic.db'))
        try:
            async with engine.begin() as c:
                await c.execute(sa.text('CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)'))
                await c.execute(sa.text("INSERT INTO alembic_version VALUES ('0021_oa_result_sets')"))
                await c.execute(sa.text("CREATE TABLE oa_policy_flow_jobs (id VARCHAR(64) PRIMARY KEY,result_protocol VARCHAR(8) NOT NULL DEFAULT 'v1',result_files JSON)"))
                await c.execute(sa.text("INSERT INTO oa_policy_flow_jobs(id) VALUES ('synthetic')"))
            await bootstrap.bootstrap_schema(engine, backend='sqlite')
            async with engine.begin() as c:
                assert (await c.execute(sa.text('SELECT version_num FROM alembic_version'))).scalar() == '0021_oa_result_sets'
                assert (await c.execute(sa.text("SELECT result_protocol FROM oa_policy_flow_jobs WHERE id='synthetic'"))).scalar() == 'v1'
                await c.execute(sa.text('ALTER TABLE oa_policy_flow_jobs RENAME COLUMN result_files TO missing_files'))
            checks.append('0021_startup_preserves_revision_and_legacy_row')
            try:
                await bootstrap.bootstrap_schema(engine, backend='sqlite')
            except RuntimeError as exc:
                assert str(exc) == 'oa_additive_schema_shape_mismatch', 'WRONG_SHAPE_ERROR'
            else:
                raise AssertionError('BAD_SCHEMA_ACCEPTED')
            checks.append('incomplete_expansion_rejected')
            async with engine.begin() as c:
                await c.execute(sa.text('ALTER TABLE oa_policy_flow_jobs RENAME COLUMN missing_files TO result_files'))
                await c.execute(sa.text("UPDATE alembic_version SET version_num='0022_unknown'"))
            try:
                await bootstrap.bootstrap_schema(engine, backend='sqlite')
            except Exception as exc:
                assert '0022_unknown' in str(exc), 'WRONG_REVISION_ERROR'
            else:
                raise AssertionError('UNKNOWN_SCHEMA_ACCEPTED')
            checks.append('unknown_successor_rejected')
            async with engine.begin() as c:
                await c.execute(sa.text("UPDATE alembic_version SET version_num='0020_ppt_confirmation'"))
            await bootstrap.bootstrap_schema(engine, backend='sqlite')
            async with engine.connect() as c:
                assert (await c.execute(sa.text('SELECT version_num FROM alembic_version'))).scalar() == '0020_ppt_confirmation'
            checks.append('0020_startup_preserved')
        finally:
            await engine.dispose()
    return {'status': 'INSTALLED_COMPAT_IMAGE_ACCEPTED', 'checks': checks,
            'temporary_files_cleaned': True, 'production_database_access': False,
            'provider_requests': 0, 'schema_migrated': False, 'synthetic_database': 'TEMPORARY_SQLITE'}


def main():
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = asyncio.run(check(json.loads(sys.argv[1])))
        code = 0
    except Exception as exc:
        result = {'status': 'INCOMPLETE', 'error_class': type(exc).__name__,
                  'raw_errors_exported': False, 'production_database_access': False}
        code = 1
    print(json.dumps(result, sort_keys=True), flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
