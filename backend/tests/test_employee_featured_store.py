"""File-backed hot flags: frozen seed, durable CAS and independent writers."""

import errno
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


def test_stale_parent_lookup_is_revalidated_before_reading(tmp_path, monkeypatch):
    from pathlib import Path

    store = FeaturedStore(tmp_path / "hot.json")
    store.initialize([OLD], actor="release")
    original = Path.is_symlink
    lookups = []

    def stale_once(path):
        if path == store.path:
            lookups.append(path)
            if len(lookups) == 1:
                raise OSError(errno.ESTALE, "synthetic stale lookup")
        return original(path)

    monkeypatch.setattr(Path, "is_symlink", stale_once)
    assert store.snapshot().flag(OLD) == (True, 1)
    assert len(lookups) == 2


def test_stale_data_open_reopens_then_commits_exactly_once(tmp_path, monkeypatch):
    import app.gateway.employee_featured_store as module

    store = FeaturedStore(tmp_path / "hot.json")
    store.initialize([OLD], actor="release")
    original_open, original_replace = module.os.open, module.os.replace
    opens, commits = [], []

    def stale_once(path, flags, *args, **kwargs):
        if path == store.path and not flags & (os.O_WRONLY | os.O_RDWR):
            opens.append(path)
            if len(opens) == 1:
                raise OSError(errno.ESTALE, "synthetic stale data handle")
        return original_open(path, flags, *args, **kwargs)

    def commit(*args, **kwargs):
        commits.append(args)
        return original_replace(*args, **kwargs)

    monkeypatch.setattr(module.os, "open", stale_once)
    monkeypatch.setattr(module.os, "replace", commit)
    assert store.set_hot(OLD, False, expected_version=1, actor="admin") == 2
    assert len(opens) == 2
    assert len(commits) == 1
    document = json.loads(store.path.read_bytes())
    assert document["revision"] == 2
    assert document["entries"][OLD.selection_key]["version"] == 2


@pytest.mark.parametrize("number,attempts", [(errno.ESTALE, 3), (errno.EIO, 1), (errno.EACCES, 1)])
def test_read_recovery_is_bounded_and_does_not_retry_other_errors(tmp_path, monkeypatch, number, attempts):
    import app.gateway.employee_featured_store as module

    store = FeaturedStore(tmp_path / "hot.json")
    store.initialize([OLD], actor="release")
    before = store.path.read_bytes()
    original = module.os.open
    calls = []

    def failed_open(path, flags, *args, **kwargs):
        if path == store.path:
            calls.append(path)
            raise OSError(number, "synthetic read failure")
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(module.os, "open", failed_open)
    with pytest.raises(FeaturedUnavailable):
        store.set_hot(OLD, False, expected_version=1, actor="admin")
    assert len(calls) == attempts
    assert store.path.read_bytes() == before


@pytest.mark.skipif(os.name == "nt", reason="Production POSIX permission contract")
def test_recovered_stale_read_keeps_permission_checks(tmp_path, monkeypatch):
    store = FeaturedStore(tmp_path / "hot.json")
    store.initialize([OLD], actor="release")
    store.path.chmod(0o666)
    original = store._read_bytes
    calls = []

    def stale_once():
        calls.append(True)
        if len(calls) == 1:
            raise OSError(errno.ESTALE, "synthetic stale read")
        return original()

    monkeypatch.setattr(store, "_read_bytes", stale_once)
    with pytest.raises(FeaturedUnavailable, match="unsafe_featured_permissions"):
        store.snapshot()
    assert len(calls) == 2


def test_uncertain_atomic_replace_is_never_replayed(tmp_path, monkeypatch):
    import app.gateway.employee_featured_store as module

    store = FeaturedStore(tmp_path / "hot.json")
    store.initialize([OLD], actor="release")
    original = module.os.replace
    commits = []

    def uncertain_replace(*args, **kwargs):
        commits.append(args)
        original(*args, **kwargs)
        raise OSError(errno.ESTALE, "synthetic reply lost after rename")

    monkeypatch.setattr(module.os, "replace", uncertain_replace)
    with pytest.raises(FeaturedUnavailable):
        store.set_hot(OLD, False, expected_version=1, actor="admin")
    assert len(commits) == 1
    data = json.loads(store.path.read_bytes())
    assert data["revision"] == 2
    assert len(data["changes"]) == 1
    with pytest.raises(FeaturedConflict):
        store.set_hot(OLD, True, expected_version=1, actor="stale-browser")
    assert len(commits) == 1


@pytest.mark.skipif(os.name == "nt", reason="Production NFS/POSIX lock ordering")
def test_path_is_revalidated_under_lock_before_opening_cached_nfs_handle(tmp_path, monkeypatch):
    import fcntl

    import app.gateway.employee_featured_store as module

    store = FeaturedStore(tmp_path / "hot.json")
    store.initialize([OLD], actor="release")
    original_stat, original_open, original_fstat = module.os.stat, module.os.open, module.os.fstat
    original_flock = fcntl.flock
    state = {"locked": False, "refreshed": False}
    data_fds, events = set(), []

    def lock(fd, mode):
        result = original_flock(fd, mode)
        if mode & fcntl.LOCK_EX:
            state["locked"] = True
            events.append("locked")
        return result

    def stat_name(path, *args, **kwargs):
        if path == store.path and kwargs.get("follow_symlinks") is False and state["locked"]:
            state["refreshed"] = True
            events.append("lookup-under-lock")
        return original_stat(path, *args, **kwargs)

    def open_file(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        if path == store.path:
            data_fds.add(fd)
            events.append("data-open")
        return fd

    def fstat_handle(fd):
        if fd in data_fds and not state["refreshed"]:
            raise OSError(errno.ESTALE, "cached handle requires name revalidation")
        return original_fstat(fd)

    monkeypatch.setattr(fcntl, "flock", lock)
    monkeypatch.setattr(module.os, "stat", stat_name)
    monkeypatch.setattr(module.os, "open", open_file)
    monkeypatch.setattr(module.os, "fstat", fstat_handle)
    assert store.set_hot(OLD, False, expected_version=1, actor="admin") == 2
    assert events.index("locked") < events.index("lookup-under-lock") < events.index("data-open")
    assert json.loads(store.path.read_bytes())["revision"] == 2


def test_named_file_and_opened_file_identity_must_match_before_update(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    store = FeaturedStore(tmp_path / "hot.json")
    store.initialize([OLD], actor="release")
    original = Path.stat
    before = store.path.read_bytes()

    def different_identity(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path == store.path and kwargs.get("follow_symlinks") is False:
            return SimpleNamespace(**{k: getattr(result, k) + (1 if k == "st_ino" else 0) for k in ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")})
        return result

    monkeypatch.setattr(Path, "stat", different_identity)
    with pytest.raises(FeaturedUnavailable, match="featured_file_changed_during_read"):
        store.set_hot(OLD, False, expected_version=1, actor="admin")
    assert store.path.read_bytes() == before
