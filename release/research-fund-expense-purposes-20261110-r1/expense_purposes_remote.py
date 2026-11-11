"""Fixed-scope maintenance code. Executed only in an isolated maintenance Pod."""

import base64
from contextlib import contextmanager
import ctypes
import errno
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time

QUALIFIER_SOURCE = None
QUALIFIER_EXPECTED = None
ROOT = Path("/mnt/skills")
NAME = "research-fund-settlement"
STARTED = time.monotonic()


class Refused(Exception):
    pass


def check(condition, code):
    if not condition:
        raise Refused(code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


def safe_path(path):
    check(
        path.is_absolute() and ROOT in (path,) + tuple(path.parents),
        "PATH_OUTSIDE_SKILLS",
    )
    for entry in (path,) + tuple(path.parents):
        check(not entry.is_symlink(), "SYMLINK_REFUSED")


def read(path, limit=2097152):
    safe_path(path)
    a = path.lstat()
    check(stat.S_ISREG(a.st_mode) and a.st_size <= limit, "INVALID_FILE")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(path), flags)
    try:
        z = os.fstat(fd)
        check(
            (a.st_dev, a.st_ino, a.st_size, a.st_mtime_ns)
            == (z.st_dev, z.st_ino, z.st_size, z.st_mtime_ns),
            "FILE_CHANGED",
        )
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(limit + 1)
        z = os.fstat(fd)
        check(
            len(data) == a.st_size
            and (a.st_size, a.st_mtime_ns) == (z.st_size, z.st_mtime_ns),
            "FILE_CHANGED",
        )
        return data
    finally:
        os.close(fd)


def manifest(path):
    safe_path(path)
    check(path.is_dir(), "PACKAGE_DIRECTORY_MISSING")
    result = []
    size = 0
    for parent, dirs, files in os.walk(str(path), followlinks=False):
        check(time.monotonic() - STARTED < 300, "REMOTE_DEADLINE")
        for name in list(dirs):
            q = Path(parent) / name
            safe_path(q)
            if name == "__pycache__":
                dirs.remove(name)
        for name in sorted(files):
            q = Path(parent) / name
            safe_path(q)
            if name.endswith((".pyc", ".pyo")):
                continue
            data = read(q)
            size += len(data)
            check(size <= 8388608 and len(result) < 512, "PACKAGE_LIMIT")
            result.append(
                {
                    "path": q.relative_to(path).as_posix(),
                    "bytes": len(data),
                    "sha256": sha(data),
                }
            )
    return sorted(result, key=lambda r: r["path"])


def verify(path, expected):
    actual = manifest(path)
    check(actual == expected["files"], "PACKAGE_MANIFEST_CHANGED")
    check(sha(canonical(actual)) == expected["tree_sha256"], "PACKAGE_TREE_CHANGED")
    return expected["tree_sha256"]


@contextmanager
def lease():
    import fcntl

    safe_path(ROOT)
    path = ROOT / ".research-fund-settlement-update.lock"
    safe_path(path)
    fd = os.open(
        str(path), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    try:
        check(stat.S_ISREG(os.fstat(fd).st_mode), "INVALID_LOCK")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def transaction(operation):
    check(
        len(operation) == 32 and all(c in "0123456789abcdef" for c in operation),
        "INVALID_OPERATION",
    )
    result = ROOT / (".research-fund-release-expense-purposes-20261110-r1-" + operation)
    safe_path(result)
    return result


def save_journal(tx, report):
    path = tx / "JOURNAL.json"
    temp = tx / "JOURNAL.next.json"
    with temp.open("xb") as out:
        out.write(canonical(report) + b"\n")
        out.flush()
        os.fsync(out.fileno())
    os.replace(str(temp), str(path))


def unpack(archive, expected, target):
    check(not target.exists() and not target.is_symlink(), "STAGE_ALREADY_PRESENT")
    blobs = {}
    uncompressed_bytes = 0
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for entry in tar:
            path = PurePosixPath(entry.name)
            check(
                entry.isfile() and not entry.issym() and not entry.islnk(),
                "UNSAFE_ARCHIVE_ENTRY",
            )
            check(
                path.parts
                and path.parts[0] == NAME
                and ".." not in path.parts
                and not path.is_absolute()
                and "\\" not in entry.name
                and ":" not in entry.name
                and len(path.parts) > 1,
                "UNSAFE_ARCHIVE_PATH",
            )
            name = "/".join(path.parts[1:])
            check(
                name not in blobs and entry.size <= 2097152 and len(blobs) < 64,
                "ARCHIVE_LIMIT",
            )
            uncompressed_bytes += entry.size
            check(uncompressed_bytes <= 8388608, "ARCHIVE_TOTAL_BOUND")
            stream = tar.extractfile(entry)
            check(stream is not None, "ARCHIVE_MEMBER_MISSING")
            data = stream.read(2097153)
            check(len(data) == entry.size, "ARCHIVE_MEMBER_SIZE")
            blobs[name] = data
    rows = [
        {"path": n, "bytes": len(b), "sha256": sha(b)} for n, b in sorted(blobs.items())
    ]
    check(
        rows == expected["files"] and sha(canonical(rows)) == expected["tree_sha256"],
        "ARCHIVE_MANIFEST_MISMATCH",
    )
    target.mkdir(mode=0o755)
    for name, data in sorted(blobs.items()):
        out = target / name
        out.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        safe_path(out)
        with out.open("xb") as handle:
            handle.write(data)
        out.chmod(0o644)
    verify(target, expected)


def qualify(skill):
    results = []
    with tempfile.TemporaryDirectory(prefix="fund-synthetic-") as temp:
        for profile in ("group-company", "national-science", "miit"):
            case = skill / "examples" / "cases" / (profile + "-complete.json")
            doc = Path(temp) / (profile + ".docx")
            for action, args in (
                ("check", ["--case", str(case), "--mode", "final"]),
                (
                    "build",
                    ["--case", str(case), "--mode", "final", "--output", str(doc)],
                ),
                ("inspect", ["--case", str(case), "--docx", str(doc)]),
            ):
                proc = subprocess.run(
                    [
                        sys.executable,
                        "-X",
                        "utf8",
                        "-B",
                        str(skill / "scripts" / "report_cli.py"),
                        action,
                    ]
                    + args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=30,
                )
                check(
                    len(proc.stdout) <= 1048576 and len(proc.stderr) <= 1048576,
                    "CLI_OUTPUT_LIMIT",
                )
                check(proc.returncode == 0, "SYNTHETIC_CLI_FAILED")
                observation = json.loads(proc.stdout)
                check(
                    observation.get("status") == "PASS", "SYNTHETIC_REPORT_NOT_ACCEPTED"
                )
                results.append(
                    {
                        "profile": profile,
                        "action": action,
                        "status": "PASS",
                        "exitcode": 0,
                    }
                )
    check(isinstance(QUALIFIER_SOURCE, str) and isinstance(QUALIFIER_EXPECTED, int) and QUALIFIER_EXPECTED > 30, 'QUALIFIER_NOT_BOUND')
    proc = subprocess.run([sys.executable, '-I', '-B', '-c', QUALIFIER_SOURCE, str(skill)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
    check(len(proc.stdout) <= 1048576 and len(proc.stderr) <= 1048576, 'QUALIFIER_OUTPUT_LIMIT')
    check(proc.returncode == 0, 'QUALIFIER_REGRESSIONS_FAILED')
    value = json.loads(proc.stdout)
    check(value.get('status') == 'PASS' and value.get('passed') == QUALIFIER_EXPECTED and value.get('user_files_read') is False and value.get('database_access') is False and value.get('provider_requests') == 0, 'QUALIFIER_CHECKS_INCOMPLETE')
    results.append({'profile': 'expense-purposes-feedback', 'action': 'regressions', 'status': 'PASS', 'passed': QUALIFIER_EXPECTED})
    return results


def exchange(a, b):
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, "renameat2", None)
    if function is None:
        raise OSError(errno.ENOSYS, "renameat2 unavailable")
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int
    result = function(-100, os.fsencode(str(a)), -100, os.fsencode(str(b)), 2)
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, "renameat2 refused")


def exchange_supported(tx):
    a = tx / "exchange-probe-a"
    b = tx / "exchange-probe-b"
    a.mkdir()
    b.mkdir()
    marker = a / "marker"
    marker.write_bytes(b"probe")
    try:
        exchange(a, b)
        check(
            (b / "marker").read_bytes() == b"probe" and not (a / "marker").exists(),
            "EXCHANGE_PROBE_INCONSISTENT",
        )
        return True
    except OSError as exc:
        check(
            exc.errno in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP, errno.ENOTSUP),
            "EXCHANGE_PROBE_FAILED",
        )
        return False
    finally:
        for p in (a / "marker", b / "marker"):
            if p.exists():
                p.unlink()
        a.rmdir()
        b.rmdir()


def stage(request):
    live = ROOT / "public" / NAME
    tx = transaction(request["operation"])
    verify(live, request["before"])
    check(not os.path.lexists(str(ROOT / "custom" / NAME)), "CUSTOM_SKILL_SHADOW_PRESENT")
    archive = base64.b64decode(request["archive_b64"], validate=True)
    check(
        len(archive) <= 1048576 and sha(archive) == request["archive_sha256"],
        "ARCHIVE_DIGEST_MISMATCH",
    )
    check(shutil.disk_usage(str(ROOT)).free >= 16777216, "SKILLS_VOLUME_FREE_SPACE_LOW")
    check(not tx.exists(), "TRANSACTION_ALREADY_PRESENT")
    tx.mkdir(mode=0o700)
    journal = {
        "status": "STAGING",
        "operation": request["operation"],
        "skill_modified": False,
        "before_tree_sha256": request["before"]["tree_sha256"],
        "after_tree_sha256": request["after"]["tree_sha256"],
    }
    save_journal(tx, journal)
    shutil.copytree(
        str(live),
        str(tx / "backup"),
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        symlinks=True,
    )
    verify(tx / "backup", request["before"])
    unpack(archive, request["after"], tx / "candidate")
    results = qualify(tx / "candidate")
    verify(tx / "candidate", request["after"])
    verify(live, request["before"])
    journal.update(
        {
            "status": "STAGED_SYNTHETIC_ACCEPTED",
            "backup_verified": True,
            "synthetic_checks": results,
            "synthetic_files_cleaned": True,
            "exchange_supported": exchange_supported(tx),
        }
    )
    save_journal(tx, journal)
    return journal


def publish(request):
    live = ROOT / "public" / NAME
    tx = transaction(request["operation"])
    old = tx / "previous-live"
    candidate = tx / "candidate"
    journal = json.loads(read(tx / "JOURNAL.json"))
    check(journal["status"] == "STAGED_SYNTHETIC_ACCEPTED", "STAGE_NOT_ACCEPTED")
    check(
        journal["operation"] == request["operation"]
        and journal["before_tree_sha256"] == request["before"]["tree_sha256"]
        and journal["after_tree_sha256"] == request["after"]["tree_sha256"],
        "TRANSACTION_BINDING_CHANGED",
    )
    verify(live, request["before"])
    verify(candidate, request["after"])
    verify(tx / "backup", request["before"])
    check(
        not os.path.lexists(str(ROOT / "custom" / NAME)) and not old.exists(),
        "PUBLICATION_PATH_CHANGED",
    )
    journal["status"] = "PUBLISH_REQUESTED"
    journal["switch_may_have_applied"] = True
    save_journal(tx, journal)
    if journal["exchange_supported"]:
        exchange(live, candidate)
        journal["switch_strategy"] = "RENAME_EXCHANGE"
    else:
        # NFS may not implement RENAME_EXCHANGE. This fallback has a short
        # directory-name gap; it must never be described as an atomic swap.
        os.rename(str(live), str(old))
        try:
            os.rename(str(candidate), str(live))
        except OSError:
            if live.exists():
                # A lost NFS reply may follow an applied rename. Read back;
                # never replay or overwrite an unknown directory.
                verify(live, request["after"])
            else:
                verify(old, request["before"])
                os.rename(str(old), str(live))
                journal.update(
                    {
                        "status": "LIVE_NAME_RESTORED_AFTER_FAILED_SECOND_RENAME",
                        "skill_modified": False,
                    }
                )
                save_journal(tx, journal)
                raise Refused("SECOND_RENAME_FAILED_LIVE_NAME_RESTORED")
        journal["switch_strategy"] = "TWO_RENAMES_WITH_SHORT_NAME_GAP"
    verify(live, request["after"])
    verify(tx / "backup", request["before"])
    journal.update(
        {
            "status": "SKILL_PACKAGE_UPDATED",
            "skill_modified": True,
            "live_manifest_verified": True,
            "backup_path": str(tx / "backup"),
            "live_path": str(live),
            "automatic_rollback": False,
        }
    )
    save_journal(tx, journal)
    return journal


def main():
    result = {
        "status": "INCOMPLETE",
        "credentials_exported": False,
        "production_database_access": False,
        "provider_requests": 0,
        "raw_errors_exported": False,
    }
    try:
        check(isinstance(QUALIFIER_SOURCE, str) and isinstance(QUALIFIER_EXPECTED, int)
              and QUALIFIER_EXPECTED > 30, "UNBOUND_REMOTE_TEMPLATE_NO_FILESYSTEM_ACTION")
        data = sys.stdin.buffer.read(2097153)
        check(len(data) <= 2097152, "INPUT_LIMIT")
        request = json.loads(data)
        check(request["mode"] in ("stage", "publish"), "INVALID_MODE")
        with lease():
            observation = (
                stage(request) if request["mode"] == "stage" else publish(request)
            )
        result.update(observation)
        result["exitcode"] = 0
    except Exception as exc:
        result["error_class"] = type(exc).__name__
        result["error_code"] = (
            str(exc) if isinstance(exc, Refused) else "MAINTENANCE_OPERATION_FAILED"
        )
        result["exitcode"] = 1
    print(json.dumps(result, sort_keys=True))
    return result["exitcode"]


if __name__ == "__main__":
    sys.exit(main())
