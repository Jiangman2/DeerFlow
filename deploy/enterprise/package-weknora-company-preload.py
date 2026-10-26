"""Append the field-checked company viewer policy and its SSO hook only.

Retains the accepted node importer, current production base, OCI runtime config,
and all old image references. Generates a preload tool, never a rollout.
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

ROOT = Path(__file__).resolve().parents[2]
BASE = "sha256:0a47db87acd2b219ca7bd548b8635b6f4cabd85ce34161b1935817786811006c"
IMPORTER_SHA = "674cda9e53dc080c726df6cb52fcdde98a20eb8c172465b2fe545cbd211ce2e5"
IDENTITY_BASE_SHA = "af64e8d928d4d2902b9f189c4e890e0dd7f46ba816b31bfad0197534df55f256"
POLICY_SHA = "b04626806d0569e2130f0dccba48bbb0b777ee8f6b16576794b546631f1bbcae"
PATHS = ("app/gateway/weknora_identity.py", "app/gateway/weknora_company_knowledge.py")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def require(value, code):
    if not value:
        raise RuntimeError(code)


def checked_sources():
    files = {path: (ROOT / "backend" / path).read_text(encoding="utf-8") for path in PATHS}
    require(sha(files[PATHS[1]].encode()) == POLICY_SHA, "FIELD_VERIFIED_POLICY_CHANGED")
    identity = files[PATHS[0]]
    addition = "from app.gateway.weknora_company_knowledge import ensure_company_knowledge_access\n"
    hook = '''    await ensure_company_knowledge_access(
        session,
        weknora_endpoint,
        administrator=str(getattr(user, "system_role", "user") or "user").lower() == "admin",
    )
'''
    require(identity.count(addition) == identity.count(hook) == 1, "SSO_HOOK_SHAPE_CHANGED")
    require(sha(identity.replace(addition, "").replace(hook, "").encode()) == IDENTITY_BASE_SHA, "SSO_BASELINE_CHANGED")
    return files


def build(raw):
    require(sha(raw) == IMPORTER_SHA, "QUALIFIED_IMPORTER_CHANGED")
    spec = importlib.util.spec_from_file_location("company_oci_helpers", ROOT / "deploy/enterprise/package-glm-stream-preload.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    source = raw.decode().replace("\r\n", "\n")
    sources = helper.literal(source, "SOURCES")
    profile, blobs = json.loads(sources["profile"]), json.loads(sources["blobs"])
    old = profile["roles"][0]
    require(old["manifest_digest"] == BASE, "PRODUCTION_BASE_MISMATCH")
    mr, cr = base64.b64decode(blobs[BASE], validate=True), base64.b64decode(blobs[old["config_digest"]], validate=True)
    require("sha256:" + sha(mr) == BASE and "sha256:" + sha(cr) == old["config_digest"], "BASE_BLOB_MISMATCH")
    manifest, config = json.loads(mr), json.loads(cr)
    require(config["architecture"] == "arm64" and config["os"] == "linux", "BASE_PLATFORM_MISMATCH")
    require(len(manifest["layers"]) == len(config["rootfs"]["diff_ids"]) == 68, "BASE_LAYERS_MISMATCH")
    payload, pins = io.BytesIO(), {}
    with tarfile.open(fileobj=payload, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for relative, content in sorted(checked_sources().items()):
            compile(content, relative, "exec")
            path, data = "app/backend/" + relative, content.encode()
            info = tarfile.TarInfo(path)
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            archive.addfile(info, io.BytesIO(data))
            pins[path] = {"bytes": len(data), "sha256": sha(data)}
    expanded, packed = payload.getvalue(), io.BytesIO()
    with gzip.GzipFile(fileobj=packed, mode="wb", filename="", mtime=0) as stream:
        stream.write(expanded)
    layer = packed.getvalue()
    ld, diff = "sha256:" + sha(layer), "sha256:" + sha(expanded)
    new_config = copy.deepcopy(config)
    new_config["rootfs"]["diff_ids"].append(diff)
    new_config["history"].append({"created": "2026-10-24T09:00:00Z", "created_by": "Opt-in company-space native viewer enrollment during SSO"})
    ncr = canonical(new_config)
    cd = "sha256:" + sha(ncr)
    new_manifest = copy.deepcopy(manifest)
    new_manifest["config"].update(digest=cd, size=len(ncr))
    new_manifest["layers"].append({"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip", "digest": ld, "size": len(layer)})
    nmr = canonical(new_manifest)
    md = "sha256:" + sha(nmr)
    tag = "deerflow.local/deer-flow-gateway:20261024-weknora-company-r1-" + md[7:19]
    chain = ""
    for item in config["rootfs"]["diff_ids"]:
        chain = item if not chain else "sha256:" + sha((chain + " " + item).encode())
    profile["schema"] = "portal-gateway-weknora-company-profile.v1"
    profile["bases"]["gateway"] = {"config_bytes": len(cr), "config_digest": old["config_digest"], "config_object_sha256": sha(canonical(config)),
        "descriptor_prefix_sha256": sha(canonical(manifest["layers"])), "diff_prefix_sha256": sha(canonical(config["rootfs"]["diff_ids"])),
        "final_chain_id": chain, "layer_count": 68, "manifest_bytes": len(mr), "manifest_digest": BASE, "runtime_config_sha256": old["runtime_config_sha256"], "tag": old["tag"]}
    profile["preserve"]["gateway"].update({old["tag"]: BASE, "deerflow.local/deer-flow-gateway@" + BASE: BASE})
    profile["roles"] = [{"base": "gateway", "role": "gateway-forward", "tag": tag, "config_digest": cd, "manifest_digest": md,
        "expected_layer_count": 69, "runtime_config_sha256": old["runtime_config_sha256"],
        "tail_layers": [{"blob_digest": ld, "compressed_bytes": len(layer), "diff_id": diff, "position": 69, "uncompressed_bytes_upper_bound": len(expanded)}]}]
    profile["tail_files"] = {"gateway-forward": {"files": pins, "uncompressed_bytes": len(expanded)}}
    evidence = json.loads((ROOT / "deploy/enterprise/weknora-company-viewer-field-20261024-r1.json").read_text(encoding="utf-8"))
    require(evidence["candidate_policy_sha256"] == POLICY_SHA and evidence["status"] == "ORDINARY_VIEWER_CANDIDATE_ACCEPTED_NOT_DEPLOYED", "VIEWER_FIELD_ACCEPTANCE_REQUIRED")
    qualification = {"viewer_field_check": evidence, "base_manifest": BASE, "source_files": pins, "baseline_identity_sha256": IDENTITY_BASE_SHA,
        "production_deployed": False, "preload_only": True, "ready_for_rollout": False, "installed_image_acceptance_pending": True,
        "auto_enrollment_enabled": False, "production_schema_migration_required": False, "frontend_change_required": False}
    sources["profile"] = canonical(profile)
    sources["blobs"] = canonical({digest: base64.b64encode(blob).decode() for digest, blob in ((md, nmr), (cd, ncr), (ld, layer))})
    source = source.replace("portal-gateway-oa-data-qa", "portal-gateway-weknora-company").replace("GATEWAY_OA_DATA_QA", "GATEWAY_WEKNORA_COMPANY").replace("--preload-gateway-oa-data-qa-once", "--preload-gateway-weknora-company-once")
    for key, value in {"SOURCES": sources, "SOURCE_PINS": {k: {"bytes": len(v), "sha256": sha(v)} for k, v in sources.items()},
                       "ROUTE_PIN": pins, "PRODUCT_QUALIFICATION": qualification, "PAYLOAD_LIMITS": {p: 262144 for p in pins}, "TAIL_LIMIT": 512 * 1024,
                       "CONTROL": "/var/lib/portal-gateway-weknora-company-node-20261024-r1"}.items():
        source = helper.replace_node(source, key, key + " = " + repr(value))
    source = helper.replace_node(source, "accepted_nfs_gate", "def accepted_nfs_gate():\n    require(PRODUCT_QUALIFICATION == " + repr(qualification) + ", 'candidate_evidence_changed')\n    require(ROUTE_PIN == " + repr(pins) + ", 'candidate_source_changed')", function=True)
    ast.parse(source, feature_version=(3, 7))
    compile(source, "<company-preload>", "exec")
    receipt = {"schema": "portal-weknora-company-preload-build.v1", "profile": profile, "qualification": qualification,
        "script_sha256": sha(source.encode()), "script_bytes": len(source.encode()), "importer_source_sha256": IMPORTER_SHA,
        "source_file_count": 2, "base_layers_preserved": True, "configuration_fields_preserved": True, "ready_for_rollout": False}
    return source.encode(), receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qualified-importer", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    raw, receipt = build(args.qualified_importer.read_bytes())
    with args.output.open("xb") as output:
        output.write(raw)
    with args.receipt.open("xb") as output:
        output.write(canonical(receipt) + b"\n")
    print(json.dumps({"script_bytes": len(raw), "script_sha256": sha(raw), "manifest": receipt["profile"]["roles"][0]["manifest_digest"], "ready_for_rollout": False}))


if __name__ == "__main__":
    main()
