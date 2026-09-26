"""BUILD-ONLY controller for the audited employee candidate. Python 3.7+.

Requires root on ARM64 datax-01 with Gateway elsewhere and three PRE-EXPORTED
OCI archives on /opt. Never contacts a registry, imports images, patches a
workload or touches employee/database/Skill data. Only unique build-owned
systemd units and output files are changed. It does not authorize rollout.
"""

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
import subprocess
import sys
import time
import uuid
from pathlib import Path, PurePosixPath

from build_resources import (
    MEMORY_BYTES,
    READ_BPS,
    TASKS,
    WRITE_BPS,
    check_leaf_io,
    check_limits,
    controller_roots,
    executor_is_bounded,
    private_network,
)
from prepare_oci import fresh_directory
from effective_sources import LiveGuard
from verify_candidate import path_allowed, verify as verify_package
from verify_oci_base import document, regular

COMPONENTS = ("gateway", "frontend", "provisioner")
HERE = Path(__file__).absolute().parent
GIB = 1024**3


def emit(message):
    print(message, flush=True)


def run(arguments, timeout=30, *, allow_absent_unit=False):
    env = dict(os.environ, KUBECONFIG="/etc/kubernetes/admin.conf")
    for name in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        env.pop(name, None)
    result = subprocess.run(
        [str(arg) for arg in arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        env=env,
    )
    missing_unit = (
        allow_absent_unit
        and arguments[0:2] == ["systemctl", "show"]
        and "--property=LoadState" in arguments
        and result.stdout.strip() == b"not-found"
    )
    if result.returncode and not missing_unit:
        # Kubernetes/daemon diagnostics may contain configuration; no raw dump.
        raise RuntimeError(
            "Read/control command failed: %s exit=%s"
            % (arguments[0], result.returncode)
        )
    if len(result.stdout) > 16 * 1024**2:
        raise RuntimeError("Control command response exceeds bound")
    return result.stdout.decode("utf-8")


def unit_absent(unit):
    return (
        run(
            ["systemctl", "show", unit, "--property=LoadState", "--value"],
            allow_absent_unit=True,
        ).strip()
        == "not-found"
    )


def stop_owned_unit(unit):
    try:
        run(["systemctl", "stop", unit], 45)
    except (RuntimeError, subprocess.TimeoutExpired):
        # Short-lived units can be garbage-collected before cleanup. A failed
        # stop of an existing unit must never be accepted as successful cleanup.
        if not unit_absent(unit):
            raise


def kube(arguments):
    return json.loads(run(["kubectl", "--request-timeout=10s"] + arguments, 20))


def digest_json(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def protected_source_paths(package):
    """Call only after verifying the immutable package. These are image paths.

    Source evidence collected with exec observes mounted bytes, which need not
    equal the underlying OCI bytes. Neither a read-only mount nor an identical
    image ID proves that an image-only candidate will run its new source.
    """
    roots = {
        "gateway": "/app/backend",
        "frontend": "/app/frontend",
        "provisioner": "/app",
    }
    result = {}
    for component, root in roots.items():
        delta = document(package / (component + "-delta.json"))
        relatives = [entry["path"] for entry in delta["entries"]]
        relatives.extend(delta["anchors"])
        result[component] = {root + "/" + path_allowed(path) for path in relatives}
    # Frontend source is compiled. Protect the generated output as well as its
    # source inputs; a mounted old .next tree defeats an otherwise valid build.
    result["frontend"].update(("/app/frontend/.next", "/app/frontend/server.js"))
    return result


def reject_source_overlays(deployments, pods, protected, reviewed=None):
    """Fail closed on unplanned source mounts without reading their contents.

    Check both templates and admitted live Pods (including terminating Pods).
    No ConfigMap/Secret is read, removed, patched or implicitly trusted. An
    overlay-aware release needs separate reviewed provenance and rollback;
    only exact bindings just verified by LiveGuard may be supplied in reviewed.
    This is not a user-selectable ignore/override switch.
    """
    objects = []
    for deployment in deployments["items"]:
        component = deployment["metadata"]["name"]
        if component in protected:
            objects.append(
                ("Deployment", component, deployment["spec"]["template"]["spec"])
            )
    for pod in pods["items"]:
        component = pod["metadata"].get("labels", {}).get("app")
        if component in protected:
            objects.append(("live Pod", component, pod["spec"]))
    for kind, component, spec in objects:
        containers = [
            c for c in spec.get("containers", []) if c.get("name") == component
        ]
        if len(containers) != 1:
            raise RuntimeError(
                "Source mount audit requires one runtime container: " + component
            )
        for mount in containers[0].get("volumeMounts", []):
            raw = mount.get("mountPath")
            if (
                not isinstance(raw, str)
                or not raw.startswith("/")
                or raw.startswith("//")
                or any(char in raw for char in ("\\", "\0", "\n", "\r"))
                or ".." in raw.split("/")
            ):
                raise ValueError("Invalid source-audit mount path")
            path = PurePosixPath(raw)
            approved = (reviewed or {}).get(component, {}).get(raw)
            if approved is not None and (
                mount.get("name") == approved["volume_name"]
                and mount.get("subPath") == approved["sub_path"]
                and mount.get("readOnly") is True
                and not mount.get("subPathExpr")
            ):
                continue
            for source in sorted(protected[component]):
                target = PurePosixPath(source)
                if path == target or path in target.parents or target in path.parents:
                    raise RuntimeError(
                        "Source volume overlay requires review: %s component=%s mount=%s target=%s"
                        % (kind, component, path, target)
                    )


def inspect_cluster(node, deployments, pods, expected, hostname):
    conditions = {
        item["type"]: item["status"]
        for item in node.get("status", {}).get("conditions", [])
    }
    if conditions.get("Ready") != "True" or any(
        conditions.get(name) != "False"
        for name in ("DiskPressure", "MemoryPressure", "PIDPressure")
    ):
        raise RuntimeError("Build node is not healthy")
    identities = {}
    for name in COMPONENTS:
        selected = [d for d in deployments["items"] if d["metadata"]["name"] == name]
        if len(selected) != 1:
            raise RuntimeError("Required production deployment missing")
        deployment = selected[0]
        spec, status = deployment["spec"], deployment.get("status", {})
        containers = spec["template"]["spec"]["containers"]
        image = next((c["image"] for c in containers if c["name"] == name), None)
        replicas = spec.get("replicas", 1)
        if (
            image != expected[name]["source_image"]
            or replicas < 1
            or status.get("observedGeneration", 0)
            < deployment["metadata"]["generation"]
            or any(
                status.get(key, 0) != replicas
                for key in (
                    "replicas",
                    "updatedReplicas",
                    "readyReplicas",
                    "availableReplicas",
                )
            )
        ):
            raise RuntimeError("Production baseline unhealthy or changed: " + name)
        identities[name] = {
            "uid": deployment["metadata"]["uid"],
            "spec_sha256": digest_json(spec),
        }
    all_pods = pods.get("items", [])
    for name in COMPONENTS:
        ready = [
            s
            for p in all_pods
            if p["metadata"].get("labels", {}).get("app") == name
            and not p["metadata"].get("deletionTimestamp")
            for s in p.get("status", {}).get("containerStatuses", [])
            if s.get("name") == name and s.get("ready")
        ]
        if not ready or any(
            s.get("imageID", "").split("://")[-1].split("@")[-1]
            != expected[name]["source_image_id"]
            for s in ready
        ):
            raise RuntimeError(
                "Ready production container image identity changed: " + name
            )
    gateway_pods = [
        p for p in all_pods if p["metadata"].get("labels", {}).get("app") == "gateway"
    ]
    if not gateway_pods or any(
        p.get("spec", {}).get("nodeName") == hostname for p in gateway_pods
    ):
        # Terminating gateway Pods still occupy the failure domain.
        raise RuntimeError("Gateway colocation forbidden (including terminating Pods)")
    if not any(
        not p["metadata"].get("deletionTimestamp")
        and any(
            s.get("name") == "gateway" and s.get("ready")
            for s in p.get("status", {}).get("containerStatuses", [])
        )
        for p in gateway_pods
    ):
        raise RuntimeError("No Ready production Gateway Pod")
    return identities


def opt_path(path):
    value = Path(path).absolute()
    if not re.fullmatch(r"/opt/[A-Za-z0-9_./-]+", str(value)) or ".." in value.parts:
        raise ValueError("Build paths must be simple absolute descendants of /opt")
    for part in (value,) + tuple(value.parents):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != 0
                or info.st_mode & 0o022
            ):
                raise ValueError(
                    "Build directories must be root-owned, unlinked and not group/world writable"
                )
    return value


def build_arguments(
    buildctl,
    address,
    context,
    component,
    layout,
    manifest,
    cgroup,
    *,
    stage="candidate",
    output=None,
    image=None,
    metadata=None,
):
    arguments = [
        buildctl,
        "--addr",
        address,
        "build",
        "--progress=plain",
        "--frontend",
        "dockerfile.v0",
        "--local",
        "context=" + str(context),
        "--local",
        "dockerfile=" + str(context),
        "--oci-layout",
        component + "_base=" + str(layout),
        "--opt",
        "filename=Dockerfile." + component,
        "--opt",
        "platform=linux/arm64",
        "--opt",
        "context:employee-"
        + component
        + "-base=oci-layout://"
        + component
        + "_base@"
        + manifest,
        "--opt",
        "target=" + stage,
        "--opt",
        "force-network-mode=none",
        "--opt",
        "cgroup-parent=" + cgroup,
    ]
    if output is not None:
        arguments += [
            "--output",
            "type=oci,name=" + image + ",dest=" + str(output),
            "--metadata-file",
            str(metadata),
        ]
    return arguments


class Builder:
    def __init__(self, package, archives, output):
        self.package, self.archives, self.output = package, archives, output
        self.index = document(package / "CANDIDATE.json")
        self.expected = self.index["components"]
        self.token = "employeesharing" + uuid.uuid4().hex[:16]
        self.slice = self.token + ".slice"
        self.cgroup = "/" + self.slice
        self.address = "unix://" + str(output / "buildkit.sock")
        self.units, self.baseline, self.roots = [], None, {}
        self.daemon_pid, self.device, self.phase_number = None, None, 0
        self.started = time.monotonic()
        self.source_guard = None
        self.binary = {
            name: shutil.which(name)
            for name in ("buildctl", "buildkitd", "systemd-run", "systemctl")
        }

    def guard(self, *, force_source_hash=False):
        verify_package(self.package)
        overlay_path = self.package / "source-overlays.json"
        # A real verified package always has this file; synthetic unit fixtures
        # without it still take the original fail-closed image-only path.
        overlays = document(overlay_path) if overlay_path.exists() else None
        workloads = COMPONENTS
        if overlays is not None and overlays["report"] is not None:
            workloads = COMPONENTS + ("ppt-master-runner",)
            if self.source_guard is None:
                components = {
                    name: document(self.package / (name + "-delta.json"))
                    for name in COMPONENTS
                }
                self.source_guard = LiveGuard(overlays, components)
            elif self.source_guard.document != overlays:
                raise RuntimeError("Effective source package changed during build")
        elif self.source_guard is not None:
            raise RuntimeError("Effective source evidence removed during build")
        if time.monotonic() - self.started > 7 * 3600:
            raise RuntimeError("Overall build deadline exceeded")
        root, opt = shutil.disk_usage("/"), shutil.disk_usage("/opt")
        if (
            root.free < 30 * GIB
            or root.used / root.total >= 0.80
            or opt.free < 80 * GIB
        ):
            raise RuntimeError("Filesystem safety reserve breached")
        memory = dict(
            line.split(":", 1)
            for line in Path("/proc/meminfo").read_text().splitlines()
        )
        if int(memory["MemAvailable"].split()[0]) * 1024 < 4 * GIB:
            raise RuntimeError("Node available memory reserve breached")
        node = kube(["get", "node", "datax-01", "-o", "json"])
        deployments = kube(
            ["-n", "deer-flow", "get", "deployment", *workloads, "-o", "json"]
        )
        pods = kube(
            [
                "-n",
                "deer-flow",
                "get",
                "pods",
                "-l",
                "app in (" + ",".join(workloads) + ")",
                "-o",
                "json",
            ]
        )
        identity = inspect_cluster(
            node,
            deployments,
            pods,
            self.expected,
            "datax-01",
        )
        reviewed = None
        if self.source_guard is not None:
            reviewed = self.source_guard.check(
                deployments,
                pods,
                self.source_guard.audit.read,
                force_hash=force_source_hash,
            )
        reject_source_overlays(
            deployments, pods, protected_source_paths(self.package), reviewed
        )
        if self.baseline is not None and identity != self.baseline:
            raise RuntimeError("Production deployment changed during build")
        self.baseline = identity
        if self.roots:
            check_limits(self.roots, self.cgroup, self.device)
        if self.daemon_pid:
            private_network(self.daemon_pid)

    def start_unit(self, suffix, command, *, pipe=False, private=True):
        unit = self.token + suffix + ".service"
        if not unit_absent(unit):
            raise RuntimeError("Refusing to reuse an existing systemd unit")
        # Record BEFORE dispatch, including an uncertain systemd-run response.
        self.units.append(unit)
        arguments = [
            self.binary["systemd-run"],
            "--unit=" + unit,
            "--slice=" + self.slice,
            "--property=Type=exec",
            "--property=RuntimeMaxSec=8h",
            "--property=TimeoutStopSec=30s",
            "--property=KillMode=control-group",
            "--property=UMask=0077",
            "--property=Nice=10",
            "--property=TasksMax=2048",
            "--property=IOSchedulingClass=best-effort",
            "--property=IOSchedulingPriority=7",
        ]
        if private:
            arguments += ["--property=PrivateNetwork=yes"]
        if self.device:
            block = "/dev/block/" + self.device
            if "unified" in self.roots:
                keys = ("IOReadBandwidthMax", "IOWriteBandwidthMax")
            else:
                keys = ("BlockIOReadBandwidth", "BlockIOWriteBandwidth")
            arguments += [
                "--property=" + key + "=" + block + " " + str(limit // 3)
                for key, limit in zip(keys, (READ_BPS, WRITE_BPS))
            ]
        if pipe:
            arguments += ["--wait", "--pipe"]
        arguments += ["--"] + [str(arg) for arg in command]
        return unit, arguments

    def start(self):
        _, command = self.start_unit("hold", ["/bin/sleep", "infinity"])
        run(command)
        mounts = controller_roots(Path("/proc/mounts").read_text())
        source = run(["findmnt", "-n", "-o", "SOURCE", "-T", "/opt"]).strip()
        info = Path(source).stat()
        if not stat.S_ISBLK(info.st_mode):
            raise RuntimeError("/opt must use a directly identifiable block device")
        device = "%s:%s" % (os.major(info.st_rdev), os.minor(info.st_rdev))
        sys_device = Path("/sys/dev/block") / device
        if (sys_device / "partition").exists():
            device = (sys_device.resolve().parent / "dev").read_text().strip()
        if not re.fullmatch(r"[0-9]+:[0-9]+", device):
            raise RuntimeError("Unsupported /opt block device topology")
        self.device = device
        block = "/dev/block/" + device
        properties = [
            "CPUAccounting=yes",
            "CPUQuota=150%",
            "MemoryAccounting=yes",
            "MemoryMax=" + str(MEMORY_BYTES),
            "TasksAccounting=yes",
            "TasksMax=" + str(TASKS),
        ]
        if "unified" in mounts:
            properties += [
                "IOAccounting=yes",
                "IOReadBandwidthMax=" + block + " " + str(READ_BPS),
                "IOWriteBandwidthMax=" + block + " " + str(WRITE_BPS),
            ]
        else:
            properties += [
                "BlockIOAccounting=yes",
                "BlockIOReadBandwidth=" + block + " " + str(READ_BPS),
                "BlockIOWriteBandwidth=" + block + " " + str(WRITE_BPS),
            ]
        run(["systemctl", "set-property", "--runtime", self.slice] + properties)
        self.roots = mounts
        check_limits(self.roots, self.cgroup, self.device)
        runtime = shutil.which("buildkit-runc") or shutil.which("runc")
        if not runtime:
            raise RuntimeError(
                "No installed OCI runtime; no online installation permitted"
            )
        settings = self.output / "bounded-runtime.json"
        write_json(
            settings,
            {
                "slice": self.slice,
                "device": self.device,
                "root": str(self.output / "buildkit"),
                "runtime": runtime,
            },
        )
        wrapper = self.output / "bounded-runtime"
        # Paths have passed the strict /opt character allowlist. No user shell
        # fragments or image-controlled command strings enter this wrapper.
        import shlex

        wrapper.write_text(
            "#!/bin/sh\nexec "
            + " ".join(
                shlex.quote(str(p))
                for p in (
                    Path(sys.executable).resolve(),
                    "-B",
                    self.package / "bounded_runc.py",
                    settings,
                )
            )
            + ' "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(0o700)
        config = self.output / "buildkitd.toml"
        config.write_text(
            '[worker.oci]\nenabled = true\nsnapshotter = "overlayfs"\nmax-parallelism = 1\nnetworkMode = "host"\nbinary = '
            + json.dumps(str(wrapper))
            + "\n[worker.containerd]\nenabled = false\n",
            encoding="utf-8",
        )
        unit, command = self.start_unit(
            "daemon",
            [
                self.binary["buildkitd"],
                "--config",
                config,
                "--root",
                self.output / "buildkit",
                "--addr",
                self.address,
            ],
        )
        run(command)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = run(
                ["systemctl", "show", unit, "--property=ActiveState", "--value"]
            ).strip()
            if state in ("inactive", "failed"):
                raise RuntimeError(
                    "Isolated BuildKit exited; inspect its local unit journal"
                )
            pid = int(
                run(
                    ["systemctl", "show", unit, "--property=MainPID", "--value"]
                ).strip()
            )
            if pid:
                private_network(pid)
                check_leaf_io(self.roots, self.cgroup + "/" + unit, self.device)
                try:
                    run(
                        [
                            self.binary["buildctl"],
                            "--addr",
                            self.address,
                            "debug",
                            "workers",
                        ],
                        5,
                    )
                    self.daemon_pid = pid
                    emit(
                        "isolated_buildkit=READY network=LOOPBACK_ONLY resource_slice="
                        + self.slice
                    )
                    return
                except (RuntimeError, subprocess.TimeoutExpired):
                    pass
            time.sleep(1)
        raise RuntimeError("Isolated BuildKit startup deadline exceeded")

    def phase(self, label, command, *, canary=False):
        self.guard()
        self.phase_number += 1
        log = self.output / ("%02d-%s.log" % (self.phase_number, label))
        unit, arguments = self.start_unit(
            "phase" + str(self.phase_number), command, pipe=True
        )
        emit("build_phase=START name=" + label)
        seen_executor = False
        process = None
        try:
            with log.open("xb") as output, log.open("rb") as reader:
                process = subprocess.Popen(
                    arguments, stdout=output, stderr=subprocess.STDOUT
                )
                next_guard, next_notice = 0, 0
                while process.poll() is None:
                    now = time.monotonic()
                    if now >= next_guard:
                        self.guard()
                        next_guard = time.monotonic() + 10
                    if canary:
                        seen_executor |= executor_is_bounded(
                            self.roots, self.cgroup, device=self.device
                        )
                    # A very fast helper can finish before a sample; systemd
                    # still applies the same explicit per-service properties.
                    controller_root = self.roots.get("unified", self.roots.get("blkio"))
                    if (
                        controller_root
                        and (controller_root / self.cgroup.lstrip("/") / unit).exists()
                    ):
                        check_leaf_io(self.roots, self.cgroup + "/" + unit, self.device)
                    if now >= next_notice:
                        emit(
                            "build_phase=RUNNING name=%s elapsed=%ss log=%s"
                            % (label, int(now - self.started), log)
                        )
                        next_notice = now + 20
                    time.sleep(0.5)
                # Raw build log stays root-private. Short JSON helper receipts
                # are read explicitly by the caller; no environment dumps.
                if process.returncode:
                    raise RuntimeError(
                        "Build phase failed: %s exit=%s log=%s"
                        % (label, process.returncode, log)
                    )
                if canary and not seen_executor:
                    raise RuntimeError(
                        "No real RUN process observed inside all resource limits"
                    )
                reader.seek(0)
                result = reader.read(16 * 1024**2)
            self.guard()
            emit("build_phase=PASS name=" + label)
            return result
        finally:
            if process is not None and process.poll() is None:
                try:
                    stop_owned_unit(unit)
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)

    def build(self):
        receipts = {}
        python = Path(sys.executable).resolve()
        for component in COMPONENTS:
            raw = self.phase(
                "stage-" + component,
                [
                    python,
                    "-B",
                    self.package / "prepare_oci.py",
                    "--archive",
                    self.archives / (component + ".base.oci.tar"),
                    "--output",
                    self.output / (component + "-base"),
                    "--delta",
                    self.package / (component + "-delta.json"),
                ],
            )
            receipts[component] = json_receipt(raw, "audited_oci_base")
            write_json(self.output / (component + "-base.json"), receipts[component])
        # Always execute an uncached short canary before expensive source/build
        # work. /proc host sampling proves the executor joins the same limits.
        context = fresh_directory(self.output / "resource-canary")
        (context / "Dockerfile.gateway").write_text(
            'FROM employee-gateway-base AS candidate\nRUN --network=none /app/backend/.venv/bin/python -c "import time; time.sleep(25)"\n',
            encoding="utf-8",
        )
        args = build_arguments(
            self.binary["buildctl"],
            self.address,
            context,
            "gateway",
            self.output / "gateway-base",
            receipts["gateway"]["manifest_digest"],
            self.cgroup,
        )
        self.phase("resource-canary", args + ["--no-cache"], canary=True)
        emit(
            "build_executor_resource_limits=PASS cpu=150% memory=12GiB read=80MiBps write=40MiBps parallelism=1"
        )
        # All three source/anchor checks must pass before ANY candidate is built.
        for component in COMPONENTS:
            self.phase(
                "source-" + component,
                build_arguments(
                    self.binary["buildctl"],
                    self.address,
                    self.package,
                    component,
                    self.output / (component + "-base"),
                    receipts[component]["manifest_digest"],
                    self.cgroup,
                    stage="source-check",
                ),
            )
        fingerprint = hashlib.sha256(
            (self.package / "PACKAGE.SHA256").read_bytes()
        ).hexdigest()[:16]
        completed = {}
        for component in COMPONENTS:
            image = (
                "deerflow.local/deer-flow-"
                + component
                + ":employee-sharing-candidate-"
                + fingerprint
            )
            archive = self.output / (component + ".candidate.oci.tar.partial")
            metadata = self.output / (component + "-build-metadata.json")
            self.phase(
                "candidate-" + component,
                build_arguments(
                    self.binary["buildctl"],
                    self.address,
                    self.package,
                    component,
                    self.output / (component + "-base"),
                    receipts[component]["manifest_digest"],
                    self.cgroup,
                    output=archive,
                    image=image,
                    metadata=metadata,
                ),
            )
            raw = self.phase(
                "verify-" + component,
                [
                    python,
                    "-B",
                    self.package / "verify_built_image.py",
                    "--archive",
                    archive,
                    "--output",
                    self.output / (component + "-candidate-check"),
                    "--image",
                    image,
                    "--metadata",
                    metadata,
                    "--base",
                    self.output / (component + "-base"),
                    "--base-receipt",
                    self.output / (component + "-base.json"),
                ],
            )
            result = json_receipt(raw, "audited_oci_base")
            final = self.output / (component + ".candidate.oci.tar")
            if final.exists():
                raise RuntimeError("Candidate export already exists")
            archive.rename(final)
            with (self.output / (final.name + ".sha256")).open("x") as checksum:
                checksum.write(result["archive_sha256"] + "  " + final.name + "\n")
            completed[component] = result
        self.guard(force_source_hash=True)
        return {
            "employee_candidate_build": "PASS",
            "components": completed,
            "ready_for_rollout": False,
            "images_imported": False,
            "production_workloads_mutated": False,
            "package_sha256": hashlib.sha256(
                (self.package / "PACKAGE.SHA256").read_bytes()
            ).hexdigest(),
            "baseline": self.baseline,
            "resource_slice": self.slice,
            "network": "DAEMON_PRIVATE_AND_ALL_RUN_NONE",
            "effective_source_report_sha256": None
            if self.source_guard is None
            else self.source_guard.document["source_report_sha256"],
        }

    def stop(self):
        errors = []
        # Stop phase units first, then daemon. Keep hold/slice alive if child
        # teardown fails, so residual build work does not lose resource limits.
        for unit in reversed(self.units[1:]):
            try:
                stop_owned_unit(unit)
            except (RuntimeError, subprocess.TimeoutExpired):
                errors.append(unit)
        root = self.roots.get("unified", self.roots.get("cpu"))
        residual = False
        if root:
            for path in (root / self.cgroup.lstrip("/") / "buildkit").glob(
                "**/cgroup.procs"
            ):
                try:
                    residual |= bool(path.read_text().strip())
                except FileNotFoundError:
                    pass
        if errors or residual:
            raise RuntimeError(
                "Build cleanup incomplete; owned slice retained: " + self.slice
            )
        if self.units:
            stop_owned_unit(self.units[0])
            stop_owned_unit(self.slice)
        emit("isolated_buildkit=STOPPED build_evidence_retained=" + str(self.output))


def json_receipt(raw, field):
    # systemd-run may prefix/suffix service messages. Exactly one JSON receipt
    # with the expected marker is required, never infer success from log text.
    rows = []
    for line in raw.decode("utf-8").splitlines():
        if line.startswith("{"):
            value = json.loads(line)
            if isinstance(value, dict) and value.get(field) == "PASS":
                rows.append(value)
    if len(rows) != 1:
        raise RuntimeError("Expected exactly one verified build receipt")
    return rows[0]


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-archives", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--build-only-ack", choices=["YES"])
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Read-only setup/cluster check; starts no units, exports no images",
    )
    args = parser.parse_args()
    if not args.check_only and args.build_only_ack != "YES":
        parser.error("Building requires --build-only-ack YES")
    if (
        os.name != "posix"
        or os.geteuid() != 0
        or platform.machine() not in ("aarch64", "arm64")
        or socket.gethostname().split(".")[0] != "datax-01"
    ):
        raise SystemExit(
            "Use root on ARM64 datax-01 only; no production rollout occurs"
        )
    os.umask(0o077)
    package, archives, output = (
        opt_path(HERE),
        opt_path(args.base_archives),
        opt_path(args.output),
    )
    if (
        output.exists()
        or output.parent == Path("/opt")
        or output in (package, archives)
        or package in output.parents
        or archives in output.parents
    ):
        raise SystemExit("Choose a new nested output directory on /opt")
    verify_package(package)
    for tool in (
        "systemctl",
        "systemd-run",
        "buildkitd",
        "buildctl",
        "kubectl",
        "findmnt",
        "ps",
    ):
        if not shutil.which(tool):
            raise SystemExit("Required local tool missing: " + tool)
    if os.stat("/opt").st_dev == os.stat("/").st_dev:
        raise SystemExit("/opt must be a separate mounted filesystem")
    memory = dict(
        line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines()
    )
    if int(memory["MemAvailable"].split()[0]) * 1024 < 16 * GIB:
        raise SystemExit(
            "At least 16 GiB available host memory required before starting"
        )
    missing = []
    for component in COMPONENTS:
        path = archives / (component + ".base.oci.tar")
        if not path.exists() and not path.is_symlink():
            missing.append(component)
            continue
        info = regular(path, 65 * GIB)
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise SystemExit(
                "Base archives must be root-owned and not writable by others"
            )
    processes = run(["ps", "-eo", "comm=,args="])
    if re.search(
        r"(?m)^\s*(buildctl|nerdctl)\s|^\s*ctr\s.*\bimages\s+import\b", processes
    ):
        raise SystemExit("Another image build/import is active")
    if args.check_only:
        controller = Builder(package, archives, output)
        controller.guard()
        emit(
            json.dumps(
                {
                    "employee_build_setup": "PASS",
                    "base_archives_missing": missing,
                    "resource_limits_live_verified": False,
                    "images_built": False,
                    "ready_for_rollout": False,
                    "production_workloads_mutated": False,
                },
                sort_keys=True,
            )
        )
        return
    if missing:
        raise SystemExit(
            "Export these exact local bases before building: " + ",".join(missing)
        )
    # Only a task-owned lock, never the runtime or a production service lock.
    import fcntl

    fd = os.open(
        "/run/lock/deerflow-employee-build.lock",
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(fd, "w") as lock:
        info = os.fstat(lock.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_mode & 0o022
            or info.st_nlink != 1
        ):
            raise RuntimeError("Unsafe build lock file")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        builder = Builder(package, archives, output)
        builder.guard()
        fresh_directory(output)
        result = None

        def interrupted(signum, frame):
            raise RuntimeError("Build interrupted; stopping only owned build units")

        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGINT, interrupted)
        signal.signal(signal.SIGHUP, interrupted)
        try:
            builder.start()
            result = builder.build()
        except BaseException as exc:
            write_json(
                output / "BUILD-FAILED.json",
                {
                    "error": str(exc),
                    "type": type(exc).__name__,
                    "ready_for_rollout": False,
                },
            )
            raise
        finally:
            builder.stop()
        result["utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        write_json(output / "BUILD-RESULT.json", result)
        emit(
            "employee_candidate_build=PASS images_imported=no ready_for_rollout=no report="
            + str(output / "BUILD-RESULT.json")
        )


if __name__ == "__main__":
    main()
