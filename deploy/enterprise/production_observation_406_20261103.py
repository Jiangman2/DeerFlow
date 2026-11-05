"""Offline, bounded-scope reconciliation of the operator's Gateway 406 samples."""
import ast
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import zlib

from enterprise_source_followup_20261103 import formatting_fingerprint

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = "release/production-observation-20261103-gw406/"
MANIFEST = ARCHIVE + "SOURCE.json"
MANIFEST_SHA256 = "54eabb68ef338eac76a8fa8f79b3bf153e27714cd0ce38ca6276d7aa2713183e"
REFERENCE = "443cbd8ab6e2e74298075976fa68af079c1f7100"


def digest(raw):
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def constants(raw, names):
    """Read sealed Python literals without executing the archived operator tool."""
    result = {}
    for node in ast.parse(raw).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in names:
                assert name not in result, "DUPLICATE_TOOL_CONSTANT"
                result[name] = ast.literal_eval(node.value)
    assert result.keys() == names, "MISSING_TOOL_CONSTANT"
    return result


def reconcile_observation(report, manifest):
    assert report["git_ref"] == REFERENCE and report["stable_readback"] is True, "OBSERVATION_REFERENCE"
    assert report["status"] == "READONLY_COMPARISON_OBSERVED", "OBSERVATION_INCOMPLETE"
    assert report["deployments"] == manifest["deployments"], "DEPLOYMENT_BINDING"
    assert report["pod_image_metadata_verified"] == {"gateway": 70, "frontend": 2}, "POD_IMAGE_SCOPE"
    assert report["all_gateway_processes_verified"] is False and report["dependencies_verified"] is False, "SCOPE_OVERCLAIM"
    assert report["browser_business_acceptance_claimed"] is False, "BUSINESS_OVERCLAIM"
    for key in ("files_modified", "workload_modified", "configuration_modified", "database_access"):
        assert report[key] is False, "READONLY_SCOPE"
    for key in ("provider_requests", "callback_requests", "secret_api_reads"):
        assert report[key] == 0, "READONLY_SCOPE"
    samples = report["samples"]
    assert len(samples) == 5 and {s["node"] for s in samples} == {"ecs-zdz-pro-%04d" % n for n in range(18, 23)}, "SAMPLE_NODE_SCOPE"
    assert len({s["uid"] for s in samples}) == 5, "SAMPLE_IDENTITY"
    variants = {r["path"].removeprefix("backend/"): r for r in manifest["format_variants"]}
    assert len(variants) == 22, "FORMAT_VARIANT_SCOPE"
    for sample in samples:
        observed = sample["observation"]
        comparison = observed["source_comparison"]
        assert comparison["expected_python_files"] == 635 and comparison["differences_truncated"] is False, "SOURCE_COUNT"
        assert comparison["counts"] == {"enterprise_exact": 613, "qualified_format_variant": 8, "mismatch": 14}, "SOURCE_COUNTS_CHANGED"
        assert comparison["unexpected_files_checked"] is False, "SOURCE_SCOPE_OVERCLAIM"
        differences = comparison["differences"]
        assert len(differences) == 22 and {r["path"] for r in differences} == variants.keys(), "DIFFERENCE_SCOPE"
        for row in differences:
            expected = variants[row["path"]]
            assert row["sha256"] == expected["runtime_sha256"], "UNRECOGNIZED_RUNTIME_HASH"
            assert row["classification"] == expected["initial_classification"], "ORIGINAL_RESULT_CHANGED"
        assert observed["config_sha256"] == "bf39d16f673d2259a0430357e7a9a435482add27194aa947a0e34cdd594f1fb7", "CONFIG_HASH"
        assert observed["work_grant_source_present"] is False, "WORK_SOURCE_PRESENT"
        assert observed["application_imported"] is False and observed["database_access"] is False, "PROBE_SCOPE"
        processes = observed["gateway_processes"]
        assert processes["status"] == "OBSERVED" and processes["processes"], "PROCESS_NOT_OBSERVED"
        assert processes["scope"] == "process_start_environment_not_live_python_objects", "PROCESS_SCOPE"
        environments = [observed["exec_environment"]] + [p["environment"] for p in processes["processes"]]
        for env in environments:
            assert env.keys() == manifest["expected_start_environment"].keys(), "ENV_SCOPE"
            assert all(v["present"] is True and v["matches_expected"] is True for v in env.values()), "START_ENV_MISMATCH"
            assert env["OA_POLICY_FLOW_RUN_TIMEOUT_SECONDS"]["value"] == 3600, "OA_TIMEOUT"
            assert env["OA_POLICY_FLOW_CALLBACK_ENABLED"]["value"] == "false", "OA_CALLBACK"
    return {"exact_lf_hash": 613, "qualified_format_only": 22, "unexplained": 0}


def verify(read):
    manifest_raw = read(MANIFEST)
    assert digest(manifest_raw) == MANIFEST_SHA256, "OBSERVATION_MANIFEST_CHANGED"
    manifest = json.loads(manifest_raw)
    assert manifest["schema"] == "production-observation.gw406.v1" and manifest["reference_commit"] == REFERENCE, "MANIFEST_REFERENCE"
    evidence = {}
    for name, expected in manifest["evidence"].items():
        evidence[name] = read(ARCHIVE + name)
        assert digest(evidence[name]) == expected, "ORIGINAL_EVIDENCE_CHANGED"
    host = constants(evidence["INSPECTOR-r1.py"], {"CHILD_B64", "CHILD_SHA256", "ENV"})
    child = zlib.decompress(base64.b64decode(host["CHILD_B64"]))
    assert hashlib.sha256(child).hexdigest() == host["CHILD_SHA256"], "ORIGINAL_CHILD_SEAL"
    child_values = constants(child, {"EXPECTED", "ENV"})
    assert host["ENV"] == child_values["ENV"] == manifest["expected_start_environment"], "INSPECTOR_ENV_BINDING"
    inventory = manifest["runtime_source_sha256"]
    assert len(inventory) == 635 and {"backend/" + p for p in child_values["EXPECTED"]} == inventory.keys(), "INSPECTOR_SOURCE_SCOPE"
    for path, expected in inventory.items():
        assert digest(read(path)) == expected, "ENTERPRISE_SOURCE_CHANGED: " + path
        assert child_values["EXPECTED"][path.removeprefix("backend/")][expected] == "enterprise_exact", "INSPECTOR_EXPECTATION_CHANGED"
    for row in manifest["format_variants"]:
        original, current = read(row["original_archive"]), read(row["path"])
        assert digest(original) == row["runtime_sha256"], "FORMAT_ORIGINAL_CHANGED"
        assert digest(current) == row["enterprise_sha256"] == inventory[row["path"]], "FORMAT_CURRENT_CHANGED"
        if row["proof"] == "sealed_layout_and_import_order":
            assert formatting_fingerprint(original, row["path"]) == formatting_fingerprint(current, row["path"]), "FORMAT_LOGIC_CHANGED"
        else:
            assert row["proof"] == "python_ast_equal", "UNKNOWN_FORMAT_PROOF"
            assert ast.dump(ast.parse(original), include_attributes=False) == ast.dump(ast.parse(current), include_attributes=False), "FORMAT_LOGIC_CHANGED"
    counts = reconcile_observation(json.loads(evidence["OPERATOR.json"]), manifest)
    pending = manifest["pending_frontend"]
    before = read(pending["previous_snapshot"]).replace(b"\r\n", b"\n")
    after = read(pending["runtime_path"]).replace(b"\r\n", b"\n")
    assert digest(before) == pending["previous_sha256"] and digest(after) == pending["current_sha256"], "PENDING_FRONTEND_CHANGED"
    for old, new in pending["copy_changes"]:
        assert before.count(old.encode()) == 1, "COPY_REPLACEMENT_SCOPE"
        before = before.replace(old.encode(), new.encode())
    assert before == after, "FRONTEND_NOT_COPY_ONLY"
    assert digest(read(pending["test_path"])) == pending["test_sha256"] and pending["test_is_runtime"] is False, "FRONTEND_TEST_BINDING"
    return {"status": "SAMPLED_SOURCE_RECONCILIATION_VERIFIED", "reference_commit": REFERENCE,
            "gateway_generation": 406, "frontend_generation": 239, "sampled_nodes": 5,
            "per_sample_python_sources": counts, "pending_frontend_copy_changes": 3,
            "production_commands_executed": False, "all_gateway_processes_verified": False,
            "dependencies_verified": False, "business_acceptance_claimed": False}


def git_blobs(ref, paths):
    cache = {}
    paths = sorted(set(paths))
    if paths:
        import io
        output = subprocess.check_output(["git", "cat-file", "--batch"], cwd=ROOT,
                                         input="".join(ref + ":" + p + "\n" for p in paths).encode())
        stream = io.BytesIO(output)
        for path in paths:
            header = stream.readline().decode().split()
            assert len(header) == 3 and header[1] == "blob", "MISSING_GIT_BLOB: " + path
            cache[path] = stream.read(int(header[2]))
            assert stream.read(1) == b"\n", "GIT_BATCH_BOUNDARY"
    return cache


def reader(ref=None, check_current=False):
    """Keep historical evidence verifiable after later intentional source changes.

    Evidence always comes from the requested revision/working tree. Runtime
    sources come from the immutable observed commit unless comparison with the
    current revision is explicitly requested. Neither mode executes the probe.
    """
    raw = git_blobs(ref, [MANIFEST])[MANIFEST] if ref else (ROOT / MANIFEST).read_bytes()
    assert digest(raw) == MANIFEST_SHA256, "OBSERVATION_MANIFEST_CHANGED"
    manifest = json.loads(raw)
    pending = manifest["pending_frontend"]
    evidence_paths = [MANIFEST, *(ARCHIVE + n for n in manifest["evidence"]),
                      *(r["original_archive"] for r in manifest["format_variants"]),
                      pending["previous_snapshot"]]
    source_paths = [*manifest["runtime_source_sha256"], pending["runtime_path"], pending["test_path"]]
    cache = git_blobs(ref, evidence_paths) if ref else {p: (ROOT / p).read_bytes() for p in evidence_paths}
    if check_current and ref is None:
        cache.update({p: (ROOT / p).read_bytes() for p in source_paths})
    else:
        cache.update(git_blobs(ref if check_current else REFERENCE, source_paths))
    return cache.__getitem__
