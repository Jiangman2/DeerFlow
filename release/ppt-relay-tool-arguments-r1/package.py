"""Freeze only this adapter, offline checks, and read-only audit; never HEAD."""

import argparse
import hashlib
import json
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
NAME = "deerflow-ppt-relay-tool-arguments-20260924-r1-candidate"
BASE_SHA = "be67018d948af473d32db24611b875fa3cb641855b472a94237c140e309892d7"
FILES = (
    "collect-runtime.py",
    "apply-candidate.py",
    "smoke.py",
    "Dockerfile.gateway",
    "README.md",
    "VERIFICATION.md",
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def package(output):
    archive = output / (NAME + ".tar.gz")
    checksum = output / (NAME + ".tar.gz.sha256")
    if archive.exists() or checksum.exists():
        raise ValueError("immutable candidate output already exists")
    source = REPO / "backend/app/gateway/codex_responses_adapter.py"
    payload = source.read_bytes().replace(b"\r\n", b"\n")
    compile(payload, str(source), "exec")
    with tempfile.TemporaryDirectory(prefix="ppt-relay-candidate-") as directory:
        root = Path(directory) / NAME
        root.mkdir()
        for name in FILES:
            (root / name).write_bytes(
                (HERE / name).read_bytes().replace(b"\r\n", b"\n")
            )
        (root / "codex_responses_adapter.py").write_bytes(payload)
        lock = {
            "source_sha256": BASE_SHA,
            "target_sha256": sha(payload),
            "changed_image_files": [
                "/app/backend/app/gateway/codex_responses_adapter.py"
            ],
            "production_rollout_ready": False,
            "required_gates": [
                "live_source_audit",
                "exact_arm64_base_offline_build",
                "isolated_live_tool_roundtrip",
                "maintenance_and_rollback",
            ],
        }
        (root / "PATCH.json").write_text(
            json.dumps(lock, indent=2) + "\n", encoding="utf-8"
        )
        manifest = "".join(
            sha(p.read_bytes()) + "  " + p.name + "\n" for p in sorted(root.iterdir())
        )
        (root / "PACKAGE.SHA256").write_bytes(manifest.encode("ascii"))

        def normalized(info):
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            info.mode = 0o755 if info.isdir() else 0o644
            return info

        with (
            archive.open("xb") as stream,
            tarfile.open(fileobj=stream, mode="w:gz") as bundle,
        ):
            bundle.add(root, arcname=NAME, filter=normalized)
    with checksum.open("x", encoding="ascii", newline="\n") as stream:
        stream.write(sha(archive.read_bytes()) + "  " + archive.name + "\n")
    with tarfile.open(archive, "r:gz") as bundle:
        members = {m.name: m for m in bundle.getmembers() if m.isfile()}
        for line in (
            bundle.extractfile(NAME + "/PACKAGE.SHA256").read().decode().splitlines()
        ):
            digest, name = line.split("  ", 1)
            if sha(bundle.extractfile(members[NAME + "/" + name]).read()) != digest:
                raise ValueError("archive verification failed")
    print("candidate_package=" + str(archive))
    print("candidate_sha256=" + sha(archive.read_bytes()))
    print("production_rollout_ready=no")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    package(parser.parse_args().output)
