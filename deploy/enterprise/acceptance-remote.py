"""One-shot Python 3.12 diagnostic child. Never modifies production config."""

import contextlib
import hashlib
import io
import json
import logging
import os
import signal
import stat
import sys
import time
import types
from pathlib import Path


class CheckFailure(RuntimeError):
    pass


def require(ok, label):
    if not ok:
        raise CheckFailure(label)


def module(name, source):
    value = types.ModuleType(name)
    value.__file__ = "<acceptance-memory-overlay>"
    value.__package__ = name.rpartition(".")[0]
    sys.modules[name] = value
    exec(compile(source, value.__file__, "exec"), value.__dict__)
    return value


def inventory(payload):
    import importlib.metadata

    paths = payload["paths"]
    sources = {}
    for name in paths:
        p = Path("/app/backend") / name
        require(not Path(name).is_absolute() and ".." not in Path(name).parts, "invalid_source_path")
        sources[name] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() and p.stat().st_size < 2097152 else "ABSENT_OR_OVERSIZE"
    versions = {}
    for name in ("httpx", "openai", "langchain", "langchain-core", "langchain-openai", "langgraph", "langgraph-checkpoint", "pydantic", "playwright"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "ABSENT"
    from deerflow.config.paths import get_paths

    root = Path(get_paths().base_dir)
    volumes = []
    for name in ("/", str(root)):
        try:
            fs = os.statvfs(name)
            volumes.append({"path": name, "available_GiB": round(fs.f_bavail * fs.f_frsize / 2**30, 2)})
        except OSError:
            volumes.append({"path": name, "status": "UNAVAILABLE"})
    return {"source_hashes": sources, "dependency_versions": versions, "runtime_home": str(root), "disk": volumes}


def shared_parent(parent):
    parent = Path(parent)
    require(parent.is_absolute() and ".." not in parent.parts, "invalid_parent_path")
    require(parent.resolve() == parent and parent.is_dir(), "unsafe_fixture_parent")
    s = parent.stat()
    require(s.st_uid in (0, os.geteuid()) and not s.st_mode & 0o022, "private_parent_required_no_permissions_changed")
    return {"result": "PASS", "is_directory": True, "uid": s.st_uid, "mode": oct(stat.S_IMODE(s.st_mode)), "files_modified": False}


def fixture_path(payload):
    root = Path(payload["fixture"])
    require(root.is_absolute() and root.name == "portal-acceptance-" + payload["nonce"], "invalid_fixture_name")
    require(len(payload["nonce"]) == 32 and all(c in "0123456789abcdef" for c in payload["nonce"]), "invalid_nonce")
    shared_parent(root.parent)
    if payload["action"] == "initialize":
        root.mkdir(mode=0o700, exist_ok=False)
    s = root.lstat()
    require(stat.S_ISDIR(s.st_mode) and not root.is_symlink() and s.st_uid == os.geteuid() and stat.S_IMODE(s.st_mode) == 0o700, "unsafe_fixture")
    return root


def storage(payload, gate=None):
    from unittest.mock import patch

    root = fixture_path(payload)
    mod = module("acceptance_featured", payload["sources"]["featured"])
    store = mod.FeaturedStore(root / "featured.json", lock_timeout=1)
    ids = [mod.EmployeeIdentity("team", "fixture-" + str(i), "acceptance", "v1") for i in range(2)]
    action = payload["action"]
    if action == "initialize":
        require(store.initialize(ids, actor="acceptance"), "already_initialized")
        # Run the real channel store only against a new fixture, never its default path.
        channels = module("acceptance_channel_store", payload["sources"]["channels"])
        path = root / "channel-fixture.json"
        channel = channels.ChannelRuntimeConfigStore(path)
        channel.set_provider_config("fixture", {"version": 1})
        before = path.read_bytes()
        with patch("os.chmod", side_effect=OSError("fixture_permissions_failure")):
            try:
                channel.set_provider_config("fixture", {"version": 2})
            except OSError:
                pass
            else:
                raise RuntimeError("permissions_failure_was_ignored")
        require(path.read_bytes() == before and not list(root.glob("*.tmp")), "permission_failure_changed_state")
        return {"stage": action, "result": "PASS", "permissions_failure_preserved_fixture": True}
    if action == "hold":
        require(gate is not None, "control_channel_required")
        with store._transaction():
            gate("held", "release")
        return {"stage": action, "result": "PASS", "synchronization": "CONTROL_STDIN_STDOUT"}
    if action == "contend":
        before = (root / "featured.json").read_bytes()
        start = time.monotonic()
        try:
            store.set_hot(ids[0], False, expected_version=1, actor="should-time-out")
        except mod.FeaturedUnavailable as exc:
            require(str(exc) == "featured_lock_busy", "unexpected_lock_error")
        else:
            raise RuntimeError("cross_node_lock_not_exclusive")
        require(time.monotonic() - start >= 0.9 and (root / "featured.json").read_bytes() == before, "lock_timeout_state_changed")
        return {"stage": action, "result": "PASS", "timeout_preserved_fixture": True}
    if action == "write":
        index = payload["index"]
        require(index in (0, 1), "bad_writer")
        require(gate is not None, "control_channel_required")
        gate("ready", "go")
        require(store.set_hot(ids[index], False, expected_version=1, actor="acceptance") == 2, "write_version")
        return {"stage": action, "writer": index, "result": "PASS"}
    if action == "verify":
        snapshot = store.snapshot()
        require(all(snapshot.flag(identity) == (False, 2) for identity in ids), "lost_concurrent_update")
        before = (root / "featured.json").read_bytes()
        try:
            store.set_hot(ids[0], True, expected_version=1, actor="stale")
        except mod.FeaturedConflict:
            pass
        else:
            raise RuntimeError("stale_writer_accepted")
        require((root / "featured.json").read_bytes() == before, "cas_changed_state")
        with patch("os.replace", side_effect=OSError("fixture_disk_error")):
            try:
                store.set_hot(ids[0], True, expected_version=2, actor="disk-failure")
            except mod.FeaturedUnavailable:
                pass
            else:
                raise RuntimeError("replace_failure_ignored")
        require((root / "featured.json").read_bytes() == before and not list(root.glob("*.tmp")), "replace_failure_changed_state")
        return {"stage": action, "result": "PASS", "revision": snapshot.document["revision"], "fixture_sha256": hashlib.sha256(before).hexdigest(), "cas": "PASS", "atomic_replace_failure": "PASS"}
    raise RuntimeError("unknown_storage_action")


def glm(payload):
    import asyncio

    key = payload.get("key", "")
    require(isinstance(key, str) and 0 < len(key) < 8192 and key.strip() == key, "invalid_key")
    module("deerflow.models.robust_openai", payload["sources"]["robust"])
    module("deerflow.models.enterprise_glm", payload["sources"]["glm"])
    probe = module("acceptance_glm_probe", payload["sources"]["glm_probe"])
    result = asyncio.run(probe._live(key, 120))
    result["execution_scope"] = "CANDIDATE_CLIENT_IN_MEMORY_PRODUCTION_DEPENDENCIES_NOT_IMAGE_ACCEPTANCE"
    return result


def main(payload, gate=None):
    logging.disable(logging.CRITICAL)
    for name in ("LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING"):
        os.environ[name] = "false"
    os.environ["OTEL_SDK_DISABLED"] = "true"
    # Suppress imported application/SDK diagnostics; never return raw errors.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        if payload["action"] == "inventory":
            return inventory(payload)
        if payload["action"] == "glm":
            return glm(payload)
        if payload["action"] == "shared_parent":
            return shared_parent(payload["parent"])
        return storage(payload, gate)


if __name__ == "__main__":
    controlled = sys.argv[1:] == ["--gated"]
    payload = {}
    output = sys.stdout

    def event(name, **kwargs):
        frame = {"event": name, "nonce": payload.get("nonce"), "action": payload.get("action"), "writer": payload.get("index")}
        frame.update(kwargs)
        output.write(json.dumps(frame) + "\n")
        output.flush()

    def control_gate(ready_event, expected_command):
        event(ready_event)
        line = sys.stdin.readline(1025)
        require(bool(line) and len(line) <= 1024, "control_disconnected_or_oversize")
        command = json.loads(line)
        require(command == {"command": expected_command, "nonce": payload["nonce"], "action": payload["action"], "writer": payload.get("index")}, "invalid_control_command")

    try:
        if controlled:
            # No subprocesses or business tools are launched on this path. A
            # lost kubectl/controller cannot leave a lock holder alive forever.
            signal.signal(signal.SIGALRM, signal.SIG_DFL)
            signal.alarm(45)
        raw = sys.stdin.readline(524289) if controlled else sys.stdin.read(524289)
        require(len(raw) <= 524288, "input_too_large")
        decoded = json.loads(raw)
        require(isinstance(decoded, dict), "invalid_payload")
        payload = decoded
        if controlled:
            require(payload.get("action") in ("hold", "write") and "key" not in payload, "invalid_gated_action")
        result = main(payload, control_gate if controlled else None)
    except Exception as exc:
        result = {"result": "FAIL", "error_type": type(exc).__name__, "raw_error_printed": False, "automatic_retry": False}
        if isinstance(exc, CheckFailure):
            result["failed_check"] = str(exc)
    if controlled:
        event("result", receipt=result)
    else:
        print(json.dumps(result))
