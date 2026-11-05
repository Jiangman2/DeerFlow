"""Pinned SOUL migration. Run only in an operator-attested idle/edit-free window.

No application writer shares this lock. It serializes migration processes only;
pre-replace rechecks detect drift but are not a distributed writer transaction.
Backups/journal remain on the shared user-data filesystem after the Pod exits.
"""

import hashlib
import json
import os
import re
import stat
import uuid
from pathlib import Path

NOTE = (
    "数据湖接口迁移说明：datalake_data_qa 调用新问数接口（app-v16p2syh），"
    "不是旧接口的组合或回退；以上业务场景通过新接口查询，具体能力以实际返回为准。"
    "datalake_smart_qa 保持不变。涉及数据湖的问题若未开启权限，提示“请先点击数据湖按钮开启权限”；"
    "权限不足、未开启、接口失败或无结果时，说明原因，不得改用外部搜索、浏览器或脚本替代数据湖查询。"
)
BACKUP_NAME = ".portal-data-qa-agent-migration-20261021-r1"


def need(ok, code):
    if not ok:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def transform(raw, pin):
    need(len(raw) == pin["bytes"] and sha(raw) == pin["sha256"], "FILE_HASH_CHANGED")
    raw.decode("utf-8")
    lines = raw.splitlines(keepends=True)
    for number, old, digest in pin["lines"]:
        need(0 < number <= len(lines), "LINE_MISSING")
        line = lines[number - 1]
        need(sha(line.rstrip(b"\r\n")) == digest and line.count(old.encode()) == 1, "LINE_CHANGED")
        lines[number - 1] = line.replace(old.encode(), b"datalake_data_qa")
    result = b"".join(lines)
    need(not any(x in result for x in (b"datalake_detail_qa", b"datalake_metrics_qa", b"datalake_metric_qa")), "RETIRED_REFERENCE_REMAINS")
    newline = b"\r\n" if b"\r\n" in raw else b"\n"
    return result + newline * 2 + NOTE.encode("utf-8") + newline


def directory(parent, name):
    return os.open(str(name), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)


def parent_fd(rootfd, relative):
    parts = Path(relative).parts
    need(len(parts) == 5 and parts[0] == "users" and parts[2] == "agents" and parts[-1] == "SOUL.md" and all(x not in ("", ".", "..") for x in parts), "PERSONAL_SOUL_PATH_REQUIRED")
    fd = os.dup(rootfd)
    try:
        for part in parts[:-1]:
            child = directory(fd, part)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_at(fd, name, private=False):
    f = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(f, "rb") as stream:
        before = os.fstat(stream.fileno())
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= 1048576, "UNSAFE_FILE")
        if private:
            need(before.st_uid == os.geteuid() and stat.S_IMODE(before.st_mode) == 0o600, "UNSAFE_BACKUP")
        raw = stream.read(1048577)
        after = os.fstat(stream.fileno())
    need(signature(before) == signature(after) and len(raw) == before.st_size, "FILE_CHANGED_DURING_READ")
    return raw, before


def signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_uid, info.st_gid, info.st_mode)


def write_new(fd, name, raw, owner=None):
    f = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    with os.fdopen(f, "wb") as stream:
        stream.write(raw)
        stream.flush()
        if owner is not None:
            os.fchown(stream.fileno(), owner[0], owner[1])
            os.fchmod(stream.fileno(), owner[2])
        os.fsync(stream.fileno())
    os.fsync(fd)


def replace_checked(fd, original, expected_info, new):
    temp = ".data-qa-migration-" + uuid.uuid4().hex + ".tmp"
    try:
        write_new(fd, temp, new, (expected_info.st_uid, expected_info.st_gid, stat.S_IMODE(expected_info.st_mode)))
        observed, info = read_at(fd, "SOUL.md")
        need(observed == original and signature(info) == signature(expected_info), "CONCURRENT_AGENT_EDIT")
        os.replace(temp, "SOUL.md", src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
        actual, info = read_at(fd, "SOUL.md")
        need(actual == new and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (expected_info.st_uid, expected_info.st_gid, stat.S_IMODE(expected_info.st_mode)), "REPLACEMENT_READBACK_FAILED")
    finally:
        try:
            os.unlink(temp, dir_fd=fd)
        except FileNotFoundError:
            pass


def migration(root, pins, scanner, operation, action):
    import fcntl

    need(re.fullmatch("[a-f0-9]{32}", operation) is not None and action in ("apply", "verify", "restore"), "INVALID_OPERATION")
    root = Path(root)
    rootfd = directory(None, root)
    backup = opfd = lock = None
    try:
        if action == "apply":
            try:
                os.mkdir(BACKUP_NAME, 0o700, dir_fd=rootfd)
                os.fsync(rootfd)
            except FileExistsError:
                pass
        backup = directory(rootfd, BACKUP_NAME)
        meta = os.fstat(backup)
        need(meta.st_uid == os.geteuid() and stat.S_IMODE(meta.st_mode) == 0o700, "UNSAFE_BACKUP_DIRECTORY")
        lock = os.open("migration.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=backup)
        meta = os.fstat(lock)
        need(stat.S_ISREG(meta.st_mode) and meta.st_nlink == 1 and meta.st_uid == os.geteuid() and stat.S_IMODE(meta.st_mode) == 0o600, "UNSAFE_LOCK")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if action == "apply":
            rows = []

            def observe(relative, raw, info):
                handle = sha(str(relative).encode())
                need(handle in pins["files"] and stat.S_IMODE(info.st_mode) == 0o644 and info.st_nlink == 1, "UNEXPECTED_AFFECTED_FILE")
                new = transform(raw, pins["files"][handle])
                rows.append(dict(handle=handle, relative=str(relative), before=sha(raw), after=sha(new), uid=info.st_uid, gid=info.st_gid, mode=stat.S_IMODE(info.st_mode)))

            inventory = scanner(root, observer=observe)
            need(inventory["inventory_sha256"] == pins["inventory_sha256"] and inventory["files_checked"] == pins["files_checked"] and len(rows) == len(pins["files"]) == 2, "INVENTORY_CHANGED")
            os.mkdir(operation, 0o700, dir_fd=backup)
            os.fsync(backup)
            opfd = directory(backup, operation)
            # Back up BOTH originals durably before changing either target.
            for row in rows:
                fd = parent_fd(rootfd, row["relative"])
                try:
                    raw, info = read_at(fd, "SOUL.md")
                    need(sha(raw) == row["before"] and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (row["uid"], row["gid"], row["mode"]), "FILE_CHANGED_BEFORE_BACKUP")
                    write_new(opfd, row["handle"] + ".original", raw)
                    need(read_at(opfd, row["handle"] + ".original", True)[0] == raw, "BACKUP_READBACK_FAILED")
                finally:
                    os.close(fd)
            write_new(opfd, "journal.json", json.dumps(rows, sort_keys=True).encode())
            for row in rows:
                fd = parent_fd(rootfd, row["relative"])
                try:
                    raw, info = read_at(fd, "SOUL.md")
                    new = transform(raw, pins["files"][row["handle"]])
                    need((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (row["uid"], row["gid"], row["mode"]), "METADATA_CHANGED")
                    replace_checked(fd, raw, info, new)
                finally:
                    os.close(fd)
        else:
            opfd = directory(backup, operation)
            meta = os.fstat(opfd)
            need(meta.st_uid == os.geteuid() and stat.S_IMODE(meta.st_mode) == 0o700, "UNSAFE_OPERATION_DIRECTORY")
            rows = json.loads(read_at(opfd, "journal.json", True)[0])
        need(len(rows) == 2 and {row["handle"] for row in rows} == set(pins["files"]), "JOURNAL_IDENTITY")
        for row in rows:
            need(sha(row["relative"].encode()) == row["handle"], "JOURNAL_PATH")
            original = read_at(opfd, row["handle"] + ".original", True)[0]
            new = transform(original, pins["files"][row["handle"]])
            need(row["before"] == sha(original) and row["after"] == sha(new), "JOURNAL_HASH")
            fd = parent_fd(rootfd, row["relative"])
            try:
                raw, info = read_at(fd, "SOUL.md")
                need((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (row["uid"], row["gid"], row["mode"]), "METADATA_CHANGED")
                if action == "restore":
                    need(raw in (original, new), "CONCURRENT_EDIT_RESTORE_REFUSED")
                    if raw == new:
                        replace_checked(fd, new, info, original)
                else:
                    need(raw == new, "MIGRATED_FILE_CHANGED")
            finally:
                os.close(fd)
        return dict(status="RESTORED" if action == "restore" else "MIGRATION_VERIFIED", files=2, tool_references=4, backup_verified=True, backup_handle=operation, raw_text_exported=False, credentials_exported=False)
    finally:
        for fd in (opfd, lock, backup, rootfd):
            if fd is not None:
                os.close(fd)
