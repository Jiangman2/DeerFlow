"""Read-only source-version and package-content check before replacing files."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
from zipfile import ZipFile


def digest(data):
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--archive", required=True)
    args = parser.parse_args()
    repo = Path(args.repo).resolve(strict=True)
    if not (repo / "frontend/package.json").is_file():
        raise SystemExit("STOP: not a DeerFlow repository")
    errors = []
    with ZipFile(args.archive) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise SystemExit("STOP: duplicate archive entries")
        for name in names:
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
                raise SystemExit("STOP: unsafe archive path")
            target = (repo / name).resolve()
            if repo not in target.parents:
                raise SystemExit("STOP: archive target escapes repository")
            if (repo / name).is_symlink():
                raise SystemExit("STOP: target is a symbolic link: " + name)
        manifest_name = "docs/frontend-smooth-streaming-20261003-r2-manifest.json"
        manifest = json.loads(archive.read(manifest_name))
        print("PACKAGE BASE: " + manifest["base_commit"])
        expected = {item["path"] for item in manifest["files"]}
        expected.update({manifest_name,
                         "docs/frontend-smooth-streaming-20261003-r2-check.py",
                         "docs/frontend-smooth-streaming-20261003-r2-install.md"})
        if set(names) != expected:
            raise SystemExit("STOP: unexpected/missing archive entries")
        for item in manifest["files"]:
            name = item["path"]
            if digest(archive.read(name)) != item["after"]:
                errors.append("PACKAGE HASH MISMATCH " + name)
                continue
            target = repo / name
            if target.exists() and not target.is_file():
                errors.append("NOT A FILE " + name)
            elif target.is_file():
                current = digest(target.read_bytes())
                if current == item["after"]:
                    print("ALREADY " + name)
                elif item["before"] is not None and current == item["before"]:
                    print("MATCH " + name)
                else:
                    errors.append("SOURCE VERSION MISMATCH " + name)
            elif item["before"] is None:
                print("NEW " + name)
            else:
                errors.append("MISSING EXISTING FILE " + name)
        # Release helper documents are also protected against overwriting a
        # pre-existing different file on repeated installation.
        for name in expected - {item["path"] for item in manifest["files"]}:
            target = repo / name
            if target.exists() and (not target.is_file() or
                                    digest(target.read_bytes()) != digest(archive.read(name))):
                errors.append("RELEASE HELPER CONFLICT " + name)
    if errors:
        print("\n".join(errors))
        raise SystemExit("STOP: do not unzip over this checkout; review the differences first")
    print("PREFLIGHT OK: source versions match; back up files before extraction")


if __name__ == "__main__":
    main()
