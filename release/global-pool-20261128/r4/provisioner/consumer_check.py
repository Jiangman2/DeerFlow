"""Probe the mounted filesystems from inside the actual consuming container."""
import json
import os
import sys
import subprocess
import urllib.request
from pathlib import Path


def main():
    expected = json.loads(Path("/mnt/skills/.df-binding.json").read_text())
    roots = {"workspace", "uploads", "outputs", "custom"}
    if set(expected) not in (roots, roots | {"legacy"}):
        return 1
    mounts = {}
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        left, right = line.split(" - ", 1)
        fields = left.split()
        mounts[fields[4]] = (right.split()[0], fields[5].split(","))
    for name, identity in expected.items():
        skill = name in ("custom", "legacy")
        path = ("/mnt/skills/" if skill else "/mnt/user-data/") + name
        filesystem, flags = mounts[path]
        st = os.stat(path, follow_symlinks=False)
        if filesystem not in ("nfs", "nfs4") or ("ro" in flags) != skill or [st.st_dev, st.st_ino] != identity:
            return 1
    if "legacy" not in expected and os.path.lexists("/mnt/skills/legacy"):
        return 1
    probe = json.loads(sys.argv[1])
    if "exec" in probe:
        return subprocess.run(probe["exec"]["command"], check=False).returncode
    http = probe["httpGet"]
    if http.get("scheme", "HTTP") != "HTTP" or http.get("host") or http.get("httpHeaders"):
        return 1
    url = f"http://127.0.0.1:{int(http['port'])}{http['path']}"
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=3) as response:
        return int(response.status != 200)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyError, ValueError, OSError):
        sys.exit(1)
