"""Kubelet-scoped volume projections for a single assignment.

The consumer owns the two emptyDirs. A separate worker on the same node
receives only that Pod's two staging directories and the selected PVC subPaths.
"""
from copy import deepcopy
import re
from uuid import UUID


DATA = "df-warm-data"
SKILLS = "df-warm-skills"
ROOTS = ("workspace", "uploads", "outputs")
USER_CLAIM = "deer-flow-userdata-rwx-pvc"
SKILL_CLAIM = "deer-flow-skills-rwx-pvc"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def component(value):
    require(isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_-]+", value),
            "Invalid identity component")
    return value


def slot(template, name, namespace):
    pod = deepcopy(template)
    pod.update(apiVersion="v1", kind="Pod")
    labels = dict(template.get("metadata", {}).get("labels", {}))
    labels.pop("sandbox-id", None)
    labels.update({"df-warm-role": "slot", "df-warm-state": "standby"})
    pod["metadata"] = {"name": name, "namespace": namespace, "labels": labels}
    spec = pod["spec"]
    require(spec.get("automountServiceAccountToken") is False, "Unexpected Kubernetes token")
    require(not any(spec.get(k) for k in ("hostPID", "hostIPC", "hostNetwork", "shareProcessNamespace")),
            "Unexpected shared namespaces")
    require({c["name"] for c in spec["containers"]} == {"ppt-master-agent-host", "sandbox"},
            "Unexpected consumer set")
    require({c["name"] for c in spec["initContainers"]} == {"prepare-user-data", "verify-skill-mounts"},
            "Unexpected cold init containers")
    for container in spec["containers"]:
        mounts = container["volumeMounts"]
        by_path = {m["mountPath"]: m for m in mounts}
        require(by_path["/mnt/user-data"]["name"] == by_path["/mnt/skills/custom"]["name"] == "user-data",
                "Unexpected production data layout")
        require(by_path["/mnt/skills/public"].get("readOnly"), "Public skills must be read-only")
        container["volumeMounts"] = [m for m in mounts if m["mountPath"] not in {
            "/mnt/user-data", "/mnt/skills/custom", "/mnt/skills/legacy"}]
        container["volumeMounts"] += [
            {"name": DATA, "mountPath": "/mnt/user-data", "mountPropagation": "HostToContainer"},
            {"name": SKILLS, "mountPath": "/mnt/skills", "mountPropagation": "HostToContainer",
             "readOnly": True}]
    init = next(c for c in spec["initContainers"] if c["name"] == "prepare-user-data")
    init["command"] = ["python3", "-c",
                       "from pathlib import Path\n"
                       "for path in ('/stage-data/workspace', '/stage-data/uploads', "
                       "'/stage-data/outputs', '/stage-skills/public'):\n"
                       "    Path(path).mkdir(mode=0o755, exist_ok=True)"]
    init.pop("args", None)
    init["volumeMounts"] = [{"name": DATA, "mountPath": "/stage-data"},
                            {"name": SKILLS, "mountPath": "/stage-skills"}]
    verifier = next(c for c in spec["initContainers"] if c["name"] == "verify-skill-mounts")
    verifier["volumeMounts"] = [m for m in verifier["volumeMounts"] if m["mountPath"] == "/mnt/skills/public"]
    verifier["command"] = ["python3", "-c", "import os; raise SystemExit(not os.path.isdir('/mnt/skills/public'))"]
    verifier.pop("args", None)
    used = {m["name"] for c in spec["containers"] + spec["initContainers"] for m in c["volumeMounts"]}
    spec["volumes"] = [v for v in spec["volumes"] if v["name"] in used]
    require(all("hostPath" not in v and v.get("persistentVolumeClaim", {}).get("claimName") != USER_CLAIM
                for v in spec["volumes"]), "Idle slot would see user storage")
    spec["volumes"] += [{"name": DATA, "emptyDir": {}}, {"name": SKILLS, "emptyDir": {}}]
    spec["enableServiceLinks"] = False
    return pod


def worker(pod, user, thread, legacy, image, program, *, kubelet_root="/var/lib/kubelet",
           legacy_subpath="custom"):
    user, thread = component(user), component(thread)
    meta = pod["metadata"]
    uid = str(UUID(meta["uid"]))
    require(meta["uid"] == uid and not meta.get("deletionTimestamp"), "Invalid target generation")
    require(meta["labels"].get("df-warm-role") == "slot", "Unexpected target")
    require(kubelet_root.startswith("/") and ".." not in kubelet_root.split("/"), "Invalid kubelet root")
    local = f"{kubelet_root}/pods/{uid}/volumes/kubernetes.io~empty-dir"
    volumes = [{"name": name, "hostPath": {"path": f"{local}/{name}", "type": "Directory"}}
               for name in (DATA, SKILLS)]
    volumes.append({"name": "selected", "persistentVolumeClaim": {"claimName": USER_CLAIM}})
    mounts = [{"name": DATA, "mountPath": "/stage-data", "mountPropagation": "Bidirectional"},
              {"name": SKILLS, "mountPath": "/stage-skills", "mountPropagation": "Bidirectional"}]
    base = "deer-flow/users/" + user
    for root in ROOTS:
        mounts.append({"name": "selected", "mountPath": "/assigned/" + root,
                       "subPath": f"{base}/threads/{thread}/user-data/{root}"})
    mounts.append({"name": "selected", "mountPath": "/assigned/custom",
                   "subPath": base + "/skills/custom", "readOnly": True})
    if legacy:
        require(legacy_subpath and all(component(c) for c in legacy_subpath.split("/")), "Invalid legacy path")
        volumes.append({"name": "legacy", "persistentVolumeClaim": {"claimName": SKILL_CLAIM, "readOnly": True}})
        mounts.append({"name": "legacy", "mountPath": "/assigned/legacy", "subPath": legacy_subpath,
                       "readOnly": True})
    return {"apiVersion": "v1", "kind": "Pod", "metadata": {
        "namespace": meta["namespace"], "name": "df-mount-" + uid,
        "labels": {"df-warm-role": "mount-worker"},
        "ownerReferences": [{"apiVersion": "v1", "kind": "Pod", "name": meta["name"], "uid": uid}]},
        "spec": {"affinity": {"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {
                     "nodeSelectorTerms": [{"matchFields": [{"key": "metadata.name", "operator": "In",
                                                            "values": [pod["spec"]["nodeName"]]}]}]}}},
                 "tolerations": deepcopy(pod["spec"].get("tolerations", [])),
                 "automountServiceAccountToken": False, "enableServiceLinks": False,
                 "restartPolicy": "Never", "volumes": volumes,
                 "containers": [{"name": "mount-worker", "image": image, "imagePullPolicy": "IfNotPresent",
                                 "command": ["python3", "-I", "-B", "-u", "-c", program],
                                 "securityContext": {"privileged": True, "runAsUser": 0},
                                 "readinessProbe": {"exec": {"command": ["test", "-f", "/tmp/df-assignment-bound"]},
                                                    "periodSeconds": 1, "timeoutSeconds": 1},
                                 "volumeMounts": mounts,
                                 "resources": {"requests": {"cpu": "10m", "memory": "32Mi"},
                                               "limits": {"cpu": "250m", "memory": "128Mi"}}}]}}
