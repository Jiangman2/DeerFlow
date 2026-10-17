"""File-backed hot flags: frozen seed, durable CAS and independent writers."""

import json
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.gateway.employee_featured_store import EmployeeIdentity, FeaturedConflict, FeaturedStore, FeaturedUnavailable

OLD = EmployeeIdentity("team", "existing", "owner")
NEW = EmployeeIdentity("team", "new", "owner", "new-publication")


def _writer(path, identity, expected):
    try:
        FeaturedStore(path).set_hot(identity, False, expected_version=expected, actor="admin")
        return "saved"
    except FeaturedConflict:
        return "conflict"


def test_explicit_seed_is_frozen_and_repeat_preserves_admin_changes(tmp_path):
    path = tmp_path / "hot.json"
    store = FeaturedStore(path)
    with pytest.raises(FeaturedUnavailable, match="not_initialized"):
        store.snapshot()
    assert not path.exists()
    assert store.initialize([OLD], actor="release") is True
    first = store.snapshot()
    assert first.flag(OLD) == (True, 1)
    assert first.flag(NEW) == (True, 0)
    store.set_hot(OLD, False, expected_version=1, actor="admin")
    assert FeaturedStore(path).initialize([OLD, NEW], actor="release") is False
    restored = FeaturedStore(path).snapshot()
    assert restored.flag(OLD) == (False, 2)
    assert restored.flag(NEW) == (True, 0)
    assert restored.document["seed_count"] == 1
    assert restored.document["changes"][-1]["actor"] == "admin"


def test_republication_preserves_admin_choice_new_publisher_defaults_hot(tmp_path):
    store = FeaturedStore(tmp_path / "hot.json")
    store.initialize([OLD], actor="release")
    store.set_hot(OLD, False, expected_version=1, actor="admin")
    replacement = EmployeeIdentity("team", OLD.name, "owner", "republished")
    assert replacement.token != OLD.token
    assert store.snapshot().flag(replacement) == (False, 2)
    assert store.snapshot().flag(EmployeeIdentity("team", OLD.name, "other")) == (True, 0)
    assert store.snapshot().flag(EmployeeIdentity("personal", OLD.name, "owner")) == (False, 0)
    assert store.snapshot().flag(EmployeeIdentity("unknown", OLD.name, "owner")) == (False, 0)
    store.set_hot(replacement, True, expected_version=2, actor="admin")
    assert store.snapshot().flag(replacement) == (True, 3)


def test_new_employee_default_hot_can_be_removed_durably(tmp_path):
    path = tmp_path / "hot.json"
    store = FeaturedStore(path)
    store.initialize([], actor="release")
    assert store.snapshot().flag(NEW) == (True, 0)
    store.set_hot(NEW, False, expected_version=0, actor="admin")
    assert FeaturedStore(path).snapshot().flag(NEW) == (False, 1)
    replacement = EmployeeIdentity(NEW.source, NEW.name, NEW.owner_id, "updated-publication")
    assert FeaturedStore(path).snapshot().flag(replacement) == (False, 1)


def test_two_processes_cannot_overwrite_same_version(tmp_path):
    path = str(tmp_path / "hot.json")
    FeaturedStore(path).initialize([OLD], actor="release")
    with multiprocessing.get_context("spawn").Pool(2) as pool:
        results = pool.starmap(_writer, [(path, OLD, 1), (path, OLD, 1)])
    assert sorted(results) == ["conflict", "saved"]
    assert FeaturedStore(path).snapshot().flag(OLD) == (False, 2)


def test_independent_updates_preserve_each_other(tmp_path):
    path = str(tmp_path / "hot.json")
    identities = [EmployeeIdentity("team", f"employee-{n}", "owner") for n in range(12)]
    FeaturedStore(path).initialize(identities, actor="release")
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert list(pool.map(lambda identity: _writer(path, identity, 1), identities)) == ["saved"] * 12
    assert all(FeaturedStore(path).snapshot().flag(identity) == (False, 2) for identity in identities)


@pytest.mark.parametrize("contents", ["{", "{}", '{"schema_version":99}', "[]"])
def test_invalid_existing_file_never_resets_or_overwrites(tmp_path, contents):
    path = tmp_path / "hot.json"
    path.write_text(contents)
    path.chmod(0o600)
    with pytest.raises(FeaturedUnavailable):
        FeaturedStore(path).initialize([OLD], actor="release")
    assert path.read_text() == contents


def test_atomic_failure_preserves_old_document(tmp_path, monkeypatch):
    import app.gateway.employee_featured_store as module

    path = tmp_path / "hot.json"
    store = FeaturedStore(path)
    store.initialize([OLD], actor="release")
    before = path.read_bytes()
    monkeypatch.setattr(module.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(FeaturedUnavailable):
        store.set_hot(OLD, False, expected_version=1, actor="admin")
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


def test_duplicate_seed_and_personal_seed_rejected(tmp_path):
    for identities in ([OLD, OLD], [OLD, EmployeeIdentity(OLD.source, OLD.name, OLD.owner_id, "other")], [EmployeeIdentity("personal", "private", "owner")]):
        with pytest.raises(ValueError):
            FeaturedStore(tmp_path / "hot.json").initialize(identities, actor="release")


def test_configured_absolute_path_required(monkeypatch):
    monkeypatch.delenv("DEERFLOW_EMPLOYEE_FEATURED_PATH", raising=False)
    with pytest.raises(FeaturedUnavailable, match="not_configured"):
        FeaturedStore()
    with pytest.raises(FeaturedUnavailable, match="absolute"):
        FeaturedStore("hot.json")


def test_document_version_and_history_bound(tmp_path):
    store = FeaturedStore(tmp_path / "hot.json")
    store.initialize([OLD], actor="release")
    for version in range(1, 260):
        store.set_hot(OLD, version % 2 == 0, expected_version=version, actor="admin")
    data = json.loads((tmp_path / "hot.json").read_bytes())
    assert data["revision"] == 260
    assert len(data["changes"]) == 256
    assert data["entries"][OLD.selection_key]["version"] == 260


@pytest.mark.skipif(os.name == "nt", reason="Production POSIX permission/lock contract")
def test_posix_permissions_links_and_lock_timeout(tmp_path):
    import fcntl

    path = tmp_path / "hot.json"
    store = FeaturedStore(path, lock_timeout=0.05)
    store.initialize([OLD], actor="release")
    assert path.stat().st_mode & 0o777 == 0o600
    with path.with_suffix(".json.lock").open("r+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(FeaturedUnavailable, match="lock_busy"):
            FeaturedStore(path, lock_timeout=0.05).snapshot()
    path.chmod(0o666)
    with pytest.raises(FeaturedUnavailable, match="permissions"):
        store.snapshot()
    path.chmod(0o600)
    other = tmp_path / "hardlink.json"
    os.link(path, other)
    with pytest.raises(FeaturedUnavailable, match="unsafe_featured_file"):
        store.snapshot()
    other.unlink()
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(FeaturedUnavailable, match="unsafe_featured_path"):
        FeaturedStore(link).snapshot()
