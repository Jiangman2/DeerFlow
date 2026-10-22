"""Bounded exact-task receipt recovery and conservative launch admission.

This is not a task resumer, process reaper or confirmation authority. Reading a
persisted status cannot prove an old process exited. Never signal stored PIDs
or replay an uncertain launch. Only this Host's mounted thread state is read.
Compatible with the deployed Python 3.10 Host (no backend/SQL dependencies).
"""

from __future__ import annotations

import copy
import json
import math
import os
import re
import stat
import time
from contextlib import contextmanager
from pathlib import Path

MAX_STATE_BYTES = 2 * 1024 * 1024
MAX_EVENT_BYTES = 32 * 1024 * 1024
MAX_ADMISSION_BYTES = 8 * 1024 * 1024
MAX_TASKS = 256
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_TERMINAL = {"completed", "failed", "cancelled"}
_STATES = _TERMINAL | {"pending", "preparing", "running"}
_PUBLIC = {
    "task_id",
    "thread_id",
    "status",
    "process_cleanup_complete",
    "session_id",
    "final_message",
    "artifacts",
    "error",
    "usage",
    "usage_records",
    "last_seq",
    "created_at",
    "updated_at",
    "worker_statuses",
    "worker_statuses_observed_at",
    "last_public_event_at",
    "active_item",
    "reported_session_usage",
    "usage_observation_status",
    "session_usage_observation",
    "research_guard",
    "stop_reason",
    "resumable",
    "cancel_requested",
    "cancel_requested_at",
    "resume_from_task_id",
    "expected_session_id",
    "confirmation_protocol",
    "last_continuation_gate",
}


def require(ok, label):
    if not ok:
        raise RuntimeError(label)


def identity(value):
    require(
        isinstance(value, str) and _ID.fullmatch(value) is not None,
        "invalid_receipt_identity",
    )
    return value


def safe_path(path):
    require(
        path.is_absolute() and path.resolve() == path.absolute(), "unsafe_receipt_path"
    )
    require(
        all(not p.is_symlink() for p in [path, *path.parents]), "unsafe_receipt_path"
    )


def read_bytes(path, limit):
    safe_path(path)
    try:
        fd = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
        try:
            before = os.fstat(fd)
            require(
                stat.S_ISREG(before.st_mode)
                and before.st_nlink == 1
                and before.st_size <= limit,
                "unsafe_receipt_file",
            )
            if os.name == "posix":
                require(
                    before.st_uid == os.geteuid() and not before.st_mode & 0o022,
                    "unsafe_receipt_permissions",
                )
            with os.fdopen(fd, "rb", closefd=False) as handle:
                raw = handle.read(limit + 1)
            after = os.fstat(fd)
            require(
                len(raw) <= limit
                and (
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                )
                == (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
                "receipt_changed_during_read",
            )
            return raw
        finally:
            os.close(fd)
    except OSError:
        raise RuntimeError("receipt_read_unavailable") from None


def json_object(raw):
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise RuntimeError("invalid_persisted_receipt") from None
    require(isinstance(value, dict), "invalid_persisted_receipt")
    return value


def validate_state(state, task_id):
    require(state.get("task_id") == task_id, "persisted_task_mismatch")
    identity(state.get("thread_id"))
    require(
        isinstance(state.get("status"), str) and state["status"] in _STATES,
        "invalid_persisted_status",
    )
    require(
        type(state.get("last_seq")) is int and 0 <= state["last_seq"] < 2**53,
        "invalid_receipt_sequence",
    )
    require(
        type(state.get("process_cleanup_complete", False)) is bool,
        "invalid_cleanup_receipt",
    )
    if state.get("session_id") is not None:
        identity(state["session_id"])
    for key in ("created_at", "updated_at"):
        require(
            type(state.get(key)) in (int, float)
            and math.isfinite(state[key])
            and state[key] >= 0,
            "invalid_receipt_time",
        )
    return state


class TaskReceipts:
    def __init__(self, root, atomic_json):
        self.root = Path(root)
        self.atomic_json = atomic_json

    def directory(self, task_id):
        path = self.root / "tasks" / identity(task_id)
        safe_path(path)
        return path

    def load(self, task_id):
        directory = self.directory(task_id)
        if not directory.exists():
            return None
        require(directory.is_dir(), "unsafe_task_directory")
        # An existing incomplete reservation is UNKNOWN, never a fresh task.
        return validate_state(
            json_object(read_bytes(directory / "state.json", MAX_STATE_BYTES)), task_id
        )

    def events(self, task_id, last_seq, after_seq):
        path = self.directory(task_id) / "events.jsonl"
        safe_path(path)
        if not path.exists():
            require(last_seq == 0, "receipt_events_missing")
            return []
        events, previous = [], 0
        for line in read_bytes(path, MAX_EVENT_BYTES).splitlines(keepends=True):
            if not line.endswith(b"\n"):
                break  # Ignore an uncommitted partial append after a crash.
            value = json_object(line)
            seq = value.get("seq")
            require(type(seq) is int and seq > previous, "invalid_event_sequence")
            previous = seq
            if after_seq < seq <= last_seq and value.get("kind") in {
                "codex",
                "host",
                "stdout",
                "stderr",
            }:
                events.append(
                    {
                        key: value[key]
                        for key in ("seq", "kind", "payload", "created_at")
                        if key in value
                    }
                )
        require(previous >= last_seq, "receipt_events_incomplete")
        return events

    @contextmanager
    def admission(self):
        """Short cross-process filesystem lock; never held during CLI execution.

        New task directories reserve a launch even if a crash precedes state
        save or process start. Incomplete/non-quiescent reservations fail closed.
        Linux uses flock, not a host-local SQLite database on the RWX volume.
        Lock support on the actual production storage still needs qualification.
        """
        safe_path(self.root)
        path = self.root / "admission.lock"
        safe_path(path)
        fd = os.open(
            path,
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            0o600,
        )
        locked = False
        try:
            metadata = os.fstat(fd)
            require(
                stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1,
                "unsafe_admission_lock",
            )
            if os.name == "posix":
                import fcntl

                require(
                    metadata.st_uid == os.geteuid() and not metadata.st_mode & 0o022,
                    "unsafe_admission_lock",
                )

                def acquire():
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

                def release():
                    fcntl.flock(fd, fcntl.LOCK_UN)
            else:
                import msvcrt

                if not metadata.st_size:
                    os.write(fd, b"\0")

                def acquire():
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

                def release():
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

            deadline = time.monotonic() + 2
            while True:
                try:
                    acquire()
                    locked = True
                    break
                except OSError:
                    require(time.monotonic() < deadline, "admission_lock_unavailable")
                    time.sleep(0.025)
            yield
        finally:
            try:
                if locked:
                    release()
            finally:
                os.close(fd)

    def require_quiescent(self, thread_id=None):
        tasks = self.root / "tasks"
        safe_path(tasks)
        if not tasks.exists():
            return
        count = total = 0
        deadline = time.monotonic() + 5
        with os.scandir(tasks) as entries:
            for entry in entries:
                count += 1
                require(
                    count <= MAX_TASKS and time.monotonic() < deadline,
                    "receipt_inventory_limit",
                )
                require(
                    not entry.is_symlink() and entry.is_dir(follow_symlinks=False),
                    "unsafe_task_directory",
                )
                directory = self.directory(entry.name)
                raw = read_bytes(directory / "state.json", MAX_STATE_BYTES)
                total += len(raw)
                require(total <= MAX_ADMISSION_BYTES, "receipt_inventory_limit")
                state = validate_state(json_object(raw), entry.name)
                if thread_id is not None:
                    require(
                        state["thread_id"] == thread_id, "thread_receipt_scope_mismatch"
                    )
                require(
                    state["status"] in _TERMINAL
                    and state.get("process_cleanup_complete") is True,
                    "prior_task_reconciliation_required",
                )

    def require_fresh_confirmation_task(self):
        """Called under admission lock; never infer freshness from an empty UI."""
        for name in ("thread.json", "admission-head.json"):
            path = self.root / name
            safe_path(path)
            require(not path.exists(), "confirmation_requires_fresh_host_task")
        tasks = self.root / "tasks"
        safe_path(tasks)
        if tasks.exists():
            with os.scandir(tasks) as entries:
                require(next(entries, None) is None, "confirmation_requires_fresh_host_task")

    def session_for(self, source_id, thread_id, expected_session_id):
        state = self.load(source_id)
        require(state is not None, "resume_source_missing")
        require(state["thread_id"] == thread_id, "resume_source_thread_mismatch")
        require(
            state["status"] == "completed" and not state.get("cancel_requested"),
            "resume_source_not_completed",
        )
        require(
            state.get("process_cleanup_complete") is True, "resume_source_not_quiescent"
        )
        require(
            state.get("session_id") == identity(expected_session_id),
            "resume_session_mismatch",
        )
        return expected_session_id


class RecoveredTask:
    """Read-only historical task, except explicit durable cancellation intent."""

    process = None

    def __init__(self, store, task_id):
        self.store, self.task_id = store, task_id
        self.task_dir = store.directory(task_id)
        state = store.load(task_id)
        require(state is not None, "persisted_task_missing")
        self.thread_id = state["thread_id"]

    @property
    def state(self):
        return self.store.load(self.task_id)

    def snapshot(self, after_seq=0):
        require(type(after_seq) is int and after_seq >= 0, "invalid_receipt_sequence")
        state = self.state
        require(
            state is not None and state["thread_id"] == self.thread_id,
            "persisted_task_changed",
        )
        payload = copy.deepcopy(
            {key: value for key, value in state.items() if key in _PUBLIC}
        )
        payload["events"] = self.store.events(
            self.task_id, state["last_seq"], after_seq
        )
        if (
            state["status"] in _TERMINAL
            and state.get("process_cleanup_complete") is True
        ):
            payload["host_recovery"] = "persisted_terminal_receipt"
        else:
            payload.update(
                status="failed",
                persisted_status=state["status"],
                process_cleanup_complete=False,
                resumable=False,
                host_recovery="process_reconciliation_required",
                error="Host 已重启，无法核实外部 Agent 已退出。原任务和会话已保留；需核对进程后继续，不会自动重放任务。",
            )
        return payload

    def request_cancel(self):
        with self.store.admission():
            state = self.state
            require(
                state is not None and state["thread_id"] == self.thread_id,
                "persisted_task_changed",
            )
            if (
                state["status"] not in _TERMINAL
                or state.get("process_cleanup_complete") is not True
                or state.get("confirmation_owner_candidate") is not None
            ):
                state["cancel_requested"] = True
                state["cancel_requested_at"] = time.time()
                self.store.atomic_json(
                    self.store.directory(self.task_id) / "state.json", state
                )


def legacy_session(path, thread_id):
    state = json_object(read_bytes(path, MAX_STATE_BYTES))
    require(state.get("thread_id") == thread_id, "persisted_session_thread_mismatch")
    return identity(state.get("session_id"))
