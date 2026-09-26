"""Leaf I/O limits for our disposable BuildKit OCI RUN specs. Python 3.7+.

cgroup-v1 does not reliably inherit parent I/O throttles. Daemon, current helper
and the single OCI RUN each get one third of the budget. This does not claim a
v1 kernel attributes all buffered writeback to a cgroup. Production runtime
configurations are never rewritten.
"""

import json
import os
import re
import sys
from pathlib import Path

from build_resources import READ_BPS, WRITE_BPS
from verify_oci_base import document, regular


def amend(spec, settings):
    prefix = "/" + settings["slice"] + "/buildkit/"
    if not re.fullmatch(r"employeesharing[a-f0-9]{16}\.slice", settings["slice"]):
        raise ValueError("Invalid owned resource slice")
    path = spec.get("linux", {}).get("cgroupsPath", "")
    if not path.startswith(prefix) or not re.fullmatch(
        r"[a-zA-Z0-9_-]+", path[len(prefix) :]
    ):
        raise ValueError("OCI executor escaped the owned resource slice")
    major, minor = [int(item) for item in settings["device"].split(":")]
    block = spec["linux"].setdefault("resources", {}).setdefault("blockIO", {})
    for direction, ceiling in (("Read", READ_BPS), ("Write", WRITE_BPS)):
        key = "throttle" + direction + "BpsDevice"
        wanted = [{"major": major, "minor": minor, "rate": ceiling // 3}]
        if block.get(key) not in (None, [], wanted):
            raise ValueError("Conflicting OCI leaf I/O limits")
        block[key] = wanted
    return spec


def main(settings_path, arguments):
    settings = document(Path(settings_path))
    operation = next(
        (
            word
            for word in arguments
            if word
            in (
                "run",
                "create",
                "exec",
                "delete",
                "state",
                "kill",
                "list",
                "start",
                "version",
                "events",
                "ps",
                "pause",
                "resume",
                "update",
            )
        ),
        None,
    )
    if operation is None and arguments not in (["--version"], ["version"]):
        raise ValueError("Unsupported build runtime operation")
    if operation in ("run", "create"):
        if arguments.count("--bundle") != 1:
            raise ValueError("Expected one explicit BuildKit bundle")
        index = arguments.index("--bundle")
        bundle = Path(arguments[index + 1]).absolute()
        root = Path(settings["root"]).absolute()
        if root not in bundle.parents or ".." in bundle.parts:
            raise ValueError("Not a bundle in the owned isolated build root")
        config = bundle / "config.json"
        before = regular(config, 16 * 1024**2)
        if before.st_uid != 0 or before.st_nlink != 1:
            raise ValueError("Unsafe owned OCI config")
        spec = amend(document(config), settings)
        temporary = bundle / "employee-bounded-config.json"
        fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(spec, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(config))
    if operation == "update":
        raise ValueError("Build runtime resource updates are not permitted")
    os.execv(settings["runtime"], [settings["runtime"]] + arguments)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
