"""One binding for one fresh slot. Kubelet resolves and pins all source subPaths."""
import ctypes
import json
import os
from pathlib import Path
import signal
import time
from contextlib import ExitStack


libc = ctypes.CDLL(None, use_errno=True)
BIND = 4096
DETACH = 2
stopping = False


def stop(_signal, _frame):
    global stopping
    stopping = True


def mountinfo():
    rows = {}
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        left, right = line.split(" - ", 1)
        a, b = left.split(), right.split()
        rows[a[4]] = (a[5].split(","), a[6:], b[0])
    return rows


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    rows = mountinfo()
    for root in ("/stage-data", "/stage-skills"):
        if not any(x.startswith("shared:") for x in rows[root][1]):
            raise RuntimeError("Staging volume does not propagate mounts")
    names = ["workspace", "uploads", "outputs", "custom"]
    if "/assigned/legacy" in rows:
        names.append("legacy")
    bound = []
    receipt = Path("/stage-skills/.df-binding.json")
    try:
        identities = {}
        with ExitStack() as stack:
            for name in names:
                skill = name in {"custom", "legacy"}
                source = Path("/assigned", name)
                target = Path("/stage-skills" if skill else "/stage-data", name)
                options, _, filesystem = rows[str(source)]
                if filesystem not in {"nfs", "nfs4"} or ("ro" in options) != skill:
                    raise RuntimeError("Unexpected source mount for " + name)
                if str(target) in rows:
                    raise RuntimeError("Target is already mounted")
                if skill:
                    target.mkdir(mode=0o755, exist_ok=True)
                def directory_fd(path):
                    fd = os.open(path, os.O_PATH | os.O_NOFOLLOW | os.O_DIRECTORY | os.O_CLOEXEC)
                    stack.callback(os.close, fd)
                    return f"/proc/self/fd/{fd}".encode()
                if libc.mount(directory_fd(source), directory_fd(target), None, ctypes.c_ulong(BIND), None):
                    raise OSError(ctypes.get_errno(), "Bind failed for " + name)
                mount_fd = os.open(target, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
                bound.append((name, mount_fd))
                if not source.samefile(target) or ("ro" in mountinfo()[str(target)][0]) != skill:
                    raise RuntimeError("Mount verification failed for " + name)
                stat = source.stat()
                identities[name] = [stat.st_dev, stat.st_ino]
        tmp = receipt.with_suffix(".tmp")
        with tmp.open("x") as output:
            json.dump(identities, output)
        tmp.chmod(0o444)
        tmp.replace(receipt)
        print("ASSIGNED_DIRECTORIES_BOUND", flush=True)
        Path("/tmp/df-assignment-bound").touch()
        while not stopping:
            time.sleep(0.5)
    finally:
        receipt.unlink(missing_ok=True)
        Path("/tmp/df-assignment-bound").unlink(missing_ok=True)
        for name, fd in reversed(bound):
            try:
                if libc.umount2(f"/proc/self/fd/{fd}".encode(), DETACH):
                    print("UNMOUNT_FAILED", name, ctypes.get_errno(), flush=True)
            finally:
                os.close(fd)


if __name__ == "__main__":
    main()
