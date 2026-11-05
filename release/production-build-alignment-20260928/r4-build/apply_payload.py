"""Exact source delta inside disposable images only; host/container Python 3.7+."""

import argparse
import ast
import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
COMPONENTS = ("gateway", "runner", "frontend", "agent-host")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate manifest key")
        result[key] = value
    return result


def rows(document, component):
    if component not in COMPONENTS:
        raise ValueError("Unsupported component")
    selected = [row for row in document["entries"] if row["component"] == component]
    if not selected or len(selected) > 16:
        raise ValueError("Unexpected source delta count")
    seen = set()
    for row in selected:
        target = PurePosixPath(row["target"])
        prefix = "/opt/ppt-master-agent-host/" if component == "agent-host" else "/app/" + ("frontend/" if component == "frontend" else "backend/")
        if not str(target).startswith(prefix) or ".." in target.parts or str(target) in seen:
            raise ValueError("Invalid or duplicate delta target")
        if target.suffix not in (".py", ".ts", ".tsx"):
            raise ValueError("Not an approved source type")
        if Path(row["payload"]).name != row["payload"] or row["payload"] in (".", ".."):
            raise ValueError("Not a flat payload name")
        seen.add(str(target))
    return selected


def safe_target(root, target):
    path = root.joinpath(*PurePosixPath(target).parts[1:])
    # The build must not let a base-image symlink redirect a source replacement.
    for parent in reversed((path.parent,) + tuple(path.parent.parents)):
        if parent == root or root in parent.parents:
            if os.path.lexists(str(parent)) and not stat.S_ISDIR(parent.lstat().st_mode):
                raise ValueError("Linked source ancestor")
    if os.path.lexists(str(path)) and not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("Linked or special source target")
    return path


def apply(package, root, component, *, check_only=False, verify_after=False):
    document = json.loads((package / "DELTA.json").read_text(encoding="utf-8"), object_pairs_hook=unique)
    selected = rows(document, component)
    planned = []
    # Validate EVERY old source and payload before the first write.
    for row in selected:
        incoming = package / row["payload"]
        if not stat.S_ISREG(incoming.lstat().st_mode) or incoming.stat().st_size > 2 * 1024**2:
            raise ValueError("Unsafe payload")
        data = incoming.read_bytes()
        if sha(data) != row["after_sha256"]:
            raise ValueError("Payload checksum mismatch")
        target = safe_target(root, row["target"])
        expected = row["after_sha256"] if verify_after else row["before_sha256"]
        if expected is None:
            if os.path.lexists(str(target)):
                raise ValueError("New source already exists: " + row["target"])
        elif not target.is_file() or sha(target.read_bytes()) != expected:
            raise ValueError("Source baseline mismatch: " + row["target"])
        if target.suffix == ".py":
            ast.parse(data.decode("utf-8"), filename=row["target"])
        planned.append((target, data))
    if not check_only and not verify_after:
        for target, data in planned:
            target.parent.mkdir(parents=True, exist_ok=True)
            mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o644
            temporary = target.with_name(target.name + ".ppt-repair-new")
            fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
            with os.fdopen(fd, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
                os.fchmod(output.fileno(), mode)
            os.replace(str(temporary), str(target))
        apply(package, root, component, verify_after=True)
    return {"ppt_repair_source": "PASS", "component": component, "files": len(planned), "mode": "after" if verify_after else "check" if check_only else "apply"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component", choices=COMPONENTS, required=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check-only", action="store_true")
    group.add_argument("--verify-after", action="store_true")
    args = parser.parse_args()
    print(json.dumps(apply(HERE, Path("/"), args.component, check_only=args.check_only, verify_after=args.verify_after), sort_keys=True))
