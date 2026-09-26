"""Raw definition edits preserve existing employees and reject stale writers."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine

from deerflow.config.paths import Paths
from deerflow.persistence.agents.drafts import DraftConflict
from deerflow.persistence.agents.file import FileAgentStore
from deerflow.persistence.agents.model import AgentRow
from deerflow.persistence.agents.sql import SqlAgentStore
from deerflow.persistence.base import Base


@pytest.fixture(params=["file", "sql"])
def draft_store(request, tmp_path, monkeypatch):
    paths = Paths(base_dir=tmp_path)
    monkeypatch.setattr("deerflow.config.agents_config.get_paths", lambda: paths)
    monkeypatch.setattr("deerflow.persistence.agents.sql.get_paths", lambda: paths)
    if request.param == "file":
        store = FileAgentStore()
    else:
        url = "sqlite:///" + (tmp_path / "drafts.db").as_posix()
        engine = create_engine(url)
        Base.metadata.create_all(engine, tables=[AgentRow.__table__])
        engine.dispose()
        store = SqlAgentStore(url)
    store.create("reports", {"description": "original", "skills": ["one"], "future_binding": {"keep": [1, 2]}}, "原有职责\n", user_id="alice")
    return store, paths


def test_atomic_field_patch_preserves_unknown_document_fields(draft_store):
    store, _ = draft_store
    original = store.read_definition("reports", user_id="alice")
    updated = store.patch_definition("reports", {"description": "changed"}, "新的职责\n", user_id="alice", expected_revision=original.revision)
    assert updated.config["description"] == "changed"
    assert updated.config["future_binding"] == {"keep": [1, 2]}
    assert updated.config["skills"] == ["one"]
    assert updated.soul == "新的职责\n"
    assert updated.revision != original.revision
    assert store.read_definition("reports", user_id="alice") == updated


def test_stale_editor_cannot_overwrite_draft_or_soul(draft_store):
    store, _ = draft_store
    original = store.read_definition("reports", user_id="alice")
    fresh = store.patch_definition("reports", {"description": "winner"}, None, user_id="alice", expected_revision=original.revision)
    with pytest.raises(DraftConflict):
        store.patch_definition("reports", {"description": "stale"}, "stale soul", user_id="alice", expected_revision=original.revision)
    assert store.read_definition("reports", user_id="alice") == fresh


def test_patch_cannot_create_missing_employee_or_cross_owner(draft_store):
    store, _ = draft_store
    original = store.read_definition("reports", user_id="alice")
    for user in ("bob", "missing"):
        with pytest.raises(FileNotFoundError):
            store.patch_definition("reports", {"description": "hijack"}, None, user_id=user, expected_revision=original.revision)
    assert store.read_definition("reports", user_id="alice") == original


def test_explicit_null_and_empty_selection_are_distinct(draft_store):
    store, _ = draft_store
    current = store.read_definition("reports", user_id="alice")
    empty = store.patch_definition("reports", {"skills": []}, None, user_id="alice", expected_revision=current.revision)
    assert empty.config["skills"] == []
    inherited = store.patch_definition("reports", {"skills": None}, None, user_id="alice", expected_revision=empty.revision)
    assert inherited.config["skills"] is None
    assert inherited.soul == current.soul


def test_two_concurrent_editors_have_one_winner(draft_store):
    store, _ = draft_store
    before = store.read_definition("reports", user_id="alice")

    def edit(value):
        try:
            store.patch_definition("reports", {"description": value}, value, user_id="alice", expected_revision=before.revision)
            return "saved"
        except DraftConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit, ("first", "second")))
    assert sorted(results) == ["conflict", "saved"]
    after = store.read_definition("reports", user_id="alice")
    assert after.config["description"] == after.soul


def test_revision_is_required_and_employee_name_is_immutable(draft_store):
    store, _ = draft_store
    before = store.read_definition("reports", user_id="alice")
    with pytest.raises(DraftConflict):
        store.patch_definition("reports", {"description": "bad"}, None, user_id="alice", expected_revision=None)
    with pytest.raises(ValueError):
        store.patch_definition("reports", {"name": "other"}, None, user_id="alice", expected_revision=before.revision)
    assert store.read_definition("reports", user_id="alice") == before


def test_reviewed_delete_retains_recovery_bytes_without_reusing_the_old_directory(draft_store):
    import json

    store, paths = draft_store
    root = paths.user_agent_dir("alice", "reports")
    root.mkdir(parents=True, exist_ok=True)
    (root / "memory.json").write_bytes(b"original-memory")
    (root / "unknown.bin").write_bytes(b"future-data")
    before = store.read_definition("reports", user_id="alice")
    store.delete_definition("reports", user_id="alice", expected_revision=before.revision)
    assert not root.exists()
    archives = list(root.parent.glob(".deleted-*"))
    assert len(archives) == 1
    record = json.loads((archives[0] / "definition.json").read_text(encoding="utf-8"))
    assert record["config"]["future_binding"] == before.config["future_binding"]
    assert record["soul"] == before.soul
    assert record["revision"] == before.revision
    assert (archives[0] / "files" / "memory.json").read_bytes() == b"original-memory"
    assert (archives[0] / "files" / "unknown.bin").read_bytes() == b"future-data"
    store.create("reports", {"description": "new employee"}, "new", user_id="alice")
    assert store.read_definition("reports", user_id="alice").soul == "new"
    assert not (root / "memory.json").exists()


def test_delete_and_edit_compete_at_one_storage_boundary(draft_store):
    from threading import Barrier

    store, _ = draft_store
    before = store.read_definition("reports", user_id="alice")
    start = Barrier(2)

    def change(delete):
        start.wait(timeout=5)
        try:
            if delete:
                store.delete_definition("reports", user_id="alice", expected_revision=before.revision)
                return "deleted"
            store.patch_definition("reports", {"description": "new"}, None, user_id="alice", expected_revision=before.revision)
            return "saved"
        except (DraftConflict, FileNotFoundError):
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(change, (True, False)))
    assert results.count("rejected") == 1
    assert len(set(results) & {"saved", "deleted"}) == 1


def test_delete_cannot_remove_definition_if_recovery_record_write_fails(draft_store, monkeypatch):
    from deerflow.persistence.agents import deletion

    store, _ = draft_store
    before = store.read_definition("reports", user_id="alice")

    def fail(*args):
        raise OSError("disk error")

    monkeypatch.setattr(deletion, "_replace_bytes", fail)
    with pytest.raises(OSError, match="disk error"):
        store.delete_definition("reports", user_id="alice", expected_revision=before.revision)
    assert store.read_definition("reports", user_id="alice") == before


def test_sql_delete_commit_failure_retains_row_and_recovery_bytes(draft_store, monkeypatch):
    import json

    from sqlalchemy import select
    from sqlalchemy.orm import Session

    store, paths = draft_store
    if not isinstance(store, SqlAgentStore):
        return
    before = store.read_definition("reports", user_id="alice")
    root = paths.user_agent_dir("alice", "reports")
    root.mkdir(parents=True)
    (root / "memory.json").write_bytes(b"unchanged-memory")
    with store._Session() as session:
        original = session.execute(select(AgentRow)).scalar_one()
        original_id = original.id
        original_created_at = original.created_at.isoformat()

    def fail(self):
        raise OSError("uncertain commit")

    monkeypatch.setattr(Session, "commit", fail)
    with pytest.raises(OSError, match="uncertain commit"):
        store.delete_definition("reports", user_id="alice", expected_revision=before.revision)
    assert store.read_definition("reports", user_id="alice") == before
    (archive,) = root.parent.glob(".deleted-*")
    record = json.loads((archive / "definition.json").read_text(encoding="utf-8"))
    assert record["row"]["id"] == original_id
    assert record["row"]["created_at"] == original_created_at
    assert record["row"]["config"]["future_binding"] == before.config["future_binding"]
    assert (archive / "files" / "memory.json").read_bytes() == b"unchanged-memory"


def test_file_interrupted_two_file_edit_recovers_exact_original_bytes(tmp_path, monkeypatch):
    from deerflow.persistence.agents import drafts

    paths = Paths(base_dir=tmp_path)
    monkeypatch.setattr("deerflow.config.agents_config.get_paths", lambda: paths)
    root = paths.user_agent_dir("alice", "reports")
    root.mkdir(parents=True)
    original = b"# hand authored\ndescription: original\nfuture_field: retained\n"
    (root / "config.yaml").write_bytes(original)
    (root / "SOUL.md").write_bytes("原样\n".encode())
    (root / "memory.json").write_text("untouched", encoding="utf-8")
    store = FileAgentStore()
    before = store.read_definition("reports", user_id="alice")
    replace = drafts.os.replace

    def crash(source, target):
        if str(target).endswith("SOUL.md"):
            raise KeyboardInterrupt("simulated process death between file commits")
        return replace(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(drafts.os, "replace", crash)
        with pytest.raises(KeyboardInterrupt):
            store.patch_definition("reports", {"description": "incomplete"}, "new soul", user_id="alice", expected_revision=before.revision)
    assert store.read_definition("reports", user_id="alice") == before
    assert (root / "config.yaml").read_bytes() == original
    assert (root / "SOUL.md").read_bytes() == "原样\n".encode()
    assert (root / "memory.json").read_text() == "untouched"


def test_sql_patch_keeps_primary_key_creation_time_and_other_users(draft_store):
    store, _ = draft_store
    if not isinstance(store, SqlAgentStore):
        pytest.skip("SQL identity assertion")
    store.create("reports", {"description": "bob"}, "bob soul", user_id="bob")
    with store._Session() as session:
        row = store._row(session, "reports", "alice")
        identity = (row.id, row.created_at)
    current = store.read_definition("reports", user_id="alice")
    store.patch_definition("reports", {"description": "edited"}, None, user_id="alice", expected_revision=current.revision)
    with store._Session() as session:
        row = store._row(session, "reports", "alice")
        assert (row.id, row.created_at) == identity
    assert store.get("reports", user_id="bob").description == "bob"


def test_existing_update_writer_retains_unknown_fields(draft_store):
    store, _ = draft_store
    store.update("reports", {"description": "bootstrap update"}, "new soul", user_id="alice")
    after = store.read_definition("reports", user_id="alice")
    assert after.config["future_binding"] == {"keep": [1, 2]}
    assert after.config["skills"] == ["one"]
    assert after.soul == "new soul"


@pytest.mark.parametrize("reader", ["get", "get_soul", "patch_config", "update"])
def test_legacy_file_entry_points_recover_interrupted_pair(tmp_path, monkeypatch, reader):
    from deerflow.persistence.agents import drafts

    paths = Paths(base_dir=tmp_path)
    monkeypatch.setattr("deerflow.config.agents_config.get_paths", lambda: paths)
    store = FileAgentStore()
    store.create("reports", {"description": "original", "future": "keep"}, "original soul", user_id="alice")
    before = store.read_definition("reports", user_id="alice")
    replace = drafts.os.replace

    def crash(source, target):
        if str(target).endswith("SOUL.md"):
            raise KeyboardInterrupt("crash")
        return replace(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(drafts.os, "replace", crash)
        with pytest.raises(KeyboardInterrupt):
            store.patch_definition("reports", {"description": "uncommitted"}, "uncommitted", user_id="alice", expected_revision=before.revision)
    if reader == "patch_config":
        store.patch_config("reports", {"category_id": "new"}, user_id="alice")
    elif reader == "update":
        store.update("reports", {"category_id": "new"}, None, user_id="alice")
    elif reader == "get":
        assert store.get("reports", user_id="alice").description == "original"
    else:
        assert store.get_soul("reports", user_id="alice") == "original soul"
    after = store.read_definition("reports", user_id="alice")
    assert after.config["description"] == "original"
    assert after.soul == "original soul"
    assert after.config["future"] == "keep"
    if reader in ("patch_config", "update"):
        assert after.config["category_id"] == "new"
