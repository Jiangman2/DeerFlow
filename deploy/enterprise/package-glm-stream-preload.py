"""Append the field-qualified GLM files to the exact current ARM64 Gateway.

Reuses the hash-pinned, previously qualified node importer. This creates a
preload-only tool, not permission or a mechanism to change any workload.
"""

import argparse
import ast
import base64
import copy
import gzip
import hashlib
import io
import json
import pathlib
import tarfile

IMPORTER_SHA = "8ec9625a5aab20a37030d4d6e69bd7f1fae65246ed2f29094be4e974d493dded"
CANARY_TOOL_SHA = "6a64de5cfbbd89decc359b2f0e8d4c2d1edf161d0905df48dcb092a8f7895582"
BASE = "sha256:58a50175cfae4a141dd1c03bbd4362a3a3f672545e2d5c08cbc2f18816a204a3"
QUALIFICATION = {
    "operation": "bf79a7b7f0bb413aa2916bab33e83ed3",
    "report_bytes": 3253,
    "report_sha256": "ab15569c02c626e085cbf00e4bf056faea0fd1e61e6e3cb341ee501c77825150",
    "candidate_bundle_sha256": "59dbde49f5a395fca612a73cbfb9442660a82db78e1a172c9f02bc068b276f04",
    "gateway": {"generation": 381, "uid": "a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1", "spec_sha256": "5a84f74566171e588433779a59b9adec1ae752c2dc3c42706ff55db1e295fbda"},
    "base_manifest": BASE,
    "text": "PASS",
    "tool_call": "PASS",
    "tool_resume": "PASS",
    "image": "PASS",
    "completion_validation": "TERMINAL_AND_DONE",
    "sdk_version": "2.32.0",
    "production_deployed": False,
}
MODEL_ROOT = "app/backend/packages/harness/deerflow/models/"
NAMES = ("enterprise_glm", "glm_stream_guard", "output_safety", "robust_openai")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def literal(source, name):
    nodes = [n for n in ast.parse(source).body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == name]
    assert len(nodes) == 1, name
    return ast.literal_eval(nodes[0].value)


def replace_node(source, name, replacement, function=False):
    body = ast.parse(source).body
    if function:
        nodes = [n for n in body if isinstance(n, ast.FunctionDef) and n.name == name]
    else:
        nodes = [n for n in body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == name]
    assert len(nodes) == 1, name
    node = nodes[0]
    lines = source.splitlines(True)
    return "".join(lines[: node.lineno - 1]) + replacement + "\n" + "".join(lines[node.end_lineno :])


def replace_exact(source, old, new):
    assert source.count(old) == 1, old[:80]
    return source.replace(old, new)


def build(old_source, canary_source):
    assert sha(old_source) == IMPORTER_SHA
    assert sha(canary_source) == CANARY_TOOL_SHA
    bundle = literal(canary_source.decode(), "BUNDLE")
    assert sha(canonical(bundle)) == QUALIFICATION["candidate_bundle_sha256"]
    old = old_source.decode().replace("\r\n", "\n")
    sources = literal(old, "SOURCES")
    previous = json.loads(sources["profile"])
    encoded = json.loads(sources["blobs"])
    role = previous["roles"][0]
    assert role["manifest_digest"] == BASE
    mraw = base64.b64decode(encoded[BASE], validate=True)
    craw = base64.b64decode(encoded[role["config_digest"]], validate=True)
    assert "sha256:" + sha(mraw) == BASE
    assert "sha256:" + sha(craw) == role["config_digest"]
    manifest, config = json.loads(mraw), json.loads(craw)
    assert config["architecture"] == "arm64" and config["os"] == "linux"
    assert len(manifest["layers"]) == len(config["rootfs"]["diff_ids"]) == 66
    files = {}
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name in NAMES:
            entry = bundle["sources"][name]
            raw = entry["source"].encode()
            assert sha(raw) == entry["sha256"]
            path = MODEL_ROOT + name + ".py"
            info = tarfile.TarInfo(path)
            info.size, info.mode, info.uid, info.gid, info.mtime = len(raw), 0o644, 0, 0, 0
            archive.addfile(info, io.BytesIO(raw))
            files[path] = {"bytes": len(raw), "sha256": sha(raw)}
    expanded = data.getvalue()
    compressed_buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed_buffer, mode="wb", filename="", mtime=0) as stream:
        stream.write(expanded)
    compressed = compressed_buffer.getvalue()
    layer_digest, diff_id = "sha256:" + sha(compressed), "sha256:" + sha(expanded)
    new_config = copy.deepcopy(config)
    new_config["rootfs"]["diff_ids"].append(diff_id)
    new_config["history"].append({"created": "2026-10-21T01:20:00Z", "created_by": "GLM response-local SSE completion guard; field canary bf79a7b7f0bb413aa2916bab33e83ed3"})
    new_craw = canonical(new_config)
    cd = "sha256:" + sha(new_craw)
    new_manifest = copy.deepcopy(manifest)
    new_manifest["config"].update(digest=cd, size=len(new_craw))
    new_manifest["layers"].append({"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip", "digest": layer_digest, "size": len(compressed)})
    new_mraw = canonical(new_manifest)
    md = "sha256:" + sha(new_mraw)
    tag = "deerflow.local/deer-flow-gateway:20261021-glm-stream-r1-" + md[7:19]
    chain = ""
    for diff in config["rootfs"]["diff_ids"]:
        chain = diff if not chain else "sha256:" + sha((chain + " " + diff).encode())
    base = {
        "config_bytes": len(craw),
        "config_digest": role["config_digest"],
        "config_object_sha256": sha(canonical(config)),
        "descriptor_prefix_sha256": sha(canonical(manifest["layers"])),
        "diff_prefix_sha256": sha(canonical(config["rootfs"]["diff_ids"])),
        "final_chain_id": chain,
        "layer_count": 66,
        "manifest_bytes": len(mraw),
        "manifest_digest": BASE,
        "runtime_config_sha256": role["runtime_config_sha256"],
        "tag": role["tag"],
    }
    profile = copy.deepcopy(previous)
    profile["schema"] = "portal-gateway-glm-stream-profile.v1"
    profile["bases"]["gateway"] = base
    profile["preserve"]["gateway"].update({role["tag"]: BASE, "deerflow.local/deer-flow-gateway@" + BASE: BASE})
    profile["roles"] = [
        {
            "base": "gateway",
            "role": "gateway-forward",
            "tag": tag,
            "config_digest": cd,
            "manifest_digest": md,
            "expected_layer_count": 67,
            "runtime_config_sha256": role["runtime_config_sha256"],
            "tail_layers": [{"blob_digest": layer_digest, "compressed_bytes": len(compressed), "diff_id": diff_id, "position": 67, "uncompressed_bytes_upper_bound": len(expanded)}],
        }
    ]
    profile["tail_files"] = {"gateway-forward": {"files": files, "uncompressed_bytes": len(expanded)}}
    sources["profile"] = canonical(profile)
    sources["blobs"] = canonical({d: base64.b64encode(b).decode() for d, b in ((md, new_mraw), (cd, new_craw), (layer_digest, compressed))})
    output = old
    for name, value in {
        "SOURCES": sources,
        "SOURCE_PINS": {k: {"bytes": len(v), "sha256": sha(v)} for k, v in sources.items()},
        "ROUTE_PIN": files,
        "PRODUCT_QUALIFICATION": QUALIFICATION,
        "CONTROL": "/var/lib/portal-gateway-glm-stream-node-20261021-r1",
        "SCHEMA": "portal-gateway-glm-stream-preload.v1",
        "TAIL_LIMIT": 512 * 1024,
        "PAYLOAD_LIMITS": {p: 262144 for p in files},
    }.items():
        output = replace_node(output, name, name + " = " + repr(value))
    output = replace_exact(
        output, "profile.get('tail_files', {}).get('gateway-forward', {}).get('files', {}).get(\n        'app/backend/packages/harness/deerflow/constants.py')", "profile.get('tail_files', {}).get('gateway-forward', {}).get('files', {})"
    )
    output = replace_exact(output, "'portal-gateway-haineng-brand-profile.v1'", "'portal-gateway-glm-stream-profile.v1'")
    output = output.replace("COMPLETE_GATEWAY_HAINENG_BRAND_PRELOAD_REVIEW_REQUIRED", "COMPLETE_GATEWAY_GLM_STREAM_PRELOAD_REVIEW_REQUIRED")
    output = output.replace("--prepare-gateway-haineng-brand-once", "--preload-gateway-glm-stream-once")
    output = output.replace("qualified_brand_source_does_not_match_image", "field_qualified_glm_source_does_not_match_image")
    output = output.replace("exact_one_reviewed_payload_path", "exact_four_field_qualified_payload_paths")
    output = output.replace("THREE_EXACT_GATEWAY_METADATA_AND_ONE_FILE_APPENDED_LAYER_BLOBS", "THREE_BLOBS_WITH_FOUR_FIELD_QUALIFIED_GLM_FILES")
    output = output.replace("actual_r6_nfs_acceptance=R6_ACCEPTANCE,", "historical_base_nfs_acceptance=R6_ACCEPTANCE, glm_candidate_acceptance=PRODUCT_QUALIFICATION,")
    output = output.replace("portal-gateway-employee-hot-uuid-report-", "portal-gateway-glm-stream-report-")
    output = replace_node(
        output,
        "accepted_nfs_gate",
        "def accepted_nfs_gate():\n    require(PRODUCT_QUALIFICATION == " + repr(QUALIFICATION) + ", 'glm_field_canary_not_bound')\n    require(ROUTE_PIN == " + repr(files) + ", 'glm_field_sources_not_bound')",
        function=True,
    )
    compile(output, "<preload-glm-stream>", "exec")
    receipt = {
        "schema": "portal-glm-stream-release-build.v1",
        "field_acceptance": QUALIFICATION,
        "profile": profile,
        "script_bytes": len(output.encode()),
        "script_sha256": sha(output.encode()),
        "importer_source_sha256": IMPORTER_SHA,
        "canary_tool_sha256": CANARY_TOOL_SHA,
        "compressed_append_bytes": len(compressed),
        "expanded_append_bytes": len(expanded),
        "configuration_fields_preserved": True,
        "base_layers_preserved": True,
        "workload_modified": False,
    }
    return output.encode(), receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qualified-importer", type=pathlib.Path, required=True)
    parser.add_argument("--canary-tool", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--receipt", type=pathlib.Path, required=True)
    args = parser.parse_args()
    output, receipt = build(args.qualified_importer.read_bytes(), args.canary_tool.read_bytes())
    with args.output.open("xb") as stream:
        stream.write(output)
    with args.receipt.open("xb") as stream:
        stream.write(canonical(receipt) + b"\n")
    print(
        json.dumps(
            {
                "script": str(args.output),
                "bytes": len(output),
                "sha256": sha(output),
                "image": receipt["profile"]["roles"][0]["tag"],
                "manifest": receipt["profile"]["roles"][0]["manifest_digest"],
                "compressed_append_bytes": receipt["compressed_append_bytes"],
            }
        )
    )


if __name__ == "__main__":
    main()
