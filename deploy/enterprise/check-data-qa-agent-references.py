"""Bounded read-only inventory of persisted custom-agent config and instructions."""


def agent_references(root, now=None):
    import hashlib
    import os
    import stat
    import time
    from pathlib import Path

    now = now or time.monotonic
    started = now()
    root = Path(root)
    names = ("datalake_detail_qa", "datalake_metrics_qa", "datalake_metric_qa")
    counts = {name: 0 for name in names}
    visited, total, files, affected = 0, 0, 0, 0
    fingerprint = hashlib.sha256()

    def require(ok):
        if not ok:
            raise ValueError("agent_reference_inventory_incomplete")

    require(root.is_dir() and not root.is_symlink())

    def entries(path):
        nonlocal visited
        require(now() - started < 15)
        try:
            info = path.lstat()
        except FileNotFoundError:
            return []
        require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode))
        result = []
        with os.scandir(str(path)) as iterator:
            for entry in iterator:
                visited += 1
                require(visited <= 20000 and now() - started < 15)
                require(not entry.is_symlink())
                if entry.is_dir(follow_symlinks=False):
                    result.append(Path(entry.path))
        return sorted(result)

    agent_dirs = entries(root / "agents") + entries(root / "team_agents")
    for user in entries(root / "users"):
        agent_dirs.extend(entries(user / "agents"))
    for directory in sorted(agent_dirs):
        for basename in ("config.yaml", "SOUL.md"):
            require(now() - started < 15)
            path = directory / basename
            try:
                fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError:
                continue
            with os.fdopen(fd, "rb") as stream:
                before = os.fstat(stream.fileno())
                require(stat.S_ISREG(before.st_mode) and before.st_size <= 1048576)
                raw = stream.read(1048577)
                after = os.fstat(stream.fileno())
            require(len(raw) == before.st_size and (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) == (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns))
            total += len(raw)
            files += 1
            require(total <= 67108864)
            found = [name for name in names if name.encode() in raw]
            affected += bool(found)
            for name in found:
                counts[name] += 1
            fingerprint.update(str(path.relative_to(root)).encode() + b"\0" + hashlib.sha256(raw).digest())
    return dict(status="PASS" if affected == 0 else "OLD_REFERENCES_FOUND", files_checked=files, affected_files=affected, retired_references=counts, inventory_sha256=fingerprint.hexdigest(), modified=False, credentials_exported=False, scope="CUSTOM_AGENT_CONFIG_AND_SOUL_NOT_CHAT_HISTORY")
