"""v6 refreshes reviewed topology evidence, not employee payloads or controls."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from candidate_bundle import json_bytes, materialize
from package_candidate import package
from verify_candidate import verify


HERE = Path(__file__).parent
REPORT_SHA = "f0889847295c3b13b02df5b9c7854a42ab203bc1916b59361efba7791c373db3"
CHANGED = {"source-overlays.json", "CANDIDATE.json", "BUILD-ONLY.md", "PACKAGE.SHA256"}


def test_frozen_v6_refresh_changes_only_frontend_metadata_and_evidence(tmp_path):
    old = HERE / "candidate-inputs-20260924-v5"
    new = HERE / "candidate-inputs-20260924-v6"
    before = {p.name: p.read_bytes() for p in old.iterdir()}
    after = {p.name: p.read_bytes() for p in new.iterdir()}
    assert before.keys() == after.keys()
    assert {n for n in before if before[n] != after[n]} == CHANGED
    prior = json.loads(before["source-overlays.json"])
    refreshed = json.loads(after["source-overlays.json"])
    raw = json_bytes(refreshed["report"])
    assert hashlib.sha256(raw).hexdigest() == REPORT_SHA
    assert refreshed["source_report_sha256"] == REPORT_SHA
    for name, state in prior["report"]["workloads"].items():
        current = refreshed["report"]["workloads"][name]
        if name != "frontend":
            assert current == state
            continue
        assert {k for k in state if state[k] != current[k]} == {
            "deployment_spec_sha256",
            "instances",
        }
        assert len(state["instances"]) == 1
        assert len(current["instances"]) == 2
        assert {i["image_id"] for i in current["instances"]} == {
            i["image_id"] for i in state["instances"]
        }
    old_index = json.loads(before["CANDIDATE.json"])
    new_index = json.loads(after["CANDIDATE.json"])
    assert {k for k in old_index if old_index[k] != new_index[k]} == {
        "source_overlays_sha256"
    }
    components = {
        n: json.loads(before[n + "-delta.json"])
        for n in ("gateway", "frontend", "provisioner")
    }
    reproduced = materialize(components, tmp_path / "reproduced", overlay_report=raw)
    assert {p.name: p.read_bytes() for p in reproduced.iterdir()} == after
    assert before == {p.name: p.read_bytes() for p in old.iterdir()}
    assert verify(new)["ready_for_rollout"] is False
    receipt = subprocess.run(
        [sys.executable, "-B", str(new / "verify_candidate.py"), str(new)],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    assert json.loads(receipt.stdout)["employee_candidate_inputs"] == "PASS"
    archive = tmp_path / "deerflow-employee-sharing-build-inputs-20260924-v6.tar.gz"
    digest = package(new, archive)
    assert archive.with_name(archive.name + ".sha256").read_bytes() == (
        digest + "  " + archive.name + "\n"
    ).encode("ascii")
