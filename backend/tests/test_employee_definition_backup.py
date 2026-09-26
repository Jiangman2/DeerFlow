"""Definition backup rehearses into a NEW directory, never overwrites live data."""

import json
import os
import stat

import pytest

from deerflow.config.paths import Paths
from deerflow.persistence.agents import definition_backup as backup
from deerflow.persistence.agents.inventory import capture_files, compare_preservation


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "live"
    root.mkdir()
    data = {
        "users/alice/agents/old/config.yaml": b"name: old\nfuture_field: preserve\n",
        "users/alice/agents/old/SOUL.md": "原有职责".encode(),
        "users/alice/agents/old/avatar.webp": b"avatar",
        "users/alice/agents/old/memory.json": b'{"private":"kept"}',
        "team_agents/shared/current.json": b'{"state":"withdrawn"}',
        "team_agents/shared/.revisions/old/config.yaml": b"skills: []\n",
        "team_agents/.resource-grants/revoked.json": b'{"state":"revoked"}',
        "agents/legacy/config.yaml": b"name: legacy\n",
    }
    for name, content in data.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return Paths(base_dir=root), data


def test_backup_roundtrip_keeps_raw_employee_and_revocation_bytes(source, tmp_path):
    paths, data = source
    initial = capture_files(paths)
    destination = tmp_path / "backup"
    report = backup.create(paths, destination)
    assert report["complete"] is True and report["scope"] == "employee_definition_files_only"
    assert report["file_count"] == len(data)
    assert report["legacy_image_compatible"] is False
    assert report["database_included"] is False
    assert report["thread_bindings_included"] is False
    assert capture_files(paths) == initial
    restored = tmp_path / "rehearsal"
    backup.restore_to_new_directory(destination, restored)
    assert compare_preservation(initial, capture_files(Paths(base_dir=restored)))["preserved"]
    assert all((restored / key).read_bytes() == content for key, content in data.items())


def test_old_backup_never_overwrites_new_employee_or_edits(source, tmp_path):
    paths, _ = source
    destination = tmp_path / "backup"
    backup.create(paths, destination)
    added = paths.user_agent_dir("bob", "after-upgrade")
    added.mkdir(parents=True)
    (added / "config.yaml").write_text("name: after-upgrade\n")
    updated = paths.user_agent_dir("alice", "old") / "SOUL.md"
    updated.write_text("new business edit")
    with pytest.raises(ValueError, match="exists"):
        backup.restore_to_new_directory(destination, paths.base_dir)
    assert updated.read_text() == "new business edit"
    assert (added / "config.yaml").exists()


def test_source_change_during_copy_never_seals_complete_backup(source, tmp_path, monkeypatch):
    paths, _ = source
    destination = tmp_path / "backup"
    copy = backup._copy_verified

    def changed(*args, **kwargs):
        copy(*args, **kwargs)
        (paths.user_agent_dir("alice", "old") / "SOUL.md").write_text("concurrent writer")

    monkeypatch.setattr(backup, "_copy_verified", changed)
    with pytest.raises((ValueError, backup.InventoryUnavailable)):
        backup.create(paths, destination)
    assert not (destination / "COMPLETE.json").exists()


def test_backup_requires_new_destination_outside_source(source, tmp_path):
    paths, _ = source
    with pytest.raises(ValueError, match="outside"):
        backup.create(paths, paths.base_dir / "snapshot")
    with pytest.raises(ValueError, match="exists"):
        backup.create(paths, tmp_path)


@pytest.mark.parametrize("corruption", ["blob", "manifest", "unsealed"])
def test_corrupt_or_incomplete_backup_cannot_restore(source, tmp_path, corruption):
    paths, _ = source
    destination = tmp_path / "backup"
    backup.create(paths, destination)
    if corruption == "unsealed":
        (destination / "COMPLETE.json").unlink()
    elif corruption == "manifest":
        (destination / "manifest.json").write_text("{}")
    else:
        next((destination / "objects").iterdir()).write_bytes(b"corrupt")
    with pytest.raises((ValueError, FileNotFoundError)):
        backup.restore_to_new_directory(destination, tmp_path / "restore")
    assert not (tmp_path / "restore").exists()


def test_restore_rejects_scope_escape_even_with_recalculated_hash(source, tmp_path):
    import hashlib

    paths, _ = source
    destination = tmp_path / "backup"
    backup.create(paths, destination)
    manifest = json.loads((destination / "manifest.json").read_text())
    record = next(iter(manifest["inventory"]["files"].values()))
    manifest["inventory"]["files"]["users/alice/threads/secret"] = record
    raw = json.dumps(manifest).encode()
    (destination / "manifest.json").write_bytes(raw)
    seal = json.loads((destination / "COMPLETE.json").read_text())
    seal["manifest_sha256"] = hashlib.sha256(raw).hexdigest()
    (destination / "COMPLETE.json").write_text(json.dumps(seal))
    with pytest.raises(ValueError, match="scope"):
        backup.restore_to_new_directory(destination, tmp_path / "restore")
    assert not (tmp_path / "restore").exists()


def test_pending_edit_is_not_silently_repaired_by_backup(source, tmp_path):
    paths, _ = source
    pending = paths.user_agent_dir("alice", "old") / ".draft-edit.pending.json"
    pending.write_text("unfinished")
    with pytest.raises(backup.InventoryUnavailable, match="pending"):
        backup.create(paths, tmp_path / "backup")
    assert pending.read_text() == "unfinished"
    assert not (tmp_path / "backup").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX execute bits require the isolated Linux rehearsal")
def test_restore_preserves_executable_and_readonly_files_without_exposing_backup(source, tmp_path):
    paths, _ = source
    directory = paths.user_agent_dir("alice", "old")
    executable = directory / "run.sh"
    readonly = directory / "reference.txt"
    # Same bytes must not collapse the two distinct mode records.
    executable.write_bytes(b"unchanged payload\n")
    readonly.write_bytes(executable.read_bytes())
    executable.chmod(0o750)
    readonly.chmod(0o440)
    destination, restored = tmp_path / "backup", tmp_path / "restored"
    backup.create(paths, destination)
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in (destination / "objects").iterdir())
    backup.restore_to_new_directory(destination, restored)
    assert stat.S_IMODE((restored / executable.relative_to(paths.base_dir)).stat().st_mode) == 0o750
    assert stat.S_IMODE((restored / readonly.relative_to(paths.base_dir)).stat().st_mode) == 0o440
    assert stat.S_IMODE(executable.stat().st_mode) == 0o750


@pytest.mark.parametrize("mode", [0o4755, -1, True, "0755", None])
def test_restore_rejects_privileged_or_missing_file_modes_before_writing(source, tmp_path, mode):
    import hashlib

    paths, _ = source
    destination = tmp_path / "backup"
    backup.create(paths, destination)
    manifest = json.loads((destination / "manifest.json").read_text())
    next(iter(manifest["inventory"]["files"].values()))["mode"] = mode
    raw = json.dumps(manifest).encode()
    (destination / "manifest.json").write_bytes(raw)
    seal = json.loads((destination / "COMPLETE.json").read_text())
    seal["manifest_sha256"] = hashlib.sha256(raw).hexdigest()
    (destination / "COMPLETE.json").write_text(json.dumps(seal))
    with pytest.raises(ValueError, match="mode"):
        backup.restore_to_new_directory(destination, tmp_path / "restore")
    assert not (tmp_path / "restore").exists()
