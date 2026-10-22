"""Exact-task, bounded recovery of the official Confirm UI daemon (Python 3.10).

Not an auto-confirmation dispatcher. This module never writes the Skill's
selection/result, resets a stage, submits /api/confirm or launches Codex. The
caller holds Host admission and verifies the latest task, cancellation and
process cleanup before restore. Unknown launches are reconciled, not replayed.
"""

import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

MAX_BYTES = 2 * 1024 * 1024
SOURCE_HASH = "7f263892dd49af2f6c48463ab5aa51367e888640f14517ad93730237e5ebd786"
PROTOCOL_HASH = "935b44c27a064e00d20b52ab9831b94811f40839fea779cfb5bf9b4ed1c1c71b"
_PORT = re.compile(r"http://(?:127\.0\.0\.1|localhost):([0-9]{4,5})(?=$|[/\s])")


def require(ok, label):
    if not ok:
        raise RuntimeError(label)


def safe_directory(path):
    require(path.is_absolute() and path.resolve() == path.absolute(), "unsafe_confirmation_path")
    require(all(not part.is_symlink() for part in (path, *path.parents)), "unsafe_confirmation_path")
    require(path.is_dir(), "confirmation_project_unavailable")


def project_path(value, workspace):
    require(isinstance(value, str) and len(value) <= 4096 and not any(ord(c) < 32 for c in value), "unsafe_confirmation_path")
    path = Path(value)
    safe_directory(workspace)
    safe_directory(path)
    try:
        relative = path.relative_to(workspace)
    except ValueError:
        raise RuntimeError("confirmation_project_outside_workspace") from None
    require(bool(relative.parts) and all(not part.startswith(".") for part in relative.parts), "confirmation_project_outside_workspace")
    safe_directory(path / "confirm_ui")
    return path


def port_number(value):
    require(type(value) is int and 5050 <= value <= 5099, "invalid_confirmation_port")
    return value


def candidate_from_event(event, workspace, runtime, *, skill_alias=None):
    """A narrow discovery hint from an executed command, never Agent prose.

    Complex shell commands are intentionally not guessed. A matching native
    health response remains required before claiming an official page state.
    """
    item = event.get("item")
    if event.get("type") != "item.completed" or not isinstance(item, dict):
        return None
    if item.get("type") != "command_execution" or type(item.get("exit_code")) is not int or item["exit_code"] != 0:
        return None
    command, output = item.get("command"), item.get("aggregated_output")
    if not isinstance(command, str) or len(command) > 12000 or not isinstance(output, str) or len(output) > MAX_BYTES:
        return None
    try:
        # Codex exposes a shell argv envelope around ordinary exec commands.
        # Unwrap only one exact -c/-lc argument, never execute/expand it.
        envelope = shlex.split(command)
        if len(envelope) == 3 and envelope[0] in {"bash", "/bin/bash", "/usr/bin/bash", "sh", "/bin/sh"} and envelope[1] in {"-c", "-lc"}:
            command = envelope[2]
        # shlex parses POSIX commands; don't allow composition or expansion.
        if any(character in command for character in "\n\r;$`|&<>"):
            return None
        tokens = shlex.split(command)
        if not tokens or Path(tokens.pop(0)).name not in {"python", "python3", "python3.10", "python3.11", "python3.12"}:
            return None
        while tokens and tokens[0] in {"-B", "-u"}:
            tokens.pop(0)
        if len(tokens) < 3:
            return None
        script = Path(tokens[0])
        if script != runtime / "scripts/confirm_ui/server.py":
            # The only alias is the normal Skill symlink installed by this
            # Host, not an arbitrary script selected by a tool response.
            if (skill_alias is None or script != skill_alias / "scripts/confirm_ui/server.py"
                    or not skill_alias.is_symlink() or skill_alias.resolve() != runtime.resolve()):
                return None
        path = project_path(tokens[1], workspace)
        arguments = tokens[2:]
        if "--daemon" not in arguments:
            return None
        while arguments:
            flag = arguments.pop(0)
            if flag in {"--daemon", "--no-browser"}:
                continue
            if flag not in {"--port", "--timeout"} or not arguments or not arguments.pop(0).isdigit():
                return None
        ports = set(int(match) for match in _PORT.findall(output))
        if len(ports) != 1:
            return None
        port = port_number(ports.pop())
        item_id = item.get("id")
        if not isinstance(item_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", item_id):
            return None
        return {"project": str(path), "port": port, "item_id": item_id}
    except (OSError, ValueError, RuntimeError):
        return None


def verify_source(source_root):
    _verify_file(source_root / "scripts/confirm_ui/server.py", SOURCE_HASH)
    _verify_file(source_root / "scripts/confirm_ui/conditional_protocol.py", PROTOCOL_HASH)


def _verify_file(path, expected):
    try:
        require(all(not part.is_symlink() for part in (path, *path.parents)), "unsafe_confirmation_source")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= MAX_BYTES, "unsafe_confirmation_source")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(MAX_BYTES + 1)
            after = os.fstat(fd)
            require((info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) == (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), "confirmation_source_changed")
        finally:
            os.close(fd)
        require(hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest() == expected, "confirmation_source_changed")
        return raw.replace(b"\r\n", b"\n")
    except OSError:
        raise RuntimeError("confirmation_source_unavailable") from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe(candidate):
    port = port_number(candidate.get("port"))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request("http://127.0.0.1:{}/api/health".format(port), headers={"Accept": "application/json"})
    try:
        with opener.open(request, timeout=2) as response:
            require(response.status == 200, "confirmation_health_unavailable")
            raw = response.read(MAX_BYTES + 1)
            require(len(raw) <= MAX_BYTES, "confirmation_health_too_large")
            health = json.loads(raw)
        health_view(candidate, health)
        return health
    except urllib.error.HTTPError:
        # A responding but unexpected service is NOT an expired page.
        raise RuntimeError("confirmation_health_unavailable") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return None
    except (ValueError, UnicodeError, RecursionError):
        raise RuntimeError("invalid_confirmation_health") from None


def health_view(candidate, health):
    require(isinstance(health, dict) and health.get("service") == "confirm_ui" and health.get("status") == "ok"
            and health.get("project") == candidate.get("project") and type(health.get("pid")) is int and health["pid"] > 0,
            "confirmation_owner_identity")
    session = health.get("session")
    require(isinstance(session, dict) and session.get("phase") == "strategist", "confirmation_owner_identity")
    stage = session.get("current_stage")
    require(stage in {"stage1", "stage2", "final"}, "confirmation_owner_identity")
    status = session.get("status")
    state = {"ready_user": "waiting_user", "waiting_agent": "preparing", "done": "confirmed", "error": "owner_error"}.get(status, "owner_error")
    if session.get("template_error") or session.get("recommendation_error"):
        state = "owner_error"
    version = session.get("recommendation_version")
    if not isinstance(version, str) or re.fullmatch(r"[a-f0-9]{64}", version) is None:
        version = None
    return {"state": state, "stage": stage, "port": port_number(candidate["port"]), "recommendation_version": version,
            "confirmation_submitted": False, "agent_started": False}


def inspect(task, *, workspace):
    candidate = task.state.get("confirmation_owner_candidate")
    if not isinstance(candidate, dict):
        return {"state": "not_discovered", "confirmation_submitted": False, "agent_started": False}
    project_path(candidate.get("project"), workspace)
    health = probe(candidate)
    return health_view(candidate, health) if health is not None else {"state": "owner_unreachable", "port": port_number(candidate["port"]), "confirmation_submitted": False, "agent_started": False}


def recovery_record(marker, task_id):
    if not marker.exists() and not marker.is_symlink():
        return None
    require(all(not part.is_symlink() for part in (marker, *marker.parents)), "unsafe_confirmation_recovery_record")
    fd = os.open(marker, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 4096, "unsafe_confirmation_recovery_record")
        if os.name == "posix":
            require(info.st_uid == os.geteuid() and not info.st_mode & 0o022, "unsafe_confirmation_recovery_record")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(4097)
        value = json.loads(raw)
        require(isinstance(value, dict) and value.get("schema") == 1 and value.get("task_id") == task_id
                and value.get("state") in {"requested", "ready"}, "unsafe_confirmation_recovery_record")
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise RuntimeError("unsafe_confirmation_recovery_record") from None
    finally:
        os.close(fd)


def restore(task, *, workspace, source_root, receipts):
    """Caller has locked/verified a cleaned-up latest task, no active Agent.

    Only the official --daemon command owns its lock/session/log writes.
    Record intent durably before launching. A lost acknowledgement, timeout or
    unverified endpoint keeps the operation unknown and refuses another launch.
    """
    candidate = task.state.get("confirmation_owner_candidate")
    require(isinstance(candidate, dict), "confirmation_owner_not_discovered")
    project = project_path(candidate.get("project"), workspace)
    port = port_number(candidate.get("port"))
    verify_source(source_root)
    health = probe(candidate)
    marker = task.task_dir / "confirmation-recovery.json"
    previous = recovery_record(marker, task.task_id)
    if health is not None:
        if previous and previous["state"] == "requested":
            receipts.atomic_json(marker, {**previous, "state": "ready", "observed_pid": health["pid"]})
        return health_view(candidate, health)
    if previous and previous["state"] != "ready":
        return {"state": "recovery_unconfirmed", "confirmation_submitted": False, "agent_started": False}
    operation = {"schema": 1, "task_id": task.task_id, "operation_id": uuid.uuid4().hex, "state": "requested", "requested_at": time.time(), "port": port}
    receipts.atomic_json(marker, operation)
    command = [sys.executable, "-B", str(source_root / "scripts/confirm_ui/server.py"), str(project), "--daemon", "--no-browser", "--port", str(port)]
    try:
        environment = dict(os.environ)
        environment.pop("PPT_MASTER_CONFIRMATION_PROTOCOL", None)
        if task.state.get("confirmation_protocol") == "ppt-confirmation-v1":
            environment["PPT_MASTER_CONFIRMATION_PROTOCOL"] = "1"
        result = subprocess.run(command, cwd=str(source_root), env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=15, shell=False, check=False)
        health = probe(candidate)  # Reconcile even when the launcher ACK failed.
        if health is not None:
            receipts.atomic_json(marker, {**operation, "state": "ready", "observed_pid": health["pid"]})
            return health_view(candidate, health)
        del result  # An exit code alone proves neither readiness nor failure.
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {"state": "recovery_unconfirmed", "confirmation_submitted": False, "agent_started": False}
