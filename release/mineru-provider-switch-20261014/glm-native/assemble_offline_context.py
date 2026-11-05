"""Assemble a new, credential-free local build context; never start a build.

Only allowlisted vendored source roots and checksum-verified public input
directories are copied. No kubeconfig, .env, home, build cache, partial downloads,
production snapshot, model config, or API key is included.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tarfile

from prepare_public_inputs import HERE, require, sha, write_once, json_once

SOURCE_ROOTS = ("cmd", "internal", "config", "migrations", "scripts", "testdata", "third_party",
                "docreader/client", "docreader/proto")
SOURCE_FILES = ("go.mod", "go.sum", "Makefile", "VERSION", "LICENSE", "docs/docs.go")
LOCK_HASHES = {
    "go.mod": "2f2555453ee70e6bd86e2edaf89be7bf4c85bf9f40f72206b1ad1ab239f2d34b",
    "go.sum": "0effbd17b64fd6da32e0cc9ce2fe89fe2a18d92a0384303ecfcec92bd7a6329c",
    "third_party/anydoc-go/Cargo.lock": "d60c6791a8b8dcebf6b64086687d90a4f8d2f101dd43a0f6eb0f2dbba8f62bf8",
}


def regular(path):
    s = path.lstat()
    require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1, "not_single_regular_file")
    require(s.st_size < 2*1024**3, "file_size_limit")


def source_map(source):
    files = {}
    for name in SOURCE_FILES:
        regular(source / name)
        files[name] = source / name
    for name in SOURCE_ROOTS:
        root = source / name
        require(root.is_dir() and not root.is_symlink(), "source_root_unsafe")
        for path in sorted(root.rglob("*")):
            require(not path.is_symlink(), "source_link")
            rel = path.relative_to(source)
            require(not any(x in ("target", "node_modules", ".git", "__pycache__", "patched-anydoc") for x in rel.parts), "unexpected_generated_source")
            if path.is_dir():
                continue
            regular(path)
            require(not path.name.startswith(".env") and path.suffix.lower() not in (".key", ".p12", ".pfx", ".pem", ".crt"), "source_credential_like_file")
            files[rel.as_posix()] = path
    for rel, expected in LOCK_HASHES.items():
        require(sha(files[rel]) == expected, "dependency_lock_changed:" + rel)
    return files


def anydoc_members(archive, expected):
    regular(archive)
    require(sha(archive) == expected, "anydoc_crate_digest")
    result = {}
    with tarfile.open(archive, "r:gz") as t:
        for entry in t:
            p = PurePosixPath(entry.name)
            require(not p.is_absolute() and all(x not in ("", ".", "..") for x in p.parts), "unsafe_crate_path")
            require(p.parts[0] == "anydoc-0.1.9" and "\\" not in entry.name, "crate_root")
            require(entry.isdir() or entry.isfile(), "crate_link_or_special")
            if entry.isdir():
                continue
            rel = PurePosixPath(*p.parts[1:]).as_posix()
            require(rel not in result and 0 <= entry.size < 32*1024**2, "crate_entry_duplicate_or_large")
            require(not any(":" in x or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?", x) for x in p.parts), "nonportable_crate_path")
            result[rel] = t.extractfile(entry).read()
    raw = result["src/lib.rs"]
    before = b"use render::markdown::document_to_markdown;"
    after = b"pub use render::markdown::document_to_markdown;"
    require(raw.splitlines().count(before) == 1 and after not in raw.splitlines(), "anydoc_patch_shape")
    # Replace exactly the line already owned by scripts/build-anydoc-lib.sh.
    lines = raw.splitlines(keepends=True)
    result["src/lib.rs"] = b"".join(after + line[len(before):] if line.rstrip(b"\r\n") == before else line for line in lines)
    result[".weknora-patched"] = b"0.1.9\n"
    return result, {"lib_rs_before_sha256": hashlib.sha256(raw).hexdigest(), "lib_rs_after_sha256": hashlib.sha256(result["src/lib.rs"]).hexdigest(), "changed_upstream_files": ["src/lib.rs"]}


def copy_exact(src, dst):
    regular(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with src.open("rb") as inp, dst.open("xb") as out:
        shutil.copyfileobj(inp, out, 1024**2)
    require(sha(src) == sha(dst), "copy_digest_mismatch")


def manifest(root, name):
    rows = []
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), "context_link")
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            require("\n" not in rel and "\r" not in rel and "\\" not in rel, "manifest_path")
            rows.append(sha(path) + "  " + rel + "\n")
    write_once(root / name, "".join(rows).encode())
    return len(rows), sha(root / name)


def assemble(source, inputs, output):
    require(not os.path.lexists(output), "new_context_required")
    require(not inputs.is_symlink() and not source.is_symlink(), "input_root_link")
    lock = json.loads((HERE / "BUILD-INPUT-LOCK.json").read_bytes())
    receipts = {}
    for phase in ("builder", "rust", "crates", "go", "go-offline", "sqlite"):
        path = inputs / (phase + "-PASS.json")
        regular(path)
        receipts[phase] = json.loads(path.read_bytes())
    require(receipts["builder"]["manifest_digest"] == lock["builder"]["manifest_digest"], "builder_receipt")
    require(receipts["go"]["go_mod_sha256"] == LOCK_HASHES["go.mod"] and receipts["go"]["go_sum_sha256"] == LOCK_HASHES["go.sum"], "go_receipt")
    require(receipts["crates"]["cargo_lock_sha256"] == LOCK_HASHES["third_party/anydoc-go/Cargo.lock"], "cargo_receipt")
    for component in lock["rust_components"]:
        require(sha(inputs / "rust" / component["url"].rsplit("/", 1)[1]) == component["sha256"], "rust_hash")
    sqlite = json.loads((HERE / "SQLITE-BUILD-LOCK.json").read_bytes())
    for item in sqlite["packages"]:
        regular(inputs / "debian" / item["filename"])
        require(sha(inputs / "debian" / item["filename"]) == item["sha256"], "sqlite_hash")
    files = source_map(source)
    patched, patch_receipt = anydoc_members(inputs / "anydoc-0.1.9.crate", lock["anydoc"]["sha256"])
    require(receipts["go-offline"].get("empty_cache_dependency_resolution") == "PASS", "offline_go_required")
    for name in ("lib_rs_before_sha256", "lib_rs_after_sha256"):
        require(patch_receipt[name] == lock["anydoc"][name], "anydoc_patch_hash")
    output.mkdir(parents=True, mode=0o700)
    for name, path in files.items():
        copy_exact(path, output / "source" / name)
    for name, raw in patched.items():
        write_once(output / "source/third_party/anydoc-go/patched-anydoc" / name, raw)
    for subtree in ("go-proxy", "cargo-local-registry", "rust", "debian"):
        root = inputs / subtree
        require(root.is_dir() and not root.is_symlink(), "offline_input_missing")
        for src in sorted(root.rglob("*")):
            require(not src.is_symlink(), "offline_input_link")
            if src.is_file():
                if ".partial-" in src.name:
                    continue
                copy_exact(src, output / "offline" / subtree / src.relative_to(root))
    copy_exact(HERE / "build_native.sh", output / "build_native.sh")
    copy_exact(HERE / "unpack_rust.py", output / "unpack_rust.py")
    copy_exact(HERE / "Dockerfile.offline", output / "Dockerfile.weknora")
    source_count, source_sha = manifest(output / "source", "SOURCE.SHA256")
    input_count, input_sha = manifest(output / "offline", "INPUTS.SHA256")
    result = {"context_assembled": True, "source_files": source_count, "input_files": input_count,
              "source_manifest_sha256": source_sha, "offline_manifest_sha256": input_sha,
              "anydoc_patch": patch_receipt, "input_receipts": receipts,
              "isolated_build_driver": "SEPARATE_CHECKSUM_BOUND_SERVER_PACKAGE_REQUIRED", "linux_build_executed": False,
              "credentials_included": False, "ready_for_build": False, "ready_for_rollout": False}
    json_once(output / "CONTEXT.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--inputs", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(assemble(args.source.resolve(), args.inputs.resolve(), args.output.absolute()), sort_keys=True))
