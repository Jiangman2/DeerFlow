"""Offline BUILD ONLY, host Python 3.7+. Never imports images or changes workloads."""

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

import runtime_audit as audit
from build_resources import check_limits, private_network
from isolated_buildkit import (
    IsolatedBuildkit,
    build_arguments,
    emit,
    json_receipt,
    opt_path,
    run,
    write_json,
)
from prepare_oci import fresh_directory
from verify_oci_base import document, regular

HERE = Path(__file__).resolve().parent
GIB = 1024**3
DEFAULT_BASE = Path(
    "/opt/deerflow-build-artifacts/20260923-channel-pdf-preview-r2/gateway.candidate.oci.tar"
)


def verify_package(root):
    manifest = regular(root / "PACKAGE.SHA256", 65536)
    if not manifest.st_size:
        raise ValueError("Empty package checksum manifest")
    names = set()
    for line in (root / "PACKAGE.SHA256").read_text(encoding="ascii").splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})  ([A-Za-z0-9_.-]+)", line)
        if not match or match[2] in names or match[2] in (".", "..", "PACKAGE.SHA256"):
            raise ValueError("Invalid package checksum manifest")
        checksum, name = match.groups()
        regular(root / name, 2 * 1024**2)
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != checksum:
            raise ValueError("Package checksum mismatch: " + name)
        names.add(name)
    actual = {p.name for p in root.iterdir() if p.is_file()}
    if actual != names | {"PACKAGE.SHA256"}:
        raise ValueError("Unexpected package files")
    for p in root.iterdir():
        if p.is_dir():
            # python -B avoids cache writes; reject even unexpected directories.
            raise ValueError("Unexpected package directory")
    patch = document(root / "PATCH.json")
    if patch["source_sha256"] != audit.EXPECTED_SOURCE:
        raise ValueError("Source baseline mismatch")
    if (
        hashlib.sha256((root / "codex_responses_adapter.py").read_bytes()).hexdigest()
        != patch["target_sha256"]
    ):
        raise ValueError("Target payload mismatch")


def inspect_cluster(node, deployments, pods, baseline):
    conditions = {
        c["type"]: c["status"] for c in node.get("status", {}).get("conditions", [])
    }
    for name, required in (
        ("Ready", "True"),
        ("DiskPressure", "False"),
        ("MemoryPressure", "False"),
        ("PIDPressure", "False"),
    ):
        if conditions.get(name) != required:
            raise RuntimeError("Build node not safe: " + name)
    for name, expected in baseline["deployments"].items():
        current = deployments[name]
        if audit.summary(current) != expected:
            raise RuntimeError("Audited production baseline changed: " + name)
        status = current.get("status", {})
        if (
            status.get("observedGeneration", 0) != current["metadata"]["generation"]
            or status.get("replicas", 0) != expected["replicas"]
            or status.get("availableReplicas", 0) != expected["replicas"]
        ):
            raise RuntimeError("Production deployment not converged: " + name)
    if len(pods) != baseline["deployments"]["gateway"]["replicas"] or not pods:
        raise RuntimeError("Gateway Pod count changed")
    identity = []
    for pod in pods:
        c = next(c for c in pod["spec"]["containers"] if c["name"] == "gateway")
        statuses = pod.get("status", {}).get("containerStatuses", [])
        s = next((s for s in statuses if s["name"] == "gateway"), {})
        if (
            pod["spec"].get("nodeName") != "ecs-zdz-pro-0022"
            or pod["metadata"].get("deletionTimestamp")
            or not s.get("ready")
            or pod.get("status", {}).get("phase") != "Running"
            or c["image"] != baseline["source_image"]
            or audit.overlaps(c)
            or not str(s.get("imageID")).endswith(baseline["source_image_id"])
        ):
            raise RuntimeError("Gateway colocated, changed, masked or unhealthy")
        identity.append((pod["metadata"]["uid"], s.get("containerID")))
    return sorted(identity)


def kube(arguments):
    return json.loads(run(["kubectl", "--request-timeout=10s"] + arguments, 20))


class Builder(IsolatedBuildkit):
    def __init__(self, package, archive, output):
        self.package, self.archive, self.output = package, archive, output
        self.baseline = document(package / "BASELINE.json")
        self.token = "pptrelay" + uuid.uuid4().hex[:16]
        self.slice = self.token + ".slice"
        self.cgroup = "/" + self.slice
        self.address = "unix://" + str(output / "buildkit.sock")
        self.units, self.roots = [], {}
        self.daemon_pid, self.device, self.phase_number = None, None, 0
        self.started, self.pod_identity = time.monotonic(), None
        self.binary = {
            n: shutil.which(n)
            for n in ("systemctl", "systemd-run", "buildkitd", "buildctl")
        }

    def guard(self, *, force_source_hash=False):
        verify_package(self.package)
        if time.monotonic() - self.started > 2 * 3600:
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
            raise RuntimeError("Available memory safety reserve breached")
        node = kube(["get", "node", "datax-01", "-o", "json"])
        deployments = {
            name: kube(["-n", "deer-flow", "get", "deployment", name, "-o", "json"])
            for name in self.baseline["deployments"]
        }
        pods = kube(
            ["-n", "deer-flow", "get", "pods", "-l", "app=gateway", "-o", "json"]
        )["items"]
        identity = inspect_cluster(node, deployments, pods, self.baseline)
        container = next(
            c
            for c in deployments["gateway"]["spec"]["template"]["spec"]["containers"]
            if c["name"] == "gateway"
        )
        if audit.overlaps(container):
            raise RuntimeError("Gateway source is covered by a live mount")
        if self.pod_identity is not None and identity != self.pod_identity:
            raise RuntimeError("Gateway restarted during candidate build")
        if force_source_hash or self.pod_identity is None:
            for pod in pods:
                actual = json.loads(
                    audit.kube(
                        [
                            "exec",
                            pod["metadata"]["name"],
                            "-c",
                            "gateway",
                            "--",
                            "/app/backend/.venv/bin/python",
                            "-B",
                            "-c",
                            audit.RUNTIME_PROBE,
                        ]
                    )
                )
                if actual != self.baseline["runtime"]:
                    raise RuntimeError("Gateway source/model/dependencies changed")
        self.pod_identity = identity
        if self.roots:
            check_limits(self.roots, self.cgroup, self.device)
        if self.daemon_pid:
            private_network(self.daemon_pid)

    def build(self):
        python = Path(os.sys.executable).resolve()
        raw = self.phase(
            "stage-gateway",
            [
                python,
                "-B",
                self.package / "prepare_oci.py",
                "--archive",
                self.archive,
                "--output",
                self.output / "gateway-base",
                "--delta",
                self.package / "BASELINE.json",
            ],
        )
        base = json_receipt(raw, "audited_oci_base")
        write_json(self.output / "gateway-base.json", base)
        context = fresh_directory(self.output / "resource-canary")
        (context / "Dockerfile.gateway").write_text(
            'FROM ppt-relay-gateway-base AS candidate\nRUN --network=none /app/backend/.venv/bin/python -c "import time; time.sleep(25)"\n',
            encoding="utf-8",
        )
        common = (self.binary["buildctl"], self.address)

        def arguments(context, **kw):
            return build_arguments(
                *common,
                context,
                "gateway",
                self.output / "gateway-base",
                base["manifest_digest"],
                self.cgroup,
                **kw,
            )

        self.phase("resource-canary", arguments(context) + ["--no-cache"], canary=True)
        emit(
            "build_executor_limits=PASS cpu=150% memory=12GiB opt_read=80MiBps opt_write=40MiBps parallelism=1"
        )
        fingerprint = hashlib.sha256(
            (self.package / "PACKAGE.SHA256").read_bytes()
        ).hexdigest()[:16]
        image = "deerflow.local/deer-flow-gateway:20260924-ppt-relay-r1-" + fingerprint
        partial = self.output / "gateway.candidate.oci.tar.partial"
        metadata = self.output / "gateway-build-metadata.json"
        self.phase(
            "candidate-gateway",
            arguments(self.package, output=partial, image=image, metadata=metadata),
        )
        raw = self.phase(
            "verify-gateway",
            [
                python,
                "-B",
                self.package / "verify_built_image.py",
                "--archive",
                partial,
                "--output",
                self.output / "gateway-candidate-check",
                "--image",
                image,
                "--metadata",
                metadata,
                "--base",
                self.output / "gateway-base",
                "--base-receipt",
                self.output / "gateway-base.json",
            ],
        )
        receipt = json_receipt(raw, "audited_oci_base")
        self.guard(force_source_hash=True)
        final = self.output / "gateway.candidate.oci.tar"
        if final.exists():
            raise RuntimeError("Refusing to replace candidate archive")
        partial.rename(final)
        with (self.output / (final.name + ".sha256")).open(
            "x", encoding="ascii"
        ) as stream:
            stream.write(receipt["archive_sha256"] + "  " + final.name + "\n")
        return {
            "ppt_relay_candidate_build": "PASS",
            "image": image,
            "image_receipt": receipt,
            "baseline": self.baseline,
            "ready_for_rollout": False,
            "images_imported": False,
            "production_workloads_mutated": False,
            "live_model_tested": False,
            "package_sha256": hashlib.sha256(
                (self.package / "PACKAGE.SHA256").read_bytes()
            ).hexdigest(),
            "network": "PRIVATE_DAEMON_ALL_RUN_NONE",
            "resource_slice": self.slice,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-archive", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--build-only-ack", choices=["YES"])
    args = parser.parse_args()
    if not args.check_only and args.build_only_ack != "YES":
        parser.error(
            "Build requires --build-only-ack YES; this NEVER authorizes rollout"
        )
    if (
        os.name != "posix"
        or os.geteuid() != 0
        or platform.machine() not in ("aarch64", "arm64")
        or socket.gethostname().split(".")[0] != "datax-01"
    ):
        raise SystemExit("Run as root on ARM64 datax-01 only")
    os.umask(0o077)
    package, output = opt_path(HERE), opt_path(args.output)
    archive = opt_path(args.base_archive.parent) / args.base_archive.name
    if (
        output.exists()
        or output.parent == Path("/opt")
        or package in output.parents
        or output == package
        or output in package.parents
        or output in archive.parents
    ):
        raise SystemExit("Choose a NEW nested output directory on /opt")
    if not output.parent.is_dir():
        raise SystemExit("Create the root-owned output parent directory first")
    verify_package(package)
    for name in (
        "systemctl",
        "systemd-run",
        "buildkitd",
        "buildctl",
        "kubectl",
        "findmnt",
        "ps",
    ):
        if not shutil.which(name):
            raise SystemExit("Required local tool missing: " + name)
    if not (shutil.which("buildkit-runc") or shutil.which("runc")):
        raise SystemExit("Required local OCI runtime missing")
    if os.stat("/opt").st_dev == os.stat("/").st_dev:
        raise SystemExit("/opt must be a separate filesystem")
    memory = dict(
        line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines()
    )
    if int(memory["MemAvailable"].split()[0]) * 1024 < 16 * GIB:
        raise SystemExit("At least 16 GiB available memory required")
    processes = run(["ps", "-eo", "comm=,args="])
    if re.search(
        r"(?m)^\s*(buildctl|nerdctl)\s|^\s*ctr\s.*\bimages\s+(import|export)\b",
        processes,
    ):
        raise SystemExit("Another image operation is active")
    missing = not archive.exists()
    if not missing:
        info = regular(archive, 65 * GIB)
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise SystemExit(
                "Base archive must be root-owned and not writable by others"
            )
    builder = Builder(package, archive, output)
    builder.guard(force_source_hash=True)
    if args.check_only:
        emit(
            json.dumps(
                {
                    "ppt_relay_build_setup": "PASS",
                    "base_archive": str(archive),
                    "base_archive_present": not missing,
                    "resource_limits_live_verified": False,
                    "ready_for_build": not missing,
                    "production_workloads_mutated": False,
                    "ready_for_rollout": False,
                },
                sort_keys=True,
            )
        )
        return
    if missing:
        raise SystemExit(
            "Base archive missing; do not export/pull automatically: " + str(archive)
        )
    import fcntl

    fd = os.open(
        "/run/lock/deerflow-ppt-relay-build.lock",
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(fd, "r+") as lock:
        info = os.fstat(lock.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_mode & 0o022
            or info.st_nlink != 1
        ):
            raise RuntimeError("Unsafe build lock")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        builder.guard(force_source_hash=True)
        fresh_directory(output)

        def interrupted(signum, frame):
            raise RuntimeError("Build interrupted; stopping owned build units only")

        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, interrupted)
        try:
            builder.start()
            result = builder.build()
        except BaseException as exc:
            write_json(
                output / "BUILD-FAILED.json",
                {"error": str(exc), "ready_for_rollout": False},
            )
            raise
        finally:
            builder.stop()
        result["utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        write_json(output / "BUILD-RESULT.json", result)
        emit(
            "ppt_relay_candidate_build=PASS production_workloads_mutated=no images_imported=no ready_for_rollout=no"
        )
        emit("build_result=" + str(output / "BUILD-RESULT.json"))


if __name__ == "__main__":
    main()
