"""Verify INTERNAL offline image inputs, without compiling/executing app code.

This host-side verifier supports Python 3.7+. Success does NOT authorize rollout.
It only reads the bounded flat package; it does not install, connect or mutate.
"""

import argparse
import base64
import hashlib
import json
import re
from pathlib import Path

from verify_oci_base import regular, unique_object

COUNTS = {"gateway": 46, "frontend": 33, "provisioner": 1}
CONTROLS = {
    "apply_image_delta.py",
    "apply_image_delta.mjs",
    "smoke_image_imports.py",
    "verify_oci_base.py",
    "verify_candidate.py",
}
FILES = (
    CONTROLS
    | {"Dockerfile." + name for name in COUNTS}
    | {name + "-delta.json" for name in COUNTS}
    | {"CANDIDATE.json", "README.txt"}
)
HEX = re.compile(r"[a-f0-9]{64}\Z")


def sha(content):
    return hashlib.sha256(content).hexdigest()


def read(root, name):
    path = root / name
    regular(path, 16 * 1024**2)
    return path.read_bytes()


def path_allowed(name):
    if (
        not isinstance(name, str)
        or any(c in name for c in ("\\", ":", "\0"))
        or any(p in ("", ".", "..") or p.endswith((" ", ".")) for p in name.split("/"))
    ):
        raise ValueError("Invalid candidate path")
    return name


def verify(root):
    root = Path(root).absolute()
    # Reject unexpected entries too: do not let an unmanifested override enter a
    # Docker build context. This package is deliberately flat and bounded.
    if {path.name for path in root.iterdir()} != FILES | {"PACKAGE.SHA256"}:
        raise ValueError("Unexpected or missing candidate package entries")
    checksums = {}
    for line in read(root, "PACKAGE.SHA256").decode("ascii").splitlines():
        parts = line.split("  ")
        if (
            len(parts) != 2
            or not HEX.fullmatch(parts[0])
            or parts[1] not in FILES
            or parts[1] in checksums
        ):
            raise ValueError("Invalid package checksum manifest")
        checksums[parts[1]] = parts[0]
    if set(checksums) != FILES:
        raise ValueError("Incomplete package checksum manifest")
    payload = {}
    for name, digest in checksums.items():
        payload[name] = read(root, name)
        if sha(payload[name]) != digest:
            raise ValueError("Package checksum mismatch: " + name)
    index = json.loads(payload["CANDIDATE.json"], object_pairs_hook=unique_object)
    if (
        index.get("schema") != 1
        or index.get("scope") != "OFFLINE_IMAGE_INPUTS_ONLY"
        or index.get("ready_for_rollout") is not False
        or index.get("images_built") is not False
        or index.get("production_workloads_mutated") is not False
        or set(index["components"]) != set(COUNTS)
    ):
        raise ValueError("Not an internal unbuilt image candidate")
    reports = None
    for component, count in COUNTS.items():
        raw = payload[component + "-delta.json"]
        delta = json.loads(raw, object_pairs_hook=unique_object)
        identity = index["components"][component]
        if (
            delta.get("schema") != 1
            or delta.get("scope") != "employee-sharing-image-candidate"
            or delta.get("component") != component
            or delta.get("ready_for_rollout") is not False
            or identity["files"] != count
            or len(delta["entries"]) != count
            or identity["delta_sha256"] != sha(raw)
            or identity["source_image"] != delta["source_image"]
            or identity["source_image_id"] != delta["source_image_id"]
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", identity["source_image_id"])
        ):
            raise ValueError("Candidate identity or component scope differs")
        current_reports = delta["source_report_sha256s"]
        if (
            len(current_reports) != 3
            or len(set(current_reports)) != 3
            or not all(HEX.fullmatch(value) for value in current_reports)
        ):
            raise ValueError("Invalid source evidence identities")
        if reports is not None and current_reports != reports:
            raise ValueError("Components use different source evidence")
        reports = current_reports
        seen = set()
        for entry in delta["entries"]:
            relative = path_allowed(entry["path"])
            allowed = (
                relative == "app.py"
                if component == "provisioner"
                else relative.startswith("src/") and relative.endswith((".ts", ".tsx"))
                if component == "frontend"
                else relative.startswith(("app/", "packages/harness/deerflow/"))
                and relative.endswith(".py")
            )
            if not allowed or relative.casefold() in seen:
                raise ValueError("Duplicate or out-of-scope delta path")
            seen.add(relative.casefold())
            if entry["source_sha256"] is not None and not HEX.fullmatch(
                entry["source_sha256"]
            ):
                raise ValueError("Invalid delta source checksum")
            content = base64.b64decode(entry["content_base64"], validate=True)
            if len(content) > 2 * 1024**2 or sha(content) != entry["target_sha256"]:
                raise ValueError("Delta payload checksum mismatch")
        for relative, digest in delta["anchors"].items():
            path_allowed(relative)
            if relative.casefold() in seen or not HEX.fullmatch(digest):
                raise ValueError("Invalid dependency anchor")
            seen.add(relative.casefold())
    return {
        "employee_candidate_inputs": "PASS",
        "files": COUNTS,
        "ready_for_rollout": False,
        "images_built": False,
        "production_workloads_mutated": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.root), sort_keys=True))
