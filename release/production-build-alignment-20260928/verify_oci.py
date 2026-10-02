"""Check a rebuilt OCI archive against the captured production image identity.

This tool reads an archive without extracting it. A matching manifest digest
alone is insufficient if a layer blob in the archive is missing or corrupted,
so every referenced blob is hashed before reporting success.
"""

import argparse
import hashlib
import json
import re
import tarfile
from pathlib import Path

DIGEST = re.compile(r"sha256:([0-9a-f]{64})\Z")


def expected_identity(lock, image):
    matches = []
    for workload in lock["workloads"]:
        for role in ("containers", "init_containers"):
            matches.extend(row for row in workload[role] if row["image"] == image)
    matches.extend(row for row in lock["runtime_images"] if row["image"] == image)
    if not matches:
        raise ValueError("image_not_in_production_lock")
    identities = {
        (row.get("manifest_digest"), row.get("config_digest")) for row in matches
    }
    if len(identities) != 1:
        raise ValueError("conflicting_production_image_identities")
    manifest, config = identities.pop()
    if not manifest or not config:
        raise ValueError("production_manifest_or_config_digest_not_collected")
    return manifest, config


def verify_archive(archive, manifest_digest, config_digest):
    for digest in (manifest_digest, config_digest):
        if not DIGEST.fullmatch(digest):
            raise ValueError("invalid_expected_digest")
    with tarfile.open(archive, "r:*") as tar:
        members = {}
        for member in tar:
            name = member.name[2:] if member.name.startswith("./") else member.name
            if name == "index.json" or name.startswith("blobs/sha256/"):
                if name in members or not member.isfile():
                    raise ValueError("duplicate_or_nonfile_oci_member")
                members[name] = member

        def blob(digest):
            match = DIGEST.fullmatch(digest)
            if not match:
                raise ValueError("invalid_oci_digest")
            name = "blobs/sha256/" + match.group(1)
            member = members.get(name)
            if member is None:
                raise ValueError("missing_oci_blob:" + digest)
            collect = digest in (manifest_digest, config_digest)
            if collect and member.size > 16 * 1024 * 1024:
                raise ValueError("oversized_oci_json_blob")
            stream = tar.extractfile(member)
            if stream is None:
                raise ValueError("unreadable_oci_blob:" + digest)
            data = bytearray()
            actual = hashlib.sha256()
            with stream:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    actual.update(chunk)
                    if collect:
                        data.extend(chunk)
            if actual.hexdigest() != match.group(1):
                raise ValueError("corrupt_oci_blob:" + digest)
            return bytes(data)

        index_member = members.get("index.json")
        if index_member is None:
            raise ValueError("missing_oci_index")
        with tar.extractfile(index_member) as stream:
            index = json.load(stream)
        reachable = set()
        pending = list(index.get("manifests", []))
        while pending:
            descriptor = pending.pop()
            digest = descriptor.get("digest", "")
            if digest in reachable:
                continue
            reachable.add(digest)
            if digest == manifest_digest:
                continue
            if not DIGEST.fullmatch(digest):
                raise ValueError("invalid_oci_index_descriptor")
            name = "blobs/sha256/" + digest[7:]
            member = members.get(name)
            if member is None or member.size > 16 * 1024 * 1024:
                raise ValueError("missing_or_oversized_oci_descriptor")
            with tar.extractfile(member) as stream:
                content = stream.read()
            if hashlib.sha256(content).hexdigest() != digest[7:]:
                raise ValueError("corrupt_oci_descriptor")
            document = json.loads(content)
            pending.extend(document.get("manifests", []))
        if manifest_digest not in reachable:
            raise ValueError("expected_manifest_not_in_oci_index")

        manifest = json.loads(blob(manifest_digest))
        observed_config = manifest.get("config", {}).get("digest")
        if observed_config != config_digest:
            raise ValueError("config_digest_mismatch")
        config = json.loads(blob(config_digest))
        if config.get("architecture") != "arm64" or config.get("os") != "linux":
            raise ValueError("unexpected_image_platform")
        layers = manifest.get("layers", [])
        if not layers:
            raise ValueError("image_has_no_layers")
        for layer in layers:
            blob(layer.get("digest", ""))
        return {
            "status": "PASS",
            "manifest_digest": manifest_digest,
            "config_digest": config_digest,
            "layers_verified": len(layers),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument(
        "--lock",
        type=Path,
        default=Path(__file__).with_name("PRODUCTION-IMAGE-LOCK.json"),
    )
    args = parser.parse_args()
    lock = json.loads(args.lock.read_text(encoding="utf-8"))
    manifest, config = expected_identity(lock, args.image)
    result = verify_archive(args.archive, manifest, config)
    print(json.dumps({"image": args.image, **result}, sort_keys=True))


if __name__ == "__main__":
    main()
