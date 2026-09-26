"""Read-only source-mount evidence; Python 3.7+, manually on datax-01.

Reads four Deployments, their Pods, and only ConfigMaps referenced by fixed
Python source-file mounts. Exports those application source keys, not Secrets,
environment values, employee data or other ConfigMap keys. Existing containers
only hash the selected source files. No application imports, builds or writes
in Kubernetes. The sole write is a NEW private local evidence JSON.
"""

import argparse
import base64
import datetime
import hashlib
import json
import os
import re
import socket
import subprocess
import time
from pathlib import Path, PurePosixPath

WORKLOADS = ("gateway", "frontend", "provisioner", "ppt-master-runner")
ROOTS = {
    "gateway": ("/app/backend/app", "/app/backend/packages/harness"),
    "ppt-master-runner": ("/app/backend/app", "/app/backend/packages/harness"),
    "frontend": ("/app/frontend/src", "/app/frontend/.next", "/app/frontend/server.js"),
    "provisioner": ("/app/app.py",),
}
LIMIT = 2 * 1024**2
_deadline = None

READER = r"""
import hashlib, json, os, stat, sys
from pathlib import Path
paths = json.loads(sys.argv[1])
if not isinstance(paths, list) or not 1 <= len(paths) <= 64:
    raise ValueError("invalid read scope")
result = {}
for value in paths:
    if not isinstance(value, str) or not value.endswith(".py") or ".." in value.split("/"):
        raise ValueError("invalid source path")
    if not (value == "/app/app.py" or value.startswith(("/app/backend/app/", "/app/backend/packages/harness/"))):
        raise ValueError("source outside whitelist")
    path = Path(value)
    for parent in path.parents:
        if not stat.S_ISDIR(parent.lstat().st_mode):
            raise ValueError("linked source parent")
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > 2 * 1024**2:
        raise ValueError("unsafe source")
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        content = stream.read(2 * 1024**2 + 1)
        opened = os.fstat(stream.fileno())
    after = path.lstat()
    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if signature(before) != signature(opened) or signature(before) != signature(after) or len(content) != before.st_size:
        raise ValueError("source changed")
    result[value] = {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
print(json.dumps(result, sort_keys=True))
"""


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(value):
    return sha(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate evidence key")
        result[key] = value
    return result


def read(arguments):
    if _deadline is not None and time.monotonic() >= _deadline:
        raise RuntimeError("source overlay audit time budget exceeded")
    if arguments[0] not in ("get", "exec") or (
        arguments[0] == "get"
        and arguments[1] not in ("deployment", "pods", "pod", "configmap")
    ):
        raise ValueError("non-read or out-of-scope operation refused")
    if arguments[0] == "exec":
        if (
            len(arguments) != 10
            or arguments[2] != "-c"
            or arguments[3] not in ("gateway", "ppt-master-runner", "provisioner")
            or arguments[4] != "--"
            or arguments[5]
            != (
                "python3"
                if arguments[3] == "provisioner"
                else "/app/backend/.venv/bin/python"
            )
            or arguments[6:9] != ["-B", "-c", READER]
        ):
            raise ValueError("only the fixed source hash reader is permitted")
        identifier(arguments[1])
        paths = json.loads(arguments[9], object_pairs_hook=unique)
        if (
            not isinstance(paths, list)
            or not 1 <= len(paths) <= 64
            or any(
                not isinstance(path, str)
                or not path.endswith(".py")
                or not is_source(arguments[3], path)
                for path in paths
            )
        ):
            raise ValueError("invalid source hash read scope")
    env = dict(os.environ, KUBECONFIG="/etc/kubernetes/admin.conf")
    for key in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        env.pop(key, None)
    result = subprocess.run(
        ["kubectl", "--request-timeout=15s", "-n", "deer-flow"] + arguments,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=30,
    )
    if result.returncode or len(result.stdout) > 16 * 1024**2:
        raise RuntimeError("bounded Kubernetes read failed; raw output withheld")
    return json.loads(result.stdout, object_pairs_hook=unique)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9.-]{0,252}", value
    ):
        raise ValueError("invalid resource identity")
    return value


def safe_path(value, *, absolute):
    if (
        not isinstance(value, str)
        or not value
        or any(c in value for c in ("\\", "\0", "\n", "\r"))
        or value.startswith("//")
        or ".." in value.split("/")
        or PurePosixPath(value).is_absolute() != absolute
    ):
        raise ValueError("unsafe source-mount path")
    return PurePosixPath(value)


def is_source(workload, path):
    mount = safe_path(path, absolute=True)
    return any(
        mount == root or mount in root.parents or root in mount.parents
        for root in map(PurePosixPath, ROOTS[workload])
    )


def bindings(workload, spec):
    containers = [c for c in spec.get("containers", []) if c.get("name") == workload]
    if len(containers) != 1:
        raise ValueError("runtime container missing or ambiguous")
    volumes = {v["name"]: v for v in spec.get("volumes", [])}
    if len(volumes) != len(spec.get("volumes", [])):
        raise ValueError("duplicate volume identity")
    result = []
    for mount in containers[0].get("volumeMounts", []):
        path = mount.get("mountPath")
        if not is_source(workload, path):
            continue
        if (
            not path.endswith(".py")
            or workload == "frontend"
            or mount.get("readOnly") is not True
            or mount.get("subPathExpr")
            or not mount.get("subPath")
        ):
            raise ValueError(
                "source mount requires explicit review: fixed readonly Python file required"
            )
        subpath = str(safe_path(mount["subPath"], absolute=False))
        volume = volumes.get(mount["name"], {})
        config = volume.get("configMap")
        if not isinstance(config, dict) or config.get("optional"):
            raise ValueError(
                "source volume is not a required ConfigMap; no Secret read allowed"
            )
        name = identifier(config.get("name"))
        if "items" in config:
            items = config["items"]
            paths = [str(safe_path(i["path"], absolute=False)) for i in items]
            if len(paths) != len(set(paths)):
                raise ValueError("duplicate ConfigMap projected path")
            keys = [i["key"] for i, p in zip(items, paths) if p == subpath]
            if len(keys) != 1:
                raise ValueError("mounted source key missing or ambiguous")
            key = keys[0]
        else:
            key = subpath
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,253}", key):
            raise ValueError("invalid source ConfigMap key")
        result.append(
            {
                "mount_path": path,
                "volume_name": mount["name"],
                "sub_path": mount["subPath"],
                "read_only": True,
                "configmap_name": name,
                "configmap_key": key,
            }
        )
    if len(result) > 64 or len({b["mount_path"] for b in result}) != len(result):
        raise ValueError("source mount scope exceeds bound or is duplicated")
    return sorted(result, key=lambda b: b["mount_path"])


def config_key(config, key):
    data, binary = config.get("data", {}), config.get("binaryData", {})
    if (key in data) == (key in binary):
        raise ValueError("source ConfigMap key absent or duplicated")
    raw = (
        data[key].encode("utf-8")
        if key in data
        else base64.b64decode(binary[key], validate=True)
    )
    if len(raw) > LIMIT:
        raise ValueError("source ConfigMap key exceeds bound")
    return raw


def instance(pod, workload):
    status = [
        s
        for s in pod.get("status", {}).get("containerStatuses", [])
        if s.get("name") == workload
    ]
    if (
        pod["metadata"].get("deletionTimestamp")
        or pod.get("status", {}).get("phase") != "Running"
        or len(status) != 1
        or status[0].get("ready") is not True
        or not status[0].get("containerID")
        or not status[0].get("imageID")
    ):
        raise ValueError("stable ready runtime container required")
    return {
        "pod": pod["metadata"]["name"],
        "uid": pod["metadata"]["uid"],
        "spec_sha256": digest(pod["spec"]),
        "container_id": status[0]["containerID"],
        "image_id": status[0]["imageID"],
        "restarts": status[0].get("restartCount", 0),
    }


def collect():
    deployments = {
        name: read(["get", "deployment", name, "-o", "json"]) for name in WORKLOADS
    }
    pods = read(["get", "pods", "-o", "json"])["items"]
    if len(pods) > 2000:
        raise ValueError("Pod list exceeds bound")
    configs, outputs = {}, {}
    for name, deployment in deployments.items():
        print("source_overlay_audit=READING workload=" + name, flush=True)
        selector = deployment["spec"].get("selector", {})
        labels = selector.get("matchLabels", {})
        if not labels or selector.get("matchExpressions"):
            raise ValueError("unsupported deployment selector")
        selected = [
            p
            for p in pods
            if all(
                p["metadata"].get("labels", {}).get(k) == v for k, v in labels.items()
            )
        ]
        replicas = deployment["spec"].get("replicas", 1)
        if not 1 <= len(selected) <= 16 or len(selected) != replicas:
            raise ValueError("deployment Pod set unstable")
        template = deployment["spec"]["template"]["spec"]
        expected = bindings(name, template)
        container = next(c for c in template["containers"] if c["name"] == name)
        instances = []
        for pod in selected:
            before = instance(pod, name)
            live = bindings(name, pod["spec"])
            live_container = next(
                c for c in pod["spec"]["containers"] if c["name"] == name
            )
            if live != expected or live_container["image"] != container["image"]:
                raise ValueError("live/template source binding or image differs")
            hashes = {}
            for binding in live:
                cm_name = binding["configmap_name"]
                if cm_name not in configs:
                    if len(configs) >= 32:
                        raise ValueError("ConfigMap scope exceeds bound")
                    configs[cm_name] = read(["get", "configmap", cm_name, "-o", "json"])
                cm = configs[cm_name]
                if cm["metadata"]["name"] != cm_name:
                    raise ValueError("ConfigMap identity differs")
                raw = config_key(cm, binding["configmap_key"])
                hashes[binding["mount_path"]] = {"sha256": sha(raw), "bytes": len(raw)}
            if hashes:
                interpreter = (
                    "python3"
                    if name == "provisioner"
                    else "/app/backend/.venv/bin/python"
                )
                actual = read(
                    [
                        "exec",
                        pod["metadata"]["name"],
                        "-c",
                        name,
                        "--",
                        interpreter,
                        "-B",
                        "-c",
                        READER,
                        json.dumps(sorted(hashes)),
                    ]
                )
                if actual != hashes:
                    raise ValueError(
                        "mounted source differs from ConfigMap (possibly stale subPath)"
                    )
            after = read(["get", "pod", pod["metadata"]["name"], "-o", "json"])
            if before != instance(after, name):
                raise ValueError("runtime instance changed during audit")
            instances.append(before)
        if len({item["image_id"] for item in instances}) != 1:
            raise ValueError("one image tag resolves to different running image IDs")
        sources = []
        for binding in expected:
            cm = configs[binding["configmap_name"]]
            raw = config_key(cm, binding["configmap_key"])
            sources.append(
                dict(
                    binding,
                    sha256=sha(raw),
                    bytes=len(raw),
                    base64=base64.b64encode(raw).decode("ascii"),
                    configmap_uid=cm["metadata"]["uid"],
                    configmap_resource_version=cm["metadata"]["resourceVersion"],
                    configmap_immutable=cm.get("immutable", False),
                )
            )
        outputs[name] = {
            "deployment_uid": deployment["metadata"]["uid"],
            "deployment_spec_sha256": digest(deployment["spec"]),
            "image": container["image"],
            "instances": instances,
            "source_bindings": sources,
        }
    for name, before in configs.items():
        after = read(["get", "configmap", name, "-o", "json"])
        if digest(before) != digest(after):
            raise ValueError("ConfigMap changed during audit")
    for name, before in deployments.items():
        after = read(["get", "deployment", name, "-o", "json"])
        if before["metadata"]["uid"] != after["metadata"]["uid"] or digest(
            before["spec"]
        ) != digest(after["spec"]):
            raise ValueError("Deployment changed during audit")
    return {
        "schema": 1,
        "audit": "employee-sharing-source-overlays-v1",
        "collected_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "workloads": outputs,
        "secrets_collected": False,
        "employee_data_collected": False,
        "production_mutation": "NONE",
        "consistent_backup": False,
        "ready_for_rollout": False,
        "coverage": "FIXED_READONLY_PYTHON_SOURCE_MOUNTS_ONLY",
    }


def write_report(path, report):
    path = Path(path).absolute()
    for parent in (path,) + tuple(path.parents):
        if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
            raise ValueError("linked report destination refused")
    raw = (
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    if len(raw) > 16 * 1024**2:
        raise ValueError("source report exceeds bound")
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def main():
    global _deadline
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        socket.gethostname().split(".")[0] != "datax-01"
        or getattr(os, "geteuid", lambda: -1)() != 0
    ):
        raise ValueError("run manually as root on datax-01")
    if os.path.lexists(str(args.output)):
        raise ValueError("existing evidence is never overwritten")
    _deadline = time.monotonic() + 180
    print(
        "employee_source_overlays=READING max_seconds=210 no_production_writes=yes",
        flush=True,
    )
    write_report(args.output, collect())
    print(
        "employee_source_overlays=COLLECTED ready_for_rollout=false evidence="
        + str(args.output)
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Never echo remote source, environment, stderr or decoding excerpts.
        safe = (
            str(error)
            if isinstance(error, (ValueError, RuntimeError))
            and not isinstance(error, json.JSONDecodeError)
            else "read_or_private_evidence_write_failed"
        )
        if len(safe) > 160 or any(c in safe for c in ("\n", "\r")):
            safe = "read_or_private_evidence_write_failed"
        print("employee_source_overlays=FAIL detail=" + safe)
        raise SystemExit(1)
