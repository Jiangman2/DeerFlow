"""Independently reconcile frozen Host test receipts without rerunning tests."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
HOST = ROOT.parent.parent.parent / "portal-ppt-merge-20261017/host-source-r1"


def ref(path):
    raw = path.read_bytes()
    return {"path": str(path), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def checked(row):
    actual = ref(Path(row["path"]))
    assert actual["sha256"] == row["sha256"] and actual["bytes"] == row["bytes"], row["path"]
    return actual


def main():
    source = HOST / "HOST-SOURCE-QUALIFICATION.json"
    assert ref(source)["sha256"] == "fd0fe02a65458e66473bc7e4a1f2a74f9e39e84cfa59257bcce90f6b4972ae53"
    doc = json.loads(source.read_text())
    q = doc["qualification"]
    latest, initial_failures, initial_errors, rounds = {}, set(), set(), []
    for number, group in enumerate(q["rounds"]):
        for field in ("command", "log", "source_preparation", "xml"):
            checked(group[field])
        tree = ET.parse(group["xml"]["path"])
        for case in tree.iter("testcase"):
            key = case.attrib["classname"] + "::" + case.attrib["name"]
            state = "ERROR" if case.find("error") is not None else "FAIL" if case.find("failure") is not None else "SKIP" if case.find("skipped") is not None else "PASS"
            latest[key] = (state, group["xml"]["path"])
            if number == 0 and state == "FAIL":
                initial_failures.add(key)
            if number == 0 and state == "ERROR":
                initial_errors.add(key)
        rounds.append(group["xml"])
    claimed = {row["test"]: row for row in q["latest_testcase_results"]}
    assert len(claimed) == len(q["latest_testcase_results"]) == 175
    assert all(latest[key] == ("PASS", row["evidence"]) and row["state"] == "PASS" for key, row in claimed.items())
    assert len(initial_failures) == 48 and initial_failures <= set(claimed)
    assert len(initial_errors) == 5 and not (initial_errors & set(claimed))
    assert set(latest) - set(claimed) == initial_errors
    for key in initial_errors:
        module = next(part for part in key.split("::")[0].split(".") if part.startswith("test_")) + ".py"
        assert module in q["excluded_test_sources"], key
    assert doc["status"] == "ISOLATED_SOURCE_REVIEW_AND_OFFLINE_QUALIFICATION_COMPLETE"
    assert doc["production_overlay_file_count"] == 12 and not doc["ready_for_rollout"]
    result = {
        "schema": "portal-ppt-host-qualification-reconciliation.v1",
        "status": "EXACT_RECEIPTS_RECONCILED_175_UNIQUE_TESTCASES_PASS",
        "qualification": ref(source), "junit_receipts": rounds,
        "source_manifest": checked(doc["source_manifest"]),
        "authority": checked(doc["authority"]),
        "independent_static_review": checked(doc["independent_static_review"]),
        "unique_passed_cases": 175, "initial_failed_cases_with_later_pass": 48,
        "excluded_gateway_only_errors": 5, "final_round_cases": 62, "final_round_subtests": 22,
        "history": "Original failure and resource/timeout limitations preserved; not represented as one clean whole-suite run",
        "test_rerun_by_this_reconciliation": False, "production_accessed": False,
        "native_arm64_qualified": False, "ready_for_build": False, "ready_for_rollout": False,
        "next": "Bind actual Host OCI and independently verify physical before/absence, probe and dependencies"
    }
    with (ROOT / "HOST-QUALIFICATION-RECONCILIATION.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"status": result["status"], "ready_for_rollout": False}))


if __name__ == "__main__":
    main()
