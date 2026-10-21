"""Build a preload-only, source-bound Data QA + GLM overlay.

Reuse the qualified importer and deterministic OCI construction. This tool
never contacts Kubernetes, a provider, or a registry and contains no secrets.
Preloading this image does not qualify configuration migration or authorize
activation; the matching versioned ConfigMap and Secret are still required.
"""

import argparse
import ast
import hashlib
import json
import pathlib
import subprocess
import types

ROOT = pathlib.Path(__file__).resolve().parents[2]
PREFIX = "backend/packages/harness/deerflow/"
BASE_COMMIT = "e09bf6d3c"
FIELD_BASE = {
    "agents/middlewares/tool_error_handling_middleware.py": "e5aa9643451548b92f01411df0b7c301a90989bdf59bb761786091bf0ed1797e",
    "community/appforge/__init__.py": "7d5e54895332f03838fcfbdca63c64c0b3265cecaf3a5f8325c71e8126348520",
    "community/appforge/tools.py": "505b74bee1af9a67d6834398da7ea7de946fe94da335c96c2a96b2cb596c74f4",
    "config/tool_config.py": "990b85b8ed1f8e2d2c16371f94888759620d7b88baca1f65d63456600352f6fe",
    "subagents/executor.py": "2697417b6521e36b7365b9f4d64ce4acf555dd72d0bf9439844da82d0912c3ac",
    "tools/builtins/task_tool.py": "2e0c0135c304cee156a7d605b4c5df0cf505390773f2c8b0ceae20264f11d883",
    "tools/tools.py": "6fd4f7b5000f1f22fbb7b1b8c4d9e1ec355c22f0c464201dee3911cfbdece0ec",
}
NEW_FILES = (
    "agents/middlewares/datalake_source_middleware.py",
    "community/appforge/data_qa_stream.py",
    "config/datalake_tools.py",
    "runtime/datalake_policy.py",
)
GLM_CANARY_SHA = "6a64de5cfbbd89decc359b2f0e8d4c2d1edf161d0905df48dcb092a8f7895582"
DATA_CANARY_SHA = "16c0ef7472a6493eebb1498ad7a81d2104a621a09c7a9fd8f150fc57b4772171"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def literal(raw, key):
    nodes = [n for n in ast.parse(raw).body if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == key for t in n.targets)]
    assert len(nodes) == 1, "literal_not_unique"
    return ast.literal_eval(nodes[0].value)


def builder():
    source = (ROOT / "deploy/enterprise/package-glm-stream-preload.py").read_text(encoding="utf-8")
    source = source.replace("portal-gateway-glm-stream", "portal-gateway-data-qa-glm")
    source = source.replace("20261021-glm-stream-r1", "20261021-data-qa-glm-r1")
    source = source.replace("--preload-gateway-glm-stream-once", "--preload-gateway-data-qa-glm-once")
    source = source.replace("COMPLETE_GATEWAY_GLM_STREAM", "COMPLETE_GATEWAY_DATA_QA_GLM")
    source = source.replace("portal-glm-stream-release-build.v1", "portal-data-qa-glm-release-build.v1")
    source = source.replace("exact_four_field_qualified_payload_paths", "exact_joint_candidate_payload_paths")
    source = source.replace("THREE_BLOBS_WITH_FOUR_FIELD_QUALIFIED_GLM_FILES", "THREE_BLOBS_WITH_SOURCE_BOUND_JOINT_CANDIDATE_FILES")
    source = source.replace("glm_candidate_acceptance=PRODUCT_QUALIFICATION", "joint_candidate_evidence=PRODUCT_QUALIFICATION")
    module = types.ModuleType("joint_preload_builder")
    exec(compile(source, "<joint-preload-builder>", "exec"), module.__dict__)
    return module


def build(importer_raw, glm_raw, data_raw):
    assert sha(glm_raw) == GLM_CANARY_SHA, "glm_canary_changed"
    assert sha(data_raw) == DATA_CANARY_SHA, "data_canary_changed"
    glm_bundle = literal(glm_raw, "BUNDLE")
    data_bundle = literal(data_raw, "BUNDLE")
    module = builder()
    assert sha(module.canonical(glm_bundle)) == module.QUALIFICATION["candidate_bundle_sha256"], "glm_bundle_changed"
    field = json.loads((ROOT / "deploy/enterprise/data-qa-field-observation-20261021-r1.json").read_bytes())
    files = {}
    for name, record in glm_bundle["sources"].items():
        assert sha(record["source"].encode()) == record["sha256"], "glm_source_changed"
        files["models/" + name] = record
    assert len(files) == 4, "glm_file_set"
    for path, expected in FIELD_BASE.items():
        commit = "739e17f9a4d5a8d1bf505a5934b5c3883a23a24c" if path == "community/appforge/tools.py" else BASE_COMMIT
        base = subprocess.check_output(["git", "show", commit + ":" + PREFIX + path], cwd=ROOT)
        assert sha(base) == expected, "field_base_mismatch:" + path
    for path in (*FIELD_BASE, *NEW_FILES):
        source = (ROOT / (PREFIX + path)).read_text(encoding="utf-8")
        compile(source, path, "exec")
        files[path[:-3]] = {"source": source, "sha256": sha(source.encode())}
    # The provider-tested tool and SSE decoder must remain byte-identical to
    # the canary. The separate source-policy addition does not call providers.
    for path in ("community/appforge/tools", "community/appforge/data_qa_stream"):
        key = "deerflow." + ("community.appforge._candidate_tools" if path.endswith("/tools") else path.replace("/", "."))
        assert files[path]["sha256"] == data_bundle["modules"][key]["sha256"], "provider_tested_source_changed"
    bundle = {"sources": files}
    bundle_raw = ("BUNDLE = " + repr(bundle) + "\n").encode()
    module.MODEL_ROOT = "app/backend/packages/harness/deerflow/"
    module.NAMES = tuple(sorted(files))
    module.CANARY_TOOL_SHA = sha(bundle_raw)
    glm_evidence = module.QUALIFICATION
    module.QUALIFICATION = {
        "candidate_bundle_sha256": sha(module.canonical(bundle)),
        "gateway": field["gateway"],
        "base_manifest": module.BASE,
        "glm": glm_evidence,
        "data_qa": field,
        "expected_existing_sources": FIELD_BASE,
        "expected_absent_sources": list(NEW_FILES),
        "related_tests_passed": 320,
        "related_tests_skipped": 1,
        "full_suite_accepted": False,
        "production_deployed": False,
        "configuration_migration_required": True,
        "preload_only": True,
    }
    output, receipt = module.build(importer_raw, bundle_raw)
    receipt.update(glm_canary_script_sha256=GLM_CANARY_SHA, data_canary_script_sha256=DATA_CANARY_SHA, source_file_count=len(files), configuration_migration_required=True, ready_for_rollout=False)
    # The generated image contains precisely these 15 source files and no
    # live configuration, key, new dependency, frontend or browser change.
    assert len(files) == 15
    return output, receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qualified-importer", required=True, type=pathlib.Path)
    parser.add_argument("--glm-canary", required=True, type=pathlib.Path)
    parser.add_argument("--data-canary", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--receipt", required=True, type=pathlib.Path)
    args = parser.parse_args()
    output, receipt = build(args.qualified_importer.read_bytes(), args.glm_canary.read_bytes(), args.data_canary.read_bytes())
    with args.output.open("xb") as stream:
        stream.write(output)
    with args.receipt.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, ensure_ascii=True, sort_keys=True, indent=2)
        stream.write("\n")
    role = receipt["profile"]["roles"][0]
    print(json.dumps({"script_bytes": len(output), "script_sha256": sha(output), "manifest": role["manifest_digest"], "config": role["config_digest"], "source_file_count": receipt["source_file_count"], "ready_for_rollout": False}))


if __name__ == "__main__":
    main()
