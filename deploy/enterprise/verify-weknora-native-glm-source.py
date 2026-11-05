"""Check the restored WeKnora build sources without service or network access."""

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
LOCK = "deploy/enterprise/weknora-native-glm-source-lock-20261026.json"
MANIFEST_SHA = "43b8484b6ef5a18ed76a6a13ab3faf8a13eca8a76589d454ae0687293971e1b0"
SOURCE_ROOTS = ("cmd", "internal", "config", "migrations", "scripts", "testdata",
                "third_party", "docreader/client", "docreader/proto")
SOURCE_FILES = ("go.mod", "go.sum", "Makefile", "VERSION", "LICENSE", "docs/docs.go")
GENERATED = "third_party/anydoc-go/patched-anydoc/"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def parse_manifest(data):
    rows = {}
    for line in data.decode("utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        require(match is not None, "INVALID_MANIFEST_LINE")
        expected, name = match.groups()
        path = PurePosixPath(name)
        require(not path.is_absolute() and ".." not in path.parts and
                "\\" not in name and ":" not in name and path.as_posix() == name,
                "UNSAFE_MANIFEST_PATH")
        require(name not in rows, "DUPLICATE_MANIFEST_PATH")
        rows[name] = expected
    return rows


def patched_anydoc(raw, lock):
    require(digest(raw) == lock["sha256"], "ANYDOC_CRATE_CHANGED")
    files = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            require(path.parts[0] == "anydoc-0.1.9" and not path.is_absolute()
                    and ".." not in path.parts and "\\" not in member.name,
                    "UNSAFE_ANYDOC_PATH")
            require(member.isfile() or member.isdir(), "UNSAFE_ANYDOC_ENTRY")
            if member.isdir():
                continue
            name = PurePosixPath(*path.parts[1:]).as_posix()
            require(name not in files and member.size < 32 * 1024**2,
                    "DUPLICATE_OR_LARGE_ANYDOC_ENTRY")
            files[name] = archive.extractfile(member).read()
    original = files["src/lib.rs"]
    require(digest(original) == lock["lib_rs_before_sha256"], "ANYDOC_PATCH_INPUT_CHANGED")
    before = b"use render::markdown::document_to_markdown;"
    after = b"pub use render::markdown::document_to_markdown;"
    require(original.splitlines().count(before) == 1 and after not in original.splitlines(),
            "ANYDOC_PATCH_SHAPE_CHANGED")
    files["src/lib.rs"] = b"".join(
        after + line[len(before):] if line.rstrip(b"\r\n") == before else line
        for line in original.splitlines(keepends=True))
    require(digest(files["src/lib.rs"]) == lock["lib_rs_after_sha256"], "ANYDOC_PATCH_OUTPUT_CHANGED")
    files[".weknora-patched"] = b"0.1.9\n"
    return files


class Reader:
    def __init__(self, root, ref=None):
        self.root, self.ref, self.cache = root, ref, {}

    def prefetch(self, paths):
        if self.ref is None:
            return
        paths = sorted(set(paths) - self.cache.keys())
        if not paths:
            return
        result = subprocess.run(
            ["git", "cat-file", "--batch"], cwd=self.root,
            input="".join(f"{self.ref}:{p}\n" for p in paths).encode(),
            capture_output=True, check=True)
        stream = io.BytesIO(result.stdout)
        for path in paths:
            header = stream.readline().decode().strip().split()
            require(len(header) == 3 and header[1] == "blob", "MISSING_GIT_BLOB:" + path)
            self.cache[path] = stream.read(int(header[2]))
            require(stream.read(1) == b"\n", "INVALID_GIT_BATCH")

    def __call__(self, path):
        if self.ref is not None:
            self.prefetch([path])
            return self.cache[path]
        local = self.root / path
        require(local.is_file() and not local.is_symlink(), "MISSING_OR_LINKED_FILE:" + path)
        return local.read_bytes()

    def source_paths(self, prefix):
        if self.ref:
            paths = subprocess.check_output(
                ["git", "ls-tree", "-r", "--name-only", self.ref, "--", prefix],
                cwd=self.root).decode().splitlines()
            paths = [p.removeprefix(prefix) for p in paths]
        else:
            paths = [p.relative_to(self.root / prefix).as_posix()
                     for p in (self.root / prefix).rglob("*") if p.is_file()]
        return {p for p in paths if p in SOURCE_FILES or
                any(p.startswith(directory + "/") for directory in SOURCE_ROOTS)}


def verify(read, lock, actual_paths):
    prefix = lock["archive_prefix"]
    manifest = read(prefix + "SOURCE.SHA256")
    require(digest(manifest) == lock["source_manifest_sha256"] == MANIFEST_SHA,
            "HISTORICAL_MANIFEST_CHANGED")
    rows = parse_manifest(manifest)
    require(len(rows) == lock["manifest_files"] == 1999, "SOURCE_COUNT_CHANGED")
    for group in ("evidence_files", "historical_recipe_files", "shared_builder_files"):
        for path, expected in lock[group].items():
            require(digest(read(path)) == expected, "ARCHIVE_INPUT_CHANGED:" + path)
    generated = patched_anydoc(read(prefix + "anydoc-0.1.9.crate"), lock["anydoc"])
    vendor_rows = {p: h for p, h in rows.items() if not p.startswith(GENERATED)}
    require(actual_paths == set(vendor_rows), "VENDOR_SOURCE_INVENTORY_CHANGED")
    require(len(vendor_rows) == lock["vendor_files"] == 1925, "VENDOR_COUNT_CHANGED")
    require(len(generated) == lock["generated_anydoc_files"] == 74, "ANYDOC_COUNT_CHANGED")
    require({GENERATED + p for p in generated} == set(rows) - set(vendor_rows),
            "ANYDOC_INVENTORY_CHANGED")
    for path, expected in rows.items():
        raw = generated[path.removeprefix(GENERATED)] if path.startswith(GENERATED) else read(lock["vendor_prefix"] + path)
        require(digest(raw) == expected, "BUILD_SOURCE_CHANGED:" + path)
    receipt = json.loads(read(prefix + "operator-build-receipt-20261026.json"))
    observation = receipt["observation"]
    require(observation["build_passed"] and observation["receipt_matches_archive"] and
            observation["matches_supplied_live_binary"], "OPERATOR_RECEIPT_NOT_ACCEPTED")
    for field in ("manifest_digest", "config_digest", "archive_sha256", "built_binary_sha256"):
        require(observation[field] == lock["image"][field], "IMAGE_RECEIPT_MISMATCH:" + field)
    require(receipt["live_binary_sha256"] == observation["built_binary_sha256"], "LIVE_BINARY_MISMATCH")
    require(lock["image"]["reference"].endswith(lock["build"]["runtime_context_sha256"][:16]),
            "RUNTIME_TAG_CONTEXT_MISMATCH")
    return {"status": "HISTORICAL_BUILD_SOURCE_AND_OPERATOR_BINARY_RECEIPT_MATCH",
            "vendor_files": len(vendor_rows), "generated_anydoc_files": len(generated),
            "manifest_files": len(rows), "historical_recipe_files": len(lock["historical_recipe_files"]),
            "image_manifest_digest": lock["image"]["manifest_digest"],
            "binary_sha256": observation["built_binary_sha256"],
            "production_access": False, "image_rebuilt": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", help="Verify Git blobs at this revision, e.g. HEAD")
    args = parser.parse_args()
    read = Reader(ROOT, args.ref)
    lock = json.loads(read(LOCK))
    rows = parse_manifest(read(lock["archive_prefix"] + "SOURCE.SHA256"))
    read.prefetch([lock["vendor_prefix"] + p for p in rows if not p.startswith(GENERATED)] +
                  list(lock["evidence_files"]) + list(lock["historical_recipe_files"]) +
                  list(lock["shared_builder_files"]))
    print(json.dumps(verify(read, lock, read.source_paths(lock["vendor_prefix"])), sort_keys=True))


if __name__ == "__main__":
    main()
