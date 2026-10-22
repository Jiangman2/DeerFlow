"""Reconcile the direct terminal projection, reconstructing the exact report bytes."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEST = ROOT / "gateway-export-evidence-r1"
MANIFEST = "sha256:ef500864eb402d0b9a76556764dc0e2a9d3fe417f6fef89fbf4e3816ee0c1c84"
CONFIG = "sha256:dff2b9d7323329733f14133b384030d6efeda948a86f082f055f1573ced224fd"
REPORT_SHA = "6fa29bd18e7e445754c6f6377f005c9248cb2f67c142bfa7f2a4a085cf8ceab9"
TAG = "deerflow.local/deer-flow-gateway:20261017-background-stream-953eb97e4aaf"
REMOTE = "/var/lib/portal-gateway-base-20261017-mdjnsuvc"


def write(path, data):
    raw = (json.dumps(data, sort_keys=True, indent=2) + "\n").encode()
    with path.open("xb") as stream:
        stream.write(raw)
    return {"path": str(path), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def main():
    report = {
        "archive": {
            "bytes": 870312448, "diff_ids_decompressed_verified": False, "extracted": False,
            "layer_descriptors": 29, "optional_docker_manifest": True,
            "path": REMOTE + "/gateway.base.oci.tar",
            "sha256": "e9a0ba974ebfd9fe9c40ff5d0de9ce0359abf8617d7d534a856ed7703d334098",
            "verified_unique_blobs": 31
        },
        "collection": "VERIFIED_PINNED_BASE_ARCHIVE", "config_digest": CONFIG,
        "directory": REMOTE,
        "disk": {"content_bytes": 870282066, "observed_free_bytes": 30314127360, "required_free_bytes": 2277435044},
        "errors": [], "handoff": "RESULT_ONLY_ON_STDOUT_AFTER_ALL_ROOT_WRITES",
        "image": TAG, "image_env": "NOT_EXPORTED_OUTSIDE_PRIVATE_OCI", "layer_descriptors": 29,
        "local_private_files_written": True, "manifest_digest": MANIFEST, "platform": "linux/arm64",
        "production_service_mutation": False, "ready_for_build": False, "ready_for_rollout": False,
        "schema": "portal-gateway-base-export.v1", "source_preconditions": "NOT_COLLECTED",
        "steps": [{"operation": operation, "status": "COMPLETE"} for operation in (
            "verify_tag_before", "verify_manifest_config", "export_fixed_local_image",
            "verify_archive_and_hash", "verify_tag_after")],
        "tag_after": MANIFEST, "tag_before": MANIFEST, "worker": "ecs-zdz-pro-0020"
    }
    raw = (json.dumps(report, sort_keys=True, indent=2) + "\n").encode()
    assert len(raw) == 1952
    assert hashlib.sha256(raw).hexdigest() == REPORT_SHA
    DEST.mkdir(exist_ok=False)
    reconstructed = write(DEST / "REPORT.reconstructed-from-terminal.json", report)
    terminal = dict(report, directory_owner="cnooc", handoff_complete=True, report_bytes=1952,
                    report_path=REMOTE + "/REPORT.json", report_sha256=REPORT_SHA)
    projection = write(DEST / "TERMINAL-PROJECTION.json", terminal)
    reconciliation = {
        "schema": "portal-ppt-gateway-export-reconciliation.v1",
        "status": "OPERATOR_EXPORT_VERIFIED_EXACT_REPORT_RECONSTRUCTED_LOCAL_ARCHIVE_PENDING",
        "source": "Direct human terminal output; image Markdown link normalized to its exact visible tag",
        "report_provenance": "Reconstructed using the reviewed exporter's sorted/indented encoding; bytes and SHA exactly match operator report metadata. No uploaded raw report is claimed.",
        "report": reconstructed, "terminal_projection": projection,
        "identity": {"tag": TAG, "manifest": MANIFEST, "config": CONFIG, "platform": "linux/arm64",
                     "running_baseline_config_identity": "MATCH_70_GATEWAYS", "layer_descriptors": 29},
        "archive": report["archive"],
        "handoff": {"complete": True, "directory_owner": "cnooc", "source": "terminal only, after report write"},
        "checks": {"exact_report_bytes_and_sha": True, "all_five_operations_complete": True,
                   "tag_before_and_after_match_fixed_manifest": True, "reported_blob_closure": True,
                   "reported_free_space_sufficient": True, "reported_errors": []},
        "local_archive": "PENDING_COPY_AND_INDEPENDENT_FULL_VERIFICATION",
        "remote_executed_script_sha": "NOT_RETURNED",
        "source_preconditions": "NOT_COLLECTED", "decompressed_diff_ids": "NOT_YET_VERIFIED",
        "production_service_mutation": False, "production_database_mutation": False,
        "ready_for_build": False, "ready_for_rollout": False
    }
    receipt = write(ROOT / "GATEWAY-EXPORT-RECONCILIATION.json", reconciliation)
    print(json.dumps({"status": reconciliation["status"], "report": reconstructed, "reconciliation": receipt}))


if __name__ == "__main__":
    main()
