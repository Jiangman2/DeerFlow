"""The dedicated Runner must recognize the shared Work schema on every startup path."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECIPE = ROOT / "docker/ppt-master-runner/Dockerfile.production-work-overlay"


def test_work_runner_overlay_copies_canonical_migration_and_orm_registration():
    recipe = RECIPE.read_text()
    base = "backend/packages/harness/deerflow/persistence/"
    target = "/app/" + base
    for relative in ("work/", "models/__init__.py", "migrations/versions/0020_work_integration_state.py"):
        assert f"COPY {base}{relative} {target}{relative}" in recipe
        assert (ROOT / base / relative).exists()


def test_work_runner_overlay_preserves_locked_dedicated_agent():
    recipe = RECIPE.read_text()
    lock = json.loads((ROOT / "docker/ppt-master-runner/RUNNER-LOCK.json").read_text())
    assert "ARG RUNNER_BASE_IMAGE" in recipe
    assert "FROM ${RUNNER_BASE_IMAGE}" in recipe
    assert lock["current_agent_sha256"] in recipe
    assert lock["current_manifest_digest"] in recipe
    assert "sha256sum -c -" in recipe
    assert "COPY backend/app" not in recipe
    assert "COPY backend/packages/harness/ " not in recipe


def test_work_runner_overlay_checks_revision_and_empty_database_metadata():
    recipe = RECIPE.read_text()
    assert "get_current_head() == '0020_work_integration_state'" in recipe
    assert "'work_integration_records' in Base.metadata.tables" in recipe
    assert "0019_digital_employee_agents" in recipe
