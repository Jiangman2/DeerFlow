"""Check frozen exporter against a retained real containerd archive, without extraction."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tarfile
import time

ROOT = Path(__file__).resolve().parent
SCRIPT_SHA = "23f61ad3164f37856c3c320c34a4dd8a0c81020e2bc9d14a783736f7a8a0be0f"
ARCHIVE = Path("C:/Users/15285/Downloads/p1-oci-download-VWvJtg/runner.candidate.oci.tar")
ARCHIVE_SHA = "d521617c5d7918053e9403a831058ecf38cad109f34e82c8b634492456a12667"
MANIFEST = "sha256:eb23de08345bab65c78264f99ce33085c4ea3574ff8d6281b62418f644fc06cd"
CONFIG = "sha256:bc378da76f8ba638256d7f70764dcd130a12cb44346a596ed7a17c4d61815cec"
TAG = "deerflow.local/deer-flow-gateway:20260928-ppt-preview-profile-r4-runner-9473ecee03df4e43"


def main():
    script = ROOT / "export_gateway_base.py"
    assert hashlib.sha256(script.read_bytes()).hexdigest() == SCRIPT_SHA
    spec = importlib.util.spec_from_file_location("frozen_export", script)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    with tarfile.open(ARCHIVE, "r:") as archive:
        manifest_raw = archive.extractfile(tool.blob_path(MANIFEST)).read()
        config_raw = archive.extractfile(tool.blob_path(CONFIG)).read()
    blobs, layers = tool.expected_blobs(manifest_raw, config_raw, MANIFEST, CONFIG, 365)
    del manifest_raw, config_raw
    receipt = tool.validate_archive(ARCHIVE, blobs, layers, time.monotonic() + 120, MANIFEST, CONFIG, TAG)
    assert receipt["sha256"] == ARCHIVE_SHA
    report = {
        "schema": "portal-gateway-base-export-independent-review.v1",
        "status": "REVIEWED_FOR_OPERATOR_EXPORT",
        "script_sha256": SCRIPT_SHA,
        "actual_containerd_archive_compatibility": {"path": str(ARCHIVE), **receipt},
        "code_review": [
            "Fixed worker, root directory, socket, namespace, tag, manifest, config and layer count",
            "No pull, import, tag, image execution, Kubernetes, source mutation or database action",
            "Bounded subprocess output, wall time and archive size; errors withhold raw stderr/config",
            "Archive members checked without extraction; duplicate layer descriptors allowed only through matching blobs",
            "Checks current tag before and after export, exact manifest/config hashes and all archived blob sizes/hashes",
            "Disk check before export; partial evidence retained on failure; no automatic retry",
            "Root-private files and immutable report before final fd-based cnooc ownership handoff",
            "Report digest is printed only after report write; no root writes after ownership handoff starts"
        ],
        "existing_test_evidence": "LOCAL-VERIFICATION.json: Linux 11/11, Windows 8 passed and 3 POSIX-only skipped; reviewed, not blindly repeated",
        "limitations": [
            "Current Gateway export and this worker's ctr implementation have not been executed locally",
            "Compatibility fixture is the retained Runner export, not current Gateway contents",
            "Compressed blob closure verified; decompressed diff IDs are deliberately outside export scope",
            "Source applicability, builds and rollout require subsequent qualification"
        ],
        "production_accessed": False,
        "ready_for_build": False,
        "ready_for_rollout": False
    }
    with (ROOT / "INDEPENDENT-REVIEW.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"status": report["status"], "script_sha256": SCRIPT_SHA, "actual_archive": receipt}))


if __name__ == "__main__":
    main()
