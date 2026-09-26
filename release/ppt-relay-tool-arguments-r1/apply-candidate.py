"""One-file image-only replacement; never run against a live Gateway container."""

import hashlib
import json
import os
import sys
from pathlib import Path

SOURCE = "app/gateway/codex_responses_adapter.py"
BASE_SHA = "be67018d948af473d32db24611b875fa3cb641855b472a94237c140e309892d7"


def apply(backend, package):
    backend = backend.resolve(strict=True)
    destination = backend / SOURCE
    if (
        destination.is_symlink()
        or backend not in destination.resolve(strict=True).parents
    ):
        raise ValueError("unsafe source path")
    if hashlib.sha256(destination.read_bytes()).hexdigest() != BASE_SHA:
        raise ValueError("exact source baseline mismatch; do not overwrite")
    target = (package / "codex_responses_adapter.py").read_bytes()
    lock = json.loads((package / "PATCH.json").read_text(encoding="utf-8"))
    if (
        lock["source_sha256"] != BASE_SHA
        or hashlib.sha256(target).hexdigest() != lock["target_sha256"]
    ):
        raise ValueError("payload integrity mismatch")
    compile(target, str(destination), "exec")
    temporary = destination.with_name(destination.name + ".ppt-relay-r1.tmp")
    with temporary.open("xb") as stream:
        stream.write(target)
    os.chmod(str(temporary), destination.stat().st_mode)
    os.replace(str(temporary), str(destination))
    print("ppt_relay_candidate_patch=PASS changed_files=1")


if __name__ == "__main__":
    apply(Path(sys.argv[1]), Path(sys.argv[2]))
