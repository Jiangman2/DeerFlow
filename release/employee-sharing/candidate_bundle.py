"""Freeze reviewed deltas as OFFLINE IMAGE INPUTS, never a rollout package.

No image builds, network calls, production connections, dependency installation
or deployment mutations. Existing destinations are never overwritten. Source
bytes come from the pinned reviews, not an unfiltered working-tree snapshot.
"""

import argparse
import base64
import json
import re
import subprocess
from pathlib import Path

from apply_image_delta import LIMIT, safe_path, sha, unique_object
from effective_sources import from_report
from probe_gateway_delta import prepare as gateway_prepare, read_verified
from provisioner_delta import prepare as provisioner_prepare

HERE = Path(__file__).resolve().parent
COUNTS = {"gateway": 46, "frontend": 33, "provisioner": 1}
CONTROLS = (
    "apply_image_delta.py",
    "apply_image_delta.mjs",
    "smoke_image_imports.py",
    "verify_oci_base.py",
    "verify_candidate.py",
    "build_offline.py",
    "build_resources.py",
    "bounded_runc.py",
    "prepare_oci.py",
    "verify_built_image.py",
    "effective_sources.py",
    "source_overlay_plan.py",
    "collect-source-overlays.py",
    "BUILD-ONLY.md",
    "Dockerfile.gateway",
    "Dockerfile.frontend",
    "Dockerfile.provisioner",
)


def json_bytes(value):
    return (
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")


def compose_inputs(baseline, repo):
    baseline, repo = Path(baseline), Path(repo)
    lock = json.loads(
        (baseline / "SOURCE-BASELINE.json").read_bytes(),
        object_pairs_hook=unique_object,
    )
    if lock.get("schema") != 1 or set(lock["components"]) != set(COUNTS):
        raise ValueError("Unsupported source audit")
    # Verify ALL captured dependencies, even when they are not patched.
    for component, evidence in lock["components"].items():
        for relative, checksum in evidence["files"].items():
            read_verified(baseline / component, relative, checksum)

    modules, receipts = gateway_prepare(
        baseline, repo / "backend", reviewed_candidate=True
    )
    sources = {
        relative: source.encode("utf-8") for source, relative in modules.values()
    }
    gateway = [
        {
            **item,
            "content_base64": base64.b64encode(sources[item["path"]]).decode("ascii"),
        }
        for item in receipts
    ]
    frontend_result = subprocess.run(
        [
            "node",
            str(HERE / "export_frontend_delta.mjs"),
            str(baseline),
            str(repo / "frontend"),
        ],
        check=True,
        capture_output=True,
        encoding="utf-8",
        timeout=90,
    )
    frontend = json.loads(frontend_result.stdout, object_pairs_hook=unique_object)
    if frontend["image_id"] != lock["components"]["frontend"]["image_id"]:
        raise ValueError("Frontend image identity differs")
    _, target, receipt = provisioner_prepare(
        baseline, repo / "docker/provisioner/app.py"
    )
    provisioner = [
        {**receipt, "content_base64": base64.b64encode(target).decode("ascii")}
    ]
    result = {}
    for component, entries in (
        ("gateway", gateway),
        ("frontend", frontend["entries"]),
        ("provisioner", provisioner),
    ):
        evidence = lock["components"][component]
        paths = {item["path"] for item in entries}
        result[component] = {
            "schema": 1,
            "scope": "employee-sharing-image-candidate",
            "ready_for_rollout": False,
            "component": component,
            "source_image": evidence["image"],
            "source_image_id": evidence["image_id"].removeprefix("containerd://"),
            "source_report_sha256s": lock["report_sha256s"],
            "anchors": {
                name: value
                for name, value in evidence["files"].items()
                if name not in paths
            },
            "entries": sorted(entries, key=lambda item: item["path"]),
        }
    validate(result)
    return result


def validate(components):
    if set(components) != set(COUNTS):
        raise ValueError("Incomplete image components")
    for name, document in components.items():
        if (
            document.get("schema") != 1
            or document.get("scope") != "employee-sharing-image-candidate"
            or document.get("ready_for_rollout") is not False
            or document.get("component") != name
            or not re.fullmatch(
                r"sha256:[a-f0-9]{64}", document.get("source_image_id", "")
            )
        ):
            raise ValueError("Invalid candidate identity")
        reports = document["source_report_sha256s"]
        if (
            len(reports) != 3
            or len(set(reports)) != 3
            or not all(re.fullmatch(r"[a-f0-9]{64}", item) for item in reports)
        ):
            raise ValueError("Invalid source report identities")
        if len(document["entries"]) != COUNTS[name]:
            raise ValueError("Incomplete reviewed delta")
        seen = set()
        for entry in document["entries"]:
            relative = safe_path(entry["path"])
            if name == "frontend":
                allowed = relative.startswith("src/") and relative.endswith(
                    (".ts", ".tsx")
                )
            elif name == "gateway":
                allowed = relative.startswith(
                    ("app/", "packages/harness/deerflow/")
                ) and relative.endswith(".py")
            else:
                allowed = relative == "app.py"
            if not allowed or relative.casefold() in seen:
                raise ValueError("Duplicate or out-of-scope candidate path")
            seen.add(relative.casefold())
            for checksum in (entry["source_sha256"], entry["target_sha256"]):
                if checksum is not None and not re.fullmatch(r"[a-f0-9]{64}", checksum):
                    raise ValueError("Invalid candidate checksum")
            content = base64.b64decode(entry["content_base64"], validate=True)
            if len(content) > LIMIT or sha(content) != entry["target_sha256"]:
                raise ValueError("Candidate content checksum mismatch")
            if name != "frontend":
                compile(content, relative, "exec")
        for relative, checksum in document["anchors"].items():
            safe_path(relative)
            if relative.casefold() in seen or not re.fullmatch(
                r"[a-f0-9]{64}", checksum
            ):
                raise ValueError("Conflicting candidate dependency anchor")
            seen.add(relative.casefold())


def materialize(components, destination, *, overlay_report=None):
    validate(components)  # no destination or partial output before validation
    overlays = from_report(overlay_report, components)
    destination = Path(destination).absolute()
    for path in (destination, *destination.parents):
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("Linked candidate destination")
    if destination.exists():
        raise ValueError("Candidate destination already exists")
    payload = {
        component + "-delta.json": json_bytes(value)
        for component, value in components.items()
    }
    payload["source-overlays.json"] = json_bytes(overlays)
    for name in CONTROLS:
        path = HERE / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > LIMIT:
            raise ValueError("Unsafe build control input")
        payload[name] = path.read_bytes()
    payload["CANDIDATE.json"] = json_bytes(
        {
            "schema": 1,
            "scope": "OFFLINE_IMAGE_INPUTS_ONLY",
            "ready_for_rollout": False,
            "images_built": False,
            "production_workloads_mutated": False,
            "source_overlays_sha256": sha(payload["source-overlays.json"]),
            "components": {
                name: {
                    "files": len(value["entries"]),
                    "source_image": value["source_image"],
                    "source_image_id": value["source_image_id"],
                    "delta_sha256": sha(payload[name + "-delta.json"]),
                }
                for name, value in components.items()
            },
            "open_gates": [
                "exact_ARM64_image_builds",
                "overlay_aware_deployment_bootstrap_and_live_source_validation",
                "consistent_data_backup_and_safe_post_write_rollback",
                "SSO_WeKnora_multi_identity_acceptance",
                "cross_node_NFS_readonly_mounts",
                "ordinary_chat_PDF_PPT_channels_regression",
            ],
        }
    )
    payload["README.txt"] = (
        "INTERNAL BUILD INPUTS ONLY - NOT A PRODUCTION HOTFIX\n"
        "Validate with: python3 -B verify_candidate.py . (the package directory must remain immutable).\n"
        "No rollout/rollback scripts or image archives are included. No production writes are authorized by this bundle.\n"
        "See BUILD-ONLY.md: the builder requires explicitly prepared local bases, resource enforcement and no Gateway colocation.\n"
        "Use verify_oci_base.py to check each exported local OCI layout against its delta JSON before binding base contexts.\n"
        "A runtime image ID is not assumed to be a registry manifest digest. All selected local layer blobs must match their descriptors.\n"
        "Build in isolated BuildKit with data under /opt, CPU/I/O/memory/concurrency bounds, and no Gateway colocation.\n"
        "Every RUN disables networking. No pip/apt/pnpm installation is performed; missing base dependencies fail the build.\n"
        "Named base contexts MUST be supplied by a verified offline builder; do not permit fallback image pulls.\n"
        "Do not run apply_image_delta inside a live production container. A failed disposable build layer must be discarded.\n"
        "Source checks/imports/builds are not business acceptance. Do not restore old images/data over new publications, revocations or employee edits.\n"
    ).encode("utf-8")
    checksums = "".join(
        sha(content) + "  " + name + "\n" for name, content in sorted(payload.items())
    )
    destination.mkdir(
        mode=0o700
    )  # parent must already exist; never silently create a broad path
    for name, content in payload.items():
        with (destination / name).open("xb") as stream:
            stream.write(content)
    with (destination / "PACKAGE.SHA256").open("xb") as stream:
        stream.write(checksums.encode("ascii"))
    # This checks the completed bytes, not just the objects used to create them.
    from verify_candidate import verify

    verify(destination)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-overlays", type=Path)
    args = parser.parse_args()
    raw = None
    if args.source_overlays is not None:
        if (
            args.source_overlays.is_symlink()
            or args.source_overlays.stat().st_size > 16 * 1024**2
        ):
            raise ValueError("Unsafe source-overlay evidence")
        raw = args.source_overlays.read_bytes()
    destination = materialize(
        compose_inputs(args.baseline, args.repo), args.output, overlay_report=raw
    )
    print(
        json.dumps(
            {
                "candidate_inputs": str(destination),
                "ready_for_rollout": False,
                "images_built": False,
            }
        )
    )


if __name__ == "__main__":
    main()
