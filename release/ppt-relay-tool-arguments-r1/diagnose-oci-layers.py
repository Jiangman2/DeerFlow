"""Read ONLY staged OCI metadata; never hash/extract layers or change files.

Host Python 3.7+. Output counts and booleans only, never image ENV/config values.
This diagnostic does not approve an image or relax any build gate.
"""

import argparse
import importlib.util
import json
from pathlib import Path


def inspect(verifier, root, expected):
    if verifier.document(root / "oci-layout").get("imageLayoutVersion") != "1.0.0":
        raise ValueError("Unsupported layout")
    matches = [
        row
        for row in verifier.manifests(verifier.document(root / "index.json"))
        if row.get("annotations", {}).get(
            "io.containerd.image.name",
            row.get("annotations", {}).get("org.opencontainers.image.ref.name"),
        )
        == expected["source_image"]
    ]
    if len(matches) != 1:
        raise ValueError("Expected one matching image")
    selected, ancestry = matches[0], []
    for _ in range(4):
        kind = selected.get("mediaType")
        if kind not in verifier.INDEX_TYPES | verifier.MANIFEST_TYPES:
            raise ValueError("Unsupported manifest type")
        content = verifier.blob(root, selected, metadata=True)
        if selected["digest"] in ancestry:
            raise ValueError("Recursive index")
        ancestry.append(selected["digest"])
        if kind in verifier.MANIFEST_TYPES:
            break
        candidates = [
            row
            for row in verifier.manifests(content)
            if row.get("platform", {}).get("os") == "linux"
            and row.get("platform", {}).get("architecture") == "arm64"
            and row.get("platform", {}).get("variant", "v8") == "v8"
        ]
        if len(candidates) != 1:
            raise ValueError("Expected one ARM64 manifest")
        selected = candidates[0]
    else:
        raise ValueError("Index depth exceeded")
    descriptor = content.get("config", {})
    if (
        content.get("schemaVersion") != 2
        or descriptor.get("mediaType") not in verifier.CONFIG_TYPES
    ):
        raise ValueError("Unsupported image config")
    config = verifier.blob(root, descriptor, metadata=True)
    rootfs = config.get("rootfs")
    layers = content.get("layers")
    diff_ids = rootfs.get("diff_ids") if isinstance(rootfs, dict) else None
    layer_count = len(layers) if isinstance(layers, list) else None
    diff_count = len(diff_ids) if isinstance(diff_ids, list) else None
    invalid = (
        [
            i
            for i, value in enumerate(diff_ids)
            if not isinstance(value, str) or not verifier.DIGEST.fullmatch(value)
        ]
        if isinstance(diff_ids, list)
        else []
    )
    return {
        "oci_layer_metadata_audit": "COMPLETE",
        "production_workloads_mutated": False,
        "layers_read_or_extracted": False,
        "full_image_verified": False,
        "ready_for_rollout": False,
        "metadata_hashes_verified": True,
        "runtime_identity_match": expected["source_image_id"]
        in ancestry + [descriptor["digest"]],
        "linux_arm64": config.get("os") == "linux"
        and config.get("architecture") == "arm64"
        and config.get("variant", "v8") == "v8",
        "layers_is_array": isinstance(layers, list),
        "layer_count": layer_count,
        "rootfs_is_object": isinstance(rootfs, dict),
        "rootfs_type_is_layers": isinstance(rootfs, dict)
        and rootfs.get("type") == "layers",
        "diff_ids_is_array": isinstance(diff_ids, list),
        "diff_id_count": diff_count,
        "counts_match": layer_count is not None
        and diff_count is not None
        and layer_count == diff_count,
        "invalid_diff_id_count": len(invalid),
        "invalid_diff_id_indices_first_8": invalid[:8],
        "build1_layer_limit": 128,
        "exceeds_build1_layer_limit": layer_count is not None and layer_count > 128,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--layout", type=Path, required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location(
        "audited_oci_verifier", args.package / "verify_oci_base.py"
    )
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    result = inspect(
        verifier, args.layout, verifier.document(args.package / "BASELINE.json")
    )
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
