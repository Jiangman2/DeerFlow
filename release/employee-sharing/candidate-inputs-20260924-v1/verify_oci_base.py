"""Read-only, offline verification of an exported ARM64 base image layout.

Check the selected manifest/config against the captured CRI identity and hash
every referenced local layer. Never fetch absent blobs, extract archives, print
image environment/credentials, or treat this as release acceptance. Supports
Python 3.7+ on the enterprise control host.

Format references: https://github.com/opencontainers/image-spec/blob/main/image-layout.md
https://github.com/opencontainers/image-spec/blob/main/descriptor.md
https://github.com/opencontainers/image-spec/blob/main/image-index.md
"""

import argparse
import hashlib
import json
import re
import stat
from pathlib import Path

OCI = "application/vnd.oci.image."
INDEX_TYPES = {
    OCI + "index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}
MANIFEST_TYPES = {
    OCI + "manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
}
CONFIG_TYPES = {
    OCI + "config.v1+json",
    "application/vnd.docker.container.image.v1+json",
}
LAYER_TYPES = {
    OCI + "layer.v1.tar",
    OCI + "layer.v1.tar+gzip",
    OCI + "layer.v1.tar+zstd",
    "application/vnd.docker.image.rootfs.diff.tar.gzip",
}
DIGEST = re.compile(r"sha256:[a-f0-9]{64}\Z")
META_LIMIT = 16 * 1024**2
LAYER_LIMIT = 32 * 1024**3


def unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate OCI metadata key")
        result[name] = value
    return result


def regular(path, limit):
    for parent in path.parents:
        info = parent.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or getattr(parent, "is_junction", lambda: False)()
        ):
            raise ValueError("Linked OCI ancestor")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ValueError("OCI input is not a bounded regular file")
    return info


def document(path):
    regular(path, META_LIMIT)
    result = json.loads(path.read_bytes(), object_pairs_hook=unique_object)
    if not isinstance(result, dict):
        raise ValueError("OCI metadata must be an object")
    return result


def blob(root, descriptor, *, metadata):
    if not isinstance(descriptor, dict) or not DIGEST.fullmatch(
        descriptor.get("digest", "")
    ):
        raise ValueError("Invalid OCI descriptor digest")
    if descriptor.get("urls") or "data" in descriptor:
        raise ValueError(
            "External or embedded OCI content is outside offline export scope"
        )
    size = descriptor.get("size")
    limit = META_LIMIT if metadata else LAYER_LIMIT
    if type(size) is not int or not 0 <= size <= limit:
        raise ValueError("Invalid OCI descriptor size")
    path = root / "blobs/sha256" / descriptor["digest"].split(":")[1]
    before = regular(path, limit)
    if before.st_size != size:
        raise ValueError("OCI blob size mismatch")
    digest = hashlib.sha256()
    chunks = []
    with path.open("rb") as stream:
        total = 0
        while True:
            content = stream.read(1024**2)
            if not content:
                break
            total += len(content)
            if total > size:
                raise ValueError("OCI blob changed while reading")
            digest.update(content)
            if metadata:
                chunks.append(content)
    after = regular(path, limit)
    if (
        total != size
        or digest.hexdigest() != descriptor["digest"].split(":")[1]
        or (before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise ValueError("OCI blob checksum mismatch or concurrent change")
    if metadata:
        result = json.loads(b"".join(chunks), object_pairs_hook=unique_object)
        if not isinstance(result, dict):
            raise ValueError("OCI metadata must be an object")
        return result
    return None


def manifests(index):
    rows = index.get("manifests")
    if (
        index.get("schemaVersion") != 2
        or not isinstance(rows, list)
        or not 1 <= len(rows) <= 128
        or not all(isinstance(row, dict) for row in rows)
    ):
        raise ValueError("Invalid OCI index")
    return rows


def verify(root, expected):
    root = Path(root).absolute()
    if document(root / "oci-layout").get("imageLayoutVersion") != "1.0.0":
        raise ValueError("Unsupported OCI layout version")
    runtime_id = expected["source_image_id"]
    if not DIGEST.fullmatch(runtime_id):
        raise ValueError("Invalid audited image identity")
    rows = manifests(document(root / "index.json"))
    matches = [
        row
        for row in rows
        if row.get("annotations", {}).get(
            "io.containerd.image.name",
            row.get("annotations", {}).get("org.opencontainers.image.ref.name"),
        )
        == expected["source_image"]
    ]
    if len(matches) != 1:
        raise ValueError("Expected exactly one matching offline image name")
    selected = matches[0]
    ancestry = []
    for _ in range(4):
        media_type = selected.get("mediaType")
        if media_type not in INDEX_TYPES | MANIFEST_TYPES:
            raise ValueError("Unsupported OCI manifest type")
        content = blob(root, selected, metadata=True)
        if selected["digest"] in ancestry:
            raise ValueError("Recursive OCI index")
        ancestry.append(selected["digest"])
        if media_type in MANIFEST_TYPES:
            break
        candidates = [
            row
            for row in manifests(content)
            if row.get("platform", {}).get("os") == "linux"
            and row.get("platform", {}).get("architecture") == "arm64"
            and row.get("platform", {}).get("variant", "v8") == "v8"
        ]
        if len(candidates) != 1:
            raise ValueError("Expected exactly one linux/arm64/v8 image")
        selected = candidates[0]
    else:
        raise ValueError("OCI index depth exceeded")
    if (
        content.get("schemaVersion") != 2
        or content.get("config", {}).get("mediaType") not in CONFIG_TYPES
    ):
        raise ValueError("Invalid image configuration descriptor")
    config = blob(root, content["config"], metadata=True)
    if (
        config.get("os") != "linux"
        or config.get("architecture") != "arm64"
        or config.get("variant", "v8") != "v8"
    ):
        raise ValueError("Expected linux/arm64/v8 configuration")
    if runtime_id not in ancestry + [content["config"]["digest"]]:
        raise ValueError("Audited runtime identity differs from exported base")
    layers = content.get("layers")
    diff_ids = config.get("rootfs", {}).get("diff_ids")
    if (
        not isinstance(layers, list)
        or not 1 <= len(layers) <= 128
        or config.get("rootfs", {}).get("type") != "layers"
        or not isinstance(diff_ids, list)
        or len(diff_ids) != len(layers)
        or not all(DIGEST.fullmatch(item) for item in diff_ids)
    ):
        raise ValueError("Invalid image layer graph")
    total = 0
    for layer in layers:
        if not isinstance(layer, dict) or layer.get("mediaType") not in LAYER_TYPES:
            raise ValueError("Unsupported image layer type")
        blob(root, layer, metadata=False)
        total += layer["size"]
        if total > 64 * 1024**3:
            raise ValueError("Offline base exceeds size bound")
    return {
        "audited_oci_base": "PASS",
        "manifest_digest": selected["digest"],
        "config_digest": content["config"]["digest"],
        "layers_verified": len(layers),
        "compressed_layer_bytes": total,
        "ready_for_rollout": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("layout", type=Path)
    parser.add_argument("delta", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.layout, document(args.delta)), sort_keys=True))
