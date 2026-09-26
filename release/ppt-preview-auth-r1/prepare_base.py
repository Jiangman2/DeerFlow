"""Extract and verify one exact local ARM64 OCI image without tar path escapes."""

import argparse
import hashlib
import json
import re
import tarfile
from pathlib import Path


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def blob(root: Path, descriptor: dict) -> dict:
    digest = descriptor.get("digest", "")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        raise ValueError("Invalid OCI descriptor")
    payload = (root / "blobs" / "sha256" / digest[7:]).read_bytes()
    if sha(payload) != digest[7:] or descriptor.get("size", len(payload)) != len(payload):
        raise ValueError("OCI blob mismatch")
    return json.loads(payload)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("image")
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Output directory already exists")
    args.output.mkdir(parents=True)
    count = 0
    with tarfile.open(args.archive, "r:") as archive:
        for member in archive:
            name = member.name[2:] if member.name.startswith("./") else member.name
            if member.isdir():
                continue
            if not member.isfile() or not (
                name in {"index.json", "oci-layout"}
                or re.fullmatch(r"blobs/sha256/[a-f0-9]{64}", name)
            ):
                raise ValueError("Unsafe or unexpected OCI member: " + name)
            target = args.output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as destination:
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError("Missing OCI member bytes")
                digest = hashlib.sha256()
                copied = 0
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    copied += len(block)
                    digest.update(block)
                    destination.write(block)
            if copied != member.size:
                raise ValueError("Truncated OCI member")
            if name.startswith("blobs/") and digest.hexdigest() != name.rsplit("/", 1)[1]:
                raise ValueError("OCI blob checksum mismatch")
            count += 1
    if count < 4:
        raise ValueError("Incomplete OCI archive")
    index = json.loads((args.output / "index.json").read_bytes())
    manifests = index.get("manifests", [])
    matches = [
        item
        for item in manifests
        if args.image
        in item.get("annotations", {}).values()
    ]
    if len(matches) != 1:
        raise ValueError("Expected exactly one matching image manifest")
    manifest = blob(args.output, matches[0])
    if "config" not in manifest:
        raise ValueError("Expected single-platform OCI manifest")
    config = blob(args.output, manifest["config"])
    if config.get("os") != "linux" or config.get("architecture") != "arm64":
        raise ValueError("Expected Linux ARM64 OCI image")
    for layer in manifest.get("layers", []):
        digest = layer.get("digest", "")
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
            raise ValueError("Invalid layer descriptor")
        if not (args.output / "blobs" / "sha256" / digest[7:]).is_file():
            raise ValueError("Missing OCI layer")
    (args.output / "manifest.digest").write_text(matches[0]["digest"] + "\n")
    print(
        "preview_oci_base=PASS image={} manifest={} config={}".format(
            args.image, matches[0]["digest"], manifest["config"]["digest"]
        )
    )


if __name__ == "__main__":
    main()
