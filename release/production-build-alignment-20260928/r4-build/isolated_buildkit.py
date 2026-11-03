"""Frozen generic isolated build controls; no production workflow code."""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from resource_canary import ExecutorCanary
from build_resources import (
    MEMORY_BYTES, READ_BPS, WRITE_BPS, TASKS, controller_roots,
    check_limits, check_leaf_io, executor_is_bounded, private_network,
)

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
        "context:ppt-relay-"
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


class IsolatedBuildkit:
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
        probe = ExecutorCanary(canary) if canary else None
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
                    if probe is not None:
                        probe.sample(reader, lambda: executor_is_bounded(
                            self.roots, self.cgroup, device=self.device
                        ))
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
                if probe is not None:
                    probe.verify()
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

