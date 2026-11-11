"""Verify the Work cloud routing candidate offline; no deployment or business calls."""

import argparse
import io
import json
from pathlib import Path
import subprocess
import tarfile

import work_cloud_routing_source_20261111 as source

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = (
    "backend/app",
    "backend/packages/harness",
    "backend/tests",
    "frontend/src",
    "frontend/tests",
)


def snapshot(ref=None):
    if ref is None:
        return lambda path: (ROOT / path).read_bytes()
    roots = list(SOURCE_ROOTS)
    for path in (*source.PARENT_MANIFESTS, *source.DOCUMENTATION_PATHS):
        if subprocess.check_output(
            ["git", "ls-tree", "--name-only", ref, path], cwd=ROOT
        ).strip():
            roots.append(path)
    if subprocess.check_output(
        ["git", "ls-tree", "--name-only", ref, source.ARCHIVE.rstrip("/")], cwd=ROOT
    ).strip():
        roots.append(source.ARCHIVE)
    raw = subprocess.check_output(["git", "archive", ref, *roots], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        files = {
            item.name: archive.extractfile(item).read()
            for item in archive
            if item.isfile()
        }
    return files.__getitem__


def verify(ref=None):
    read = snapshot(ref)
    _, result = source.historical_view(read)
    if result is None:
        return {
            "status": "HISTORICAL_TREE_WITHOUT_WORK_CLOUD_ROUTING_CANDIDATE",
            "production_commands_executed": False,
        }
    args = ["git", "diff", "--name-only", source.BASE_COMMIT]
    if ref:
        args.append(ref)
    changed = set(
        subprocess.check_output(
            [*args, "--", *SOURCE_ROOTS, *source.DOCUMENTATION_PATHS], cwd=ROOT
        )
        .decode()
        .splitlines()
    )
    if ref is None:
        changed.update(
            subprocess.check_output(
                [
                    "git",
                    "ls-files",
                    "--others",
                    "--exclude-standard",
                    "--",
                    *SOURCE_ROOTS,
                    *source.DOCUMENTATION_PATHS,
                ],
                cwd=ROOT,
            )
            .decode()
            .splitlines()
        )
    manifest = json.loads(read(source.MANIFEST))
    assert changed == set(manifest["changes"]), (
        "WORK_CLOUD_ROUTING_UNDECLARED_SOURCE_DELTA",
        sorted(changed ^ set(manifest["changes"])),
    )
    return {
        **result,
        "status": "WORK_CLOUD_ROUTING_CANDIDATE_SOURCE_VERIFIED",
        "candidate_status": "NOT_DEPLOYED",
        "git_ref": ref,
        "production_commands_executed": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ref", help="Verify immutable committed bytes instead of the working tree"
    )
    print(json.dumps(verify(parser.parse_args().ref), sort_keys=True))
