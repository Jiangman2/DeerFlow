"""Production Linux lock/atomic-save contract. All data are disposable fixtures."""

import multiprocessing
import os
import time
from pathlib import Path

import pytest

from app.channels.runtime_config_store import ChannelRuntimeConfigConflict, ChannelRuntimeConfigStore


def _write(path, provider, value, barrier):
    store = ChannelRuntimeConfigStore(path)
    barrier.wait(timeout=15)
    store.set_provider_config(provider, {"fixture": value})


def _cas(path, value, barrier, queue):
    store = ChannelRuntimeConfigStore(path)
    before = store.get_provider_config("feishu")
    barrier.wait(timeout=15)
    try:
        store.commit_provider_config("feishu", {"fixture": value}, before)
        queue.put("saved")
    except ChannelRuntimeConfigConflict:
        queue.put("conflict")


def _hold_lock(path, ready, release):
    import fcntl

    with Path(path).open("a+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        ready.set()
        release.wait(timeout=15)


def _join(processes):
    try:
        for process in processes:
            process.join(timeout=20)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)


def test_distinct_provider_concurrent_writes_preserve_all_updates(tmp_path):
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(4)
    path = str(tmp_path / "runtime.json")
    processes = [context.Process(target=_write, args=(path, "fixture-" + str(i), i, barrier)) for i in range(4)]
    for process in processes:
        process.start()
    _join(processes)
    assert ChannelRuntimeConfigStore(path).load_all() == {"fixture-" + str(i): {"fixture": i} for i in range(4)}


def test_same_provider_concurrent_cas_rejects_stale_writer(tmp_path, monkeypatch):
    monkeypatch.delenv("DEERFLOW_FEISHU_DOMAIN", raising=False)
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    queue = context.Queue()
    path = str(tmp_path / "runtime.json")
    store = ChannelRuntimeConfigStore(path)
    store.set_provider_config("feishu", {"fixture": 0})
    processes = [context.Process(target=_cas, args=(path, i, barrier, queue)) for i in (1, 2)]
    for process in processes:
        process.start()
    _join(processes)
    assert sorted([queue.get(timeout=3), queue.get(timeout=3)]) == ["conflict", "saved"]
    queue.close()
    assert store.get_provider_config("feishu")["fixture"] in (1, 2)


def test_real_lock_timeout_preserves_file_then_recovers(tmp_path):
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    path = tmp_path / "runtime.json"
    store = ChannelRuntimeConfigStore(path)
    store.set_provider_config("fixture", {"old": True})
    before = path.read_bytes()
    holder = context.Process(target=_hold_lock, args=(str(path) + ".lock", ready, release))
    holder.start()
    try:
        assert ready.wait(timeout=10)
        start = time.monotonic()
        with pytest.raises(TimeoutError, match="lock busy"):
            store.set_provider_config("fixture", {"new": True})
        assert 4.8 <= time.monotonic() - start < 10
        assert path.read_bytes() == before
    finally:
        release.set()
        _join([holder])
    store.set_provider_config("fixture", {"new": True})
    assert store.get_provider_config("fixture") == {"new": True}


def test_atomic_replace_failure_preserves_prior_configuration(tmp_path, monkeypatch):
    path = tmp_path / "runtime.json"
    store = ChannelRuntimeConfigStore(path)
    store.set_provider_config("fixture", {"old": True})
    before = path.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", lambda *_: (_ for _ in ()).throw(OSError("fixture disk error")))
        with pytest.raises(OSError, match="fixture disk error"):
            store.set_provider_config("fixture", {"new": True})
    assert path.read_bytes() == before
    assert list(tmp_path.glob("*.tmp")) == []
    assert store.get_provider_config("fixture") == {"old": True}


def test_corrupt_file_does_not_get_replaced_by_empty_configuration(tmp_path):
    path = tmp_path / "runtime.json"
    path.write_text("{broken")
    path.chmod(0o600)
    with pytest.raises(ValueError):
        ChannelRuntimeConfigStore(path).set_provider_config("fixture", {"new": True})
    assert path.read_text() == "{broken"


def test_linux_is_required_for_this_production_lock_contract():
    # Deliberately do not skip/replace flock on Windows to manufacture success.
    assert os.name == "posix", "Run the release backend tests in Linux"
