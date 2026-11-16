"""Record the operator's new read-only source evidence without rewriting SOURCE."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG = Path("C:/Users/15285/Downloads/log(25).txt")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    raw = LOG.read_bytes()
    rows = []
    for line in raw.decode("utf-8-sig").splitlines():
        if line.startswith("{"):
            value = json.loads(line)
            if value.get("schema") == "energy-delivery-sequence-inspection.v1":
                rows.append(value)
    if len(rows) != 1:
        raise ValueError("ONE_FIXED_OPERATOR_OBSERVATION_REQUIRED")
    report = rows[0]
    expected = {
        "packages/harness/deerflow/sandbox/tools.py": (99410, "a8e8b4f3ff94f79e2e0458414f4c2d55310bdd170a8859c00b99dd1d65ad929c"),
        "packages/harness/deerflow/tools/builtins/present_file_tool.py": (19640, "eccfe1e5975c771465abf96f1da60fdb2a11e0712716aeb2453a2d10fbe30e3b"),
        "packages/harness/deerflow/utils/conversation_url.py": (5042, "f5ac36f16038a3532cd3110d134b72e58c42693c1b9ab9a1b6390d9b4209c0b8"),
    }
    actual = report["observation"]["fixed_source_hashes"]
    assert report["status"] == "DELIVERY_SEQUENCE_OBSERVED_NO_CHANGE"
    assert report["stable_gateway_readback"] is True and report["workload_modified"] is False
    assert report["gateway"] == {"generation": 416, "spec_sha256": "3924fcb409ba6982b1d0acb909a276529c86ccd9d814f85af22a838479113df6", "uid": "a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1"}
    assert set(actual) == set(expected)
    for name, (size, sha) in expected.items():
        row = actual[name]
        assert row == {"bytes": size, "present": True, "sha256": sha, "stable_readback": True}
    assert digest((ROOT / "parent/backend/packages/harness/deerflow/sandbox/tools.py").read_bytes()) == expected["packages/harness/deerflow/sandbox/tools.py"][1]
    assert digest((ROOT / "backend/packages/harness/deerflow/utils/conversation_url.py").read_bytes()) == expected["packages/harness/deerflow/utils/conversation_url.py"][1]
    result = {
        "schema": "task-conversation-context-field-baseline.v1",
        "status": "OPERATOR_FIXED_PARENT_SOURCE_MATCHED_OFFLINE",
        "original_source_manifest_modified": False,
        "production_mutation_performed": False,
        "production_parent_source_sample_matched": True,
        "all_gateway_sources_verified_by_this_observation": False,
        "qualification_scope": "ONE_READY_OWNED_GATEWAY_POD_FIXED_THREE_SOURCE_FILES",
        "input_file": LOG.name, "input_bytes": len(raw), "input_sha256": digest(raw),
        "report_schema": report["schema"], "observed_at": report["observed_at"],
        "gateway": report["gateway"], "sample": report["sample"], "replica_set": report["replica_set"],
        "fixed_source_hashes": actual, "gateway_mount_metadata": report["gateway_mount_metadata"],
        "accepted_scheduler_plan": {"path": "/var/lib/portal-scheduler-budget-20261113-r5/PLAN.json", "operation": "b904a96416294639ae3acb1245728084", "sha256": "d5432e73ccb749580ce698e4d98b0e812f041f9bfdfc295eba76da0373caeaad", "basis": "OPERATOR_20261113_READONLY_70_POD_ACCEPTANCE_IN_CONVERSATION"},
    }
    target = ROOT / "FIELD-BASELINE.json"
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(result, sort_keys=True, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "field_file_sha256": digest(target.read_bytes()), "observed_at": report["observed_at"]}))


if __name__ == "__main__":
    main()
