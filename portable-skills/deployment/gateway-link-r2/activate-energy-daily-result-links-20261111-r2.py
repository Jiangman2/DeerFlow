"""One exact Gateway 413 -> 414 source overlay; operator idle-window execution.

The six application files are sealed assets. This tool never closes admission:
its read-only idle observations do not lock out a new task after the last check.
Gateway shutdown interrupts in-flight work. Use an agreed idle window, and do
not treat readiness or the unchanged rolling strategy as task-drain proof.
Python 3.7-compatible host. Empty assets intentionally make preparation inert.
"""

import argparse
import base64
import copy
import datetime
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import types
import uuid
from pathlib import Path, PurePosixPath

ASSETS = {}
SOURCE_INSPECTOR_SHA = (
    "c6cfbeabfd8e21c9513742261df85c7a5dede9d7cb87889f8ea7a4233ec3cd7d"
)
FILE_KEYS = {
    "app/channels/manager.py": "manager.py",
    "app/integrations/work/notifications.py": "notifications.py",
    "app/scheduler/work_notification_delivery.py": "work_notification_delivery.py",
    "app/scheduler/work_notifications.py": "work_notifications.py",
    "packages/harness/deerflow/tools/builtins/present_file_tool.py": "present_file_tool.py",
    "packages/harness/deerflow/utils/conversation_url.py": "conversation_url.py",
}
EXPECTED = {
    "app/channels/manager.py": (
        94358,
        "1a782faf4e4cdbd491871972cca3e501d7a3011da3869fac398f994bedef56f1",
    ),
    "app/integrations/work/notifications.py": (
        10025,
        "2f25958bcb2556685d153bef55f72afebd360deb157cd13164ceb4261001f039",
    ),
    "app/scheduler/work_notification_delivery.py": (
        4297,
        "d446868a681754fa98241b62ebfe45a886c6bf21b0da0f68d0d69cd111e8a42e",
    ),
    "app/scheduler/work_notifications.py": (
        14201,
        "d3f7e6c4acc4fd0cb7e4f6b31861ddf8b68ff1d2f44761a99da90536a6982645",
    ),
    "packages/harness/deerflow/tools/builtins/present_file_tool.py": (
        19640,
        "eccfe1e5975c771465abf96f1da60fdb2a11e0712716aeb2453a2d10fbe30e3b",
    ),
    "packages/harness/deerflow/utils/conversation_url.py": (
        4466,
        "ef20ca84241617a489859fca4f660b07ba5923616828e0fad654b6c59d2f4abc",
    ),
}
ASSET_NAMES = frozenset(
    ("source_inspector", "qualifier", "task_origin_guard", "skill_reader")
    + tuple(FILE_KEYS.values())
)
NS = "deer-flow"
DEPLOYMENT = "gateway"
DEPLOYMENT_UID = "a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1"
BEFORE_GENERATION = 413
BEFORE_SPEC_SHA = "696ffb5c27862a8e2e4f1d43c2482de79a3df991329524b214894f0bd29711ba"
IMAGE = "deerflow.local/deer-flow-gateway@sha256:c0fd1a6e981d551ab65459f8125b0e847ac7276433e020d1b65f88dea888ae29"
EXPECTED_ORIGIN = "https://cnoocclaw.cnooc.com.cn"
VOLUME = "energy-result-link-20261111-r2"
OPENSSL_CM = "deer-flow-gateway-openssl-compat"
OPENSSL_PATH = "/etc/deerflow-openssl"
OPENSSL_SHA = "20130c1b277e0a5fb79a27af7dc35be38162a6e086aa91e45b4f249d73c61604"
CONTROL = Path("/var/lib/portal-energy-result-link-20261111-r2")
LABEL = "portal.energy-result-link/operation"
TOTAL_SECONDS = 6300
ROLLOUT_SECONDS = 5400
KUBE = [
    "kubectl",
    "--kubeconfig=/etc/kubernetes/admin.conf",
    "--request-timeout=20s",
    "-n",
    NS,
]
SAFE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


class Refused(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise Refused(code)


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def sha(value):
    return hashlib.sha256(value).hexdigest()


def safe(path):
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts, "PRIVATE_PATH")
    for part in (path,) + tuple(path.parents):
        if not part.exists() and not part.is_symlink():
            continue
        state = part.lstat()
        require(
            not stat.S_ISLNK(state.st_mode)
            and not getattr(state, "st_file_attributes", 0) & 0x400,
            "PRIVATE_PATH_LINK",
        )
    return path


def private_write(path, raw):
    path = safe(path)
    fd = os.open(
        str(path),
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)


def private_read(path):
    path = safe(path)
    fd = os.open(
        str(path),
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
    )
    try:
        before = os.fstat(fd)
        require(
            stat.S_ISREG(before.st_mode)
            and before.st_nlink == 1
            and before.st_size <= 4194304,
            "PRIVATE_FILE_BOUND",
        )
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(4194305)
        after = os.fstat(fd)

        def stamp(s):
            return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)

        require(
            len(raw) == before.st_size
            and stamp(before) == stamp(after) == stamp(path.stat()),
            "PRIVATE_FILE_CHANGED",
        )
        return raw
    finally:
        os.close(fd)


def load_assets():
    require(set(ASSETS) == ASSET_NAMES, "RELEASE_ASSETS_NOT_SEALED")
    assets = {}
    for name in sorted(ASSET_NAMES):
        item = ASSETS[name]
        require(
            isinstance(item, dict)
            and set(item) == {"base64", "sha256"}
            and isinstance(item["base64"], str)
            and len(item["base64"]) <= 1048576,
            "ASSET_SHAPE",
        )
        raw = base64.b64decode(item["base64"], validate=True)
        require(0 < len(raw) <= 524288 and sha(raw) == item["sha256"], "ASSET_HASH")
        # Application/qualification Python syntax belongs to the production
        # Python 3.12 interpreter, not the Python 3.7 operator host.
        assets[name] = raw
    require(
        sha(assets["source_inspector"]) == SOURCE_INSPECTOR_SHA,
        "SOURCE_INSPECTOR_CHANGED",
    )
    for name, key in FILE_KEYS.items():
        require(
            (len(assets[key]), sha(assets[key])) == EXPECTED[name],
            "CANDIDATE_SOURCE_CHANGED",
        )
    module = types.ModuleType("sealed_gateway_source_inspector")
    exec(
        compile(assets["source_inspector"], "<sealed-source-inspector>", "exec"),
        module.__dict__,
    )
    assets["pre"] = module
    return assets


def strict_identity(obj):
    meta = obj["metadata"]
    return {
        "uid": meta["uid"],
        "generation": meta.get("generation"),
        "spec_sha256": sha(canonical(obj["spec"])),
    }


def candidate_spec(deployment, configmap):
    require(
        strict_identity(deployment)
        == {
            "uid": DEPLOYMENT_UID,
            "generation": BEFORE_GENERATION,
            "spec_sha256": BEFORE_SPEC_SHA,
        },
        "GATEWAY_BASELINE_CHANGED",
    )
    spec = copy.deepcopy(deployment["spec"])
    pod = spec["template"]["spec"]
    containers = pod.get("containers", [])
    require(
        len(containers) == 1
        and containers[0].get("name") == "gateway"
        and containers[0].get("image") == IMAGE,
        "GATEWAY_CONTAINER_CHANGED",
    )
    require(
        all(v.get("name") != VOLUME for v in pod.get("volumes", [])),
        "OVERLAY_VOLUME_EXISTS",
    )
    mounts = containers[0].get("volumeMounts", [])
    for name in FILE_KEYS:
        target = PurePosixPath("/app/backend") / name
        for mount in mounts:
            base = PurePosixPath(mount["mountPath"])
            require(
                base.is_absolute() and ".." not in base.parts, "EXISTING_MOUNT_SHAPE"
            )
            require(
                base != target
                and base not in target.parents
                and target not in base.parents,
                "SOURCE_ALREADY_MOUNTED",
            )
    pod.setdefault("volumes", []).append(
        {"name": VOLUME, "configMap": {"name": configmap, "defaultMode": 292}}
    )
    containers[0].setdefault("volumeMounts", []).extend(
        {
            "name": VOLUME,
            "mountPath": "/app/backend/" + name,
            "subPath": key,
            "readOnly": True,
        }
        for name, key in FILE_KEYS.items()
    )
    # Removing only the appended mounts and volume must exactly reproduce input.
    check = copy.deepcopy(spec)
    check["template"]["spec"]["volumes"].pop()
    del check["template"]["spec"]["containers"][0]["volumeMounts"][-len(FILE_KEYS) :]
    if "volumes" not in deployment["spec"]["template"]["spec"]:
        check["template"]["spec"].pop("volumes")
    if "volumeMounts" not in deployment["spec"]["template"]["spec"]["containers"][0]:
        check["template"]["spec"]["containers"][0].pop("volumeMounts")
    require(check == deployment["spec"], "PATCH_SCOPE_EXCEEDED")
    return spec


def cas_patch(deployment, expected):
    meta = deployment["metadata"]
    require(
        isinstance(meta.get("resourceVersion"), str) and meta["resourceVersion"],
        "RESOURCE_VERSION_REQUIRED",
    )
    return [
        {"op": "test", "path": "/metadata/uid", "value": DEPLOYMENT_UID},
        {"op": "test", "path": "/metadata/generation", "value": BEFORE_GENERATION},
        {
            "op": "test",
            "path": "/metadata/resourceVersion",
            "value": meta["resourceVersion"],
        },
        {"op": "test", "path": "/spec", "value": deployment["spec"]},
        {"op": "replace", "path": "/spec", "value": expected},
    ]


def configmap_manifest(operation, name, assets):
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": name, "namespace": NS, "labels": {LABEL: operation}},
        "immutable": True,
        "data": {key: assets[key].decode("utf-8") for key in FILE_KEYS.values()},
    }


def maintenance_manifest(operation, name, node, configmap):
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "namespace": NS, "labels": {LABEL: operation}},
        "spec": {
            "nodeName": node,
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "enableServiceLinks": False,
            "activeDeadlineSeconds": 900,
            "terminationGracePeriodSeconds": 5,
            "securityContext": {
                "runAsUser": 0,
                "runAsGroup": 0,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "maintenance",
                    "image": IMAGE,
                    "imagePullPolicy": "Never",
                    "command": [
                        "python3",
                        "-I",
                        "-B",
                        "-c",
                        "import time; time.sleep(850)",
                    ],
                    "env": [
                        {"name": "HOME", "value": "/tmp"},
                        {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"},
                        {
                            "name": "OPENSSL_CONF",
                            "value": OPENSSL_PATH + "/openssl.cnf",
                        },
                    ],
                    "resources": {
                        "requests": {"cpu": "100m", "memory": "128Mi"},
                        "limits": {"cpu": "1", "memory": "1Gi"},
                    },
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "volumeMounts": [
                        {"name": "tmp", "mountPath": "/tmp"},
                        {
                            "name": "openssl-compat",
                            "mountPath": OPENSSL_PATH,
                            "readOnly": True,
                        },
                    ]
                    + [
                        {
                            "name": VOLUME,
                            "mountPath": "/app/backend/" + path,
                            "subPath": key,
                            "readOnly": True,
                        }
                        for path, key in FILE_KEYS.items()
                    ],
                }
            ],
            "volumes": [
                {"name": "tmp", "emptyDir": {"sizeLimit": "256Mi"}},
                {
                    "name": "openssl-compat",
                    "configMap": {
                        "name": OPENSSL_CM,
                        "defaultMode": 292,
                        "items": [{"key": "openssl.cnf", "path": "openssl.cnf"}],
                    },
                },
                {"name": VOLUME, "configMap": {"name": configmap, "defaultMode": 292}},
            ],
        },
    }


def openssl_identity(deployment, configmap):
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]
    require(
        [v for v in container.get("env", []) if v.get("name") == "OPENSSL_CONF"]
        == [{"name": "OPENSSL_CONF", "value": OPENSSL_PATH + "/openssl.cnf"}],
        "OPENSSL_ENV_NOT_FIXED",
    )
    mounts = [
        v
        for v in container.get("volumeMounts", [])
        if v.get("mountPath") == OPENSSL_PATH
    ]
    require(
        len(mounts) == 1
        and mounts[0].get("readOnly") is True
        and not mounts[0].get("subPath")
        and not mounts[0].get("subPathExpr"),
        "OPENSSL_MOUNT_NOT_FIXED",
    )
    volumes = [v for v in pod.get("volumes", []) if v.get("name") == mounts[0]["name"]]
    require(
        len(volumes) == 1
        and set(volumes[0]) == {"name", "configMap"}
        and volumes[0]["configMap"].get("name") == OPENSSL_CM,
        "OPENSSL_VOLUME_NOT_FIXED",
    )
    items = volumes[0]["configMap"].get("items")
    require(
        items is None or items == [{"key": "openssl.cnf", "path": "openssl.cnf"}],
        "OPENSSL_PROJECTION_NOT_FIXED",
    )
    require(
        configmap["metadata"].get("name") == OPENSSL_CM
        and not configmap["metadata"].get("deletionTimestamp")
        and set(configmap.get("data", {})) == {"openssl.cnf"}
        and not configmap.get("binaryData"),
        "OPENSSL_CONFIGMAP_SHAPE",
    )
    raw = configmap["data"]["openssl.cnf"].encode("utf-8")
    require(len(raw) == 189 and sha(raw) == OPENSSL_SHA, "OPENSSL_CONFIGMAP_HASH")
    return {
        "name": OPENSSL_CM,
        "uid": configmap["metadata"]["uid"],
        "bytes": 189,
        "sha256": OPENSSL_SHA,
    }


def accepted_idle(result):
    require(
        isinstance(result, dict)
        and result.get("status") == "GATEWAY_IDLE_ORIGIN_OBSERVED"
        and type(result.get("idle")) is bool
        and result.get("origin") == EXPECTED_ORIGIN
        and result.get("read_only") is True
        and result.get("transaction_readonly_verified") is True
        and result.get("provider_requests") == 0
        and result.get("work_messages_sent") == 0,
        "IDLE_ORIGIN_CHECK_FAILED",
    )
    for name in (
        "active_runs",
        "active_oa",
        "active_callbacks",
        "scheduled_running",
        "active_work_notifications",
    ):
        require(
            type(result.get(name)) is int and result[name] == 0, "ACTIVE_TASKS_PRESENT"
        )
    require(result["idle"] is True, "IDLE_RESULT_INCONSISTENT")
    return {
        name: result[name]
        for name in (
            "status",
            "idle",
            "origin",
            "read_only",
            "transaction_readonly_verified",
            "active_runs",
            "active_oa",
            "active_callbacks",
            "scheduled_running",
            "active_work_notifications",
        )
    }


def candidate_guard(deployment, expected, converged=False):
    require(
        deployment["metadata"].get("uid") == DEPLOYMENT_UID
        and not deployment["metadata"].get("deletionTimestamp")
        and deployment["metadata"].get("generation") == BEFORE_GENERATION + 1
        and deployment["spec"] == expected,
        "POST_PATCH_SPEC_DRIFT",
    )
    if converged:
        status = deployment.get("status", {})
        require(
            status.get("observedGeneration") == BEFORE_GENERATION + 1
            and all(
                status.get(k, 0) == 70
                for k in (
                    "replicas",
                    "updatedReplicas",
                    "readyReplicas",
                    "availableReplicas",
                )
            )
            and status.get("terminatingReplicas", 0) == 0,
            "GATEWAY_NOT_CONVERGED",
        )


def maintenance_guard(pod, requested, uid=None, image_id=None):
    require(
        pod["metadata"].get("name") == requested["metadata"]["name"]
        and pod["metadata"].get("labels", {}).get(LABEL)
        == requested["metadata"]["labels"][LABEL]
        and not pod["metadata"].get("ownerReferences")
        and not pod["metadata"].get("deletionTimestamp"),
        "MAINTENANCE_IDENTITY",
    )
    if uid is not None:
        require(pod["metadata"].get("uid") == uid, "MAINTENANCE_UID_CHANGED")
    require(
        not pod["spec"].get("initContainers")
        and not pod["spec"].get("ephemeralContainers")
        and all(
            pod["spec"].get(key, False) is False
            for key in ("hostNetwork", "hostPID", "hostIPC", "shareProcessNamespace")
        ),
        "MAINTENANCE_UNEXPECTED_PRIVILEGE",
    )
    for key, value in requested["spec"].items():
        if key != "containers":
            require(pod["spec"].get(key) == value, "MAINTENANCE_SPEC_CHANGED")
    require(len(pod["spec"].get("containers", [])) == 1, "MAINTENANCE_CONTAINERS")
    actual = pod["spec"]["containers"][0]
    require(
        not actual.get("envFrom")
        and not actual.get("lifecycle")
        and not actual.get("volumeDevices")
        and not actual.get("ports")
        and all(
            not actual.get(key)
            for key in ("livenessProbe", "readinessProbe", "startupProbe")
        ),
        "MAINTENANCE_UNEXPECTED_CONTAINER_ACCESS",
    )
    for key, value in requested["spec"]["containers"][0].items():
        require(actual.get(key) == value, "MAINTENANCE_CONTAINER_CHANGED")
    states = pod.get("status", {}).get("containerStatuses", [])
    ready = (
        pod.get("status", {}).get("phase") == "Running"
        and len(states) == 1
        and states[0].get("ready") is True
    )
    if ready:
        require(
            states[0].get("restartCount") == 0 and states[0].get("imageID") == image_id,
            "MAINTENANCE_RUNTIME_IMAGE",
        )
    return ready


def endpoint_pods(slices, service_uid):
    require(
        isinstance(slices, dict)
        and isinstance(slices.get("items"), list)
        and 0 < len(slices["items"]) <= 128,
        "ENDPOINT_SHAPE",
    )
    found = set()
    for item in slices["items"]:
        refs = item["metadata"].get("ownerReferences", [])
        require(
            any(
                r.get("kind") == "Service"
                and r.get("uid") == service_uid
                and r.get("name") == "gateway"
                for r in refs
            ),
            "ENDPOINT_SERVICE_OWNER",
        )
        for endpoint in item.get("endpoints", []):
            conditions = endpoint.get("conditions", {})
            require(
                conditions.get("ready") is True
                and conditions.get("terminating", False) is False,
                "ENDPOINT_NOT_READY",
            )
            target = endpoint.get("targetRef", {})
            require(
                target.get("kind") == "Pod"
                and target.get("namespace", NS) == NS
                and isinstance(target.get("name"), str)
                and isinstance(target.get("uid"), str),
                "ENDPOINT_TARGET",
            )
            found.add((target["name"], target["uid"]))
    require(len(found) == 70, "ENDPOINT_POD_COUNT")
    return found


class Updater:
    def __init__(self, assets):
        self.assets, self.pre = assets, assets["pre"]
        self.started, self.commands = time.monotonic(), 0
        self.operation = uuid.uuid4().hex
        self.cm_name = "gateway-energy-links-" + self.operation[:12]
        self.maint_name = "gateway-links-qual-" + self.operation[:12]
        self.maint_uid = self.cm_uid = None
        self.known_pods, self.known_rs, self.known_nodes = set(), set(), set()
        self.expected = self.before = self.service = self.sample = None
        self.report = {
            "schema": "energy-daily-gateway-result-link-activation.v2",
            "tool_revision": "r2",
            "operation": self.operation,
            "status": "INCOMPLETE",
            "once_claimed": False,
            "patch_request_count": 0,
            "patch_may_have_applied": False,
            "automatic_retry": False,
            "automatic_rollback": False,
            "credentials_exported": False,
            "secret_api_reads": 0,
            "database_mutations": 0,
            "provider_requests": 0,
            "work_messages_sent": 0,
            "new_task_admission_locked": False,
            "observed_after_gate_new_task_race": True,
            "active_task_safe_shutdown_proven": False,
            "business_acceptance_pending": True,
            "existing_strategy_preserved": True,
            "other_deployments_modified": False,
            "normal_application_startup_database_and_redis_access_may_occur": True,
            "existing_work_channel_connections_may_reconnect": True,
            "maintenance_create_requested": False,
            "maintenance_delete_requested": False,
            "configmap_create_requested": False,
            "raw_errors_exported": False,
        }

    def journal(self, phase):
        self.report["last_step"] = phase
        if self.report["once_claimed"]:
            private_write(
                CONTROL
                / ("JOURNAL-%04d.json" % len(list(CONTROL.glob("JOURNAL-*.json")))),
                canonical(self.report),
            )

    def command(
        self, args, input_bytes=None, timeout=30, limit=4194304, allow_exit_one=False
    ):
        require(
            isinstance(args, list) and args and all(isinstance(s, str) for s in args),
            "COMMAND_SHAPE",
        )
        allowed = False
        if args[0] == "get":
            fixed = [
                self.pre.LIST_ARGS,
                ["get", "deployment", "gateway", "-o", "json"],
                ["get", "service", "gateway", "-o", "json"],
                ["get", "configmap", self.cm_name, "-o", "json"],
                ["get", "configmap", OPENSSL_CM, "-o", "json"],
                ["get", "pod", self.maint_name, "-o", "json"],
                ["get", "pod", self.maint_name, "-o", "json", "--ignore-not-found"],
                [
                    "get",
                    "endpointslices",
                    "-l",
                    "kubernetes.io/service-name=gateway",
                    "-o",
                    "json",
                ],
            ]
            fixed += [["get", "pod", name, "-o", "json"] for name in self.known_pods]
            fixed += [
                ["get", "replicaset", name, "-o", "json"] for name in self.known_rs
            ]
            fixed += [["get", "node", name, "-o", "json"] for name in self.known_nodes]
            allowed = args in fixed
        if args[0] == "exec":
            allowed = (
                len(args) >= 11
                and args[1] in self.known_pods | {self.maint_name}
                and args[2] == "-c"
                and args[3]
                == ("maintenance" if args[1] == self.maint_name else "gateway")
                and args[4] == "--"
                and args[5] in ("python3", "/app/backend/.venv/bin/python")
                and args[6:10] == ["-I", "-B", "-u", "-c"]
            )
        if args[0] == "create":
            require(input_bytes is not None, "CREATE_INPUT_REQUIRED")
            obj = json.loads(input_bytes)
            allowed = (
                args == ["create", "-f", "-", "-o", "json"]
                and obj.get("kind") in ("ConfigMap", "Pod")
                and obj.get("metadata", {}).get("name")
                in (self.cm_name, self.maint_name)
                and obj["metadata"].get("namespace") == NS
                and obj["metadata"].get("labels", {}).get(LABEL) == self.operation
            )
            expected = (
                configmap_manifest(self.operation, self.cm_name, self.assets)
                if obj.get("kind") == "ConfigMap"
                else maintenance_manifest(
                    self.operation,
                    self.maint_name,
                    self.sample["spec"]["nodeName"],
                    self.cm_name,
                )
            )
            require(obj == expected, "CREATE_MANIFEST_SCOPE")
        if args[0] == "patch":
            allowed = args == [
                "patch",
                "deployment",
                "gateway",
                "--type=json",
                "--patch-file",
                str(CONTROL / "PATCH.json"),
                "-o",
                "json",
            ]
            require(
                allowed
                and input_bytes is None
                and private_read(CONTROL / "PATCH.json")
                == canonical(cas_patch(self.before, self.expected)),
                "PATCH_BODY_SCOPE",
            )
        if args[0] == "delete":
            obj = json.loads(input_bytes or b"{}")
            allowed = args == [
                "delete",
                "--raw",
                "/api/v1/namespaces/deer-flow/pods/" + self.maint_name,
                "-f",
                "-",
            ] and obj == {
                "apiVersion": "v1",
                "kind": "DeleteOptions",
                "preconditions": {"uid": self.maint_uid},
                "gracePeriodSeconds": 5,
            }
        require(allowed, "COMMAND_SCOPE")
        self.commands += 1
        remaining = TOTAL_SECONDS - (time.monotonic() - self.started)
        require(self.commands <= 2400 and remaining > 0, "HOST_BUDGET")
        timeout = min(timeout, remaining)
        prefix = list(KUBE)
        prefix[2] = "--request-timeout=%ds" % max(20, min(180, int(timeout) - 2))
        with (
            tempfile.TemporaryFile() as source,
            tempfile.TemporaryFile() as output,
            tempfile.TemporaryFile() as errors,
        ):
            if input_bytes is not None:
                require(len(input_bytes) <= 4194304, "INPUT_BOUND")
                source.write(input_bytes)
                source.seek(0)
            process = subprocess.Popen(
                prefix + args,
                stdin=source if input_bytes is not None else subprocess.DEVNULL,
                stdout=output,
                stderr=errors,
                env={"PATH": SAFE_PATH, "HOME": "/root", "LANG": "C.UTF-8"},
                start_new_session=os.name == "posix",
            )
            until = time.monotonic() + timeout
            try:
                while process.poll() is None:
                    require(
                        os.fstat(output.fileno()).st_size <= limit
                        and os.fstat(errors.fileno()).st_size <= 131072,
                        "COMMAND_OUTPUT_BOUND",
                    )
                    require(time.monotonic() < until, "COMMAND_TIMEOUT")
                    time.sleep(0.05)
                require(
                    os.fstat(output.fileno()).st_size <= limit
                    and os.fstat(errors.fileno()).st_size <= 131072,
                    "COMMAND_OUTPUT_BOUND",
                )
                require(
                    process.returncode in ((0, 1) if allow_exit_one else (0,)),
                    "KUBECTL_COMMAND_FAILED",
                )
                output.seek(0)
                raw = output.read(limit + 1)
                if args[-1:] == ["--ignore-not-found"] and not raw.strip():
                    return None
                return (
                    raw.decode("utf-8") if "--no-headers" in args else json.loads(raw)
                )
            finally:
                if process.poll() is None:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    process.wait(timeout=5)

    def get(self, kind, name):
        return self.command(["get", kind, name, "-o", "json"])

    def child(
        self, pod, container, source, arguments=(), timeout=30, interpreter="python3"
    ):
        require(
            interpreter in ("python3", "/app/backend/.venv/bin/python"),
            "CHILD_INTERPRETER",
        )
        return self.command(
            [
                "exec",
                pod,
                "-c",
                container,
                "--",
                interpreter,
                "-I",
                "-B",
                "-u",
                "-c",
                source,
            ]
            + list(arguments),
            timeout=timeout,
            limit=65536,
            allow_exit_one=True,
        )

    def source(self, pod, candidate=False, health=False):
        mounts, projections = self.pre.mount_projection(pod)
        if candidate:
            for name, key in FILE_KEYS.items():
                mount = mounts[name]
                require(
                    mount.get("source") == "configmap_mount"
                    and mount.get("configmap_name") == self.cm_name
                    and mount.get("volume") == VOLUME
                    and mount.get("subpath") == key
                    and mount.get("read_only") is True
                    and mount.get("mapped_keys") == [key],
                    "CANDIDATE_MOUNT_PROJECTION",
                )
        code = self.pre.CHILD
        if health:
            # The original fixed-file reader runs before one bounded localhost GET.
            # Use one shared namespace: imported reader functions resolve its globals.
            code = (
                "import json,sys,http.client,signal\ng={'__name__':'source_reader'}\nexec("
                + repr(self.pre.CHILD)
                + ",g,g)\n"
                + r"""
def stop(*args):raise ValueError('FLEET_CHECK_TIMEOUT')
signal.signal(signal.SIGALRM,stop);signal.alarm(20)
out={'status':'INCOMPLETE'}
try:
 out=g['inspect'](g['ROOT'],json.loads(sys.argv[1]))
 c=http.client.HTTPConnection('127.0.0.1',8001,timeout=5)
 try:
  c.request('GET','/health',headers={'Connection':'close'})
  r=c.getresponse(); body=r.read(65537)
  if r.status!=200 or len(body)>65536:raise ValueError('LOCAL_HEALTH_FAILED')
  out['local_health_status']=r.status
 finally:c.close()
except Exception:
 out={'status':'INCOMPLETE','error_code':'FIXED_SOURCE_OR_HEALTH_FAILED'}
finally:signal.alarm(0)
print(json.dumps(out,sort_keys=True))
sys.exit(int(out['status']=='INCOMPLETE'))
"""
            )
        result = self.child(
            pod["metadata"]["name"],
            "gateway",
            code,
            [canonical(projections).decode()],
            timeout=30,
        )
        normalized = self.pre.runtime_projection(result)
        require(
            normalized.get("status") == "FIXED_SOURCE_HASHES_OBSERVED",
            "FIXED_SOURCE_CHECK_FAILED",
        )
        for name in FILE_KEYS:
            row = result["files"][name]
            expected = self.pre.REFERENCES[name]["candidate" if candidate else "parent"]
            require(
                row.get("sha256") == expected
                and row.get("present") == (expected is not None),
                "RUNTIME_SOURCE_MISMATCH",
            )
        if health:
            require(result.get("local_health_status") == 200, "LOCAL_HEALTH_FAILED")
        return {name: result["files"][name] for name in FILE_KEYS}

    def idle(self):
        result = self.child(
            self.sample["metadata"]["name"],
            "gateway",
            self.assets["task_origin_guard"].decode(),
            ["--inspect-idle-and-origin"],
            timeout=60,
            interpreter="/app/backend/.venv/bin/python",
        )
        # Preserve only a bounded static diagnostic, never a raw database/config exception.
        if result.get("status") != "GATEWAY_IDLE_ORIGIN_OBSERVED":
            self.report["idle_diagnostic"] = {
                k: result[k]
                for k in ("status", "error_code", "step")
                if isinstance(result.get(k), str)
                and re.fullmatch("[A-Za-z0-9_-]{1,100}", result[k])
            }
        else:
            self.report["idle_diagnostic"] = {
                k: result[k]
                for k in (
                    "active_runs",
                    "active_oa",
                    "active_callbacks",
                    "scheduled_running",
                    "active_work_notifications",
                )
                if type(result.get(k)) is int and 0 <= result[k] < 100000000
            }
            self.report["idle_diagnostic"]["idle"] = result.get("idle") is True
        return accepted_idle(result)

    def verify_installed_skill(self, pod):
        path = Path("/var/lib/portal-energy-portable-install-20261111-r1/REPORT.json")
        raw = private_read(path)
        receipt = json.loads(raw)
        require(
            receipt.get("status") == "PORTABLE_SKILL_INSTALLED_GATEWAY_LINK_PENDING"
            and receipt.get("after_tree_sha256")
            == "f880bc0bd379612a20c0f62fe0c3c14389aad4a9aa26960f5d67a3145f8a7044"
            and receipt.get("old_skill_removed_from_discovery") is True
            and receipt.get("maintenance_pod_absent") is True,
            "PORTABLE_SKILL_INSTALL_RECEIPT_REQUIRED",
        )
        binding = {
            "path": str(path),
            "sha256": sha(raw),
            "operation": receipt.get("operation"),
        }
        existing = self.report.get("accepted_skill_install")
        require(
            existing is None or existing == binding, "SKILL_INSTALL_RECEIPT_CHANGED"
        )
        self.report["accepted_skill_install"] = binding
        containers = pod["spec"]["containers"]
        require(len(containers) == 1, "ONE_GATEWAY_CONTAINER_REQUIRED")
        mounts = [
            m
            for m in containers[0].get("volumeMounts", [])
            if m.get("mountPath") == "/mnt/skills"
        ]
        require(
            len(mounts) == 1
            and not mounts[0].get("subPath")
            and not mounts[0].get("subPathExpr"),
            "READONLY_SHARED_SKILLS_MOUNT_REQUIRED",
        )
        volumes = [
            v
            for v in pod["spec"].get("volumes", [])
            if v.get("name") == mounts[0]["name"]
        ]
        require(
            len(volumes) == 1
            and volumes[0].get("persistentVolumeClaim", {}).get("claimName")
            == "deer-flow-skills-rwx-pvc",
            "SKILLS_PVC_BINDING_CHANGED",
        )
        require(
            mounts[0].get("readOnly") is True
            or volumes[0].get("persistentVolumeClaim", {}).get("readOnly") is True,
            "READONLY_SHARED_SKILLS_MOUNT_REQUIRED",
        )
        result = self.child(
            pod["metadata"]["name"],
            "gateway",
            self.assets["skill_reader"].decode(),
            timeout=40,
        )
        require(
            result.get("status") == "PORTABLE_SKILL_TREE_VERIFIED"
            and result.get("files") == 105
            and result.get("tree_sha256")
            == "f880bc0bd379612a20c0f62fe0c3c14389aad4a9aa26960f5d67a3145f8a7044"
            and result.get("read_only") is True,
            "LIVE_PORTABLE_SKILL_VIEW_CHANGED",
        )
        return {
            "pod": pod["metadata"]["name"],
            "uid": pod["metadata"]["uid"],
            "tree_sha256": result["tree_sha256"],
            "files": 105,
        }

    def baseline(self):
        deployment = self.get("deployment", DEPLOYMENT)
        self.pre.deployment_guard(deployment)
        candidates = self.pre.parse_candidates(self.command(self.pre.LIST_ARGS))
        require(len(candidates) == 70, "READY_GATEWAY_COUNT")
        self.known_pods.update(name for name, uid in candidates)
        pod = self.get("pod", candidates[0][0])
        require(pod["metadata"]["uid"] == candidates[0][1], "SAMPLE_UID_CHANGED")
        rs_name = self.pre.owner(pod, "ReplicaSet")["name"]
        self.known_rs.add(rs_name)
        rs = self.get("replicaset", rs_name)
        runtime = self.pre.pod_guard(pod, rs, deployment)
        self.known_nodes.add(runtime["node"])
        node = self.command(["get", "node", runtime["node"], "-o", "json"])
        require(
            any(
                c.get("type") == "Ready" and c.get("status") == "True"
                for c in node.get("status", {}).get("conditions", [])
            ),
            "SAMPLE_NODE_NOT_READY",
        )
        service = self.get("service", "gateway")
        require(
            service["spec"].get("selector") == {"app": "gateway"},
            "GATEWAY_SERVICE_SELECTOR",
        )
        self.before, self.sample, self.service = deployment, pod, service
        self.report["openssl_compatibility"] = openssl_identity(
            deployment, self.get("configmap", OPENSSL_CM)
        )
        self.report["before"] = strict_identity(deployment)
        self.report["sample"] = runtime
        self.report["baseline_sources"] = self.source(pod)
        self.verify_installed_skill(pod)
        self.report["initial_idle_observation"] = self.idle()
        require(
            self.get("deployment", DEPLOYMENT)["spec"] == deployment["spec"],
            "BASELINE_CHANGED_DURING_INSPECTION",
        )

    def claim(self):
        safe(CONTROL)
        require(not CONTROL.exists(), "ONCE_DIRECTORY_EXISTS_REVIEW_REQUIRED")
        os.mkdir(str(CONTROL), 0o700)
        self.report["once_claimed"] = True
        private_write(CONTROL / "BEFORE-DEPLOYMENT.json", canonical(self.before))
        private_write(CONTROL / "CANDIDATE-SPEC.json", canonical(self.expected))
        self.journal("CLAIMED_WITH_PRIVATE_BACKUP")

    def qualify(self):
        cm = configmap_manifest(self.operation, self.cm_name, self.assets)
        self.report["configmap_create_requested"] = True
        self.journal("CREATE_IMMUTABLE_SOURCE_CONFIGMAP")
        created = self.command(["create", "-f", "-", "-o", "json"], canonical(cm))
        self.cm_uid = created["metadata"]["uid"]
        require(
            created.get("data") == cm["data"] and created.get("immutable") is True,
            "CONFIGMAP_CREATE_READBACK",
        )
        self.report["configmap"] = {
            "name": self.cm_name,
            "uid": self.cm_uid,
            "data_sha256": sha(canonical(cm["data"])),
        }
        requested = maintenance_manifest(
            self.operation,
            self.maint_name,
            self.sample["spec"]["nodeName"],
            self.cm_name,
        )
        self.report["maintenance_create_requested"] = True
        self.journal("CREATE_ISOLATED_QUALIFICATION_POD")
        pod = self.command(["create", "-f", "-", "-o", "json"], canonical(requested))
        self.maint_uid = pod["metadata"]["uid"]
        self.report["maintenance"] = {"name": self.maint_name, "uid": self.maint_uid}
        image_id = self.sample["status"]["containerStatuses"][0]["imageID"]
        until = time.monotonic() + 180
        while True:
            pod = self.get("pod", self.maint_name)
            if maintenance_guard(pod, requested, self.maint_uid, image_id):
                break
            require(
                pod.get("status", {}).get("phase") not in ("Failed", "Succeeded"),
                "MAINTENANCE_TERMINATED",
            )
            require(time.monotonic() < until, "MAINTENANCE_READY_TIMEOUT")
            time.sleep(3)
        self.journal("QUALIFY_SIX_FIXED_SOURCE_FILES")
        result = self.child(
            self.maint_name,
            "maintenance",
            self.assets["qualifier"].decode(),
            ["--qualify-fixed-candidate"],
            timeout=150,
            interpreter="/app/backend/.venv/bin/python",
        )
        if result.get("status") != "GATEWAY_CONVERSATION_LINK_SYNTHETIC_ACCEPTED":
            self.report["qualification_diagnostic"] = {
                k: result[k]
                for k in ("status", "error_code", "step")
                if isinstance(result.get(k), str)
                and re.fullmatch("[A-Za-z0-9_-]{1,100}", result[k])
            }
        require(
            result.get("status") == "GATEWAY_CONVERSATION_LINK_SYNTHETIC_ACCEPTED"
            and result.get("candidate_files_verified") == len(EXPECTED)
            and type(result.get("synthetic_checks")) is int
            and result["synthetic_checks"] > 0
            and all(
                result.get(k) is False
                for k in (
                    "application_started",
                    "database_access",
                    "live_configuration_verified",
                )
            )
            and result.get("openssl_compat_verified") is True
            and result.get("temporary_files_cleaned") is True
            and result.get("provider_requests") == 0
            and result.get("work_messages_sent") == 0,
            "SYNTHETIC_QUALIFICATION_FAILED",
        )
        require(
            result.get("candidate_files")
            == {
                name: {"bytes": size, "sha256": digest}
                for name, (size, digest) in EXPECTED.items()
            },
            "QUALIFIED_SOURCE_SET",
        )
        self.report["qualification"] = result
        # Only this owned, secret-free Pod is removed; keep the immutable CM.
        self.cleanup_maintenance()

    def cleanup_maintenance(self):
        pod = self.get("pod", self.maint_name)
        require(
            pod["metadata"]["uid"] == self.maint_uid
            and pod["metadata"].get("labels", {}).get(LABEL) == self.operation
            and not pod["metadata"].get("ownerReferences"),
            "MAINTENANCE_CLEANUP_IDENTITY",
        )
        self.report["maintenance_delete_requested"] = True
        self.journal("DELETE_ONLY_OWNED_QUALIFICATION_POD")
        delete = {
            "apiVersion": "v1",
            "kind": "DeleteOptions",
            "preconditions": {"uid": self.maint_uid},
            "gracePeriodSeconds": 5,
        }
        self.command(
            [
                "delete",
                "--raw",
                "/api/v1/namespaces/deer-flow/pods/" + self.maint_name,
                "-f",
                "-",
            ],
            canonical(delete),
        )
        until = time.monotonic() + 60
        while True:
            result = self.command(
                ["get", "pod", self.maint_name, "-o", "json", "--ignore-not-found"]
            )
            if result is None:
                break
            require(
                result["metadata"]["uid"] == self.maint_uid,
                "MAINTENANCE_REPLACED_DURING_DELETE",
            )
            require(time.monotonic() < until, "MAINTENANCE_DELETE_TIMEOUT")
            time.sleep(2)
        self.report["maintenance_absent"] = True

    def patch(self):
        fresh = self.get("deployment", DEPLOYMENT)
        self.pre.deployment_guard(fresh)
        pod = self.get("pod", self.sample["metadata"]["name"])
        require(
            pod["metadata"]["uid"] == self.sample["metadata"]["uid"],
            "SAMPLE_REPLACED_BEFORE_PATCH",
        )
        rs_name = self.pre.owner(pod, "ReplicaSet")["name"]
        require(rs_name in self.known_rs, "SAMPLE_REPLICASET_CHANGED")
        require(
            self.pre.pod_guard(pod, self.get("replicaset", rs_name), fresh)
            == self.report["sample"],
            "SAMPLE_RUNTIME_CHANGED_BEFORE_PATCH",
        )
        self.source(pod)
        require(
            openssl_identity(fresh, self.get("configmap", OPENSSL_CM))
            == self.report["openssl_compatibility"],
            "OPENSSL_CHANGED_BEFORE_PATCH",
        )
        cm = self.get("configmap", self.cm_name)
        require(
            cm["metadata"]["uid"] == self.cm_uid
            and cm.get("immutable") is True
            and cm.get("data")
            == configmap_manifest(self.operation, self.cm_name, self.assets)["data"],
            "SOURCE_CONFIGMAP_CHANGED",
        )
        self.verify_installed_skill(pod)
        self.report["final_idle_observation"] = self.idle()
        # Do not obtain another resourceVersion after the idle check: its CAS
        # rejects any deployment metadata/spec writer that raced this observation.
        self.before = fresh
        private_write(
            CONTROL / "PATCH.json", canonical(cas_patch(fresh, self.expected))
        )
        self.report["patch_request_count"] = 1
        self.report["patch_may_have_applied"] = True
        self.journal("GATEWAY_SPEC_CAS_REQUEST")
        after = self.command(
            [
                "patch",
                "deployment",
                "gateway",
                "--type=json",
                "--patch-file",
                str(CONTROL / "PATCH.json"),
                "-o",
                "json",
            ]
        )
        candidate_guard(after, self.expected)
        self.report["patch_acknowledged"] = True
        self.report["after"] = strict_identity(after)
        self.journal("WAIT_UNCHANGED_STRATEGY_ROLLOUT")

    def rollout(self):
        until, last_progress = time.monotonic() + ROLLOUT_SECONDS, 0.0
        while True:
            deployment = self.get("deployment", DEPLOYMENT)
            candidate_guard(deployment, self.expected)
            status = deployment.get("status", {})
            if time.monotonic() - last_progress >= 45:
                print(
                    json.dumps(
                        {
                            "phase": "WAIT_GATEWAY_414_ROLLOUT",
                            "desired": 70,
                            "updated": status.get("updatedReplicas", 0),
                            "ready": status.get("readyReplicas", 0),
                            "available": status.get("availableReplicas", 0),
                        }
                    ),
                    flush=True,
                )
                last_progress = time.monotonic()
            if (
                status.get("observedGeneration") == 414
                and all(
                    status.get(k, 0) == 70
                    for k in (
                        "replicas",
                        "updatedReplicas",
                        "readyReplicas",
                        "availableReplicas",
                    )
                )
                and status.get("terminatingReplicas", 0) == 0
            ):
                break
            require(
                not any(
                    c.get("type") == "Progressing"
                    and c.get("status") == "False"
                    and c.get("reason") == "ProgressDeadlineExceeded"
                    for c in status.get("conditions", [])
                ),
                "ROLLOUT_PROGRESS_DEADLINE",
            )
            require(time.monotonic() < until, "ROLLOUT_WAIT_TIMEOUT")
            time.sleep(10)
        return deployment

    def verify(self, deployment):
        self.journal("VERIFY_ALL_SEVENTY_NEW_GATEWAY_PODS")
        rows = self.pre.parse_candidates(self.command(self.pre.LIST_ARGS))
        require(len(rows) == 70, "FINAL_READY_COUNT")
        self.known_pods.update(name for name, uid in rows)
        rs_cache, accepted, skill_views = {}, [], {}
        for index, (name, uid) in enumerate(rows):
            pod = self.get("pod", name)
            require(pod["metadata"]["uid"] == uid, "FLEET_POD_REPLACED")
            rs_name = self.pre.owner(pod, "ReplicaSet")["name"]
            self.known_rs.add(rs_name)
            if rs_name not in rs_cache:
                rs_cache[rs_name] = self.get("replicaset", rs_name)
            before = self.pre.pod_guard(pod, rs_cache[rs_name], deployment)
            require(
                pod["status"]["containerStatuses"][0]["imageID"]
                == self.sample["status"]["containerStatuses"][0]["imageID"],
                "FLEET_IMAGE_ID_CHANGED",
            )
            self.source(pod, candidate=True, health=True)
            if before["node"] not in skill_views:
                skill_views[before["node"]] = self.verify_installed_skill(pod)
            after = self.pre.pod_guard(
                self.get("pod", name), rs_cache[rs_name], deployment
            )
            require(before == after, "FLEET_RUNTIME_CHANGED_DURING_CHECK")
            accepted.append(
                {
                    "name": name,
                    "uid": uid,
                    "runtime": after,
                    "source_files_verified": len(EXPECTED),
                    "health_status": 200,
                }
            )
            if index % 10 == 9:
                print(
                    json.dumps(
                        {
                            "phase": "VERIFY_GATEWAY_414",
                            "verified_pods": index + 1,
                            "expected": 70,
                        }
                    ),
                    flush=True,
                )
        require(len(rs_cache) == 1, "MULTIPLE_CURRENT_REPLICASETS")
        service = self.get("service", "gateway")
        require(
            service["metadata"]["uid"] == self.service["metadata"]["uid"]
            and service["spec"] == self.service["spec"],
            "GATEWAY_SERVICE_CHANGED",
        )
        slices = self.command(
            [
                "get",
                "endpointslices",
                "-l",
                "kubernetes.io/service-name=gateway",
                "-o",
                "json",
            ]
        )
        require(
            endpoint_pods(slices, service["metadata"]["uid"]) == set(rows),
            "ENDPOINT_SET_MISMATCH",
        )
        final = self.get("deployment", DEPLOYMENT)
        candidate_guard(final, self.expected, converged=True)
        require(
            self.pre.parse_candidates(self.command(self.pre.LIST_ARGS)) == rows,
            "FINAL_FLEET_CHANGED",
        )
        self.report["skill_node_views"] = skill_views
        self.report.update(
            status="GATEWAY_CONVERSATION_LINK_DEPLOYED_RUNTIME_ACCEPTED",
            after=strict_identity(final),
            verified_pods=accepted,
            all_gateway_pods_verified=True,
            service_backends_verified=True,
            candidate_source_files_verified=len(EXPECTED),
            loaded_modules_business_acceptance=False,
        )
        self.journal("RUNTIME_ACCEPTED_BUSINESS_CHECK_PENDING")

    def run(self):
        self.journal("READ_FIXED_BASELINE_AND_IDLE_STATE")
        self.baseline()
        self.expected = candidate_spec(self.before, self.cm_name)
        self.claim()
        self.qualify()
        self.patch()
        self.verify(self.rollout())
        return self.report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--activate-result-links-in-idle-window-once",
        action="store_true",
        required=True,
    )
    parser.parse_args(argv)
    updater = None
    result = {
        "schema": "energy-daily-gateway-result-link-activation.v2",
        "status": "INCOMPLETE",
        "once_claimed": False,
        "patch_may_have_applied": False,
        "patch_request_count": 0,
        "provider_requests": 0,
        "work_messages_sent": 0,
        "credentials_exported": False,
        "automatic_retry": False,
        "automatic_rollback": False,
    }
    try:
        require(
            os.name == "posix"
            and os.uname().nodename.split(".")[0] == "datax-01"
            and os.geteuid() == 0,
            "DATAX01_ROOT_REQUIRED",
        )
        assets = load_assets()
        updater = Updater(assets)
        result = updater.report
        updater.run()
    except Exception as exc:
        result["status"] = "INCOMPLETE"
        result["error_class"] = type(exc).__name__
        result["error_code"] = (
            str(exc)
            if isinstance(exc, Refused) and re.fullmatch("[A-Z0-9_]{1,100}", str(exc))
            else "LOCAL_FAILURE_REDACTED"
        )
    result.update(
        observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        commands=updater.commands if updater else 0,
        elapsed_seconds=round(time.monotonic() - updater.started, 3) if updater else 0,
    )
    if updater and result["once_claimed"]:
        try:
            raw = canonical(result)
            private_write(CONTROL / "REPORT.json", raw)
            result["report_path"] = str(CONTROL / "REPORT.json")
            result["report_sha256"] = sha(raw)
            result["report_bytes"] = len(raw)
        except Exception:
            result["receipt_write_failed"] = True
    print(json.dumps(result, sort_keys=True))
    return int(result["status"] == "INCOMPLETE")


if __name__ == "__main__":
    sys.exit(main())
