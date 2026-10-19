"""Actual local statx plus fault injection; not a production NFS claim."""

import ctypes
import errno
import json
import os

import pytest

import app.gateway.employee_featured_store as M


@pytest.fixture
def store(tmp_path):
    value = M.FeaturedStore(tmp_path / "featured.json")
    value.initialize([M.EmployeeIdentity("team", "local-only", "local", "v1")], actor="test")
    return value


def test_real_linux_statx_is_usable_with_exact_256_byte_aligned_buffer(store):
    assert ctypes.sizeof(ctypes.c_uint64 * 32) == 256
    store._refresh_path_metadata()
    assert store.snapshot().document["revision"] == 1


def test_exact_flags_and_mask_and_refresh_precedes_open(store, monkeypatch):
    original_function = M._linux_statx()
    events = []

    def forced(fd, path, flags, mask, result):
        assert fd == -100 and path == os.fsencode(store.path)
        assert flags == 0x2900 and mask == 0x7FF
        events.append("refresh")
        return original_function(fd, path, flags, mask, result)

    monkeypatch.setattr(M, "_linux_statx", lambda: forced)
    original_open = M.os.open

    def opened(path, flags, *a, **kw):
        if path == store.path:
            assert events and events[-1] == "refresh"
            events.append("open")
        return original_open(path, flags, *a, **kw)

    monkeypatch.setattr(M.os, "open", opened)
    store.snapshot()
    assert events == ["refresh", "open"]


@pytest.mark.parametrize("code", [errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP])
def test_unsupported_sync_refuses_without_cached_fallback_or_mutation(store, monkeypatch, code):
    before = store.path.read_bytes()
    calls = []

    def failed(*args):
        calls.append(True)
        ctypes.set_errno(code)
        return -1

    monkeypatch.setattr(M, "_linux_statx", lambda: failed)
    identity = M.EmployeeIdentity("team", "local-only", "local", "v1")
    with pytest.raises(M.FeaturedUnavailable, match="featured_metadata_sync_unavailable"):
        store.set_hot(identity, False, expected_version=1, actor="test")
    assert len(calls) == 1 and store.path.read_bytes() == before


def test_missing_required_attribute_mask_refuses(store, monkeypatch):
    def no_metadata(*args):
        return 0  # zero-initialized result with no supported fields

    monkeypatch.setattr(M, "_linux_statx", lambda: no_metadata)
    with pytest.raises(M.FeaturedUnavailable, match="featured_metadata_sync_unavailable"):
        store.snapshot()


@pytest.mark.parametrize("code,attempts", [(errno.ESTALE, 3), (errno.EIO, 1), (errno.EACCES, 1)])
def test_only_stale_sync_errors_are_retried_and_never_write(store, monkeypatch, code, attempts):
    before = store.path.read_bytes()
    calls = []

    def failed(*args):
        calls.append(True)
        ctypes.set_errno(code)
        return -1

    monkeypatch.setattr(M, "_linux_statx", lambda: failed)
    with pytest.raises(M.FeaturedUnavailable, match="featured_storage_unavailable"):
        store.snapshot()
    assert len(calls) == attempts and store.path.read_bytes() == before


def test_one_stale_sync_then_fresh_read_commits_once(store, monkeypatch):
    original = M._linux_statx()
    calls = []

    def failed_once(*args):
        calls.append(True)
        if len(calls) == 1:
            ctypes.set_errno(errno.ESTALE)
            return -1
        return original(*args)

    monkeypatch.setattr(M, "_linux_statx", lambda: failed_once)
    identity = M.EmployeeIdentity("team", "local-only", "local", "v1")
    assert store.set_hot(identity, False, expected_version=1, actor="test") == 2
    data = json.loads(store.path.read_bytes())
    assert len(calls) == 2 and data["revision"] == 2 and len(data["changes"]) == 1


def test_force_sync_does_not_follow_symlink_and_existing_security_checks_reject_it(store, tmp_path):
    target = tmp_path / "target.json"
    target.write_bytes(store.path.read_bytes())
    target.chmod(0o600)
    store.path.unlink()
    store.path.symlink_to(target)
    before = target.read_bytes()
    store._refresh_path_metadata()  # the link metadata itself, no following
    with pytest.raises(M.FeaturedUnavailable, match="unsafe_featured_path"):
        store.snapshot()
    assert target.read_bytes() == before


def test_non_linux_path_does_not_resolve_statx(store, monkeypatch):
    monkeypatch.setattr(M.sys, "platform", "win32")

    def forbidden():
        raise AssertionError("Linux API called on another platform")

    monkeypatch.setattr(M, "_linux_statx", forbidden)
    store._refresh_path_metadata()


def test_missing_libc_entrypoint_fails_closed_without_library_search(monkeypatch):
    M._linux_statx.cache_clear()

    def missing(name, **kwargs):
        assert name is None and kwargs == {"use_errno": True}
        return object()

    monkeypatch.setattr(M.ctypes, "CDLL", missing)
    with pytest.raises(M.FeaturedUnavailable, match="featured_metadata_sync_unavailable"):
        M._linux_statx()
    M._linux_statx.cache_clear()
