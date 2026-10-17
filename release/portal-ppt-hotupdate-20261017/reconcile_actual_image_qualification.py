"""Reconcile retained actual-image test receipts without rerunning containers."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUN = ROOT.parent.parent.parent / "portal-ppt-merge-20261017/backend-image-qualification-r4"


def ref(path):
    raw = path.read_bytes()
    return {"path": str(path), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def main():
    run_ref = ref(RUN / "RUN.json")
    report = json.loads((RUN / "RUN.json").read_bytes())
    assert report["status"] == "PASS_ACTUAL_IMAGES_MINIMAL_POSTGRES_BOOTSTRAP_ONLY"
    assert report["errors"] == [] and report["automatic_retry"] is False
    assert report["build_report_sha256"] == "88b4cfc91b9df522a1aed8425d08837e59910b4dc9cd5122d5bacd3edd384443"
    assert ref(RUN / "qualify_images.py")["sha256"] == "fb39e4ab37a40bb20ad5102e7a66b012c6948b4bba2eaea0f4afc0da6ee4f1a5"
    assert ref(RUN / "bootstrap_probe.py")["sha256"] == report["probe_sha256"] == "c6cf155f4924e073b85a17a83b6fd5bdb65a34ef25b49a170de24877d6fb7d36"
    labels = ["gateway-upgrade", "runner-restart", "gateway-rollback-restart", "runner-rollback-restart", "gateway-reforward-restart"]
    assert [step["label"] for step in report["steps"]] == labels
    assert len(report["loaded_images"]) == 4
    snapshot = report["snapshot_sha256_all_five_steps"]
    image_configs = {image["config"]: image for image in report["loaded_images"]}
    logs = []
    for step in report["steps"]:
        log = ref(RUN / (step["label"] + ".log"))
        assert log["bytes"] == step["log_bytes"] and log["sha256"] == step["log_sha256"]
        receipts = [json.loads(line) for line in (RUN / (step["label"] + ".log")).read_text(encoding="utf-8").splitlines() if line.startswith("{")]
        assert receipts == [step["receipt"]]
        receipt = step["receipt"]
        assert receipt["status"] == "PASS" and receipt["revision"] == "0020_ppt_confirmation"
        assert receipt["snapshot_sha256"] == snapshot
        assert receipt["application_source_mount"] is False and receipt["inherited_environment_cleared_before_application_import"] is True
        assert receipt["production_accessed"] is False and receipt["model_requests"] == 0
        assert step["process"]["returncode"] == 0 and step["process"]["failure_code"] is None and step["process"]["seconds"] < 180
        assert image_configs[step["image_config"]]["local_image_id"] == step["local_image_id"]
        logs.append(log)
    version = json.loads((RUN / "POSTGRES-VERSION.json").read_bytes())
    assert version["owner_uuid"] == report["owner_uuid"] and version["server_version"] == "16.15"
    assert version["database_mutated"] is False and version["container_identity_resources_mounts_verified"] is True
    expected_containers = {step["container_id"] for step in report["steps"]} | {version["container_id"]}
    assert len(expected_containers) == 6
    assert all(row["removed"] is True for row in report["cleanup"])
    assert {row["id"] for row in report["cleanup"] if row["resource"] == "container"} == expected_containers
    assert len(report["cleanup"]) == 7 and sum(row["resource"] == "network" for row in report["cleanup"]) == 1
    assert report["native_arm64"] is False and report["production_accessed"] is False and report["model_requests"] == 0
    evidence = {
        "schema": "portal-ppt-actual-image-qualification-parent-reconciliation.v1",
        "status": "FIVE_ACTUAL_IMAGE_DATABASE_SCENARIOS_ACCEPTED_LOCAL_EMULATION_ONLY",
        "run": run_ref, "logs": logs, "postgres_version_receipt": ref(RUN / "POSTGRES-VERSION.json"),
        "scenarios": labels, "snapshot_sha256_all_five_steps": snapshot,
        "disposable_postgres_server_version": "16.15",
        "owned_containers_removed": 6, "owned_networks_removed": 1,
        "qualification_limits": report["limits"],
        "native_arm64_qualified": False, "ready_for_rollout": False,
        "production_service_or_database_mutation": False, "additional_test_execution": False
    }
    with (ROOT / "ACTUAL-IMAGE-QUALIFICATION-RECONCILIATION.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(evidence, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"status": evidence["status"], "run_sha256": run_ref["sha256"], "scenarios": 5, "ready_for_rollout": False}))


if __name__ == "__main__":
    main()
