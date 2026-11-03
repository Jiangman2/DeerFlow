"""Read kernel-enforced limits for our unique build slice. Python 3.7+.

Fail closed if CPU, memory, PID or block-I/O controllers are not effective.
No writes to cgroups, existing services, node configuration or global limits.
"""

import os
from pathlib import Path
from resource_canary import bounded_leaf

CPU_RATIO = 1.5
MEMORY_BYTES = 12 * 1024**3
READ_BPS = 80 * 1024**2
WRITE_BPS = 40 * 1024**2
TASKS = 2048


def controller_roots(mounts):
    roots = {}
    for line in mounts.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        if fields[2] == "cgroup2":
            roots["unified"] = Path(fields[1])
        elif fields[2] == "cgroup":
            for item in fields[3].split(","):
                if item in ("cpu", "memory", "blkio", "pids"):
                    roots[item] = Path(fields[1])
    # A hybrid host's systemd hierarchy may be v2 while controllers remain v1.
    if all(key in roots for key in ("cpu", "memory", "blkio", "pids")):
        roots.pop("unified", None)
    return roots


def _bounded(value, ceiling):
    if not 0 < int(value) <= ceiling:
        raise RuntimeError("Build resource limit is absent or too high")


def check_limits(roots, cgroup, device):
    if (
        not cgroup.startswith("/pptrelay")
        or "/" in cgroup[1:]
        or not cgroup.endswith(".slice")
    ):
        raise ValueError("Not an owned top-level build slice")
    relative = cgroup.lstrip("/")
    if "unified" in roots:
        directory = roots["unified"] / relative
        quota, period = (directory / "cpu.max").read_text().split()
        _bounded(quota, int(period) * CPU_RATIO)
        _bounded((directory / "memory.max").read_text(), MEMORY_BYTES)
        _bounded((directory / "pids.max").read_text(), TASKS)
        rows = [
            line.split() for line in (directory / "io.max").read_text().splitlines()
        ]
        values = next(
            (
                dict(p.split("=", 1) for p in row[1:])
                for row in rows
                if row[0] == device
            ),
            {},
        )
        _bounded(values.get("rbps", "max"), READ_BPS)
        _bounded(values.get("wbps", "max"), WRITE_BPS)
        return "v2"
    if not all(key in roots for key in ("cpu", "memory", "blkio", "pids")):
        raise RuntimeError("Required build cgroup controllers missing")
    cpu = roots["cpu"] / relative
    _bounded(
        (cpu / "cpu.cfs_quota_us").read_text(),
        int((cpu / "cpu.cfs_period_us").read_text()) * CPU_RATIO,
    )
    memory = roots["memory"] / relative
    _bounded((memory / "memory.limit_in_bytes").read_text(), MEMORY_BYTES)
    if (memory / "memory.use_hierarchy").read_text().strip() != "1":
        raise RuntimeError("Memory limit does not cover build children")
    _bounded((roots["pids"] / relative / "pids.max").read_text(), TASKS)
    for direction, ceiling in (("read", READ_BPS), ("write", WRITE_BPS)):
        path = roots["blkio"] / relative / ("blkio.throttle.%s_bps_device" % direction)
        values = dict(line.split() for line in path.read_text().splitlines())
        _bounded(values.get(device, "0"), ceiling)
    return "v1"


def process_paths(text):
    result = {}
    for line in text.splitlines():
        _, controllers, path = line.split(":", 2)
        for controller in controllers.split(","):
            result[controller or "unified"] = path
    return result


def check_leaf_io(roots, cgroup, device):
    """Do not assume v1 parent throttle applies to service or executor leaves."""
    if not cgroup.startswith("/pptrelay") or ".." in cgroup.split("/"):
        raise ValueError("Not an owned I/O leaf")
    relative = cgroup.lstrip("/")
    if "unified" in roots:
        rows = [
            line.split()
            for line in (roots["unified"] / relative / "io.max")
            .read_text()
            .splitlines()
        ]
        values = next(
            (
                dict(p.split("=", 1) for p in row[1:])
                for row in rows
                if row[0] == device
            ),
            {},
        )
        bounded_leaf(values.get("rbps", "missing"), READ_BPS // 3, cgroup, device, "read", "io")
        bounded_leaf(values.get("wbps", "missing"), WRITE_BPS // 3, cgroup, device, "write", "io")
    else:
        for direction, ceiling in (("read", READ_BPS), ("write", WRITE_BPS)):
            path = (
                roots["blkio"] / relative / ("blkio.throttle.%s_bps_device" % direction)
            )
            values = dict(line.split() for line in path.read_text().splitlines())
            bounded_leaf(values.get(device, "missing"), ceiling // 3, cgroup, device, direction, "blkio")


def executor_is_bounded(roots, cgroup, proc=Path("/proc"), device=None):
    """Observe a real RUN PID in EVERY limited controller, not just daemon PID."""
    primary = roots.get("unified", roots.get("cpu"))
    if primary is None:
        return False
    prefix = cgroup + "/buildkit/"
    for path in (primary / cgroup.lstrip("/") / "buildkit").glob("*/cgroup.procs"):
        try:
            for pid in path.read_text().split():
                mapping = process_paths((proc / pid / "cgroup").read_text())
                required = (
                    ("unified",)
                    if "unified" in roots
                    else ("cpu", "memory", "blkio", "pids")
                )
                if all(mapping.get(key, "").startswith(prefix) for key in required):
                    if device:
                        check_leaf_io(
                            roots, mapping.get("unified", mapping.get("blkio")), device
                        )
                    return True
        except FileNotFoundError:
            continue  # a short-lived process may finish during sampling
    return False


def private_network(pid, proc=Path("/proc")):
    own = os.readlink(str(proc / "self/ns/net"))
    other = os.readlink(str(proc / str(pid) / "ns/net"))
    interfaces = [
        line.split(":", 1)[0].strip()
        for line in (proc / str(pid) / "net/dev").read_text().splitlines()[2:]
    ]
    if own == other or interfaces != ["lo"]:
        raise RuntimeError("BuildKit daemon is not in loopback-only private network")
