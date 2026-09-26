"""Check a disposable candidate OCI export; no import/rollout. Python 3.7+."""

import argparse
import hashlib
import json
from pathlib import Path

from prepare_oci import extract
from verify_oci_base import DIGEST, document, verify


def configuration(layout, manifest_digest):
    manifest = document(layout / "blobs/sha256" / manifest_digest.split(":")[1])
    digest = manifest["config"]["digest"]
    if not DIGEST.fullmatch(digest):
        raise ValueError("Invalid image config digest")
    return document(layout / "blobs/sha256" / digest.split(":")[1])


def preserved(base, candidate):
    if base.get("config") != candidate.get("config"):
        raise ValueError("Image runtime config changed outside employee source delta")
    old = base["rootfs"]["diff_ids"]
    new = candidate["rootfs"]["diff_ids"]
    if len(new) <= len(old) or new[: len(old)] != old:
        raise ValueError("Candidate no longer inherits the exact base layers")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--base-receipt", type=Path, required=True)
    args = parser.parse_args()
    digest = document(args.metadata)["containerimage.digest"]
    layout = extract(args.archive, args.output)
    result = verify(layout, {"source_image": args.image, "source_image_id": digest})
    base = configuration(args.base, document(args.base_receipt)["manifest_digest"])
    preserved(base, configuration(layout, result["manifest_digest"]))
    checksum = hashlib.sha256()
    with args.archive.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024**2), b""):
            checksum.update(chunk)
    result.update(
        image=args.image,
        archive_sha256=checksum.hexdigest(),
        runtime_config="PRESERVED",
        base_layer_prefix="PRESERVED",
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
