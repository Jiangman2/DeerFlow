#!/usr/bin/env python3
"""Read-only portal/PPT baseline refresh; stdlib controller, Python 3.7+.

Run on datax-01: sudo python3 -B refresh_baseline_v3.py --collect
Only fixed Kubernetes GETs and read-only source/metadata probes are permitted.
The sole persistent side effect is a NEW private local report directory.
No websites/models, database connections, migrations, builds, or rollout.
Terminal API records do not prove runtime termination or authorize cleanup.
"""
import argparse
import collections
import concurrent.futures
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time

COMPONENTS = ("gateway", "frontend", "ppt-master-runner", "provisioner")
NAMESPACE = "deer-flow"
MAX_PARALLEL = 4
MAX_OUTPUT = 8 * 1024 * 1024
MAX_ITEMS = 2000
BACKEND = "/app/backend/"
SOURCE_FILES = {
    "gateway": tuple(BACKEND + p for p in (
        "app/gateway/app.py",
        "app/gateway/oa_policy_flow_service.py",
        "app/gateway/oa_policy_flow_config.py",
        "app/gateway/routers/oa_policy_flow.py",
        "packages/harness/deerflow/persistence/oa_policy_flow_jobs/model.py",
        "packages/harness/deerflow/persistence/oa_policy_flow_jobs/sql.py",
        "packages/harness/deerflow/persistence/migrations/versions/0014_oa_policy_flow_jobs.py",
        "app/gateway/routers/uploads.py",
        "app/gateway/upload_document_parser.py",
        "packages/harness/deerflow/community/url_safety.py",
        "packages/harness/deerflow/tools/builtins/present_file_tool.py",
        "app/gateway/routers/agents.py",
        "app/gateway/routers/models.py",
        "app/gateway/routers/features.py",
        "app/gateway/routers/browser.py",
        "app/gateway/routers/artifacts.py",
        "app/gateway/routers/employee_directory.py",
        "app/gateway/routers/ppt_confirmations.py",
        "app/gateway/employee_directory_seed.py",
        "app/gateway/employee_directory_maintenance.py",
        "app/gateway/employee_featured_store.py",
        "app/gateway/ppt_runtime_broker.py",
        "app/gateway/codex_responses_adapter.py",
        "packages/harness/deerflow/models/enterprise_glm.py",
        "packages/harness/deerflow/models/robust_openai.py",
        "packages/harness/deerflow/models/factory.py",
        "packages/harness/deerflow/config/model_config.py",
        "packages/harness/deerflow/community/browser_automation/tools.py",
        "packages/harness/deerflow/community/browser_automation/session.py",
        "packages/harness/deerflow/community/browser_automation/request_guard.py",
        "packages/harness/deerflow/community/browser_automation/url_policy.py",
        "packages/harness/deerflow/persistence/models/__init__.py",
        "packages/harness/deerflow/persistence/migrations/versions/0020_ppt_confirmation.py",
        "packages/harness/deerflow/persistence/ppt_confirmation/model.py",
        "packages/harness/deerflow/persistence/ppt_confirmation/sql.py",
        "packages/harness/deerflow/ppt_confirmation/admission.py",
        "packages/harness/deerflow/ppt_confirmation/continuation.py",
        "packages/harness/deerflow/ppt_confirmation/controller.py",
        "packages/harness/deerflow/ppt_confirmation/host_owner.py",
        "packages/harness/deerflow/ppt_confirmation/lifecycle.py",
        "packages/harness/deerflow/ppt_confirmation/monitor.py",
        "packages/harness/deerflow/ppt_confirmation/proposal.py",
    )),
    "frontend": tuple("/app/frontend/" + p for p in (
        "package.json", ".next/BUILD_ID", "server.js",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "tsconfig.json",
        "next-env.d.ts",
        "next.config.js",
        "postcss.config.js",
        "components.json",
        "eslint.config.js",
        "prettier.config.js",
        ".npmrc",
        ".dockerignore",
        "Dockerfile",
        "Dockerfile.slim",
        "Dockerfile.slim.dockerignore",
        "src/env.js",
        "scripts/postcss-semantic-alpha-fallback-webpack.cjs",
        "scripts/postcss-semantic-alpha-fallback.mjs",
        "scripts/verify-standalone.mjs",
        "src/app/mock/api/threads/[thread_id]/artifacts/[[...artifact_path]]/route.ts",
        "src/core/shop-cart/api-files.ts",
        "src/core/shop-cart/config.ts",
        "src/core/shop-cart/download.ts",
        "src/core/shop-cart/filenames.ts",
        "src/core/shop-cart/mode.ts",
        "src/core/shop-cart/new-api.ts",
        "src/core/shop-cart/selected-files.ts",
        "src/core/shop-cart/xlsx.ts",
        "src/server/shop-cart/new-api.ts",
        "src/app/api/shop-cart/download/route.ts",
        "src/app/api/shop-cart/new/download/route.ts",
        "src/app/api/shop-cart/new/list/route.ts",
        "src/components/workspace/shop-cart-attachments/index.ts",
        "src/components/workspace/shop-cart-attachments/new-shop-cart-dialog.tsx",
        "src/components/workspace/shop-cart-attachments/shop-cart-attachment-button.tsx",
        "src/components/workspace/shop-cart-attachments/shop-cart-dialog.tsx",
        "src/components/workspace/input-box.tsx",
        "src/components/workspace/mode-selector.tsx",
        "src/components/workspace/portal-tools-menu.tsx",
        "src/components/workspace/user-guide-button.tsx",
        "src/components/workspace/agents/agent-card.tsx",
        "src/components/workspace/agents/agent-gallery.tsx",
        "src/components/workspace/messages/ppt-main-progress.tsx",
        "src/components/workspace/ppt-confirmation-opt-in.tsx",
        "src/components/workspace/ppt-confirmation-state.tsx",
        "src/app/workspace/chats/[thread_id]/page.tsx",
        "src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx",
        "src/core/agents/employee-directory.ts",
        "src/core/agents/types.ts",
        "src/core/models/types.ts",
        "src/core/portal-links.ts",
        "src/core/tasks/ppt-confirmation.ts",
        "src/core/ppt-confirmation/api.ts",
        "src/core/ppt-confirmation/consent.ts",
        "src/app/workspace/ppt-confirmation/[thread_id]/[task_id]/page.tsx",
    )),
    "ppt-master-runner": tuple(BACKEND + p for p in (
        "packages/harness/deerflow/agents/ppt_master_runner/agent.py",
        "packages/harness/deerflow/persistence/models/__init__.py",
        "packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py",
        "packages/harness/deerflow/runtime/runs/manager.py",
        "app/gateway/services.py",
    )),
    "provisioner": ("/app/app.py",),
}
PYTHON_DEPENDENCIES = ("httpx", "openai", "langchain", "langchain-core",
                       "langchain-openai", "langgraph", "langgraph-checkpoint",
                       "pydantic", "playwright")
DEPENDENCIES = {c: PYTHON_DEPENDENCIES for c in COMPONENTS}
DEPENDENCIES["frontend"] = ("next", "react", "react-dom")
DEPENDENCIES["provisioner"] = ("fastapi", "uvicorn", "kubernetes", "pydantic")
NAME = re.compile(r"[a-z0-9][a-z0-9.-]{0,252}$")
SHA = re.compile(r"[0-9a-f]{64}$")
SOURCE_STATUSES = {"PRESENT", "MISSING", "UNSAFE", "OVERSIZE", "CHANGED", "READ_FAILED"}


class CheckFailure(Exception):
    def __init__(self, code, evidence=None):
        self.code = code
        self.evidence = evidence or {}
        Exception.__init__(self, code)


def require(ok, code):
    if not ok:
        raise CheckFailure(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def digest(value):
    return hashlib.sha256(value).hexdigest()


def failure(exc):
    # Never stringify an external exception, stderr, URL, or Kubernetes object.
    return {"code": exc.code if isinstance(exc, CheckFailure) else "unexpected_read_failure"}


def identifier(value):
    require(isinstance(value, str) and NAME.fullmatch(value), "invalid_resource_identity")
    return value


def opaque(value):
    require(isinstance(value, str) and 0 < len(value) <= 1024, "missing_runtime_identity")
    return digest(value.encode("utf-8"))


def image_ref(value):
    pattern = (r"(?:[A-Za-z0-9][A-Za-z0-9._-]*(?::[0-9]{1,5})?/)?"
               r"[A-Za-z0-9][A-Za-z0-9._/-]*(?::[A-Za-z0-9_.-]+)?(?:@sha256:[0-9a-f]{64})?")
    if isinstance(value, str) and len(value) <= 512 and re.fullmatch(pattern, value):
        return value
    return "REDACTED_INVALID_IMAGE_REFERENCE"


def image_digest(value):
    if not isinstance(value, str):
        return "UNRECOGNIZED"
    bare = re.fullmatch(r"(?:(?:containerd|docker)://)?(sha256:[0-9a-f]{64})", value)
    named = re.fullmatch(r"(?:(?:docker-pullable|containerd)://)?[A-Za-z0-9][A-Za-z0-9._:/-]{0,511}@(sha256:[0-9a-f]{64})", value)
    match = bare or named
    return match.group(1) if match else "UNRECOGNIZED"


def safe_path(value):
    if isinstance(value, str) and value.startswith("/") and len(value) <= 512 and not re.search(r"[\x00-\x20?#]", value):
        return value
    return "UNSET" if value is None else "REDACTED_UNEXPECTED_PATH"


def deployment_selector(deployment):
    selector = deployment["spec"].get("selector", {})
    labels, expressions = selector.get("matchLabels", {}), selector.get("matchExpressions", [])
    require(isinstance(labels, dict) and isinstance(expressions, list)
            and 0 < len(labels) + len(expressions) <= 64, "invalid_selector")

    def key(value):
        require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.][A-Za-z0-9_./-]{0,316}", value), "invalid_selector_key")
        return value

    def label(value):
        require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{0,63}", value), "invalid_selector_value")
        return value

    parts = [key(k) + "=" + label(v) for k, v in sorted(labels.items())]
    for row in expressions:
        require(isinstance(row, dict), "invalid_selector_expression")
        name, op, values = key(row.get("key")), row.get("operator"), row.get("values", [])
        require(isinstance(values, list), "invalid_selector_values")
        if op in ("In", "NotIn"):
            require(0 < len(values) <= 64, "invalid_selector_values")
            parts.append(name + (" in (" if op == "In" else " notin (") + ",".join(label(v) for v in values) + ")")
        else:
            require(op in ("Exists", "DoesNotExist") and not values, "invalid_selector_operator")
            parts.append(("!" if op == "DoesNotExist" else "") + name)
    result = ",".join(parts)
    require(len(result) <= 4096, "selector_too_large")
    return result


def controller_owner(obj, uid, kind):
    return any(o.get("controller") is True and o.get("uid") == uid and o.get("kind") == kind
               for o in obj.get("metadata", {}).get("ownerReferences", []))


def fingerprint(deployment):
    meta, spec, status = deployment["metadata"], deployment["spec"], deployment.get("status", {})
    desired = spec.get("replicas", 1)
    require(type(desired) is int and 0 <= desired <= MAX_ITEMS, "invalid_replica_count")
    return {"uid_sha256": opaque(meta["uid"]), "generation": meta.get("generation"),
            "spec_sha256": digest(canonical(spec)), "desired": desired,
            "observed_generation": status.get("observedGeneration"),
            "counts": {k: status.get(k, 0) for k in ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas")},
            "deleting": bool(meta.get("deletionTimestamp")),
            "images": {identifier(c["name"]): image_ref(c.get("image"))
                       for c in spec["template"]["spec"]["containers"]}}


def converged(fp):
    return (not fp["deleting"] and fp["desired"] > 0 and
            fp["observed_generation"] == fp["generation"] and
            all(n == fp["desired"] for n in fp["counts"].values()))


def runtime_shape(deployment, component):
    spec = deployment["spec"]["template"]["spec"]
    containers = [c for c in spec["containers"] if c["name"] == component]
    require(len(containers) == 1, "target_container_missing")
    container = containers[0]
    command, args = container.get("command", []), container.get("args", [])
    launcher = "CUSTOM_REDACTED"
    if not command and not args:
        launcher = "IMAGE_DEFAULT_UNRESOLVED"
    elif command + args in (["node", "server.js"], ["/usr/local/bin/node", "server.js"]):
        launcher = "NODE_STANDALONE"
    elif command + args in (["pnpm", "start"], ["pnpm", "run", "start"]):
        launcher = "PNPM_START"
    volume_types = {}
    for volume in spec.get("volumes", []):
        kinds = [k for k in ("secret", "configMap", "persistentVolumeClaim", "nfs", "hostPath", "emptyDir",
                             "projected", "csi", "downwardAPI", "ephemeral") if k in volume]
        volume_types[volume["name"]] = kinds[0] if len(kinds) == 1 else "OTHER"
    probes = {}
    for name in ("livenessProbe", "readinessProbe", "startupProbe"):
        if name not in container:
            continue
        probe = container[name]
        methods = [k for k in ("httpGet", "tcpSocket", "exec", "grpc") if k in probe]
        row = {"sha256": digest(canonical(probe)), "kind": methods[0] if len(methods) == 1 else "OTHER"}
        row["timing"] = {k: probe[k] for k in ("initialDelaySeconds", "periodSeconds", "timeoutSeconds",
                                              "successThreshold", "failureThreshold")
                         if type(probe.get(k)) is int}
        if row["kind"] == "httpGet":
            row["http_headers_count"] = len(probe["httpGet"].get("httpHeaders", []))
        probes[name] = row
    return {"launcher": launcher, "command_args_sha256": digest(canonical([command, args])),
            "working_directory": safe_path(container.get("workingDir")),
            "ports": sorted({p["containerPort"] for p in container.get("ports", [])
                             if type(p.get("containerPort")) is int and 0 < p["containerPort"] < 65536}),
            "mounts": [{"volume_name_sha256": digest(str(mount.get("name")).encode("utf-8")),
                        "path": safe_path(mount.get("mountPath")), "read_only": mount.get("readOnly", False) is True,
                        "sub_path_present": bool(mount.get("subPath") or mount.get("subPathExpr")),
                        "reference_type": volume_types.get(mount.get("name"), "UNKNOWN")}
                       for mount in container.get("volumeMounts", [])],
            "probes": probes, "environment_values": "NOT_EXPORTED",
            "resources_sha256": digest(canonical(container.get("resources", {})))}


CONTAINER_STATUS_FIELDS = ("containerStatuses", "initContainerStatuses", "ephemeralContainerStatuses")
READY_CONDITIONS = {"Ready", "ContainersReady"}
DIAGNOSTIC_CONDITIONS = READY_CONDITIONS | {"PodReadyToStartContainers"}
STATUS_REASONS = {"Evicted", "NodeLost", "Shutdown", "DeadlineExceeded", "Completed", "Error",
                  "OOMKilled", "ContainerStatusUnknown", "ContainerCreating", "PodInitializing",
                  "CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerError",
                  "CreateContainerConfigError", "RunContainerError", "StartError", "InvalidImageName"}


def safe_reason(value):
    # Reasons are diagnostics, not an unrestricted text export channel.
    return value if value in STATUS_REASONS else (None if value is None else "REDACTED_OTHER_REASON")


def optional_hash(value):
    return digest(canonical(value)) if value is not None else None


def container_state(state):
    require(isinstance(state, dict) and set(state) <= {"running", "waiting", "terminated"},
            "invalid_container_state")
    result = {}
    for kind, detail in state.items():
        require(isinstance(detail, dict), "invalid_container_state")
        row = {"detail_sha256": digest(canonical(detail))}
        if kind in ("waiting", "terminated"):
            reason = detail.get("reason")
            require(reason is None or isinstance(reason, str), "invalid_container_reason")
            row.update(reason=safe_reason(reason), reason_sha256=optional_hash(reason))
        if kind == "terminated":
            for original, output in (("exitCode", "exit_code"), ("signal", "signal")):
                number = detail.get(original)
                require(number is None or type(number) is int, "invalid_container_exit")
                row[output] = number
            row["container_id_sha256"] = optional_hash(detail.get("containerID"))
        result[kind] = row
    return result


def status_projection(status):
    projected = {}
    for field in CONTAINER_STATUS_FIELDS:
        statuses = status.get(field, [])
        require(isinstance(statuses, list) and len(statuses) <= MAX_ITEMS, "invalid_container_statuses")
        rows = []
        for container in statuses:
            require(isinstance(container, dict), "invalid_container_status")
            for key in ("ready", "started"):
                require(container.get(key) is None or type(container[key]) is bool, "invalid_container_readiness")
            restarts = container.get("restartCount")
            require(restarts is None or type(restarts) is int and restarts >= 0, "invalid_restart_count")
            rows.append({"name": identifier(container.get("name")), "ready": container.get("ready"),
                         "started": container.get("started"), "restart_count": restarts,
                         "container_id_sha256": optional_hash(container.get("containerID")),
                         "image_id_sha256": optional_hash(container.get("imageID")),
                         "image_digest": image_digest(container.get("imageID")),
                         "state": container_state(container.get("state", {})),
                         "last_state": container_state(container.get("lastState", {}))})
        projected[field] = sorted(rows, key=lambda row: row["name"])
    return projected


def pod_identity(pod, component):
    meta, spec, status = pod["metadata"], pod["spec"], pod.get("status", {})
    containers = status_projection(status)
    conditions = status.get("conditions", [])
    require(isinstance(conditions, list) and all(isinstance(c, dict) for c in conditions)
            and len(conditions) <= MAX_ITEMS, "invalid_pod_conditions")
    terminal_phase = status.get("phase") in ("Failed", "Succeeded")
    contradiction = terminal_phase and (
        any(c["ready"] is True or "running" in c["state"] for rows in containers.values() for c in rows)
        or any(c.get("type") in READY_CONDITIONS and c.get("status") == "True" for c in conditions))
    # A terminal phase is only a control-plane classification. Empty statuses,
    # ContainerStatusUnknown, and exit 137 are not runtime-cleanup evidence.
    terminal = terminal_phase and not contradiction
    statuses = [c for c in status.get("containerStatuses", []) if c.get("name") == component]
    target = statuses[0] if len(statuses) == 1 else {}
    ready = (not meta.get("deletionTimestamp") and status.get("phase") == "Running" and
             any(c.get("type") == "Ready" and c.get("status") == "True" for c in status.get("conditions", []))
             and target.get("ready") is True and "running" in target.get("state", {})
             and bool(target.get("containerID")) and bool(target.get("imageID")))
    reason = status.get("reason")
    require(reason is None or isinstance(reason, str), "invalid_pod_reason")
    owners = meta.get("ownerReferences", [])
    require(isinstance(owners, list) and len(owners) <= MAX_ITEMS, "invalid_pod_owners")
    owner_projection = [{"kind": o.get("kind") if o.get("kind") in ("ReplicaSet", "Deployment") else "OTHER",
                         "name": identifier(o["name"]) if o.get("name") else None,
                         "uid_sha256": opaque(o.get("uid")), "controller": o.get("controller") is True}
                        for o in owners]
    return {"name": identifier(meta["name"]), "uid_sha256": opaque(meta["uid"]),
            "node": identifier(spec.get("nodeName", "unscheduled")),
            "spec_sha256": digest(canonical(spec)), "ready": ready,
            "deleting": bool(meta.get("deletionTimestamp")),
            "deletion_timestamp_sha256": optional_hash(meta.get("deletionTimestamp")),
            "owners": sorted(owner_projection, key=lambda row: (row["kind"], row["uid_sha256"])),
            "owner_references_sha256": digest(canonical(owners)),
            "phase": status.get("phase") if status.get("phase") in ("Pending", "Running", "Succeeded", "Failed", "Unknown") else "UNRECOGNIZED",
            "reason": safe_reason(reason), "reason_sha256": optional_hash(reason),
            "classification": "CONTROL_PLANE_TERMINAL" if terminal else "ACTIVE_OR_UNRESOLVED",
            "terminal_state_contradiction": contradiction,
            "retired_runtime_termination": "NOT_VERIFIED" if terminal_phase else "NOT_APPLICABLE",
            "terminal_status_sha256": digest(canonical(status)) if terminal_phase else None,
            "container_statuses": containers,
            "readiness_conditions": sorted([{ "type": c.get("type"), "status": c.get("status")
                if c.get("status") in ("True", "False", "Unknown") else "UNRECOGNIZED"}
                for c in conditions if c.get("type") in DIAGNOSTIC_CONDITIONS], key=lambda c: c["type"]),
            "container_id_sha256": opaque(target["containerID"]) if target.get("containerID") else None,
            "image_id_sha256": opaque(target["imageID"]) if target.get("imageID") else None,
            "image_digest": image_digest(target.get("imageID")),
            "restart_count": target.get("restartCount", 0)}


PYTHON_PROBE = r'''
import hashlib, json, os, signal, stat, sys
from pathlib import Path
signal.signal(signal.SIGALRM, lambda *_: os._exit(124))
signal.alarm(45)
FILES = __FILES__
PACKAGES = __PACKAGES__
MAX_BYTES = 4 * 1024 * 1024
def file_receipt(name):
    fd = None
    try:
        p = Path(name)
        if not p.exists() and not p.is_symlink():
            return {"status":"MISSING"}
        before = p.lstat()
        if not stat.S_ISREG(before.st_mode) or str(p.resolve()) != name:
            return {"status":"UNSAFE"}
        if before.st_size > MAX_BYTES:
            return {"status":"OVERSIZE"}
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW)
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            return {"status":"CHANGED"}
        h = hashlib.sha256()
        length = 0
        while True:
            part = os.read(fd, 65536)
            if not part: break
            length += len(part)
            if length > MAX_BYTES: return {"status":"OVERSIZE"}
            h.update(part)
        after = os.fstat(fd)
        if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            return {"status":"CHANGED"}
        return {"status":"PRESENT","sha256":h.hexdigest(),"bytes":length}
    except FileNotFoundError:
        return {"status":"MISSING"}
    except Exception:
        return {"status":"READ_FAILED"}
    finally:
        if fd is not None: os.close(fd)
try:
    from importlib.metadata import version
except ImportError:
    try:
        from pkg_resources import get_distribution
        def version(name): return get_distribution(name).version
    except ImportError:
        def version(name): raise LookupError()
versions = {}
for package in PACKAGES:
    try: versions[package] = version(package)
    except Exception: versions[package] = "UNAVAILABLE"
machine = os.uname().machine
architecture = machine if machine in ("x86_64", "amd64", "aarch64", "arm64") else "OTHER"
print(json.dumps({"sources":{p:file_receipt(p) for p in FILES},"dependencies":versions,
                  "runtime":".".join(str(v) for v in sys.version_info[:3]), "architecture":architecture}))
'''
NODE_PROBE = r'''
const fs=require('fs'),crypto=require('crypto');
const files=__FILES__, packages=__PACKAGES__, limit=4*1024*1024;
function receipt(name) {
 let fd;
 try {
  const s=fs.lstatSync(name);
  if (!s.isFile() || s.isSymbolicLink() || fs.realpathSync(name)!==name) return {status:'UNSAFE'};
  if(s.size>limit) return {status:'OVERSIZE'};
  fd=fs.openSync(name,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);
  const opened=fs.fstatSync(fd);
  if(opened.ino!==s.ino || opened.dev!==s.dev) return {status:'CHANGED'};
  const hash=crypto.createHash('sha256'), buf=Buffer.alloc(65536); let total=0,n;
  while((n=fs.readSync(fd,buf,0,buf.length,null))>0) {
   total+=n; if(total>limit) return {status:'OVERSIZE'}; hash.update(buf.subarray(0,n));
  }
  const after=fs.fstatSync(fd);
  if(opened.size!==after.size || opened.mtimeMs!==after.mtimeMs || opened.ctimeMs!==after.ctimeMs) return {status:'CHANGED'};
  return {status:'PRESENT',sha256:hash.digest('hex'),bytes:total};
 } catch(e) {return {status:e.code==='ENOENT'?'MISSING':'READ_FAILED'};}
 finally {if(fd!==undefined) fs.closeSync(fd);}
}
const versions={};
for(const name of packages) {
 try {
  const p=fs.realpathSync('/app/frontend/node_modules/'+name+'/package.json');
  if(!p.startsWith('/app/frontend/') || !fs.statSync(p).isFile() || fs.statSync(p).size>1048576) throw Error();
  versions[name]=JSON.parse(fs.readFileSync(p,'utf8')).version;
 } catch(e) {versions[name]='UNAVAILABLE';}
}
console.log(JSON.stringify({sources:Object.fromEntries(files.map(p=>[p,receipt(p)])),
 dependencies:versions,runtime:process.version,
 architecture:({x64:'x86_64',arm64:'arm64'})[process.arch]||'OTHER'}));
'''


def source_command(pod_name, component):
    identifier(pod_name)
    require(component in COMPONENTS, "unapproved_component")
    template = NODE_PROBE if component == "frontend" else PYTHON_PROBE
    source = template.replace("__FILES__", json.dumps(SOURCE_FILES[component])).replace(
        "__PACKAGES__", json.dumps(DEPENDENCIES[component]))
    prefix = ["exec", "-i", pod_name, "-c", component, "--"]
    if component == "frontend":
        return prefix + ["env", "-u", "NODE_OPTIONS", "-u", "NODE_PATH", "node", "-e", source], None
    python = "/app/backend/.venv/bin/python" if component in ("gateway", "ppt-master-runner") else "python3"
    return prefix + [python, "-I", "-B", "-"], source.encode("utf-8")


def validate_command(args, stdin):
    require(isinstance(args, list), "unapproved_command")
    if len(args) == 6 and args[0:2] == ["get", "deployment"]:
        require(args[2] in COMPONENTS and args[3:] == ["--show-managed-fields=false", "-o", "json"]
                and stdin is None, "unapproved_deployment_read")
        return
    if len(args) == 7 and args[0] == "get" and args[1] in ("pods", "replicasets"):
        require(args[2] == "--selector" and isinstance(args[3], str) and 0 < len(args[3]) <= 4096
                and re.fullmatch(r"[A-Za-z0-9_./=,!() -]+", args[3])
                and args[4:] == ["--show-managed-fields=false", "-o", "json"]
                and stdin is None, "unapproved_workload_list")
        return
    if len(args) >= 9 and args[:2] == ["exec", "-i"] and args[3] == "-c" and args[5] == "--":
        expected, source = source_command(args[2], args[4])
        require(args == expected and stdin == source, "unapproved_source_probe")
        return
    raise CheckFailure("unapproved_command")


def run_bounded(command, stdin, timeout=60, limit=MAX_OUTPUT):
    """Drain both pipes continuously; retain at most limit bytes, never stderr."""
    started = time.monotonic()
    env = dict(os.environ, KUBECONFIG="/etc/kubernetes/admin.conf")
    output = bytearray()
    counters = {"stdout_bytes": 0, "stderr_bytes": 0}
    exceeded, pipe_error = threading.Event(), threading.Event()
    try:
        proc = subprocess.Popen(command, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                start_new_session=(os.name == "posix"))
    except Exception:
        raise CheckFailure("command_start_failed")

    def drain(stream, key):
        try:
            while True:
                part = stream.read(65536)
                if not part:
                    break
                counters[key] += len(part)
                if key == "stdout_bytes" and len(output) < limit:
                    output.extend(part[:limit - len(output)])
                if counters[key] > limit:
                    exceeded.set()
        except Exception:
            pipe_error.set()
        finally:
            stream.close()

    def feed():
        try:
            proc.stdin.write(stdin)
            proc.stdin.close()
        except Exception:
            pipe_error.set()

    threads = [threading.Thread(target=drain, args=(proc.stdout, "stdout_bytes"), daemon=True),
               threading.Thread(target=drain, args=(proc.stderr, "stderr_bytes"), daemon=True)]
    if stdin is not None:
        threads.append(threading.Thread(target=feed, daemon=True))
    for thread in threads:
        thread.start()
    code = None
    while proc.poll() is None:
        if exceeded.is_set():
            code = "command_output_limit"
            break
        if time.monotonic() - started >= timeout:
            code = "command_timeout"
            break
        time.sleep(0.025)
    if code:
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        code = code or "command_cleanup_incomplete"
    for thread in threads:
        thread.join(timeout=0.5)
    if any(thread.is_alive() for thread in threads):
        code = code or "command_pipe_cleanup_incomplete"
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    evidence = dict(counters, retained_stdout_bytes=len(output),
                    elapsed_ms=int((time.monotonic() - started) * 1000))
    if exceeded.is_set():
        code = "command_output_limit"
    code = code or ("command_pipe_failed" if pipe_error.is_set() else None)
    code = code or ("command_failed" if proc.returncode else None)
    if code:
        raise CheckFailure(code, evidence)
    return bytes(output), evidence


class Kubectl:
    def __init__(self):
        self.steps = []
        self.lock = threading.Lock()

    def call(self, args, stdin=None):
        validate_command(args, stdin)
        step = {"operation": "source_digest" if args[0] == "exec" else "get_" + args[1],
                "selector_scoped": "--selector" in args, "status": "STARTED"}
        with self.lock:
            self.steps.append(step)
        try:
            raw, evidence = run_bounded(["kubectl", "--request-timeout=30s", "-n", NAMESPACE] + args,
                                       stdin, timeout=90 if args[0] == "exec" else 60)
            step.update(evidence)
            result = json.loads(raw)
            require(isinstance(result, dict), "invalid_json_object")
            step["status"] = "COMPLETE"
            return result
        except Exception as exc:
            step.update(status="FAILED", error=failure(exc))
            if isinstance(exc, CheckFailure):
                step.update(exc.evidence)
            raise


def deployment_get(kube, name):
    return kube.call(["get", "deployment", name, "--show-managed-fields=false", "-o", "json"])


def owned_pods(kube, deployment):
    selector = deployment_selector(deployment)
    def listed(resource):
        result = kube.call(["get", resource, "--selector", selector, "--show-managed-fields=false", "-o", "json"])
        rows = result.get("items")
        require(isinstance(rows, list) and len(rows) <= MAX_ITEMS, "workload_inventory_limit")
        return rows
    sets = listed("replicasets")
    owners = {r["metadata"]["uid"] for r in sets if controller_owner(r, deployment["metadata"]["uid"], "Deployment")}
    return [p for p in listed("pods") if any(controller_owner(p, uid, "ReplicaSet") for uid in owners)]


def clean_receipt(value, component):
    require(set(value) == {"sources", "dependencies", "runtime", "architecture"}, "invalid_source_receipt")
    require(isinstance(value["sources"], dict) and set(value["sources"]) == set(SOURCE_FILES[component]),
            "invalid_source_paths")
    sources = {}
    for path, row in value["sources"].items():
        require(isinstance(row, dict) and row.get("status") in SOURCE_STATUSES, "invalid_source_status")
        status = row["status"]
        if status == "PRESENT":
            require(set(row) == {"status", "sha256", "bytes"} and isinstance(row["sha256"], str)
                    and SHA.fullmatch(row["sha256"]) and type(row["bytes"]) is int
                    and 0 <= row["bytes"] <= 4 * 1024 * 1024, "invalid_source_hash")
            sources[path] = dict(row)
        else:
            require(set(row) == {"status"}, "unexpected_source_fields")
            sources[path] = {"status": status}
    require(isinstance(value["dependencies"], dict) and set(value["dependencies"]) == set(DEPENDENCIES[component]),
            "invalid_dependency_receipt")
    versions = {}
    for package, version in value["dependencies"].items():
        require(isinstance(version, str) and (version == "UNAVAILABLE" or
                re.fullmatch(r"[0-9][0-9A-Za-z.+_!-]{0,95}", version)), "invalid_dependency_version")
        versions[package] = version
    require(isinstance(value["runtime"], str) and re.fullmatch(r"v?[0-9]+(?:\.[0-9]+){1,3}", value["runtime"]),
            "invalid_language_version")
    require(value["architecture"] in ("x86_64", "amd64", "aarch64", "arm64", "OTHER"), "invalid_architecture")
    return {"sources": sources, "dependencies": versions, "runtime": value["runtime"],
            "architecture": value["architecture"]}


def sample_source(kube, pod, component):
    identity = pod_identity(pod, component)
    require(identity["ready"], "unready_probe_forbidden")
    result = {"identity": identity, "status": "FAILED"}
    try:
        args, stdin = source_command(identity["name"], component)
        receipt = clean_receipt(kube.call(args, stdin), component)
        result.update(receipt, status="COLLECTED",
                      source_fingerprint_sha256=digest(canonical(receipt["sources"])))
        present = sum(row["status"] == "PRESENT" for row in receipt["sources"].values())
        result["source_presence"] = {"present": present, "requested": len(receipt["sources"])}
        result["source_qualification"] = "HASHES_ONLY_REVIEW_REQUIRED"
        if component == "frontend" and not any("/src/" in p and v["status"] == "PRESENT"
                                              for p, v in receipt["sources"].items()):
            result["source_qualification"] = "SOURCE_UNAVAILABLE_REVIEW_REQUIRED"
        if any(v["status"] not in ("PRESENT", "MISSING") for v in receipt["sources"].values()):
            result["status"] = "INCOMPLETE"
    except Exception as exc:
        result["error"] = failure(exc)
    return result


def collect(kube, progress=None):
    progress = progress or (lambda value: None)
    report = {"schema": "portal-ppt-readonly-baseline.v3",
              "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "namespace": NAMESPACE, "components": {},
              "database_migration_metadata": "NOT_COLLECTED",
              "agent_host_source": "NOT_COLLECTED_NO_USER_SANDBOX_EXEC",
              "provider_requests": 0, "website_requests": 0,
              "production_mutation": False, "shared_storage_writes": False,
              "retired_runtime_termination": "NOT_VERIFIED",
              "environment_values": "NOT_EXPORTED",
              "source_scope": "FIXED_PATHS_ALL_OWNED_ACTIVE_READY_REPLICAS",
              "ready_for_build": False, "ready_for_rollout": False}
    before, pods_before = {}, {}
    for component in COMPONENTS:
        progress("inventory_before component=" + component)
        row = {"samples": {}, "errors": [], "stable": False}
        report["components"][component] = row
        try:
            deployment = deployment_get(kube, component)
            before[component] = deployment
            row["before"] = fingerprint(deployment)
            row["runtime_shape"] = runtime_shape(deployment, component)
            pods = owned_pods(kube, deployment)
            identities = [pod_identity(pod, component) for pod in pods]
            require(len({v["name"] for v in identities}) == len(identities), "duplicate_pod_identity")
            pods_before[component] = pods
            row["pods_before"] = sorted(identities, key=lambda p: p["name"])
            row["owned_count"] = len(pods)
            active = [p for p in identities if p["classification"] != "CONTROL_PLANE_TERMINAL"]
            row["active_count"] = len(active)
            row["terminal_count"] = len(identities) - len(active)
            row["retired_runtime_termination"] = "NOT_VERIFIED"
            row["ready_count"] = sum(p["ready"] for p in active)
            if any(p["image_digest"] == "UNRECOGNIZED" for p in active):
                row["errors"].append({"code": "unrecognized_running_image_identity"})
            if any(p["terminal_state_contradiction"] for p in identities):
                row["errors"].append({"code": "terminal_pod_state_contradiction"})
            if not converged(row["before"]) or len(active) != row["before"]["desired"] or row["ready_count"] != len(active):
                row["errors"].append({"code": "deployment_or_owned_pods_not_converged"})
        except Exception as exc:
            row["errors"].append(failure(exc))
    for component in COMPONENTS:
        row = report["components"][component]
        candidates = [p for p in pods_before.get(component, []) if pod_identity(p, component)["ready"]]
        progress("source_digests component=" + component + " ready_replicas=" + str(len(candidates)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_PARALLEL) as executor:
            futures = {executor.submit(sample_source, kube, pod, component): pod["metadata"]["name"] for pod in candidates}
            for done, future in enumerate(concurrent.futures.as_completed(futures), 1):
                name = futures[future]
                try:
                    row["samples"][name] = future.result()
                except Exception as exc:
                    row["samples"][name] = {"status": "FAILED", "error": failure(exc)}
                if done % 10 == 0 or done == len(candidates):
                    progress("source_digests component=" + component + " completed=" + str(done) + "/" + str(len(candidates)))
        fingerprints = {s.get("source_fingerprint_sha256") for s in row["samples"].values() if s.get("status") == "COLLECTED"}
        row["source_uniform"] = (len(fingerprints) == 1 and len(row["samples"]) == len(candidates)
                                 and bool(candidates) and
                                 all(s.get("status") == "COLLECTED" for s in row["samples"].values()))
        if len(fingerprints) > 1:
            row["errors"].append({"code": "replica_source_hashes_differ"})
        if any(s.get("status") != "COLLECTED" for s in row["samples"].values()):
            row["errors"].append({"code": "source_sampling_incomplete"})
    for component in COMPONENTS:
        progress("inventory_after component=" + component)
        row = report["components"][component]
        try:
            after = deployment_get(kube, component)
            row["after"] = fingerprint(after)
            pods = owned_pods(kube, after)
            row["pods_after"] = sorted([pod_identity(p, component) for p in pods], key=lambda p: p["name"])
            row["stable"] = (row.get("before") == row["after"] and
                             row.get("pods_before") == row["pods_after"] and converged(row["after"]))
            if not row["stable"]:
                row["errors"].append({"code": "deployment_or_pod_drift"})
        except Exception as exc:
            row["errors"].append(failure(exc))
    report["steps"] = list(kube.steps)
    complete = all(not r["errors"] and r["stable"] and r["source_uniform"] for r in report["components"].values())
    report["collection"] = "COMPLETE_REVIEW_REQUIRED" if complete else "INCOMPLETE_REVIEW_REQUIRED"
    return report


def summary(report):
    components = {}
    def groups(counts):
        # Divergent fleets can have thousands of distinct hashes; stdout stays
        # compact while the full private report retains every replica.
        rows = sorted(counts.items())
        return {"groups": dict(rows[:32]), "group_count": len(rows),
                "groups_omitted_from_stdout": max(0, len(rows) - 32)}
    for name, row in report.get("components", {}).items():
        counts = collections.Counter(s.get("source_fingerprint_sha256", "INCOMPLETE")
                                     for s in row.get("samples", {}).values())
        images = collections.Counter(p.get("image_digest") or "UNRECOGNIZED" for p in row.get("pods_after", [])
                                     if p.get("classification") != "CONTROL_PLANE_TERMINAL")
        architectures = collections.Counter(s.get("architecture", "UNKNOWN") for s in row.get("samples", {}).values())
        components[name] = {"stable": row.get("stable", False), "desired": row.get("before", {}).get("desired"),
                            "owned": row.get("owned_count"), "ready": row.get("ready_count"),
                            "active": row.get("active_count"), "terminal_count": row.get("terminal_count"),
                            "retired_runtime_termination": "NOT_VERIFIED",
                            "sampled": len(row.get("samples", {})), "errors": row.get("errors", []),
                            "images": row.get("after", row.get("before", {})).get("images", {}),
                            "image_identity_groups": groups(images), "source_hash_groups": groups(counts),
                            "observed_architectures": dict(architectures),
                            "source_uniform": row.get("source_uniform", False)}
    return {"schema": "portal-ppt-readonly-baseline.v3", "collection": report.get("collection"), "components": components,
            "retired_runtime_termination": "NOT_VERIFIED",
            "database_migration_metadata": "NOT_COLLECTED", "ready_for_build": False,
            "ready_for_rollout": False, "production_mutation": False}


def private_output(requested=None):
    parent = Path(requested).parent if requested else Path("/var/lib")
    require(parent.is_absolute(), "absolute_private_output_required")
    for path in [parent] + list(parent.parents):
        info = path.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                "unsafe_output_ancestor")
    if requested:
        output = Path(requested)
        require(output.is_absolute() and ".." not in output.parts and output.name not in ("", ".", ".."),
                "invalid_output_path")
        os.mkdir(str(output), 0o700)  # Exclusive: preserve every earlier result.
    else:
        output = Path(tempfile.mkdtemp(prefix="portal-ppt-baseline-", dir=str(parent)))
    info = output.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700,
            "private_output_mode_required")
    return output


def write_report(directory, report):
    raw = json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    require(len(raw) <= 16 * 1024 * 1024, "report_size_limit")
    path = directory / "REPORT.json"
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return str(path), digest(raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collect", action="store_true", help="explicitly collect read-only current baseline")
    parser.add_argument("--out", help="NEW absolute private output directory; never overwrites")
    args = parser.parse_args(argv)
    require(args.collect, "explicit_collect_required")
    require(os.name == "posix" and os.geteuid() == 0 and socket.gethostname().split(".")[0] == "datax-01",
            "root_on_datax01_required")
    output = private_output(args.out)
    kube = Kubectl()
    try:
        report = collect(kube, lambda value: print("baseline_progress=" + value, flush=True))
    except Exception as exc:
        report = {"schema": "portal-ppt-readonly-baseline.v3", "collection": "INCOMPLETE_REVIEW_REQUIRED",
                  "error": failure(exc), "steps": kube.steps, "production_mutation": False}
    path, sha = write_report(output, report)
    result = summary(report)
    result.update(report_path=path, report_sha256=sha, report_private=True)
    print(json.dumps(result, sort_keys=True, ensure_ascii=True), flush=True)
    return 0 if report["collection"] == "COMPLETE_REVIEW_REQUIRED" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(json.dumps({"collection": "INCOMPLETE_REVIEW_REQUIRED", "error": failure(error),
                          "production_mutation": False, "raw_error_exported": False}), flush=True)
        sys.exit(1)
