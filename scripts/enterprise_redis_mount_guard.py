#!/usr/bin/env python3
"""Stage a fail-closed data-disk mount guard on isolated Redis nodes.

This does not restart services, uncordon the node, or create Redis storage.
Do not attempt a reboot until kubelet and containerd are enabled at boot.
"""

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List


PILOT_NODE = "ecs-zdz-pro-0012"
REVISION = "r4-truthful-observation-fields"
NODE_UUIDS = {
    "ecs-zdz-pro-0012": "c6fbc7b6-c6a7-4fc0-8e64-d8a229750ea0",
    "ecs-zdz-pro-0014": "d99ec92f-c427-4099-a938-e343c13de429",
    "ecs-zdz-pro-0016": "3c87df50-4b2a-4285-a3c1-a3ee9e355c00",
}
MOUNT = "/mnt/data"
MOUNT_UNIT = "mnt-data.mount"
DROPIN_NAME = "20-deerflow-data-mount.conf"
UNITS = ("containerd", "kubelet")
DROPIN = """[Unit]
RequiresMountsFor=/mnt/data
BindsTo=mnt-data.mount
After=mnt-data.mount
AssertPathIsMountPoint=/mnt/data
"""


def run(*argv: str, timeout: int = 20) -> str:
    result = subprocess.run(
        argv, check=True, universal_newlines=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
    )
    return result.stdout.strip()


def property_map(unit: str) -> Dict[str, str]:
    output = run(
        "systemctl", "show", unit,
        "-p", "ActiveState", "-p", "MainPID", "-p", "RequiresMountsFor",
        "-p", "BindsTo", "-p", "After", "-p", "UnitFileState",
    )
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


def dropin_path(unit: str) -> Path:
    return Path("/etc/systemd/system") / (unit + ".service.d") / DROPIN_NAME


def read_fstab(disk_uuid: str) -> bool:
    for line in Path("/etc/fstab").read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) < 4 or fields[0].startswith("#"):
            continue
        if fields[1] != MOUNT:
            continue
        return (
            fields[0] == "UUID=" + disk_uuid
            and fields[2] == "xfs"
            and "nofail" in fields[3].split(",")
        )
    return False


def preflight() -> Dict[str, object]:
    actual_node = socket.gethostname().split(".", 1)[0]
    if actual_node not in NODE_UUIDS:
        raise RuntimeError("unsupported node: " + actual_node)
    disk_uuid = NODE_UUIDS[actual_node]
    if os.geteuid() != 0:
        raise RuntimeError("root required")
    version = run("systemctl", "--version").splitlines()[0]
    match = re.match(r"systemd\s+(\d+)", version)
    if not match or int(match.group(1)) < 218:
        raise RuntimeError("unsupported systemd version: " + version)
    mount_fields = run("findmnt", "-rn", "-o", "SOURCE,FSTYPE", "-T", MOUNT).split()
    if mount_fields != ["/dev/vdb1", "xfs"]:
        raise RuntimeError("/mnt/data is not the expected independent XFS disk")
    if run("blkid", "-s", "UUID", "-o", "value", "/dev/vdb1") != disk_uuid:
        raise RuntimeError("data-disk UUID mismatch")
    if not read_fstab(disk_uuid):
        raise RuntimeError("/etc/fstab does not match the audited disk")
    root_config = run("containerd", "config", "dump", timeout=30)
    if not re.search(r"^root\s*=\s*['\"]?/mnt/data/containerd['\"]?\s*$", root_config, re.M):
        raise RuntimeError("containerd root is not /mnt/data/containerd")
    if run("systemctl", "is-active", MOUNT_UNIT) != "active":
        raise RuntimeError("data-disk mount unit is inactive")
    pids: Dict[str, str] = {}
    for unit in UNITS:
        props = property_map(unit)
        if props.get("ActiveState") != "active" or not props.get("MainPID", "").isdigit():
            raise RuntimeError(unit + " is not active")
        if props.get("UnitFileState") != "enabled":
            raise RuntimeError(unit + " is not enabled for boot: " + props.get("UnitFileState", "unknown"))
        if int(props["MainPID"]) <= 0:
            raise RuntimeError(unit + " has no main PID")
        path = dropin_path(unit)
        if path.exists() and path.read_text(encoding="utf-8") != DROPIN:
            raise RuntimeError("conflicting drop-in: " + str(path))
        pids[unit] = props["MainPID"]
    return {"node": actual_node, "disk_uuid": disk_uuid, "service_pids": pids}


def write_new_dropin(path: Path) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    descriptor, temporary = tempfile.mkstemp(prefix=".mount-guard-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(DROPIN)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        # Refuse a race with an operator-created drop-in instead of replacing it.
        os.link(temporary, path)
    finally:
        try:
            Path(temporary).unlink()
        except FileNotFoundError:
            pass
    return True


def verify_loaded(before: Dict[str, object]) -> None:
    expected_pids = before["service_pids"]
    assert isinstance(expected_pids, dict)
    for unit in UNITS:
        props = property_map(unit)
        if props.get("ActiveState") != "active" or props.get("MainPID") != expected_pids[unit]:
            raise RuntimeError(unit + " restarted or stopped during daemon-reload")
        if props.get("UnitFileState") != "enabled":
            raise RuntimeError(unit + " is no longer enabled for boot")
        if MOUNT not in props.get("RequiresMountsFor", "").split():
            raise RuntimeError(unit + " missing RequiresMountsFor")
        if MOUNT_UNIT not in props.get("BindsTo", "").split():
            raise RuntimeError(unit + " missing BindsTo")
        if MOUNT_UNIT not in props.get("After", "").split():
            raise RuntimeError(unit + " missing After")
        if dropin_path(unit).read_text(encoding="utf-8") != DROPIN:
            raise RuntimeError(unit + " drop-in changed unexpectedly")
    if run("systemctl", "is-active", MOUNT_UNIT) != "active":
        raise RuntimeError("mount stopped during daemon-reload")


def apply(before: Dict[str, object]) -> List[str]:
    created: List[Path] = []
    try:
        for unit in UNITS:
            path = dropin_path(unit)
            if write_new_dropin(path):
                created.append(path)
        run("systemctl", "daemon-reload", timeout=30)
        verify_loaded(before)
    except Exception:
        for path in created:
            if path.exists() and path.read_text(encoding="utf-8") == DROPIN:
                path.unlink()
        if created:
            run("systemctl", "daemon-reload", timeout=30)
        raise
    return [str(path) for path in created]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="install drop-ins; no service restart")
    parser.add_argument("--node", help="required with --apply; must equal local hostname")
    args = parser.parse_args()
    try:
        before = preflight()
        if args.apply and args.node != before["node"]:
            raise RuntimeError("--apply requires --node matching the local hostname")
        installed_before = [unit for unit in UNITS if dropin_path(unit).exists()]
        if installed_before and len(installed_before) != len(UNITS):
            raise RuntimeError("partial mount-guard installation: " + ",".join(installed_before))
        if installed_before and not args.apply:
            verify_loaded(before)
        result: Dict[str, object] = {
            "mount_guard": "PREFLIGHT_PASS",
            "revision": REVISION,
            **before,
            "installed_units_before": installed_before,
            "services_restarted": False,
            "kubernetes_state": "NOT_CHECKED",
            "redis_state": "NOT_CHECKED",
            "missing_disk_failure_test": "NOT_PERFORMED",
        }
        if args.apply:
            result["new_dropins"] = apply(before)
            result["installed_units_after"] = [unit for unit in UNITS if dropin_path(unit).exists()]
            result["mount_guard"] = "DROPINS_LOADED"
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"mount_guard": "FAIL", "error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
