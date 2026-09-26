"""v5 changes only build validation; the frozen employee/source deltas stay exact."""

import ast
import hashlib
import json
import subprocess
import sys
import tarfile
from pathlib import Path

from candidate_bundle import materialize
from package_candidate import package


HERE = Path(__file__).parent


def test_v5_layer_control_bundle_keeps_all_frozen_v4_business_bytes(tmp_path):
    frozen = HERE / "candidate-inputs-20260924-v4"
    before = {item.name: item.read_bytes() for item in frozen.iterdir()}
    components = {
        name: json.loads(before[name + "-delta.json"])
        for name in ("gateway", "frontend", "provisioner")
    }
    destination = materialize(
        components,
        tmp_path / "v5",
        overlay_report=(HERE / "audited-source-overlays-20260924.json").read_bytes(),
    )
    after = {item.name: item.read_bytes() for item in destination.iterdir()}
    assert before.keys() == after.keys()
    assert {name for name in before if before[name] != after[name]} == {
        "verify_oci_base.py",
        "BUILD-ONLY.md",
        "PACKAGE.SHA256",
    }
    assert before == {item.name: item.read_bytes() for item in frozen.iterdir()}
    # Check actual delivered host controls against Python 3.7 grammar. This is
    # not a claim of having executed them on a real Python 3.7 ARM64 host.
    ast.parse(after["verify_oci_base.py"], feature_version=(3, 7))
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(destination / "verify_candidate.py"),
            str(destination),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    receipt = json.loads(result.stdout)
    assert receipt["employee_candidate_inputs"] == "PASS"
    assert receipt["ready_for_rollout"] is False
    archive = tmp_path / "deerflow-employee-sharing-build-inputs-20260924-v5.tar.gz"
    digest = package(destination, archive)
    assert digest == hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = archive.with_name(archive.name + ".sha256").read_bytes()
    assert checksum == (digest + "  " + archive.name + "\n").encode("ascii")
    assert b"\r" not in checksum
    with tarfile.open(archive, "r:gz") as stream:
        for member in stream.getmembers():
            assert member.isfile() and member.mode == 0o644
            assert (
                stream.extractfile(member).read() == after[member.name.split("/", 1)[1]]
            )
