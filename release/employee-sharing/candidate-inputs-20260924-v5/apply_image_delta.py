"""Apply a reviewed delta ONLY inside a disposable image build stage.

No downloads, imports of application code, database or Kubernetes calls. This is
not a live deployment/rollback tool and is not transactional across files. A
failed build layer must be discarded. Validate the entire bundle before writes.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import stat
import sys
import uuid
from pathlib import Path

LIMIT = 2 * 1024**2


def sha(data):
    return hashlib.sha256(data).hexdigest()


def unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate manifest key")
        result[name] = value
    return result


def safe_path(value):
    if (
        not isinstance(value, str)
        or not value
        or any(c in value for c in ("\\", ":", "\0"))
    ):
        raise ValueError("Unsafe delta path")
    for part in value.split("/"):
        if part in ("", ".", "..") or part.endswith((" ", ".")):
            raise ValueError("Unsafe delta path")
    return value


def check_path(path):
    """Reject links (including dangling ones) and non-directory ancestors."""
    for parent in reversed(path.parents):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        if (
            not stat.S_ISDIR(info.st_mode)
            or getattr(parent, "is_junction", lambda: False)()
        ):
            raise ValueError("Unsafe source ancestor")
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (
        not stat.S_ISREG(info.st_mode)
        or getattr(path, "is_junction", lambda: False)()
        or info.st_size > LIMIT
    ):
        raise ValueError("Unsafe source file")
    return info


def prepare(root, manifest):
    root = Path(root).absolute()
    if not root.is_dir():
        raise ValueError("Image source root missing")
    if (
        manifest.get("schema") != 1
        or manifest.get("scope") != "employee-sharing-image-candidate"
        or manifest.get("ready_for_rollout") is not False
    ):
        raise ValueError("Not an employee image candidate")
    component = manifest.get("component")
    if component not in ("gateway", "provisioner") or not re.fullmatch(
        r"sha256:[a-f0-9]{64}", manifest.get("source_image_id", "")
    ):
        raise ValueError("Unsupported image identity")
    entries, anchors = manifest.get("entries"), manifest.get("anchors")
    if (
        not isinstance(entries, list)
        or not 1 <= len(entries) <= 128
        or not isinstance(anchors, dict)
        or len(anchors) > 512
    ):
        raise ValueError("Invalid delta scope")
    seen, prepared = set(), []
    for entry in entries:
        name = safe_path(entry["path"])
        allowed = (
            name == "app.py"
            if component == "provisioner"
            else name.startswith(("app/", "packages/harness/deerflow/"))
            and name.endswith(".py")
        )
        if not allowed or name.casefold() in seen:
            raise ValueError("Duplicate or unreviewable delta path")
        seen.add(name.casefold())
        for checksum in (entry["source_sha256"], entry["target_sha256"]):
            if checksum is not None and not re.fullmatch(r"[a-f0-9]{64}", checksum):
                raise ValueError("Invalid delta checksum")
        content = base64.b64decode(entry["content_base64"], validate=True)
        if len(content) > LIMIT or sha(content) != entry["target_sha256"]:
            raise ValueError("Delta content checksum mismatch")
        compile(content, name, "exec")
        path = root / name
        info = check_path(path)
        before = None if info is None else sha(path.read_bytes())
        if before != entry["source_sha256"] and before != entry["target_sha256"]:
            raise ValueError("Image source differs: " + name)
        if info is not None and stat.S_IMODE(info.st_mode) & ~0o777:
            raise ValueError("Special source mode is forbidden")
        prepared.append((path, content, before, info))
    for name, checksum in anchors.items():
        safe_path(name)
        if name.casefold() in seen or not re.fullmatch(r"[a-f0-9]{64}", checksum):
            raise ValueError("Conflicting image anchor")
        seen.add(name.casefold())
        path = root / name
        if check_path(path) is None or sha(path.read_bytes()) != checksum:
            raise ValueError("Image dependency anchor differs: " + name)
    return prepared


def apply(root, manifest, *, check_only=True):
    prepared = prepare(root, manifest)
    if not check_only:
        for path, content, before, info in prepared:
            # Build stages have no concurrent app writers. Still reject a change
            # between validation and this individual atomic replacement.
            current = check_path(path)
            if (None if current is None else sha(path.read_bytes())) != before:
                raise ValueError("Image source changed during apply")
            if before == sha(content):
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".employee-tmp-" + uuid.uuid4().hex)
            try:
                with temporary.open("xb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.chmod(stat.S_IMODE(info.st_mode) if info else 0o644)
                if info and os.name != "nt":
                    os.chown(temporary, info.st_uid, info.st_gid)
                temporary.replace(path)
            finally:
                if temporary.exists():
                    temporary.unlink()
    return {
        "scope": "image-stage-only",
        "mode": "check-only" if check_only else "apply",
        "files": len(prepared),
        "ready_for_rollout": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("manifest", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-only", action="store_true")
    mode.add_argument("--image-build-stage", action="store_true")
    args = parser.parse_args()
    if args.manifest.stat().st_size > 64 * 1024**2:
        raise ValueError("Image manifest too large")
    document = json.loads(args.manifest.read_bytes(), object_pairs_hook=unique_object)
    print(json.dumps(apply(args.root, document, check_only=args.check_only)))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError, SyntaxError) as error:
        print("employee_image_delta=FAIL detail=" + str(error), file=sys.stderr)
        sys.exit(1)
