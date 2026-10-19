"""Offline, fault-injected Linux tests. These are not actual NFS evidence."""

import errno
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.gateway.employee_featured_store as M

OLD = M.EmployeeIdentity("team", "synthetic-old", "local", "v1")
OTHER = M.EmployeeIdentity("team", "synthetic-other", "local", "v1")
FIELDS = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")


def altered(s, **changes):
    data = {name: getattr(s, name) for name in FIELDS}
    data.update(changes)
    return SimpleNamespace(**data)


@pytest.fixture
def store(tmp_path):
    s = M.FeaturedStore(tmp_path / "flags.json")
    s.initialize([OLD, OTHER], actor="offline")
    return s


def track_data_fds(monkeypatch, store):
    original = M.os.open
    fds = []

    def opened(path, flags, *args, **kwargs):
        if path == store.path and fds:
            assert_closed(fds[-1:])
        fd = original(path, flags, *args, **kwargs)
        if path == store.path:
            fds.append(fd)
        return fd

    monkeypatch.setattr(M.os, "open", opened)
    return fds


def observe_only_data_read_stat(monkeypatch, store, observe):
    original_stat, original_read = Path.stat, store._read_bytes
    active = []

    def reading():
        active.append(True)
        try:
            return original_read()
        finally:
            active.pop()

    def stated(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if active and path == store.path and kwargs.get("follow_symlinks") is False:
            return observe(result)
        return result

    monkeypatch.setattr(store, "_read_bytes", reading)
    monkeypatch.setattr(Path, "stat", stated)


def assert_closed(fds):
    assert fds
    for fd in fds:
        with pytest.raises(OSError) as error:
            os.fstat(fd)
        assert error.value.errno == errno.EBADF


def test_preopen_cached_inode_can_precede_open_revalidated_inode(store, monkeypatch):
    calls = []

    def stat_name(s):
        calls.append(True)
        if len(calls) == 1:
            return altered(s, st_ino=s.st_ino + 700)
        return s

    observe_only_data_read_stat(monkeypatch, store, stat_name)
    fds = track_data_fds(monkeypatch, store)
    assert store.set_hot(OLD, False, expected_version=1, actor="admin") == 2
    assert len(calls) == 3 and len(fds) == 1
    assert_closed(fds)
    assert json.loads(store.path.read_bytes())["revision"] == 2


def test_persistent_postopen_identity_mismatch_never_writes(store, monkeypatch):
    before = store.path.read_bytes()
    original = Path.stat

    def stat_name(path, *args, **kwargs):
        s = original(path, *args, **kwargs)
        return altered(s, st_ino=s.st_ino + 1) if path == store.path and kwargs.get("follow_symlinks") is False else s

    monkeypatch.setattr(Path, "stat", stat_name)
    fds = track_data_fds(monkeypatch, store)
    with pytest.raises(M.FeaturedUnavailable, match="featured_file_changed_during_read"):
        store.set_hot(OLD, False, expected_version=1, actor="admin")
    assert len(fds) == 3
    assert_closed(fds)
    assert store.path.read_bytes() == before


def test_one_postopen_identity_mismatch_recovers_before_single_commit(store, monkeypatch):
    original_replace = M.os.replace
    calls, commits = [], []

    def stat_name(s):
        calls.append(True)
        if len(calls) == 2:
            return altered(s, st_ino=s.st_ino + 1)
        return s

    def replaced(*args, **kwargs):
        commits.append(True)
        return original_replace(*args, **kwargs)

    observe_only_data_read_stat(monkeypatch, store, stat_name)
    monkeypatch.setattr(M.os, "replace", replaced)
    fds = track_data_fds(monkeypatch, store)
    assert store.set_hot(OLD, False, expected_version=1, actor="admin") == 2
    assert len(fds) == 2 and len(commits) == 1
    assert_closed(fds)
    assert json.loads(store.path.read_bytes())["revision"] == 2


def test_newer_document_seen_after_read_retry_preserves_version_cas(store, monkeypatch):
    newer = M.FeaturedStore(store.path.with_name("synthetic-newer.json"))
    newer.initialize([OLD, OTHER], actor="offline")
    newer.set_hot(OLD, False, expected_version=1, actor="prior-admin")
    before = newer.path.read_bytes()
    original = store._read_bytes
    calls = []

    def changed_once():
        calls.append(True)
        if len(calls) == 1:
            # A failed read obtained v1, then the name changed to v2. A retry
            # must discard the first bytes and CAS against the freshly read v2.
            assert json.loads(original())["revision"] == 1
            os.replace(newer.path, store.path)
            raise M._ReadIdentityChanged()
        return original()

    monkeypatch.setattr(store, "_read_bytes", changed_once)
    with pytest.raises(M.FeaturedConflict, match="featured_version_conflict"):
        store.set_hot(OLD, True, expected_version=1, actor="stale-admin")
    assert len(calls) == 2 and store.path.read_bytes() == before


def test_post_read_path_change_requires_reopen_and_preserves_document(store, monkeypatch):
    calls = []

    def named(s):
        calls.append(True)
        return altered(s, st_ino=s.st_ino + 1) if len(calls) == 3 else s

    observe_only_data_read_stat(monkeypatch, store, named)
    fds = track_data_fds(monkeypatch, store)
    assert store.set_hot(OLD, False, expected_version=1, actor="admin") == 2
    assert len(fds) == 2
    assert_closed(fds)
    assert json.loads(store.path.read_bytes())["revision"] == 2


@pytest.mark.parametrize("when", ["after-open", "after-read"])
def test_fd_metadata_change_must_reopen_before_any_write(store, monkeypatch, when):
    original_check = store._check_file
    calls = []

    def checked(fd):
        s = original_check(fd)
        calls.append(fd)
        # The first invocation is the stable lock descriptor, then data before/after.
        target = 2 if when == "after-open" else 3
        return altered(s, st_mtime_ns=s.st_mtime_ns + 1) if len(calls) == target else s

    monkeypatch.setattr(store, "_check_file", checked)
    fds = track_data_fds(monkeypatch, store)
    assert store.set_hot(OLD, False, expected_version=1, actor="admin") == 2
    assert len(fds) == 2
    assert_closed(fds)
    assert json.loads(store.path.read_bytes())["revision"] == 2


@pytest.mark.parametrize("number,attempts", [(errno.ESTALE, 3), (errno.EIO, 1), (errno.EACCES, 1)])
def test_read_errors_close_each_descriptor_and_only_stale_retries(store, monkeypatch, number, attempts):
    original_fdopen = M.os.fdopen

    class BrokenRead:
        def __init__(self, s):
            self.s = s

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.s.close()

        def fileno(self):
            return self.s.fileno()

        def read(self, *args):
            raise OSError(number, "offline-error-only")

    def wrapped(fd, *args, **kwargs):
        return BrokenRead(original_fdopen(fd, *args, **kwargs))

    fds = track_data_fds(monkeypatch, store)
    before = store.path.read_bytes()
    monkeypatch.setattr(M.os, "fdopen", wrapped)
    with pytest.raises(M.FeaturedUnavailable):
        store.set_hot(OLD, False, expected_version=1, actor="admin")
    assert len(fds) == attempts
    assert_closed(fds)
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("place", ["fd", "named"])
def test_permissions_are_never_retried(store, monkeypatch, place):
    before = store.path.read_bytes()
    fds = track_data_fds(monkeypatch, store)
    if place == "fd":
        store.path.chmod(0o666)
    else:
        original = Path.stat

        def unsafe(path, *args, **kwargs):
            s = original(path, *args, **kwargs)
            return altered(s, st_mode=(s.st_mode & ~0o777) | 0o666) if path == store.path and kwargs.get("follow_symlinks") is False else s

        monkeypatch.setattr(Path, "stat", unsafe)
    with pytest.raises(M.FeaturedUnavailable, match="unsafe_featured_permissions"):
        store.set_hot(OLD, False, expected_version=1, actor="admin")
    assert len(fds) == 1
    assert_closed(fds)
    assert store.path.read_bytes() == before


def test_symlink_target_is_not_a_recoverable_identity_change(store, monkeypatch):
    target = store.path.with_name("elsewhere.json")
    store.path.rename(target)
    store.path.symlink_to(target)
    before = target.read_bytes()
    calls = []
    original = M.os.open

    def opened(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(M.os, "open", opened)
    with pytest.raises(M.FeaturedUnavailable, match="unsafe_featured_path"):
        store.set_hot(OLD, False, expected_version=1, actor="admin")
    assert not calls and target.read_bytes() == before


def test_bad_json_is_not_a_recoverable_read_error(store, monkeypatch):
    store.path.write_bytes(b"{")
    original = store._read_bytes
    calls = []

    def read_once():
        calls.append(True)
        return original()

    monkeypatch.setattr(store, "_read_bytes", read_once)
    with pytest.raises(M.FeaturedUnavailable):
        store.set_hot(OLD, False, expected_version=1, actor="admin")
    assert len(calls) == 1 and store.path.read_bytes() == b"{"


def test_uncertain_rename_still_commits_only_once_and_old_cas_cannot_retry(store, monkeypatch):
    original = M.os.replace
    commits = []

    def uncertain(*args, **kwargs):
        commits.append(True)
        original(*args, **kwargs)
        raise OSError(errno.ESTALE, "reply-lost-after-rename")

    monkeypatch.setattr(M.os, "replace", uncertain)
    with pytest.raises(M.FeaturedUnavailable):
        store.set_hot(OLD, False, expected_version=1, actor="admin")
    assert len(commits) == 1
    with pytest.raises(M.FeaturedConflict):
        store.set_hot(OLD, True, expected_version=1, actor="stale-admin")
    assert len(commits) == 1
    doc = json.loads(store.path.read_bytes())
    assert doc["revision"] == 2 and len(doc["changes"]) == 1


def test_uncertain_directory_fsync_after_rename_is_not_replayed(store, monkeypatch):
    import stat

    original_sync, original_replace = M.os.fsync, M.os.replace
    commits, failed_sync = [], []

    def synced(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            failed_sync.append(True)
            raise OSError(errno.ESTALE, "lost-directory-sync-reply")
        return original_sync(fd)

    def replaced(*args, **kwargs):
        commits.append(True)
        return original_replace(*args, **kwargs)

    monkeypatch.setattr(M.os, "fsync", synced)
    monkeypatch.setattr(M.os, "replace", replaced)
    with pytest.raises(M.FeaturedUnavailable):
        store.set_hot(OLD, False, expected_version=1, actor="admin")
    assert len(commits) == len(failed_sync) == 1
    with pytest.raises(M.FeaturedConflict):
        store.set_hot(OLD, True, expected_version=1, actor="stale-admin")
    assert len(commits) == len(failed_sync) == 1
    assert json.loads(store.path.read_bytes())["revision"] == 2


def test_unrelated_recovery_error_is_not_a_retry_signal(store, monkeypatch):
    calls = []

    def denied():
        calls.append(True)
        raise M.FeaturedUnavailable("unsafe_featured_file")

    monkeypatch.setattr(store, "_read_bytes", denied)
    with pytest.raises(M.FeaturedUnavailable, match="unsafe_featured_file"):
        store.snapshot()
    assert len(calls) == 1
