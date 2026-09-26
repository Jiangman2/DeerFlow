import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from deerflow.community.aio_sandbox.local_backend import LocalContainerBackend


@pytest.fixture
def backend(monkeypatch, tmp_path):
    instance = object.__new__(LocalContainerBackend)
    instance._runtime = "docker"
    instance._container_prefix = "sandbox"
    projection = tmp_path / "projection"
    storage = SimpleNamespace(get_skills_root_path=lambda: projection, get_container_root=lambda: "/mnt/skills")
    monkeypatch.setattr("deerflow.skills.agent_publication.get_thread_published_skill_storage", lambda *a, **kw: storage)
    return instance, projection


def inspect(monkeypatch, mounts):
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps([{"Name": "/sandbox-123", "Mounts": mounts}])))
    monkeypatch.setattr("deerflow.community.aio_sandbox.local_backend.subprocess.run", run)
    return run


def test_docker_verifies_exact_readonly_root_on_every_adoption(backend, monkeypatch):
    instance, projection = backend
    run = inspect(monkeypatch, [{"Type": "bind", "Source": str(projection), "Destination": "/mnt/skills", "RW": False}])
    instance.validate_employee_skills("123", "thread", user_id="bob")
    assert run.call_args.args[0] == ["docker", "inspect", "sandbox-123"]
    assert run.call_args.kwargs["timeout"] <= 15


@pytest.mark.parametrize("variant", ["missing", "writable", "wrong-owner", "nested-overlay", "ancestor-overlay", "duplicate", "volume"])
def test_docker_rejects_ambiguous_mount_without_restarting_container(backend, monkeypatch, variant):
    instance, projection = backend
    mount = {"Type": "bind", "Source": str(projection), "Destination": "/mnt/skills", "RW": False}
    mounts = [mount]
    if variant == "missing":
        mounts = []
    elif variant == "writable":
        mount["RW"] = True
    elif variant == "wrong-owner":
        mount["Source"] = str(Path(projection).parent / "alice")
    elif variant == "volume":
        mount["Type"] = "volume"
    else:
        mounts.append({**mount, "Destination": {"nested-overlay": "/mnt/skills/custom", "ancestor-overlay": "/mnt", "duplicate": "/mnt/skills"}[variant]})
    run = inspect(monkeypatch, mounts)
    with pytest.raises(RuntimeError):
        instance.validate_employee_skills("123", "thread", user_id="bob")
    assert run.call_count == 1
