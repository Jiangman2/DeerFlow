"""Add the Gateway's 0023 migration file to the R4 PPT Runner deployment."""
import argparse
import hashlib
import json
import subprocess
import sys

NS = "deer-flow"
RUNNER = "ppt-master-runner"
TAG = "35f398710736da63"
LABEL = "deerflow.io/global-pool-release"
IMAGE = "deerflow.local/deer-flow-ppt-master-runner@sha256:f72420132c8f23801306266002c2ffcbef817d186b51eb9ac2dfaf64a567d3d2"
FILE = "0023_procurement_review_jobs.py"
ROOT = "/app/backend/packages/harness/deerflow/persistence/migrations/versions"
SHA = "baac2093327c6e68df6d1db783d2f62f6dbf18f2f38f411cbae031e885d1579d"
CM = "ppt-runner-migration-0023-" + SHA[:12]
VOLUME = "ppt-runner-migration-0023"
PYTHON = "/app/backend/.venv/bin/python"


def require(value, message):
    if not value:
        raise RuntimeError(message)


def kub(*args, body=None):
    return subprocess.check_output(["kubectl", "-n", NS, *args], input=body, timeout=90)


def read(kind, name):
    data = kub("get", kind, name, "--ignore-not-found", "-o", "json")
    return json.loads(data) if data.strip() else None


def pod_for(deployment):
    name = deployment["metadata"]["name"]
    selector = deployment["spec"]["selector"]
    require(selector.get("matchLabels") and not selector.get("matchExpressions"), "Unexpected selector")
    labels = ",".join(k + "=" + v for k, v in selector["matchLabels"].items())
    pods = json.loads(kub("get", "pods", "-l", labels, "-o", "json"))["items"]
    for p in pods:
        if p["metadata"].get("deletionTimestamp"):
            continue
        if name == RUNNER and not any(c["name"] == name and c["image"] == IMAGE for c in p["spec"]["containers"]):
            continue
        if any(c["name"] == name and c["ready"] for c in p["status"].get("containerStatuses", [])):
            return p["metadata"]["name"]
    raise RuntimeError("No Ready source container for " + name)


def verify_chain(pod, name, source, *, installed=False):
    # ScriptDirectory only reads revision files; it does not run env.py or connect to the database.
    code = """
import hashlib, sys, tempfile
from pathlib import Path
from alembic.script import ScriptDirectory
root, filename, sha, mode = sys.argv[1:]
root = Path(root)
target = filename[:-3]
parent = "0022_work_integration_state"
def verify(s):
    if s.get_heads() != [target] or s.get_revision(target).down_revision != parent:
        raise ValueError("Unexpected migration chain")
    list(s.walk_revisions())
if mode == "installed":
    if hashlib.sha256((root/filename).read_text(encoding="utf-8").encode()).hexdigest() != sha:
        raise ValueError("Installed source hash mismatch")
    verify(ScriptDirectory(str(root.parent)))
else:
    if ScriptDirectory(str(root.parent)).get_heads() != [parent] or (root/filename).exists():
        raise ValueError("Runner baseline differs from 0022")
    with tempfile.TemporaryDirectory() as tmp:
        versions = Path(tmp)/"versions"
        versions.mkdir()
        for p in root.glob("*.py"):
            (versions/p.name).symlink_to(p)
        (versions/filename).write_bytes(sys.stdin.buffer.read())
        verify(ScriptDirectory(tmp))
"""
    kub("exec", "-i", pod, "-c", name, "--", PYTHON, "-B", "-c", code,
        ROOT, FILE, SHA, "installed" if installed else "preview", body=source)


def patch_for(d):
    tpl = d["spec"]["template"]
    require(tpl["metadata"].get("annotations", {}).get(LABEL) == TAG, "Runner is not on expected R4 template")
    spec = tpl["spec"]
    index, c = next((i, c) for i, c in enumerate(spec["containers"]) if c["name"] == RUNNER)
    require(c["image"] == IMAGE, "Runner image differs")
    mount = {"name": VOLUME, "readOnly": True, "mountPath": ROOT + "/" + FILE, "subPath": FILE}
    volume = {"name": VOLUME, "configMap": {"name": CM}}
    mounts = c.get("volumeMounts", [])
    volumes = spec.get("volumes", [])
    matching = [m for m in mounts if m["mountPath"] == mount["mountPath"] or m["name"] == VOLUME]
    existing = [v for v in volumes if v["name"] == VOLUME]
    if matching or existing:
        require(matching == [mount] and len(existing) == 1 and existing[0].get("configMap", {}).get("name") == CM,
                "Existing migration mount conflicts")
        return []
    return [
        {"op": "test", "path": "/metadata/resourceVersion", "value": d["metadata"]["resourceVersion"]},
        {"op": "add", "path": "/spec/template/spec/volumes/-", "value": volume},
        {"op": "add", "path": f"/spec/template/spec/containers/{index}/volumeMounts/-", "value": mount},
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", required=True)
    parser.parse_args()
    d = read("deployment", RUNNER)
    patch = patch_for(d)
    gateway = pod_for(read("deployment", "gateway"))
    source = kub("exec", gateway, "-c", "gateway", "--", "cat", ROOT + "/" + FILE)
    source = source.replace(b"\r\n", b"\n")
    require(hashlib.sha256(source).hexdigest() == SHA, "Gateway migration differs from the verified release source")
    verify_chain(gateway, "gateway", b"", installed=True)
    old = read("configmap", CM)
    if old:
        require(old.get("immutable") and old.get("data") == {FILE: source.decode()}, "ConfigMap differs")
    if patch:
        verify_chain(pod_for(d), RUNNER, source)
        print("RUNNER_0022_PLUS_0023_CHAIN_VERIFIED", flush=True)
        args = ["patch", "deployment", RUNNER, "--type=json", "-p", json.dumps(patch), "-o", "name"]
        kub(*args, "--dry-run=server")
        if old is None:
            obj = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": CM, "namespace": NS},
                   "immutable": True, "data": {FILE: source.decode()}}
            kub("create", "-f", "-", "-o", "name", body=json.dumps(obj).encode())
        kub(*args)
        print("RUNNER_0023_ROLLOUT_SUBMITTED", flush=True)
    else:
        require(old, "Expected ConfigMap is missing")
    subprocess.run(["kubectl", "-n", NS, "rollout", "status", "deployment/" + RUNNER, "--timeout=15m"], check=True)
    current = read("deployment", RUNNER)
    require(not patch_for(current), "Runner template changed during rollout")
    verify_chain(pod_for(current), RUNNER, b"", installed=True)
    print("PPT_RUNNER_0023_READY")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.SubprocessError) as e:
        sys.exit("REPAIR_STOPPED: " + str(e)[:500])
