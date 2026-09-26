"""Compose only employee RO projection changes on the audited R16 provisioner.

This creates in-memory candidate bytes, not a rollout or deployment mutation.
Image/profile/proxy settings, health probes and unrelated handlers are retained.
"""

import hashlib
import json
from pathlib import Path

from gateway_delta import select_symbols, symbols

SELECTED = (
    "EMPLOYEE_SKILL_PROJECTION_ANNOTATION",
    "CreateSandboxRequest",
    "SandboxResponse",
    "_build_volumes",
    "_validate_employee_projection",
    "_build_volume_mounts",
    "_build_pod",
    "_assert_existing_sandbox_identity",
    "create_sandbox",
)


def prepare(baseline, candidate):
    from probe_gateway_delta import read_verified
    from source_baseline import _unique_object

    baseline = Path(baseline)
    lock = json.loads(
        (baseline / "SOURCE-BASELINE.json").read_text(encoding="utf-8"),
        object_pairs_hook=_unique_object,
    )["components"]["provisioner"]
    source = read_verified(
        baseline / "provisioner", "app.py", lock["files"].get("app.py")
    )
    source_sha = hashlib.sha256(source).hexdigest()
    review = json.loads(
        Path(__file__).with_name("provisioner-review.json").read_text(encoding="utf-8"),
        object_pairs_hook=_unique_object,
    )
    if (
        review.get("schema") != 1
        or review.get("scope") != "employee-sharing-only"
        or review.get("ready_for_rollout") is not False
        or set(review["reviewed_candidates"]) != {"app.py"}
    ):
        raise ValueError("Invalid provisioner candidate review")
    if lock["image_id"].removeprefix("containerd://") != review["source_image_id"]:
        raise ValueError("Provisioner production image differs from review")
    candidate = Path(candidate)
    local = read_verified(
        candidate.parent, candidate.name, review["reviewed_candidates"]["app.py"]
    )
    target = select_symbols(
        source.decode("utf-8"), local.decode("utf-8"), SELECTED, ()
    ).encode("utf-8")
    return (
        source,
        target,
        {
            "path": "app.py",
            "component": "provisioner",
            "source_image": lock["image"],
            "source_image_id": lock["image_id"],
            "source_sha256": source_sha,
            "target_sha256": hashlib.sha256(target).hexdigest(),
            "candidate_sha256": hashlib.sha256(local).hexdigest(),
            "selected_symbols": list(SELECTED),
            "unrelated_symbols": "PRESERVED",
            "production_workloads_mutated": False,
        },
    )


__all__ = ["SELECTED", "prepare", "symbols"]
