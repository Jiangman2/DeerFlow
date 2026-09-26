#!/usr/bin/env python3
"""Read-only storage planning evidence; host Python 3.7+, stdlib only.

Does not enumerate employees, read their files, query SQL, stop writers, mount
volumes or run migrations. Config is read only inside an existing Gateway and
only two storage enum values leave that process. No app imports/lifespan.
This is NOT a backup, maintenance fence, rollback approval or rollout check.
"""

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import socket
import subprocess
import time


_deadline = None


READER = r"""
import json, os, stat
from pathlib import Path

def path_info(path):
    try:
        value = path.lstat()
    except FileNotFoundError:
        return {"kind": "missing"}
    kind = "directory" if stat.S_ISDIR(value.st_mode) else "file" if stat.S_ISREG(value.st_mode) else "link_or_special"
    return {"kind": kind, "uid": value.st_uid, "gid": value.st_gid, "mode": stat.S_IMODE(value.st_mode), "device": value.st_dev}

def inspect():
    # Explicit env only: do not guess a path supplied via .env/project defaults.
    result = {"config_selection": "UNRESOLVED", "agent_storage": "UNKNOWN", "database": "UNKNOWN",
              "data_home": None, "data_home_state": "UNRESOLVED", "roots": {}, "database_queried": False,
              "employee_files_read": False, "config_values_exported": ["agent_storage.backend", "database.backend"]}
    selected = os.environ.get("DEER_FLOW_CONFIG_PATH")
    if selected:
        import yaml
        class UniqueLoader(yaml.SafeLoader):
            pass
        def mapping(loader, node, deep=False):
            loader.flatten_mapping(node)
            values = {}
            for key_node, value_node in node.value:
                key = loader.construct_object(key_node, deep=deep)
                if key in values:
                    raise ValueError("duplicate configuration key")
                values[key] = loader.construct_object(value_node, deep=deep)
            return values
        UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
        source = Path(selected)
        # Projected ConfigMaps intentionally use symlinks. Verify the opened
        # regular file and selected target do not change across this bounded read.
        before = source.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > 2097152:
            raise ValueError("invalid configuration file")
        with source.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            raw = stream.read(2097153)
            ended = os.fstat(stream.fileno())
        after = source.stat()
        sig = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
        if len(raw) > 2097152 or any(sig(x) != sig(before) for x in (opened, ended, after)):
            raise ValueError("configuration changed")
        config = yaml.load(raw, Loader=UniqueLoader)
        if not isinstance(config, dict):
            raise ValueError("configuration is not a mapping")
        def enum(section, allowed, default):
            group = config.get(section, {})
            if not isinstance(group, dict):
                return "UNKNOWN"
            value = group.get("backend", default)
            if isinstance(value, str) and value.startswith("$"):
                value = os.environ.get(value[1:])
            return value if isinstance(value, str) and value in allowed else "UNKNOWN"
        result.update(config_selection="EXPLICIT_ENV", config_path=str(source.absolute()),
                      agent_storage=enum("agent_storage", ("file", "db"), "file"),
                      database=enum("database", ("postgres", "sqlite", "memory"), "sqlite"))
    value = os.environ.get("DEER_FLOW_HOME")
    if value:
        root = Path(value)
        result["data_home"] = str(root)
        if not root.is_absolute() or ".." in root.parts or any(p.is_symlink() or getattr(p, "is_junction", lambda: False)() for p in (root,) + tuple(root.parents)):
            result["data_home_state"] = "LINKED_OR_UNSAFE"
        else:
            info = path_info(root)
            result["data_home_state"] = info["kind"]
            result["data_home_metadata"] = info
            if info["kind"] == "directory":
                result["roots"] = {name: path_info(root / name) for name in ("agents", "team_agents", "users", "threads")}
    return result

try:
    print(json.dumps(inspect(), sort_keys=True))
except Exception:
    # YAML errors can quote credentials from the source line. Never print them.
    print("storage_reader=FAIL detail=bounded_config_or_path_check_failed")
    raise SystemExit(1)
"""


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def kubectl(arguments):
    if _deadline is not None and time.monotonic() >= _deadline:
        raise RuntimeError("Storage audit time budget exceeded")
    if arguments[0] not in ("get", "exec"):
        raise RuntimeError("non-read command forbidden")
    env = dict(os.environ, KUBECONFIG="/etc/kubernetes/admin.conf")
    for key in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        env.pop(key, None)
    completed = subprocess.run(
        ["kubectl", "--request-timeout=15s", "-n", "deer-flow"] + arguments,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        env=env,
    )
    if completed.returncode or len(completed.stdout) > 16 * 1024 * 1024:
        raise RuntimeError(
            "bounded kubectl read failed; no raw configuration was printed"
        )
    try:
        return json.loads(completed.stdout)
    except (ValueError, UnicodeError):
        raise RuntimeError("storage reader did not return valid evidence") from None


def mounts(container):
    allowed = (
        "name",
        "mountPath",
        "subPath",
        "subPathExpr",
        "readOnly",
        "mountPropagation",
    )
    return [
        {key: m[key] for key in allowed if key in m}
        for m in container.get("volumeMounts", [])
    ]


def project_pod(pod):
    volumes = []
    for volume in pod["spec"].get("volumes", []):
        item = {"name": volume["name"]}
        if "persistentVolumeClaim" in volume:
            item.update(
                kind="pvc",
                claim=volume["persistentVolumeClaim"]["claimName"],
                read_only=volume["persistentVolumeClaim"].get("readOnly", False),
            )
        elif "nfs" in volume:
            item.update(
                kind="nfs",
                **{
                    k: volume["nfs"][k]
                    for k in ("server", "path", "readOnly")
                    if k in volume["nfs"]
                },
            )
        elif "hostPath" in volume:
            item.update(kind="hostPath", path=volume["hostPath"].get("path"))
        else:
            item["kind"] = next(
                (
                    k
                    for k in (
                        "emptyDir",
                        "configMap",
                        "secret",
                        "projected",
                        "csi",
                        "ephemeral",
                    )
                    if k in volume
                ),
                "unknown",
            )
        volumes.append(item)
    containers = []
    for category in ("containers", "initContainers", "ephemeralContainers"):
        for container in pod["spec"].get(category, []):
            containers.append(
                {
                    "name": container["name"],
                    "category": category,
                    "image": container.get("image"),
                    "mounts": mounts(container),
                }
            )
    return {
        "pod": pod["metadata"]["name"],
        "uid": pod["metadata"]["uid"],
        "node": pod["spec"].get("nodeName"),
        "phase": pod.get("status", {}).get("phase"),
        "deleting": bool(pod["metadata"].get("deletionTimestamp")),
        "containers": containers,
        "volumes": volumes,
    }


def project_pv(pv):
    spec = pv["spec"]
    result = {
        "name": pv["metadata"]["name"],
        "uid": pv["metadata"]["uid"],
        "spec_digest": digest(spec),
        "access_modes": spec.get("accessModes", []),
        "reclaim_policy": spec.get("persistentVolumeReclaimPolicy"),
        "volume_mode": spec.get("volumeMode", "Filesystem"),
    }
    if "nfs" in spec:
        result.update(
            kind="nfs",
            **{
                k: spec["nfs"][k]
                for k in ("server", "path", "readOnly")
                if k in spec["nfs"]
            },
        )
    elif "csi" in spec:
        result.update(
            kind="csi",
            driver=spec["csi"].get("driver"),
            handle_digest=digest(spec["csi"].get("volumeHandle")),
        )
    elif "local" in spec or "hostPath" in spec:
        key = "local" if "local" in spec else "hostPath"
        result.update(kind=key, path=spec[key].get("path"))
    else:
        result["kind"] = "unknown"
    return result


def require_same_instance(before, after):
    def instance(pod):
        status = next(
            (
                x
                for x in pod.get("status", {}).get("containerStatuses", [])
                if x["name"] == "gateway"
            ),
            {},
        )
        if (
            not status.get("ready")
            or not status.get("containerID")
            or pod["metadata"].get("deletionTimestamp")
            or pod.get("status", {}).get("phase") != "Running"
        ):
            raise RuntimeError("Gateway instance changed or is not Ready")
        return [pod["metadata"]["uid"], digest(pod["spec"])] + [
            status.get(k) for k in ("imageID", "containerID", "restartCount")
        ]

    if instance(before) != instance(after):
        raise RuntimeError("Gateway instance changed during storage read")


def consumers(pods, claims):
    result = []
    for pod in pods:
        projected = project_pod(pod)
        volumes = {
            v["name"]: v for v in projected["volumes"] if v.get("claim") in claims
        }
        for container in projected["containers"]:
            for mount in container["mounts"]:
                if mount["name"] in volumes:
                    volume = volumes[mount["name"]]
                    result.append(
                        {
                            "pod": projected["pod"],
                            "node": projected["node"],
                            "phase": projected["phase"],
                            "deleting": projected["deleting"],
                            "container": container["name"],
                            "category": container["category"],
                            "claim": volume["claim"],
                            "read_only": bool(
                                volume["read_only"] or mount.get("readOnly", False)
                            ),
                        }
                    )
    return result


def data_mounts(pod, home):
    if not home or not PurePosixPath(home).is_absolute():
        return []
    projected = project_pod(pod)
    volumes = {v["name"]: v for v in projected["volumes"]}
    matches = []
    for container in projected["containers"]:
        if container["name"] != "gateway":
            continue
        for mount in container["mounts"]:
            path = PurePosixPath(mount["mountPath"])
            if path == PurePosixPath(home) or path in PurePosixPath(home).parents:
                matches.append(
                    dict(
                        mount,
                        **{
                            k: v
                            for k, v in volumes[mount["name"]].items()
                            if k != "name"
                        },
                    )
                )
    return sorted(matches, key=lambda x: len(x["mountPath"]), reverse=True)[:1]


def collect():
    deployment = kubectl(["get", "deployment", "gateway", "-o", "json"])
    pods = kubectl(["get", "pods", "-o", "json"])["items"]
    if len(pods) > 2000:
        raise RuntimeError("Pod inventory limit exceeded")
    # Select by Deployment selector, not by a guessed Pod name prefix.
    selector = deployment["spec"].get("selector", {}).get("matchLabels", {})
    if deployment["spec"].get("selector", {}).get("matchExpressions"):
        raise RuntimeError("Gateway selector requires explicit review")
    gateways = [
        p
        for p in pods
        if selector
        and all(
            p["metadata"].get("labels", {}).get(k) == v for k, v in selector.items()
        )
    ]
    if not 1 <= len(gateways) <= 16:
        raise RuntimeError("No stable bounded Gateway Pod set")
    runtime, mapped = [], []
    for pod in gateways:
        require_same_instance(pod, pod)
        report = kubectl(
            [
                "exec",
                pod["metadata"]["name"],
                "-c",
                "gateway",
                "--",
                "/app/backend/.venv/bin/python",
                "-B",
                "-c",
                READER,
            ]
        )
        require_same_instance(
            pod, kubectl(["get", "pod", pod["metadata"]["name"], "-o", "json"])
        )
        runtime.append({"pod": project_pod(pod), "storage": report})
        mapped.extend(data_mounts(pod, report.get("data_home")))
    pvcs = kubectl(["get", "pvc", "-o", "json"])["items"]
    if len(pvcs) > 64:
        raise RuntimeError("PVC inventory limit exceeded")
    claims, volumes = [], {}
    for pvc in pvcs:
        spec = pvc["spec"]
        claims.append(
            {
                "name": pvc["metadata"]["name"],
                "uid": pvc["metadata"]["uid"],
                "spec_digest": digest(spec),
                "volume": spec.get("volumeName"),
                "access_modes": spec.get("accessModes", []),
                "storage_class": spec.get("storageClassName"),
                "phase": pvc.get("status", {}).get("phase"),
            }
        )
        if spec.get("volumeName") and spec["volumeName"] not in volumes:
            volumes[spec["volumeName"]] = project_pv(
                kubectl(["get", "pv", spec["volumeName"], "-o", "json"])
            )
    after = kubectl(["get", "deployment", "gateway", "-o", "json"])
    if after["metadata"]["uid"] != deployment["metadata"]["uid"] or digest(
        after["spec"]
    ) != digest(deployment["spec"]):
        raise RuntimeError("Gateway Deployment changed during collection")
    return {
        "schema": 1,
        "audit": "employee-sharing-storage-topology-v1",
        "collected_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "gateway": runtime,
        "data_mounts": mapped,
        "pvcs": claims,
        "pvs": list(volumes.values()),
        "namespace_volume_consumers": consumers(pods, {x["name"] for x in claims}),
        "consistent_backup": False,
        "writers_quiesced": False,
        "external_writers": "UNKNOWN",
        "ready_for_rollout": False,
        "production_mutation": "NONE",
        "database_queried": False,
        "snapshot_scope": "planning_only_not_atomic_cluster_snapshot",
    }


def write_report(destination, report):
    destination = Path(destination).absolute()
    if any(
        p.is_symlink() or getattr(p, "is_junction", lambda: False)()
        for p in (destination,) + tuple(destination.parents)
    ):
        raise RuntimeError("Linked report destination refused")
    raw = (
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    if len(raw) > 16 * 1024 * 1024:
        raise RuntimeError("Report limit exceeded")
    fd = os.open(str(destination), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def main():
    global _deadline
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if (
        socket.gethostname().split(".")[0] != "datax-01"
        or getattr(os, "geteuid", lambda: -1)() != 0
    ):
        raise RuntimeError("Run manually as root on datax-01")
    if os.path.lexists(str(args.output)):
        raise RuntimeError("Existing evidence is never overwritten")
    _deadline = time.monotonic() + 180
    print(
        "employee_storage_topology=READING max_seconds=210 no_database_queries=yes",
        flush=True,
    )
    write_report(args.output, collect())
    print(
        "employee_storage_topology=COLLECTED production_workloads_mutated=no database_queried=no"
    )
    print("ready_for_rollout=false evidence=" + str(args.output))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(
            "employee_storage_topology=FAIL detail=read_or_private_evidence_write_failed"
        )
        raise SystemExit(1)
