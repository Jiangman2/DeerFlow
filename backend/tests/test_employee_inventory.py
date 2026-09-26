"""Release inventory must detect loss without normalizing or repairing data."""

import json
import os

import pytest
from sqlalchemy import create_engine, event, insert, update

from deerflow.config.paths import Paths
from deerflow.persistence.agents import inventory
from deerflow.persistence.agents.model import AgentRow
from deerflow.persistence.base import Base


@pytest.fixture
def employee_files(tmp_path):
    paths = Paths(base_dir=tmp_path)
    contents = {
        "users/alice/agents/employee/config.yaml": b"name: employee\nfuture_binding: keep\n",
        "users/alice/agents/employee/SOUL.md": "职责\n".encode(),
        "users/alice/agents/employee/memory.json": b'{"facts":["private"]}',
        "users/alice/agents/employee/avatar.webp": b"avatar",
        "agents/legacy/config.yaml": b"name: legacy\n",
        "team_agents/shared/config.yaml": b"name: shared\npublished_by_user_id: alice\n",
        "users/alice/agents/memory-only/memory.json": b"retain-memory-only",
    }
    for relative, value in contents.items():
        file = tmp_path / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(value)
    # Not employee data: never scan uploads, credential stores or unrelated files.
    (tmp_path / "users/alice/threads").mkdir()
    (tmp_path / "users/alice/threads/private.txt").write_text("not in inventory")
    return paths, contents


def test_file_inventory_is_complete_non_mutating_and_hash_only(employee_files):
    paths, contents = employee_files
    before = sorted(str(p.relative_to(paths.base_dir)) for p in paths.base_dir.rglob("*"))
    result = inventory.capture_files(paths)
    assert result["employee_count"] == 3
    assert set(result["files"]) == set(contents)
    assert result["legacy_image_compatible"] is True
    assert "future_binding" not in json.dumps(result)
    assert "private.txt" not in json.dumps(result)
    assert before == sorted(str(p.relative_to(paths.base_dir)) for p in paths.base_dir.rglob("*"))
    assert all((paths.base_dir / name).read_bytes() == value for name, value in contents.items())


def test_versioned_avatar_marker_blocks_unadapted_old_image(employee_files):
    paths, _ = employee_files
    marker = paths.user_agent_dir("alice", "employee") / ".avatar-assets" / "FORMAT-v1"
    marker.parent.mkdir()
    marker.write_bytes(b"avatar-reference-v1\n")
    result = inventory.capture_files(paths)
    assert result["legacy_image_compatible"] is False
    assert marker.relative_to(paths.base_dir).as_posix() in result["legacy_image_blockers"]


@pytest.mark.parametrize("failure", ["delete", "rename", "unknown-field", "avatar", "memory"])
def test_existing_employee_changes_block_preservation_check(employee_files, failure):
    paths, _ = employee_files
    before = inventory.capture_files(paths)
    root = paths.user_agent_dir("alice", "employee")
    if failure == "delete":
        (root / "config.yaml").unlink()
    elif failure == "rename":
        root.rename(root.with_name("renamed"))
    else:
        target = "config.yaml" if failure == "unknown-field" else "avatar.webp" if failure == "avatar" else "memory.json"
        (root / target).write_bytes(b"changed")
    report = inventory.compare_preservation(before, inventory.capture_files(paths))
    assert report["preserved"] is False
    assert report["missing"] or report["changed"]


def test_new_employee_is_preserved_as_addition_not_restore_target(employee_files):
    paths, _ = employee_files
    before = inventory.capture_files(paths)
    root = paths.user_agent_dir("bob", "new-after-rollout")
    root.mkdir(parents=True)
    (root / "config.yaml").write_text("name: new-after-rollout\n")
    after = inventory.capture_files(paths)
    report = inventory.compare_preservation(before, after)
    assert report["preserved"] is True and len(report["added"]) == 1
    assert after["employee_count"] == 4
    assert (root / "config.yaml").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX execute bits require the isolated Linux rehearsal")
def test_losing_execution_permission_is_not_reported_as_data_preserved(employee_files):
    paths, _ = employee_files
    script = paths.user_agent_dir("alice", "employee") / "run.sh"
    script.write_text("#!/bin/sh\ntrue\n")
    script.chmod(0o750)
    before = inventory.capture_files(paths)
    script.chmod(0o640)
    after = inventory.capture_files(paths)
    assert not inventory.compare_preservation(before, after)["preserved"]


def test_versioned_or_withdrawn_publication_blocks_unadapted_image_downgrade(employee_files):
    paths, _ = employee_files
    root = paths.team_agent_dir("new-published")
    root.mkdir()
    for state in ("published", "withdrawn"):
        (root / "current.json").write_text(json.dumps({"state": state}))
        result = inventory.capture_files(paths)
        assert result["employee_count"] == 4
        assert result["legacy_image_compatible"] is False
        assert result["legacy_image_blockers"] == ["team_agents/new-published/current.json"]


def test_pending_draft_requires_recovery_before_inventory_without_repairing_it(employee_files):
    paths, _ = employee_files
    pending = paths.user_agent_dir("alice", "employee") / ".draft-edit.pending.json"
    pending.write_text("interrupted")
    with pytest.raises(inventory.InventoryUnavailable, match="pending"):
        inventory.capture_files(paths)
    assert pending.read_text() == "interrupted"


def test_limits_do_not_turn_partial_inventory_into_success(employee_files):
    paths, _ = employee_files
    with pytest.raises(inventory.InventoryUnavailable, match="limit"):
        inventory.capture_files(paths, max_files=2)
    with pytest.raises(inventory.InventoryUnavailable, match="limit"):
        inventory.capture_files(paths, max_bytes=10)


def test_symlink_employee_root_is_not_followed(employee_files, tmp_path):
    paths, _ = employee_files
    outside = tmp_path / "outside"
    outside.mkdir()
    link = paths.team_agent_dir("link")
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Host has no symlink permission; execute on Linux")
    with pytest.raises(inventory.InventoryUnavailable, match="link"):
        inventory.capture_files(paths)


@pytest.fixture
def employee_db(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "employees.db").as_posix())
    Base.metadata.create_all(engine, tables=[AgentRow.__table__])
    with engine.begin() as conn:
        conn.execute(insert(AgentRow), {"id": "stable-id", "user_id": "alice", "name": "employee", "config": {"future_binding": {"keep": 1}}, "soul": "原始职责"})
    yield engine
    engine.dispose()


def test_sql_inventory_reads_raw_identity_and_unknown_fields_without_writes(employee_db):
    calls = []
    event.listen(employee_db, "before_cursor_execute", lambda conn, cur, stmt, params, context, many: calls.append(stmt))
    before = inventory.capture_sql(employee_db)
    assert before["employee_count"] == 1
    assert set(before["rows"]) == {"stable-id"}
    assert before["rows"]["stable-id"]["user_id"] == "alice"
    assert all(stmt.lstrip().upper().startswith("SELECT") for stmt in calls)
    assert "future_binding" not in json.dumps(before)
    with employee_db.begin() as conn:
        conn.execute(update(AgentRow).values(config={"future_binding": {"keep": 2}}))
    assert not inventory.compare_preservation(before, inventory.capture_sql(employee_db))["preserved"]


def test_sql_added_rows_do_not_require_deleting_new_employees(employee_db):
    before = inventory.capture_sql(employee_db)
    with employee_db.begin() as conn:
        conn.execute(insert(AgentRow), {"id": "new-id", "user_id": "bob", "name": "other", "config": {}, "soul": "new"})
    report = inventory.compare_preservation(before, inventory.capture_sql(employee_db))
    assert report["preserved"] is True and report["added"] == ["new-id"]
    with pytest.raises(inventory.InventoryUnavailable, match="limit"):
        inventory.capture_sql(employee_db, max_rows=1)


def test_incomplete_or_mixed_inventory_cannot_claim_preservation(employee_files, employee_db):
    paths, _ = employee_files
    files = inventory.capture_files(paths)
    with pytest.raises(inventory.InventoryUnavailable):
        inventory.compare_preservation(files, inventory.capture_sql(employee_db))
    with pytest.raises(inventory.InventoryUnavailable):
        inventory.compare_preservation(files, {**files, "complete": False})
