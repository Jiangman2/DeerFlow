"""Four sequential OFFLINE candidates. No image import, rollout or database writes."""

import argparse
import datetime
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import socket
import stat
import time
import uuid
from pathlib import Path

from apply_payload import rows, unique
from build_resources import check_limits, private_network
from isolated_buildkit import IsolatedBuildkit, build_arguments, emit, json_receipt, opt_path, run, write_json
from prepare_oci import fresh_directory
from resource_canary import canary_dockerfile
from verify_oci_base import DIGEST, document, regular

HERE = Path(__file__).resolve().parent
COMPONENTS = ("gateway", "runner")
GIB = 1024**3


def verify_package(root):
    names = set()
    info = regular(root / "PACKAGE.SHA256", 65536)
    if os.name == "posix" and (info.st_uid != 0 or info.st_mode & 0o022 or info.st_nlink != 1):
        raise ValueError("Unsafe package checksum ownership or permissions")
    for line in (root / "PACKAGE.SHA256").read_text(encoding="ascii").splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})  ([A-Za-z0-9_.-]+)", line)
        if not match or match[2] in names or match[2] in (".", "..", "PACKAGE.SHA256"):
            raise ValueError("Invalid package manifest")
        checksum, name = match.groups()
        info = regular(root / name, 2 * 1024**2)
        if os.name == "posix" and (info.st_uid != 0 or info.st_mode & 0o022 or info.st_nlink != 1):
            raise ValueError("Unsafe package file ownership or permissions: " + name)
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != checksum:
            raise ValueError("Package checksum mismatch: " + name)
        names.add(name)
    if not names or {p.name for p in root.iterdir()} != names | {"PACKAGE.SHA256"}:
        raise ValueError("Unexpected package contents")
    delta = document(root / "DELTA.json")
    if delta.get("database_migrations") != [] or delta.get("provisioner_changed") is not False or delta.get("csrf_middleware_changed") is not False:
        raise ValueError("Out-of-scope source delta")
    if len(delta["entries"]) != 2:
        raise ValueError("Unexpected delta count")
    for component in COMPONENTS:
        for row in rows(delta, component):
            data = (root / row["payload"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != row["after_sha256"]:
                raise ValueError("Delta payload checksum mismatch")
    return delta


def kube(args):
    return json.loads(run(["kubectl", "--request-timeout=8s"] + args, 15), object_pairs_hook=unique)


def mount_covers(container, targets):
    for mount in container.get("volumeMounts", []):
        mounted = mount.get("mountPath", "").rstrip("/")
        if mounted and any(target == mounted or target.startswith(mounted + "/") for target in targets):
            return True
    return False


def cluster_state(node, deployments, pods, baseline, delta):
    conditions = {row["type"]: row["status"] for row in node.get("status", {}).get("conditions", [])}
    if node["metadata"]["name"] != "datax-01" or any(conditions.get(key) != value for key, value in (("Ready", "True"), ("DiskPressure", "False"), ("MemoryPressure", "False"), ("PIDPressure", "False"))):
        raise RuntimeError("Build-node health gate failed")
    state, image_ids = {}, {}
    for name, expected in baseline["deployments"].items():
        current = deployments[name]
        containers = current["spec"]["template"]["spec"]["containers"]
        found = next((row for row in containers if row["name"] == expected["container"]), {})
        status = current.get("status", {})
        count = expected["replicas"]
        if found.get("image") != expected["image"] or found.get("imagePullPolicy") != "Never" or current["spec"].get("replicas", 1) != count or status.get("observedGeneration") != current["metadata"]["generation"] or any(status.get(key, 0) != count for key in ("replicas", "readyReplicas", "availableReplicas", "updatedReplicas")):
            raise RuntimeError("Deployment baseline/convergence changed: " + name)
        component = expected.get("component")
        targets = [row["target"] for row in delta["entries"] if row["component"] == component]
        targets += list(baseline.get("protected_sources", {}).get(component, {}))
        selected = [pod for pod in pods if pod["metadata"].get("labels", {}).get("app") == name]
        if len(selected) != count:
            raise RuntimeError("Unexpected production Pod count: " + name)
        identities, ids = [], set()
        for pod in selected:
            container = next((c for c in pod["spec"]["containers"] if c["name"] == expected["container"]), {})
            cs = next((c for c in pod.get("status", {}).get("containerStatuses", []) if c["name"] == expected["container"]), {})
            match = re.search(r"sha256:[a-f0-9]{64}$", cs.get("imageID", ""))
            if pod["metadata"].get("deletionTimestamp") or pod.get("status", {}).get("phase") != "Running" or not cs.get("ready") or not match or container.get("image") != expected["image"] or mount_covers(container, targets):
                raise RuntimeError("Production Pod changed, masked or unready: " + name)
            if name == "gateway" and pod["spec"].get("nodeName") != "ecs-zdz-pro-0022":
                raise RuntimeError("Gateway must remain on ecs-zdz-pro-0022; build colocation forbidden")
            if name == "frontend" and pod["spec"].get("nodeName") not in ("datax-02", "ecs-zdz-pro-0015"):
                raise RuntimeError("Frontend scheduling baseline changed")
            ids.add(match.group())
            identities.append((pod["metadata"]["uid"], cs.get("containerID"), cs.get("restartCount", 0)))
        if len(ids) != 1:
            raise RuntimeError("Production replicas disagree on image identity: " + name)
        state[name] = {"spec_sha256": hashlib.sha256(json.dumps(current["spec"], sort_keys=True).encode()).hexdigest(), "generation": current["metadata"]["generation"], "pods": sorted(identities)}
        if component:
            image_ids[component] = next(iter(ids))
            if image_ids[component] != baseline["components"][component]["image_id"]:
                raise RuntimeError("Deployed Build2 image identity changed: " + name)
        if name == "provisioner":
            env = {e["name"]: e.get("value", "") for e in found.get("env", [])}
            allowed = set(env.get("SANDBOX_PASSTHROUGH_ENV_KEYS", "").split(","))
            if not {"NO_PROXY", "no_proxy", "DISABLE_BROWSER", "DISABLE_MCP_BROWSER", "CODEX_CONFIG_TOML", "CODEX_MODEL_CATALOG_JSON", "PPT_MASTER_ENTERPRISE_RELAY_REQUIRED"} <= allowed:
                raise RuntimeError("Provisioner passthrough repair must remain intact")
            for key, required in baseline.get("provisioner_allowlists_required", {}).items():
                if not set(required) <= set(env.get(key, "").split(",")):
                    raise RuntimeError("Captured Provisioner allowlist lost entries: " + key)
    return state, image_ids


def source_probe(package, component, pod, container):
    selected = rows(document(package / "DELTA.json"), component)
    expected = {row["target"]: row["before_sha256"] for row in selected}
    expected.update(document(package / "BASELINE.json").get("protected_sources", {}).get(component, {}))
    if component == "frontend":
        script = "const fs=require('fs'),c=require('crypto'),paths=" + json.dumps(list(expected)) + ";const o={};for(const p of paths){if(fs.existsSync(p)){const s=fs.lstatSync(p);if(!s.isFile()||s.size>2097152)throw Error('Unsafe source');o[p]=c.createHash('sha256').update(fs.readFileSync(p)).digest('hex');}else{o[p]=null;}}console.log(JSON.stringify(o));"
        command = ["node", "-e", script]
    else:
        script = "import hashlib,json,os,stat\npaths=" + repr(list(expected)) + "\nresult={}\nfor p in paths:\n if os.path.lexists(p):\n  s=os.lstat(p)\n  if not stat.S_ISREG(s.st_mode) or s.st_size>2097152: raise ValueError('Unsafe source')\n  with open(p,'rb') as stream: result[p]=hashlib.sha256(stream.read(2097153)).hexdigest()\n else: result[p]=None\nprint(json.dumps(result,sort_keys=True))"
        command = ["/app/backend/.venv/bin/python", "-B", "-c", script]
    actual = kube(["-n", "deer-flow", "exec", pod, "-c", container, "--"] + command)
    if actual != expected:
        raise RuntimeError("Live source does not match immutable baseline: " + component)


def archive_input(path):
    archive = opt_path(path.parent) / path.name
    info = regular(archive, 65 * GIB)
    sidecar = Path(str(archive) + ".sha256")
    checksum_info = regular(sidecar, 4096)
    if any(item.st_uid != 0 or item.st_mode & 0o022 or item.st_nlink != 1 for item in (info, checksum_info)):
        raise ValueError("Base and checksum must be root-owned, single-link, not group/world writable: " + str(archive))
    match = re.fullmatch(r"([a-f0-9]{64}) [ *](?:\./)?" + re.escape(archive.name) + r"\n?", sidecar.read_text(encoding="ascii"))
    if not match:
        raise ValueError("Invalid base checksum sidecar: " + str(sidecar))
    return archive, match.group(1)


class Builder(IsolatedBuildkit):
    def __init__(self, package, archives, output):
        self.package, self.archives, self.output = package, archives, output
        self.baseline = document(package / "BASELINE.json")
        self.delta = verify_package(package)
        self.token = "pptrelay" + uuid.uuid4().hex[:16]  # frozen controls require this owned namespace
        self.slice, self.cgroup = self.token + ".slice", "/" + self.token + ".slice"
        self.address = "unix://" + str(output / "buildkit.sock")
        self.units, self.roots = [], {}
        self.daemon_pid, self.device, self.phase_number = None, None, 0
        self.started, self.production_state = time.monotonic(), None
        self.binary = {name: shutil.which(name) for name in ("systemctl", "systemd-run", "buildkitd", "buildctl")}
        self.image_ids = {}

    def guard(self, *, source=False):
        verify_package(self.package)
        if time.monotonic() - self.started > 4 * 3600:
            raise RuntimeError("Overall four-hour candidate build deadline exceeded")
        root, opt = shutil.disk_usage("/"), shutil.disk_usage("/opt")
        if root.free < 30 * GIB or root.used / root.total >= 0.80 or opt.free < 100 * GIB:
            raise RuntimeError("Filesystem safety reserve breached")
        memory = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        if int(memory["MemAvailable"].split()[0]) * 1024 < 4 * GIB:
            raise RuntimeError("Available memory reserve breached")
        node = kube(["get", "node", "datax-01", "-o", "json"])
        deployments = {d["metadata"]["name"]: d for d in kube(["-n", "deer-flow", "get", "deployments"] + list(self.baseline["deployments"]) + ["-o", "json"])["items"]}
        pods = kube(["-n", "deer-flow", "get", "pods", "-l", "app in (gateway,ppt-master-runner,frontend,provisioner)", "-o", "json"])["items"]
        state, ids = cluster_state(node, deployments, pods, self.baseline, self.delta)
        if self.production_state is not None and state != self.production_state:
            raise RuntimeError("Production specification or Pod identity changed during build")
        if source or self.production_state is None:
            for name, expected in self.baseline["deployments"].items():
                component = expected.get("component")
                if component in COMPONENTS:
                    for pod in pods:
                        if pod["metadata"].get("labels", {}).get("app") == name:
                            source_probe(self.package, component, pod["metadata"]["name"], expected["container"])
        self.production_state, self.image_ids = state, ids
        if self.roots:
            check_limits(self.roots, self.cgroup, self.device)
        if self.daemon_pid:
            private_network(self.daemon_pid)

    def build(self):
        python = Path(os.sys.executable).resolve()
        results = {}
        fingerprint = hashlib.sha256((self.package / "PACKAGE.SHA256").read_bytes()).hexdigest()[:16]
        for component in COMPONENTS:
            entry = self.baseline["components"][component]
            archive, checksum = self.archives[component]
            identity = {"source_image": entry["image"], "source_image_id": entry.get("image_id", self.image_ids.get(component)), "archive_sha256": checksum}
            if not DIGEST.fullmatch(identity["source_image_id"]):
                raise ValueError("Missing audited runtime image identity")
            if entry.get("archive_sha256") and checksum != entry["archive_sha256"]:
                raise ValueError("Agent Host archive differs from immutable audit")
            identity_path = self.output / (component + "-identity.json")
            write_json(identity_path, identity)
            layout = self.output / (component + "-base")
            base = json_receipt(self.phase("stage-" + component, [python, "-B", self.package / "stage_base.py", "--archive", archive, "--output", layout, "--identity", identity_path]), "audited_oci_base")
            base_receipt = self.output / (component + "-base.json")
            write_json(base_receipt, base)
            def arguments(context, **kwargs):
                return build_arguments(self.binary["buildctl"], self.address, context, component, layout, base["manifest_digest"], self.cgroup, **kwargs)
            if component == "gateway":
                context = fresh_directory(self.output / "resource-canary")
                ready_token = "ppt_runtime_resource_ready_" + uuid.uuid4().hex
                (context / "Dockerfile.gateway").write_text(canary_dockerfile(ready_token), encoding="utf-8")
                self.phase("resource-canary", arguments(context) + ["--no-cache"], canary=ready_token)
                emit("build_executor_limits=PASS cpu=150% memory=12GiB opt_read=80MiBps opt_write=40MiBps parallelism=1")
            self.phase("source-check-" + component, arguments(self.package, stage="source-check"))
            image = entry["candidate_repository"] + ":20260928-ppt-preview-profile-r4-" + component + "-" + fingerprint
            partial = self.output / (component + ".candidate.oci.tar.partial")
            metadata = self.output / (component + "-build-metadata.json")
            self.phase("candidate-" + component, arguments(self.package, output=partial, image=image, metadata=metadata))
            result = json_receipt(self.phase("verify-" + component, [python, "-B", self.package / "verify_built_image.py", "--archive", partial, "--output", self.output / (component + "-candidate-check"), "--image", image, "--metadata", metadata, "--base", layout, "--base-receipt", base_receipt]), "audited_oci_base")
            self.guard(source=True)
            final = self.output / (component + ".candidate.oci.tar")
            if final.exists():
                raise RuntimeError("Refusing to replace candidate archive")
            partial.rename(final)
            with Path(str(final) + ".sha256").open("x", encoding="ascii") as stream:
                stream.write(result["archive_sha256"] + "  " + final.name + "\n")
            results[component] = result
            emit("candidate_component=PASS name=" + component + " image=" + image)
        return {"ppt_runtime_candidate_build": "PASS", "components": results, "package_manifest_sha256": hashlib.sha256((self.package / "PACKAGE.SHA256").read_bytes()).hexdigest(), "production_baseline_fingerprints": self.production_state, "ready_for_rollout": False, "images_imported": False, "production_workloads_mutated": False, "live_model_tested": False, "browser_acceptance": "REQUIRED", "network": "PRIVATE_DAEMON_ALL_RUN_NONE", "resource_slice": self.slice}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check-only", action="store_true")
    group.add_argument("--build-only-ack", choices=["YES"])
    args = parser.parse_args()
    if os.name != "posix" or os.geteuid() != 0 or platform.machine() not in ("aarch64", "arm64") or socket.gethostname().split(".")[0] != "datax-01":
        raise SystemExit("Run as root on ARM64 datax-01 only")
    os.umask(0o077)
    package, output = opt_path(HERE), opt_path(args.output)
    delta = verify_package(package)
    baseline = document(package / "BASELINE.json")
    if output.exists() or output.parent == Path("/opt") or package == output or package in output.parents or output in package.parents or not output.parent.is_dir():
        raise SystemExit("Choose a NEW nested output directory under a root-owned /opt parent")
    for name in ("systemctl", "systemd-run", "buildkitd", "buildctl", "kubectl", "findmnt", "ps"):
        if not shutil.which(name):
            raise SystemExit("Required local tool missing: " + name)
    if not (shutil.which("buildkit-runc") or shutil.which("runc")) or os.stat("/opt").st_dev == os.stat("/").st_dev:
        raise SystemExit("Local OCI runtime and separate /opt filesystem required")
    memory = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    if int(memory["MemAvailable"].split()[0]) * 1024 < 16 * GIB:
        raise SystemExit("At least 16 GiB available memory required")
    processes = run(["ps", "-eo", "comm=,args="])
    if re.search(r"(?m)^\s*(buildctl|nerdctl)\s|^\s*ctr\s.*\bimages\s+(import|export)\b", processes):
        raise SystemExit("Another image operation is active")
    archives, missing, input_errors = {}, [], []
    for component in COMPONENTS:
        path = Path(baseline["components"][component]["archive"])
        if output == path.parent or output in path.parents:
            raise SystemExit("Output overlaps a base archive")
        if not path.exists() or not Path(str(path) + ".sha256").exists():
            missing.append(component)
        else:
            try:
                archive, checksum = archive_input(path)
                expected = baseline["components"][component].get("archive_sha256")
                if expected and checksum != expected:
                    raise ValueError("Archive sidecar differs from immutable Agent Host audit: " + str(path))
                archives[component] = (archive, checksum)
            except (ValueError, OSError) as exc:
                input_errors.append({"component": component, "error": str(exc)})
    if input_errors:
        emit(json.dumps({"ppt_runtime_build_setup": "FAIL", "base_input_errors": input_errors, "missing_components": missing, "ready_for_build": False, "ready_for_rollout": False, "production_workloads_mutated": False}, sort_keys=True))
        raise SystemExit(1)
    builder = Builder(package, archives, output)
    builder.delta = delta
    builder.guard(source=True)
    if args.check_only:
        emit(json.dumps({"ppt_runtime_build_setup": "PASS", "bases_present": sorted(archives), "missing_components": missing, "ready_for_build": not missing, "resource_limits_live_verified": False, "production_workloads_mutated": False, "ready_for_rollout": False}, sort_keys=True))
        return
    if missing:
        raise SystemExit("Base archive/checksum missing; no automatic pull/export: " + ",".join(missing))
    import fcntl
    fd = os.open("/run/lock/deerflow-ppt-relay-build.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "r+") as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or info.st_nlink != 1:
            raise RuntimeError("Unsafe shared build lock")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        builder.guard(source=True)
        fresh_directory(output)
        def interrupted(signum, frame):
            raise RuntimeError("Build interrupted; only owned units will stop")
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, interrupted)
        try:
            builder.start()
            result = builder.build()
        except BaseException as exc:
            write_json(output / "BUILD-FAILED.json", {"error": str(exc), "ready_for_rollout": False, "production_workloads_mutated": False})
            raise
        finally:
            builder.stop()
        result["utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        write_json(output / "BUILD-RESULT.json", result)
        emit("ppt_runtime_candidate_build=PASS production_workloads_mutated=no images_imported=no ready_for_rollout=no")
        emit("build_result=" + str(output / "BUILD-RESULT.json"))


if __name__ == "__main__":
    main()
