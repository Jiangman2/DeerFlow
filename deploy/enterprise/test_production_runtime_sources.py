"""No-cluster tests for the production source fingerprint collector."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

SPEC = importlib.util.spec_from_file_location("runtime_sources", Path(__file__).with_name("inspect-production-runtime-sources.py"))
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def test_runtime_manifest_exports_hashes_not_contents(tmp_path):
    root = tmp_path / "backend"
    (root / "app").mkdir(parents=True)
    (root / "packages/harness").mkdir(parents=True)
    secret = b"# synthetic-private-sentinel\r\nx = 1\r\n"
    (root / "app/probe.py").write_bytes(secret)
    (root / "app/.env").write_text("private-env")
    code = M.REMOTE_PY.replace("/app/backend", str(root))
    p = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, check=True)
    result = json.loads(p.stdout)
    assert result["file_count"] == 1
    assert result["files"][0]["sha256"] != result["files"][0]["lf_sha256"]
    assert b"synthetic-private-sentinel" not in p.stdout and b"private-env" not in p.stdout


@pytest.mark.parametrize("kind", ["symlink", "large"])
def test_runtime_manifest_refuses_nonregular_or_large_source(tmp_path, kind):
    root = tmp_path / "backend"
    (root / "app").mkdir(parents=True)
    (root / "packages/harness").mkdir(parents=True)
    p = root / "app/probe.py"
    if kind == "symlink":
        p.symlink_to(root / "outside.py")
    else:
        with p.open("wb") as stream:
            stream.truncate(2097153)
    result = subprocess.run([sys.executable, "-I", "-B", "-c", M.REMOTE_PY.replace("/app/backend", str(root))], capture_output=True)
    assert result.returncode != 0 and not result.stdout


class Reader:
    def __init__(self, failure=None):
        self.failure = failure
        self.execs = []
        self.dep = {"metadata": {"uid": "dep", "generation": 1}, "spec": {"replicas": 1}, "status": {"observedGeneration": 1, "replicas": 1, "readyReplicas": 1, "availableReplicas": 1, "updatedReplicas": 1}}
        self.pod = {"metadata": {"name": "sample", "uid": "pod", "ownerReferences": [{"controller": True, "uid": "rs"}]}, "spec": {"containers": [{"name": "gateway", "image": "fixture"}]}, "status": {"phase": "Running", "containerStatuses": [{"name": "gateway", "ready": True, "containerID": "container", "imageID": "fixture"}]}}

    def get(self, resource, name=None):
        if resource == "deployments":
            dep = copy.deepcopy(self.dep)
            if self.failure == "baseline":
                dep["metadata"]["generation"] = 2
            if self.failure == "rollout":
                dep["status"]["updatedReplicas"] = 0
            return dep
        if resource == "replicasets":
            return {"items": [{"metadata": {"uid": "rs", "ownerReferences": [{"controller": True, "uid": "wrong" if self.failure == "owner" else "dep"}]}}]}
        if resource == "pods":
            pod = copy.deepcopy(self.pod)
            if name:
                if self.failure == "replaced":
                    pod["status"]["containerStatuses"][0]["containerID"] = "new"
                return pod
            return {"items": [pod]}
        raise AssertionError(resource)

    def command(self, args):
        self.execs.append(args)
        return {"status": "RUNTIME_SOURCE_HASHES_COLLECTED"}


@pytest.mark.parametrize("failure", [None, "baseline", "rollout", "owner", "replaced"])
def test_binding_and_pod_identity(monkeypatch, failure):
    reader = Reader(failure)
    monkeypatch.setattr(M, "PINS", {"gateway": {**M.binding(reader.dep), "container": "gateway", "image": "fixture"}})
    if failure:
        with pytest.raises(M.Failure):
            M.collect_sources(reader)
        assert len(reader.execs) == (1 if failure == "replaced" else 0)
    else:
        result = M.collect_sources(reader)
        assert len(result) == 1 and len(reader.execs) == 1
        assert reader.execs[0][-4:-1] == ["-I", "-B", "-c"]


def test_public_ui_program_is_fixed_and_hash_only():
    assert "sha256sum" in M.REMOTE_UI
    assert "chmod" not in M.REMOTE_UI and "curl" not in M.REMOTE_UI
    assert "/usr/share/nginx/html" in M.REMOTE_UI and "/docker-entrypoint.sh" in M.REMOTE_UI


def test_public_ui_shell_syntax():
    result = subprocess.run(["sh", "-n"], input=M.REMOTE_UI.encode(), capture_output=True)
    assert result.returncode == 0
