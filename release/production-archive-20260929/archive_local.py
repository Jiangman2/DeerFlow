"""Preserve local release evidence without publishing raw packages or executing them."""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import tarfile

PRODUCTION_COMMIT = "523f1f0fbc6145c454b5a1430d60775fe7215150"
PINNED = {
    "production-source-snapshot-20260928T034713Z-ik2wlooj.tar.gz": "c55e4dfbe43899df8f25f1659b242193da9e3b4e55d662af0f09457800ee208d",
    "production-source-snapshot-20260928T042326Z-9aza7iit.tar.gz": "ccdf9cb0714d0f4acfef2b5b6b027096d9f1d15439894c0329c1a7e5591fc457",
    "production-source-snapshot-20260928T043123Z-acm9y5ot.tar.gz": "d776e10cdf39b671e2ef2a3166a0acfc092651c68887b4a1c1450aad0c3ded6e",
    "production-source-supplement-r7-20260928T062128Z.tar.gz": "d6ae36377a196bd882f0a89c4433284217326c3b7706af3dd86af0a70bcad49b",
    "production-external-source-r8-20260928T080436Z.tar.gz": "264fd43a02caec2a6988fe20c9e4f587f37915e9e52dd9a588a636f50563bc75",
    "ppt-preview-r4-rollout-result-OhLlw4ad.json": "58699339fd3d2f013936a3f221e06b0ce0f3c5246be499a4215eeb10dda8373b",
}
RECEIPTS = (
    "ppt-runtime-r2-rollout-result-bP7mLQtM.json",
    "ppt-context-rollout-result-TatXcjmM.json",
    "ppt-receipt-r3-any-active-result-RuocCYGS.json",
)
MAX_FILE = 1024 * 1024 * 1024
MAX_TOTAL = 5 * 1024 * 1024 * 1024


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def safe_name(name):
    path = PurePosixPath(name)
    return (
        bool(name)
        and str(path) == name
        and not path.is_absolute()
        and ".." not in path.parts
        and "\\" not in name
        and not any(ord(c) < 32 for c in name)
    )


def signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def gather(repository, downloads):
    entries = []
    for label, root in (
        ("historical-local", repository / "outputs"),
        ("ppt-releases", repository / "release/ppt-runtime-repair-r1/outputs"),
    ):
        if root.is_symlink() or not root.is_dir():
            raise ValueError("missing_or_linked_package_root")
        for path in sorted(root.iterdir()):
            if path.name.endswith((".tar.gz", ".zip")) and path.is_file():
                entries.append((label + "/" + path.name, path))
                sidecar = path.with_name(path.name + ".sha256")
                if sidecar.exists():
                    entries.append((label + "/" + sidecar.name, sidecar))
    for name in PINNED:
        entries.append(("server-evidence/" + name, downloads / name))
    missing = []
    for name in RECEIPTS:
        path = downloads / name
        if path.is_file():
            entries.append(("rollout-receipts/" + name, path))
        else:
            missing.append(name)
    return entries, missing


def pack(entries, target):
    """Write originals unchanged, then verify the entire resulting archive."""
    rows = []
    seen = set()
    total = 0
    with target.open("xb") as raw_output:
        with tarfile.open(
            fileobj=raw_output, mode="w", format=tarfile.PAX_FORMAT
        ) as bundle:
            for number, (name, path) in enumerate(entries, 1):
                if not safe_name(name) or name in seen or name == "MANIFEST.json":
                    raise ValueError("unsafe_or_duplicate_member")
                seen.add(name)
                before = path.lstat()
                if (
                    not stat.S_ISREG(before.st_mode)
                    or path.is_symlink()
                    or getattr(before, "st_file_attributes", 0) & 0x400
                    or before.st_size > MAX_FILE
                ):
                    raise ValueError("unsafe_input: " + name)
                total += before.st_size
                if total > MAX_TOTAL:
                    raise ValueError("archive_size_limit")
                # Bound memory to one input; original nested archives remain opaque.
                with path.open("rb") as source:
                    if signature(os.fstat(source.fileno())) != signature(before):
                        raise ValueError("input_replaced")
                    content = source.read(before.st_size + 1)
                if len(content) != before.st_size or signature(
                    path.lstat()
                ) != signature(before):
                    raise ValueError("input_changed")
                checksum = hashlib.sha256(content).hexdigest()
                expected = PINNED.get(path.name)
                if expected and checksum != expected:
                    raise ValueError("pinned_evidence_digest_mismatch: " + path.name)
                member = tarfile.TarInfo(name)
                member.size = len(content)
                member.mode = 0o600
                bundle.addfile(member, io.BytesIO(content))
                rows.append(
                    {
                        "name": name,
                        "bytes": len(content),
                        "sha256": checksum,
                        "provenance": "PINNED_EVIDENCE"
                        if expected
                        else "LOCAL_ORIGINAL_UNCLASSIFIED",
                    }
                )
                if number % 40 == 0:
                    print(
                        "archive_copy files={} bytes={}".format(number, total),
                        flush=True,
                    )
            manifest = {
                "schema": 1,
                "classification": "PRIVATE_REVIEW_NOT_FOR_GITHUB",
                "production_commit": PRODUCTION_COMMIT,
                "files": rows,
                "historical_packages": "DO_NOT_EXECUTE_AS_A_ROLLBACK_PLAN",
                "offsite_backup": "PENDING",
                "server_images_included": False,
            }
            payload = (
                json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
            ).encode("utf-8")
            member = tarfile.TarInfo("MANIFEST.json")
            member.size = len(payload)
            member.mode = 0o600
            bundle.addfile(member, io.BytesIO(payload))
    verify(target)
    return manifest


def verify(target):
    actual = {}
    manifest = None
    total = 0
    with tarfile.open(target, "r|") as bundle:
        for member in bundle:
            if (
                not member.isfile()
                or not safe_name(member.name)
                or member.name in actual
                or manifest is not None
                or member.size > MAX_FILE
                or len(actual) > 5000
            ):
                raise ValueError("unsafe_archive_member")
            total += member.size
            if total > MAX_TOTAL + 2 * 1024 * 1024:
                raise ValueError("archive_size_limit")
            source = bundle.extractfile(member)
            if member.name == "MANIFEST.json":
                if member.size > 2 * 1024 * 1024:
                    raise ValueError("oversized_manifest")
                manifest = json.load(source)
            else:
                value = hashlib.sha256()
                for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
                    value.update(block)
                actual[member.name] = (member.size, value.hexdigest())
    if not manifest or manifest.get("schema") != 1:
        raise ValueError("missing_manifest")
    expected = {row["name"]: (row["bytes"], row["sha256"]) for row in manifest["files"]}
    if len(expected) != len(manifest["files"]) or expected != actual:
        raise ValueError("member_integrity_mismatch")
    return len(actual)


def source_snapshot(repository, output, commit=PRODUCTION_COMMIT):
    # git archive applies eol/export attributes. Read raw Git objects so this
    # snapshot also preserves LF blobs with eol=crlf and export-ignored files.
    target = output / ("production-source-" + commit[:8] + "-raw.tar.gz")
    expected = {}
    for record in subprocess.check_output(
        ["git", "-C", str(repository), "ls-tree", "-rz", commit]
    ).split(b"\0"):
        if record:
            meta, name = record.split(b"\t", 1)
            mode, kind, oid = meta.split()
            if kind != b"blob":
                raise ValueError("gitlink_requires_separate_backup")
            expected[name.decode("utf-8")] = (mode, oid.decode("ascii"))
    with target.open("xb") as stream:
        batch = subprocess.Popen(
            ["git", "-C", str(repository), "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        try:
            with tarfile.open(
                fileobj=stream, mode="w:gz", compresslevel=3, format=tarfile.PAX_FORMAT
            ) as bundle:
                for number, (name, (mode, oid)) in enumerate(expected.items(), 1):
                    if not safe_name(name) or mode not in (
                        b"100644",
                        b"100755",
                        b"120000",
                    ):
                        raise ValueError("unsupported_git_entry")
                    batch.stdin.write((oid + "\n").encode("ascii"))
                    batch.stdin.flush()
                    header = batch.stdout.readline().split()
                    if len(header) != 3 or header[:2] != [oid.encode("ascii"), b"blob"]:
                        raise ValueError("git_object_unavailable")
                    size = int(header[2])
                    if not 0 <= size <= MAX_FILE:
                        raise ValueError("git_blob_size_limit")
                    data = batch.stdout.read(size)
                    if len(data) != size or batch.stdout.read(1) != b"\n":
                        raise ValueError("truncated_git_object")
                    calculated = hashlib.sha1(
                        ("blob " + str(size) + "\0").encode() + data
                    ).hexdigest()
                    if calculated != oid:
                        raise ValueError("git_object_integrity_mismatch")
                    member = tarfile.TarInfo("source/" + name)
                    member.mode = 0o755 if mode == b"100755" else 0o644
                    if mode == b"120000":
                        member.type = tarfile.SYMTYPE
                        member.linkname = data.decode("utf-8")
                        bundle.addfile(member)
                    else:
                        member.size = size
                        bundle.addfile(member, io.BytesIO(data))
                    if number % 5000 == 0:
                        print("source_snapshot files=" + str(number), flush=True)
            batch.stdin.close()
            if batch.wait(timeout=30) != 0:
                raise ValueError("git_object_reader_failed")
        finally:
            if batch.poll() is None:
                batch.kill()
                batch.wait()
            batch.stdout.close()
            if not batch.stdin.closed:
                batch.stdin.close()
    seen = set()
    with tarfile.open(target, "r|gz") as bundle:
        for member in bundle:
            if member.isdir():
                continue
            if not member.name.startswith("source/") or not safe_name(member.name):
                raise ValueError("unsafe_git_archive_member")
            relative = member.name[len("source/") :]
            if relative in seen or relative not in expected:
                raise ValueError("unexpected_git_archive_member")
            mode, expected_oid = expected[relative]
            if member.issym() and mode == b"120000":
                data = member.linkname.encode("utf-8")
            elif member.isfile() and mode != b"120000":
                data = bundle.extractfile(member).read()
            else:
                raise ValueError("git_archive_type_changed")
            oid = hashlib.sha1(
                ("blob " + str(len(data)) + "\0").encode() + data
            ).hexdigest()
            if oid != expected_oid:
                raise ValueError("git_archive_bytes_changed: " + relative)
            seen.add(relative)
    if seen != set(expected):
        raise ValueError("git_archive_missing_files")
    return {
        "name": target.name,
        "bytes": target.stat().st_size,
        "sha256": digest(target),
        "git_commit": commit,
        "verified_git_blobs": len(seen),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path)
    parser.add_argument("--downloads", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Verify and reuse an existing private bundle; never overwrite outputs",
    )
    args = parser.parse_args()
    if args.verify:
        print("archive_members_verified=" + str(verify(args.verify)))
        return
    if not all((args.repository, args.downloads, args.output)):
        parser.error("provide --repository, --downloads and a new --output directory")
    os.umask(0o077)
    if args.output.is_symlink():
        raise ValueError("linked_output_directory")
    args.output.mkdir(mode=0o700, parents=False, exist_ok=args.resume)
    for name in (
        "ARCHIVE-INDEX.json",
        "SHA256SUMS",
        "production-source-" + PRODUCTION_COMMIT[:8] + "-raw.tar.gz",
    ):
        if (args.output / name).exists():
            raise FileExistsError("refusing_to_overwrite: " + name)
    entries, missing = gather(args.repository, args.downloads)
    print("archive_inputs=" + str(len(entries)), flush=True)
    private = args.output / "historical-packages-and-evidence.private.tar"
    if args.resume:
        if private.is_symlink() or not private.is_file():
            raise ValueError("unsafe_resume_bundle")
        verify(private)
        with tarfile.open(private, "r|") as bundle:
            for member in bundle:
                if member.name == "MANIFEST.json":
                    manifest = json.load(bundle.extractfile(member))
        if manifest["production_commit"] != PRODUCTION_COMMIT:
            raise ValueError("resume_commit_mismatch")
        if {row["name"] for row in manifest["files"]} != {name for name, _ in entries}:
            raise ValueError("resume_inventory_changed")
        for name, expected in PINNED.items():
            row = next(
                row
                for row in manifest["files"]
                if row["name"] == "server-evidence/" + name
            )
            if row["sha256"] != expected:
                raise ValueError("resume_pinned_evidence_mismatch")
    else:
        manifest = pack(entries, private)
    print("private_archive=VERIFIED", flush=True)
    source = source_snapshot(args.repository, args.output)
    print("production_source_snapshot=VERIFIED", flush=True)
    artifacts = [
        {
            "name": private.name,
            "sha256": digest(private),
            "bytes": private.stat().st_size,
            "classification": manifest["classification"],
        },
        source,
    ]
    index = {
        "schema": 1,
        "production_commit": PRODUCTION_COMMIT,
        "local_archive_integrity": "PASS",
        "offsite_backup": "PENDING",
        "server_oci_images": "NOT_CAPTURED",
        "private_configuration_backup": "NOT_CAPTURED",
        "database_and_volume_backup": "NOT_CAPTURED",
        "isolated_restore_test": "NOT_PERFORMED",
        "all_historical_packages": "NOT_PROVEN_COMPLETE",
        "optional_receipts_missing": missing,
        "artifacts": artifacts,
        "files": manifest["files"],
    }
    (args.output / "ARCHIVE-INDEX.json").write_text(
        json.dumps(index, indent=2) + "\n", encoding="utf-8"
    )
    lines = [row["sha256"] + "  " + row["name"] for row in artifacts]
    lines.append(digest(args.output / "ARCHIVE-INDEX.json") + "  ARCHIVE-INDEX.json")
    (args.output / "SHA256SUMS").write_bytes(("\n".join(lines) + "\n").encode("ascii"))
    print("archive_output=" + str(args.output), flush=True)


if __name__ == "__main__":
    main()
