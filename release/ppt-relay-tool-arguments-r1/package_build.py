"""Freeze an independent build-only bundle from the already delivered candidate.

Only selected generic build infrastructure is reused. No Employee payload,
deployment patch, migration, frontend, provisioner or Skill is included.
"""

import argparse
import ast
import hashlib
import io
import json
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
INFRA = HERE.parent / "employee-sharing"
NAME = "deerflow-ppt-relay-tool-arguments-20260924-r1-build1"
CANDIDATE_SHA = "1f8c9281467d463e9fa6ba51363d06d6bcad222fd00fddd437df5768f11abd23"
BASE_ID = "sha256:a18f04d64152ea0b1272863e2dd57c2706573a4642bc06943da36a9518fb0856"
BASE_SOURCE = "be67018d948af473d32db24611b875fa3cb641855b472a94237c140e309892d7"
HELPERS = (
    "build_resources.py",
    "bounded_runc.py",
    "prepare_oci.py",
    "verify_oci_base.py",
    "verify_built_image.py",
)
HOST_FILES = ("build-candidate.py", "BUILD-README.md", "BUILD-VERIFICATION.md")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def freeze_infrastructure():
    source = (INFRA / "build_offline.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {
        "emit",
        "run",
        "unit_absent",
        "stop_owned_unit",
        "opt_path",
        "build_arguments",
        "json_receipt",
        "write_json",
    }
    methods = {"start_unit", "start", "phase", "stop"}
    lines = source.splitlines(keepends=True)
    selected = []
    found = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in functions:
            selected.append("".join(lines[node.lineno - 1 : node.end_lineno]))
            found.add(node.name)
    owner = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Builder"
    )
    body = []
    for node in owner.body:
        if isinstance(node, ast.FunctionDef) and node.name in methods:
            body.append("".join(lines[node.lineno - 1 : node.end_lineno]))
            found.add(node.name)
    if found != functions | methods:
        raise ValueError("Audited generic build helpers changed shape")
    header = '''"""Frozen generic isolated build controls; no production workflow code."""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from build_resources import (
    MEMORY_BYTES, READ_BPS, WRITE_BPS, TASKS, controller_roots,
    check_limits, check_leaf_io, executor_is_bounded, private_network,
)

'''
    content = (
        header
        + "\n\n".join(selected)
        + "\n\nclass IsolatedBuildkit:\n"
        + "\n\n".join(body)
        + "\n"
    )
    content = content.replace("employeesharing", "pptrelay").replace(
        "context:employee-", "context:ppt-relay-"
    )
    if "employee" in content or "LiveGuard" in content:
        raise ValueError("Non-infrastructure content leaked into frozen builder")
    result = {"isolated_buildkit.py": content.encode()}
    provenance = {
        "isolated_buildkit.py": {
            "source_sha256": sha(source.encode()),
            "selected_functions": sorted(functions),
            "selected_methods": sorted(methods),
        }
    }
    for name in HELPERS:
        raw = (INFRA / name).read_text(encoding="utf-8")
        rewritten = (
            raw.replace("employeesharing", "pptrelay")
            .replace("employee-bounded-config", "ppt-relay-bounded-config")
            .replace("employee source delta", "PPT relay source delta")
        )
        result[name] = rewritten.encode()
        provenance[name] = {"source_sha256": sha(raw.encode()), "namespace_only": True}
    result["BUILD-INFRA-PROVENANCE.json"] = (
        json.dumps(provenance, indent=2) + "\n"
    ).encode()
    return result


def package(candidate, audit, output):
    raw = candidate.read_bytes()
    if sha(raw) != CANDIDATE_SHA:
        raise ValueError("Candidate differs from immutable delivered R1")
    report_bytes = audit.read_bytes()
    report = json.loads(report_bytes)
    pods = report["gateway_pods"]
    if (
        report.get("baseline_match") is not True
        or report.get("stable_gateway_spec") is not True
        or report.get("production_workloads_mutated") is not False
        or report.get("source_mount_overlaps")
        or len(pods) != 1
        or pods[0]["image_id"] != BASE_ID
        or pods[0]["runtime"]["source_sha256"] != BASE_SOURCE
        or pods[0]["node"] == "datax-01"
        or not pods[0]["ready"]
        or pods[0]["deleting"]
    ):
        raise ValueError("Runtime audit does not authorize this base build")
    allow = {
        "codex_responses_adapter.py",
        "PATCH.json",
        "apply-candidate.py",
        "smoke.py",
        "Dockerfile.gateway",
        "collect-runtime.py",
        "README.md",
        "VERIFICATION.md",
    }
    payload = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as bundle:
        for member in bundle:
            basename = Path(member.name).name
            if basename in allow and member.isfile():
                if basename in payload or member.size > 1024**2:
                    raise ValueError("Unexpected candidate shape")
                payload[basename] = bundle.extractfile(member).read()
    if set(payload) != allow:
        raise ValueError("Incomplete immutable candidate")
    payload["runtime_audit.py"] = payload.pop("collect-runtime.py")
    payload["Dockerfile.gateway"] = payload["Dockerfile.gateway"].replace(
        b"FROM ppt-relay-gateway-base\n", b"FROM ppt-relay-gateway-base AS candidate\n"
    )
    baseline = {
        "audit_sha256": sha(report_bytes),
        "audit_utc": report["utc"],
        "source_image": pods[0]["image"],
        "source_image_id": BASE_ID,
        "source_sha256": BASE_SOURCE,
        "runtime": pods[0]["runtime"],
        "deployments": report["deployments"],
        "candidate_sha256": CANDIDATE_SHA,
        "production_rollout_ready": False,
    }
    payload["BASELINE.json"] = (json.dumps(baseline, indent=2) + "\n").encode()
    payload.update(freeze_infrastructure())
    for name in HOST_FILES:
        payload[name] = (HERE / name).read_text(encoding="utf-8").encode()
    for name, data in payload.items():
        if name.endswith(".py"):
            ast.parse(
                data,
                feature_version=(3, 12)
                if name in ("smoke.py", "codex_responses_adapter.py")
                else (3, 7),
            )
    payload["PACKAGE.SHA256"] = "".join(
        sha(data) + "  " + name + "\n" for name, data in sorted(payload.items())
    ).encode("ascii")
    archive = output / (NAME + ".tar.gz")
    checksum = output / (NAME + ".tar.gz.sha256")
    if archive.exists() or checksum.exists():
        raise ValueError("Immutable build bundle already exists")
    with (
        archive.open("xb") as stream,
        tarfile.open(fileobj=stream, mode="w:gz") as bundle,
    ):
        directory = tarfile.TarInfo(NAME)
        directory.type, directory.mode = tarfile.DIRTYPE, 0o755
        bundle.addfile(directory)
        for name, data in sorted(payload.items()):
            info = tarfile.TarInfo(NAME + "/" + name)
            info.size, info.mode = len(data), 0o644
            bundle.addfile(info, io.BytesIO(data))
    with checksum.open("x", encoding="ascii", newline="\n") as stream:
        stream.write(sha(archive.read_bytes()) + "  " + archive.name + "\n")
    print("ppt_relay_build_package=" + str(archive))
    print("production_rollout_ready=no")
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    package(args.candidate, args.audit, args.output)
