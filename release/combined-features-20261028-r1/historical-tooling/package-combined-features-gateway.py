"""Append exactly the sealed 11-file delta to the accepted 69-layer Gateway.

Produces an offline cache preload tool only. No deployment, model requests,
database migration, credentials, or paused Work sources are included.
"""
import argparse
import ast
import base64
import copy
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile

HERE = Path(__file__).resolve().parent
IMPORTER_SHA = "a06550303777ab2f4eed091bdbe92addaf0821ffd8fe48b8c2600339c41fe935"
BASE = "sha256:ba642b5484ad6103146de433432d24de4d0243ecbbdd251d2752e8aa961c5e30"
SEAL_SHA = "1087240d465ff5fd337afcc5937f0cfe429ae9c2818f2b94b60d1ee775ee3c41"


def sha(raw): return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def require(ok, code):
    if not ok: raise RuntimeError(code)


def build(importer, snapshot):
    require(sha(importer) == IMPORTER_SHA, "QUALIFIED_IMPORTER_CHANGED")
    raw_seal = (snapshot / "SOURCE.json").read_bytes()
    require(sha(raw_seal) == SEAL_SHA, "SOURCE_SEAL_CHANGED")
    seal = json.loads(raw_seal)
    require(not seal["work_update_included"] and not seal["migration_required"], "UNEXPECTED_SCOPE")
    require(seal["gateway_base_image"] == "deerflow.local/deer-flow-gateway@" + BASE, "SEALED_BASE_CHANGED")
    require(len(seal["gateway_overlay"]) == 11, "FILE_SET_CHANGED")
    for row in seal["source_inventories"]["gateway"]:
        require(sha((snapshot / "gateway/source" / row["path"]).read_bytes()) == row["sha256"], "SOURCE_CHANGED")
    spec = importlib.util.spec_from_file_location("combined_oci_helpers", HERE / "package-glm-stream-preload.py")
    helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
    source = importer.decode().replace("\r\n", "\n")
    sources = helper.literal(source, "SOURCES")
    profile, blobs = json.loads(sources["profile"]), json.loads(sources["blobs"])
    old = copy.deepcopy(profile["roles"][0])
    require(old["manifest_digest"] == BASE, "BASE_MISMATCH")
    mr, cr = base64.b64decode(blobs[BASE], validate=True), base64.b64decode(blobs[old["config_digest"]], validate=True)
    require("sha256:" + sha(mr) == BASE and "sha256:" + sha(cr) == old["config_digest"], "BASE_BLOB_MISMATCH")
    manifest, config = json.loads(mr), json.loads(cr)
    require(config["architecture"] == "arm64" and config["os"] == "linux", "PLATFORM_MISMATCH")
    require(len(manifest["layers"]) == len(config["rootfs"]["diff_ids"]) == 69, "LAYERS_MISMATCH")
    payload, pins = io.BytesIO(), {}
    with tarfile.open(fileobj=payload, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for row in sorted(seal["gateway_overlay"], key=lambda row: row["path"]):
            name = row["path"]
            require(name.startswith(("backend/app/gateway/", "backend/packages/harness/deerflow/")) and name.endswith(".py") and ".." not in name.split("/"), "PAYLOAD_PATH")
            data = (snapshot / "gateway/overlay" / name).read_bytes()
            require(sha(data) == row["sha256"] and len(data) == row["bytes"], "PAYLOAD_CHANGED")
            compile(data, name, "exec")
            path = "app/" + name
            info = tarfile.TarInfo(path)
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            archive.addfile(info, io.BytesIO(data)); pins[path] = {"bytes": len(data), "sha256": sha(data)}
    expanded, packed = payload.getvalue(), io.BytesIO()
    with gzip.GzipFile(fileobj=packed, mode="wb", filename="", mtime=0) as stream: stream.write(expanded)
    layer = packed.getvalue(); ld, diff = "sha256:" + sha(layer), "sha256:" + sha(expanded)
    new_config = copy.deepcopy(config)
    new_config["rootfs"]["diff_ids"].append(diff)
    new_config["history"].append({"created": "2026-10-28T12:00:00Z", "created_by": "Sealed optional V4.1, employee analytics, public competition display, data-task boundary"})
    ncr = canonical(new_config); cd = "sha256:" + sha(ncr)
    new_manifest = copy.deepcopy(manifest)
    new_manifest["config"].update(digest=cd, size=len(ncr))
    new_manifest["layers"].append({"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip", "digest": ld, "size": len(layer)})
    nmr = canonical(new_manifest); md = "sha256:" + sha(nmr)
    tag = "deerflow.local/deer-flow-gateway:20261028-combined-features-r1-" + md[7:19]
    chain = ""
    for item in config["rootfs"]["diff_ids"]:
        chain = item if not chain else "sha256:" + sha((chain + " " + item).encode())
    profile["schema"] = "portal-gateway-combined-features-profile.v1"
    profile["bases"]["gateway"] = {"config_bytes": len(cr), "config_digest": old["config_digest"], "config_object_sha256": sha(canonical(config)),
        "descriptor_prefix_sha256": sha(canonical(manifest["layers"])), "diff_prefix_sha256": sha(canonical(config["rootfs"]["diff_ids"])),
        "final_chain_id": chain, "layer_count": 69, "manifest_bytes": len(mr), "manifest_digest": BASE,
        "runtime_config_sha256": old["runtime_config_sha256"], "tag": old["tag"]}
    profile["preserve"]["gateway"].update({old["tag"]: BASE, "deerflow.local/deer-flow-gateway@" + BASE: BASE})
    profile["roles"] = [{"base": "gateway", "role": "gateway-forward", "tag": tag, "config_digest": cd, "manifest_digest": md,
        "expected_layer_count": 70, "runtime_config_sha256": old["runtime_config_sha256"],
        "tail_layers": [{"blob_digest": ld, "compressed_bytes": len(layer), "diff_id": diff, "position": 70, "uncompressed_bytes_upper_bound": len(expanded)}]}]
    profile["tail_files"] = {"gateway-forward": {"files": pins, "uncompressed_bytes": len(expanded)}}
    qualification = {"base_manifest": BASE, "source_seal_sha256": SEAL_SHA, "source_files": pins,
        "production_deployed": False, "preload_only": True, "ready_for_rollout": False, "installed_image_acceptance_pending": True,
        "provider_acceptance_pending": True, "cross_pod_storage_acceptance_pending": True,
        "production_schema_migration_required": False, "frontend_change_required": True, "work_update_included": False}
    sources["profile"] = canonical(profile)
    sources["blobs"] = canonical({digest: base64.b64encode(blob).decode() for digest, blob in ((md, nmr), (cd, ncr), (ld, layer))})
    source = source.replace("portal-gateway-weknora-company", "portal-gateway-combined-features").replace("GATEWAY_WEKNORA_COMPANY", "GATEWAY_COMBINED_FEATURES").replace("--preload-gateway-weknora-company-once", "--preload-gateway-combined-features-once")
    for key, value in {"SOURCES": sources, "SOURCE_PINS": {k: {"bytes": len(v), "sha256": sha(v)} for k,v in sources.items()},
        "ROUTE_PIN": pins, "PRODUCT_QUALIFICATION": qualification, "PAYLOAD_LIMITS": {p: 262144 for p in pins}, "TAIL_LIMIT": 512 * 1024,
        "CONTROL": "/var/lib/portal-gateway-combined-features-node-20261028-r1"}.items():
        source = helper.replace_node(source, key, key + " = " + repr(value))
    source = helper.replace_node(source, "accepted_nfs_gate", "def accepted_nfs_gate():\n    require(PRODUCT_QUALIFICATION == " + repr(qualification) + ", 'candidate_evidence_changed')\n    require(ROUTE_PIN == " + repr(pins) + ", 'candidate_source_changed')", function=True)
    ast.parse(source, feature_version=(3, 7))
    compile(source, "<combined-preload>", "exec")
    receipt = {"schema": "portal-combined-gateway-preload-build.v1", "profile": profile, "qualification": qualification,
        "script_sha256": sha(source.encode()), "script_bytes": len(source.encode()), "importer_source_sha256": IMPORTER_SHA,
        "source_file_count": len(pins), "base_layers_preserved": True, "configuration_fields_preserved": True, "ready_for_rollout": False}
    return source.encode(), receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qualified-importer", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    raw, receipt = build(args.qualified_importer.read_bytes(), args.snapshot)
    with args.output.open("xb") as output: output.write(raw)
    with args.receipt.open("xb") as output: output.write(canonical(receipt) + b"\n")
    print(json.dumps({"script_bytes": len(raw), "script_sha256": sha(raw), "manifest": receipt["profile"]["roles"][0]["manifest_digest"], "ready_for_rollout": False}))
