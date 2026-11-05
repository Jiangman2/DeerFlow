"""Read current deployment metadata for source reconciliation; Python 3.7+.

No exec, logs, Secret reads, provider calls or cluster writes. ConfigMap values
are used only for content hashes and are never included in the output.
"""

import argparse
import collections
import datetime
import hashlib
import json
import re
import subprocess
import threading
import time


BASE = ["kubectl", "--kubeconfig=/etc/kubernetes/admin.conf", "--request-timeout=15s", "-n", "deer-flow"]
LIMIT = 24 * 1024 * 1024
DEADLINE_SECONDS = 300
EXACT_NAMES = {"gateway", "gateway-run-dispatcher", "frontend", "nginx", "ppt-master-runner", "provisioner"}
PREFIXES = ("weknora-", "mineru-", "deer-flow-redis", "gateway-redis")
SECRET_NAME = re.compile(r"PASSWORD|SECRET|TOKEN|API_KEY|CREDENTIAL|AUTHORIZATION", re.I)
CAPACITY_NAME = re.compile(r"CONCURRENCY|WORKERS|TIMEOUT|MAX_FILE_SIZE|MEMORY_LIMIT|CPU_LIMIT|EPHEMERAL_STORAGE_LIMIT")
NUMBER = re.compile(r"[0-9]+(?:\.[0-9]+)?(?:m|Ki|Mi|Gi|Ti|ms|s)?\Z")


class Failure(Exception):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def selected(name):
    return name in EXACT_NAMES or name.startswith(PREFIXES)


class Reader:
    def __init__(self):
        self.started = time.monotonic()
        self.queries = 0

    def get(self, resource, name=None):
        if resource not in {"deployments", "statefulsets", "daemonsets", "services", "ingresses", "pods", "configmap"}:
            raise Failure("RESOURCE_NOT_ALLOWED")
        remaining = DEADLINE_SECONDS - (time.monotonic() - self.started)
        if remaining < 1 or self.queries >= 150:
            raise Failure("QUERY_BUDGET")
        args = BASE + ["get", resource] + ([name] if name else []) + ["-o", "json"]
        self.queries += 1
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        chunks = [bytearray(), bytearray()]
        overflow = threading.Event()

        def read(pipe, index):
            while True:
                block = pipe.read(65536)
                if not block:
                    break
                if len(chunks[index]) + len(block) > LIMIT:
                    overflow.set()
                    process.kill()
                    break
                chunks[index].extend(block)

        threads = [threading.Thread(target=read, args=(process.stdout, 0)), threading.Thread(target=read, args=(process.stderr, 1))]
        for thread in threads:
            thread.start()
        try:
            process.wait(timeout=min(25, remaining))
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            raise Failure("QUERY_TIMEOUT")
        finally:
            for thread in threads:
                thread.join(timeout=5)
            process.stdout.close()
            process.stderr.close()
        if any(thread.is_alive() for thread in threads) or overflow.is_set():
            raise Failure("QUERY_OUTPUT_LIMIT")
        if process.returncode:
            raise Failure("QUERY_NONZERO")
        return json.loads(chunks[0])


def identity(obj):
    m = obj.get("metadata", {})
    return {k: m.get(k) for k in ("name", "uid", "generation", "resourceVersion")}


def config_refs(value):
    refs = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"configMap", "configMapRef", "configMapKeyRef"} and isinstance(item, dict) and item.get("name"):
                refs.add(item["name"])
            refs.update(config_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.update(config_refs(item))
    return refs


def container_projection(c):
    env = []
    for e in c.get("env", []):
        row = {"name": e.get("name")}
        if "valueFrom" in e:
            row["valueFrom"] = e["valueFrom"]
        elif CAPACITY_NAME.search(e.get("name", "")) and not SECRET_NAME.search(e.get("name", "")) and NUMBER.fullmatch(str(e.get("value", ""))):
            row["numeric_capacity_value"] = e["value"]
        else:
            row["literal_value"] = "WITHHELD"
        env.append(row)
    return {
        "name": c.get("name"), "image": c.get("image"), "imagePullPolicy": c.get("imagePullPolicy"),
        "resources": c.get("resources", {}), "env": env, "envFrom": c.get("envFrom", []),
        "volumeMounts": c.get("volumeMounts", []),
        "command_present": bool(c.get("command")), "args_present": bool(c.get("args")),
        "probe_kinds": {k: [v for v in ("httpGet", "tcpSocket", "grpc", "exec") if v in c[k]] for k in ("livenessProbe", "readinessProbe", "startupProbe") if k in c},
    }


def workload_projection(obj):
    spec = obj["spec"]
    pod = spec.get("template", {}).get("spec", {})
    volumes = []
    for v in pod.get("volumes", []):
        row = {"name": v.get("name")}
        for key in ("configMap", "secret", "persistentVolumeClaim", "emptyDir", "projected"):
            if key in v:
                row[key] = v[key]
        if "hostPath" in v or "nfs" in v or "csi" in v:
            row["other_volume_types"] = [key for key in ("hostPath", "nfs", "csi") if key in v]
        volumes.append(row)
    return {
        "kind": obj["kind"], "identity": identity(obj), "spec_sha256": digest(spec),
        "replicas": spec.get("replicas"), "strategy": spec.get("strategy", spec.get("updateStrategy")),
        "status": {k: obj.get("status", {}).get(k) for k in ("observedGeneration", "replicas", "updatedReplicas", "readyReplicas", "availableReplicas", "unavailableReplicas", "currentRevision", "updateRevision")},
        "containers": [container_projection(c) for c in pod.get("containers", [])],
        "initContainers": [container_projection(c) for c in pod.get("initContainers", [])],
        "volumes": volumes, "nodeSelector": pod.get("nodeSelector", {}), "affinity": pod.get("affinity", {}),
        "tolerations": pod.get("tolerations", []), "serviceAccountName": pod.get("serviceAccountName"),
        "terminationGracePeriodSeconds": pod.get("terminationGracePeriodSeconds"),
    }


def read_workloads(reader):
    rows = []
    for kind in ("deployments", "statefulsets", "daemonsets"):
        rows.extend(o for o in reader.get(kind).get("items", []) if selected(o.get("metadata", {}).get("name", "")))
    return rows


def binding(objects):
    return {(o["kind"], o["metadata"]["name"]): (o["metadata"]["uid"], o["metadata"].get("generation"), digest(o["spec"])) for o in objects}


def collect(reader):
    before = read_workloads(reader)
    if not before:
        raise Failure("NO_MATCHED_WORKLOADS")
    references = set().union(*(config_refs(o["spec"]) for o in before))
    if len(references) > 60:
        raise Failure("CONFIGMAP_COUNT_LIMIT")
    configs = []
    for name in sorted(references):
        obj = reader.get("configmap", name)
        configs.append({"identity": identity(obj), "immutable": obj.get("immutable", False), "content_sha256": digest({k: obj.get(k, {}) for k in ("data", "binaryData")}),
                        "keys": sorted(set(obj.get("data", {})) | set(obj.get("binaryData", {})))})
    services = []
    for obj in reader.get("services").get("items", []):
        if selected(obj["metadata"]["name"]):
            s = obj["spec"]
            services.append({"identity": identity(obj), "spec_sha256": digest(s), "type": s.get("type"), "selector": s.get("selector"), "ports": s.get("ports"), "sessionAffinity": s.get("sessionAffinity")})
    ingresses = [{"identity": identity(o), "spec_sha256": digest(o["spec"]), "ingressClassName": o["spec"].get("ingressClassName"), "hosts": [r.get("host") for r in o["spec"].get("rules", [])]}
                 for o in reader.get("ingresses").get("items", [])]
    selectors = [o["spec"].get("selector", {}).get("matchLabels", {}) for o in before]
    counts = collections.Counter()
    for obj in reader.get("pods").get("items", []):
        labels = obj.get("metadata", {}).get("labels", {})
        if not any(s and all(labels.get(k) == v for k, v in s.items()) for s in selectors):
            continue
        for c in obj.get("status", {}).get("containerStatuses", []):
            key = (obj.get("spec", {}).get("nodeName"), c.get("name"), c.get("imageID"), c.get("ready", False), bool(obj["metadata"].get("deletionTimestamp")))
            counts[key] += 1
    image_counts = [{"node": k[0], "container": k[1], "imageID": k[2], "ready": k[3], "terminating": k[4], "count": v} for k, v in sorted(counts.items(), key=lambda row: repr(row[0]))]
    changed_configs = []
    for saved in configs:
        obj = reader.get("configmap", saved["identity"]["name"])
        current = digest({k: obj.get(k, {}) for k in ("data", "binaryData")})
        if identity(obj)["uid"] != saved["identity"]["uid"] or current != saved["content_sha256"]:
            changed_configs.append(saved["identity"]["name"])
    after = read_workloads(reader)
    stable = binding(before) == binding(after) and not changed_configs
    return {"status": "CURRENT_METADATA_COLLECTED" if stable else "CHANGED_DURING_COLLECTION",
            "workload_specs_stable": binding(before) == binding(after), "changed_configmaps": changed_configs,
            "workloads": [workload_projection(o) for o in before], "configmaps": configs,
            "services": services, "ingresses": ingresses, "runtime_image_counts": image_counts,
            "release_authorized": False, "full_source_or_effective_database_configuration_collected": False,
            "scope": "CURRENT_KUBERNETES_METADATA_AND_CONFIG_HASHES_NOT_ATOMIC_CLUSTER_SNAPSHOT"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect-current-readonly", action="store_true", required=True)
    parser.parse_args()
    reader = Reader()
    result = {"schema": "portal-current-deployment-archive.v1", "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "workload_modified": False, "configuration_modified": False, "credentials_exported": False,
              "secret_data_read": False, "pod_exec_requested": False, "provider_requests": 0}
    code = 0
    try:
        result.update(collect(reader))
        if result["status"] != "CURRENT_METADATA_COLLECTED":
            code = 1
    except Exception as error:
        code = 1
        result.update(status="INCOMPLETE", error_class=type(error).__name__, error_code=str(error) if isinstance(error, Failure) else "READ_FAILED")
    result.update(queries=reader.queries, elapsed_seconds=round(time.monotonic() - reader.started, 3), exitcode=code)
    print(json.dumps(result, sort_keys=True, ensure_ascii=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
