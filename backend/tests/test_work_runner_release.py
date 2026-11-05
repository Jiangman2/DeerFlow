"""The dedicated Runner must recognize the shared Work schema on every startup path."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RECIPE = ROOT / "docker/ppt-master-runner/Dockerfile.production-work-overlay"


def test_work_runner_overlay_copies_canonical_migration_and_orm_registration():
    recipe = RECIPE.read_text()
    base = "backend/packages/harness/deerflow/persistence/"
    target = "/app/" + base
    for relative in ("work/", "models/__init__.py", "oa_policy_flow_jobs/model.py", "migrations/versions/0021_oa_result_sets.py", "migrations/versions/0022_work_integration_state.py"):
        assert f"COPY {base}{relative} {target}{relative}" in recipe
        assert (ROOT / base / relative).exists()


def test_work_runner_overlay_preserves_locked_dedicated_agent():
    recipe = RECIPE.read_text()
    lock = json.loads((ROOT / "deploy/enterprise/production-source-lock-20261023.json").read_text())
    assert "ARG RUNNER_BASE_IMAGE" in recipe
    assert "FROM ${RUNNER_BASE_IMAGE}" in recipe
    assert lock["source_sets"]["ppt-master-runner"]["backend/packages/harness/deerflow/agents/ppt_master_runner/agent.py"] in recipe
    assert lock["images"]["ppt-master-runner"] in recipe
    assert "sha256sum -c -" in recipe
    assert "COPY backend/app" not in recipe
    assert "COPY backend/packages/harness/ " not in recipe


def test_work_runner_overlay_checks_revision_and_empty_database_metadata():
    recipe = RECIPE.read_text()
    assert "get_current_head() == '0022_work_integration_state'" in recipe
    assert "'work_integration_records' in Base.metadata.tables" in recipe
    assert "0021_oa_result_sets" in recipe


@pytest.mark.parametrize("previous", ["empty", "0021_oa_result_sets"])
def test_accepted_runner_source_with_work_overlay_bootstraps_without_replacing_agent(tmp_path, previous):
    snapshot = ROOT / "release/production-source-20261022/ppt-master-runner"
    runner = tmp_path / "runner"
    shutil.copytree(snapshot / "backend", runner / "backend")
    for line in RECIPE.read_text().splitlines():
        if not line.startswith("COPY "):
            continue
        source, destination = line.split()[1:]
        source = ROOT / source
        destination = runner / destination.removeprefix("/app/")
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
    agent = "backend/packages/harness/deerflow/agents/ppt_master_runner/agent.py"
    lock = json.loads((ROOT / "deploy/enterprise/production-source-lock-20261023.json").read_text())
    assert hashlib.sha256((runner / agent).read_bytes().replace(b"\r\n", b"\n")).hexdigest() == lock["source_sets"]["ppt-master-runner"][agent]
    code = """
import asyncio, sys
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine
from deerflow.persistence.bootstrap import _get_alembic_config, _upgrade, bootstrap_schema
async def main():
    engine = create_async_engine('sqlite+aiosqlite:///' + sys.argv[1])
    try:
        if sys.argv[2] != 'empty':
            await asyncio.to_thread(_upgrade, _get_alembic_config(engine), sys.argv[2])
        await bootstrap_schema(engine, backend='sqlite')
        await bootstrap_schema(engine, backend='sqlite')
        async with engine.connect() as conn:
            assert (await conn.execute(sa.text('SELECT version_num FROM alembic_version'))).scalar_one() == '0022_work_integration_state'
            tables = await conn.run_sync(lambda c: sa.inspect(c).get_table_names())
            assert {'work_integration_records', 'ppt_confirmation_tasks', 'ppt_confirmation_gates'} <= set(tables)
            columns = await conn.run_sync(lambda c: {x['name'] for x in sa.inspect(c).get_columns('oa_policy_flow_jobs')})
            assert {'result_protocol', 'result_files'} <= columns
    finally:
        await engine.dispose()
asyncio.run(main())
"""
    env = {**os.environ, "PYTHONPATH": str(runner / "backend/packages/harness")}
    result = subprocess.run([sys.executable, "-B", "-c", code, str(tmp_path / "runner.db"), previous], cwd=runner, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
