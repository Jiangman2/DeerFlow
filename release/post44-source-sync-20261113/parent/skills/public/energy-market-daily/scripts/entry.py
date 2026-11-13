"""Thin launcher for the byte-identical upstream project; no business calculations."""

import argparse
import datetime as dt
import hashlib
import http.client
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_SHA = "eb66843bbe6170d86d33b65efa593be263e778275c6f7d491e1fd0c3c6d98f99"
TZ = dt.timezone(dt.timedelta(hours=8))
MUTABLE = ("app/data/", "app/sources/", "app/output/", "app/state/", "reproduced/")
COLLECTOR_HOST = "energy-market-collector.deer-flow.svc.cluster.local"
# Qualified on the existing ARM64 document-runtime1 image. Full Chrome's GPU
# process crashes on that runtime; this bundled headless executable passed the
# same launch/new-page/screenshot path. No browser download or upstream edit.
LINUX_REPORT_BROWSER = "/root/.cache/ms-playwright/chromium_headless_shell-1228/chrome-linux/headless_shell"
ENTRY_REVISION = "20261110-chat-feedback-r1"


class WorkflowBlocked(ValueError):
    """A fixed, sanitized outer-workflow report; never raw provider output."""

    def __init__(self, report):
        self.report = dict(report, status="INCOMPLETE", blocked_outputs=True,
                           provider_requests=0, work_messages_sent=0)
        super().__init__(self.report["error_code"])


def require(ok, code):
    if not ok:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path, limit=5 * 1024 * 1024):
    path = Path(path)
    require(not any(p.is_symlink() for p in [path, *path.parents]), "SYMLINK_REFUSED")
    fd = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        before = os.fstat(fd)
        require(
            stat.S_ISREG(before.st_mode) and before.st_size <= limit,
            "FILE_BOUND_OR_TYPE",
        )
        with os.fdopen(fd, "rb", closefd=False) as f:
            raw = f.read(limit + 1)
        after = os.fstat(fd)
        require(
            len(raw) <= limit
            and (before.st_ino, before.st_size, before.st_mtime_ns)
            == (after.st_ino, after.st_size, after.st_mtime_ns),
            "FILE_CHANGED_DURING_READ",
        )
        return raw
    finally:
        os.close(fd)


def save(path, data):
    path = Path(path)
    require(not any(p.is_symlink() for p in [path, *path.parents]), "SYMLINK_REFUSED")
    tmp = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    with tmp.open("x", encoding="utf-8") as f:
        json.dump(
            data, f, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
        )
    os.replace(tmp, path)


def original_files():
    raw = read(ROOT / "assets/energy-daily-v1.0.original.zip")
    require(sha(raw) == ARCHIVE_SHA, "ORIGINAL_ARCHIVE_CHANGED")
    expected = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for item in archive.infolist():
            path = PurePosixPath(item.filename)
            require(
                not path.is_absolute()
                and ".." not in path.parts
                and ":" not in item.filename
                and "\\" not in item.filename
                and not stat.S_ISLNK(item.external_attr >> 16),
                "ARCHIVE_PATH",
            )
            if item.is_dir():
                continue
            parts = (
                path.parts[1:] if path.parts[0] == "energy-daily-v1.0" else path.parts
            )
            name = PurePosixPath(*parts).as_posix()
            require(name not in expected, "ARCHIVE_DUPLICATE")
            expected[name] = sha(archive.read(item))
    require(len(expected) == 158, "ORIGINAL_FILE_SET_CHANGED")
    verify_tree(ROOT / "upstream", expected, False)
    return expected


def verify_tree(folder, expected, working):
    actual = set()
    for p in folder.rglob("*"):
        require(not p.is_symlink(), "SYMLINK_REFUSED")
        if p.is_file():
            rel = p.relative_to(folder).as_posix()
            if working and rel.startswith(MUTABLE):
                continue
            require(
                rel in expected and sha(read(p)) == expected[rel],
                "ORIGINAL_FILE_CHANGED",
            )
            actual.add(rel)
    needed = {n for n in expected if not (working and n.startswith(MUTABLE))}
    require(actual == needed, "ORIGINAL_FILE_MISSING")


def input_hashes(engine):
    result, total = {}, 0
    for sub in ("app/data", "app/sources"):
        for p in sorted((engine / sub).rglob("*")):
            require(not p.is_symlink(), "INPUT_SYMLINK")
            if p.is_file():
                data = read(p, 4 * 1024 * 1024)
                total += len(data)
                require(total <= 32 * 1024 * 1024 and len(result) < 400, "INPUT_BUDGET")
                result[p.relative_to(engine).as_posix()] = sha(data)
    return result


def original_command(project, script, args, browser=None, timeout=120):
    # Use a private subprocess, original argv and the existing upstream browser override.
    env_names = (
        "PATH",
        "SystemRoot",
        "WINDIR",
        "HOME",
        "USERPROFILE",
        "LOCALAPPDATA",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LD_LIBRARY_PATH",
        "LANG",
        "LC_ALL",
        "PLAYWRIGHT_BROWSERS_PATH",
        "FONTCONFIG_PATH",
    )
    env = {k: os.environ[k] for k in env_names if k in os.environ}
    env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", TZ="Asia/Shanghai")
    if browser:
        require(Path(browser).is_file(), "BROWSER_EXECUTABLE_MISSING")
        env["REPORT_BROWSER_EXECUTABLE"] = str(browser)
    engine = project / "engine"
    command = [sys.executable, "-B", str(engine / "app" / script)] + args
    with tempfile.TemporaryFile() as log:
        child = subprocess.Popen(
            command,
            cwd=str(engine),
            env=env,
            stdout=log,
            stderr=log,
            start_new_session=os.name != "nt",
        )
        try:
            child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                os.killpg(child.pid, signal.SIGKILL)
            else:
                child.kill()
            child.wait(timeout=10)
            raise ValueError("ORIGINAL_COMMAND_TIMEOUT") from None
        size = log.tell()
        require(size <= 256 * 1024, "ORIGINAL_OUTPUT_BOUND")
        log.seek(0)
        output = log.read()
    require(
        child.returncode == 0,
        "ORIGINAL_COMMAND_FAILED_" + script.replace(".py", "").upper(),
    )
    return output


def project_meta(project, expected):
    meta = json.loads(read(project / "PROJECT.json"))
    require(
        meta.get("schema") == "energy-daily-session.v1"
        and meta.get("archive_sha256") == ARCHIVE_SHA,
        "PROJECT_IDENTITY",
    )
    day = dt.date.fromisoformat(meta["publication_date"])
    require(
        day.isoformat() == meta["publication_date"]
        and day <= dt.datetime.now(TZ).date(),
        "PUBLICATION_DATE",
    )
    require(meta.get("mode") in (None, "generate", "reproduce"), "PROJECT_MODE")
    require(meta.get("data_date", edition(meta)["data_date"]) == edition(meta)["data_date"],
            "PROJECT_DATA_DATE")
    verify_tree(project / "engine", expected, True)
    return meta


def edition(meta):
    return dict(publication_date=meta["publication_date"],
                data_date=(dt.date.fromisoformat(meta["publication_date"]) - dt.timedelta(days=1)).isoformat(),
                mode=meta.get("mode") or "legacy_unspecified")


def inputs_status(project, meta, expected):
    identity = edition(meta)
    rows = []
    for kind in ("energy", "financial", "news"):
        day = identity["publication_date" if kind == "news" else "data_date"]
        name = "app/data/" + kind + "_" + day + ".json"
        path = project / "engine" / name
        require(not path.is_symlink(), "INPUT_SYMLINK")
        if not path.exists():
            state = "missing"
        else:
            digest = sha(read(path, 4 * 1024 * 1024))
            state = "bundled_sample_identical" if expected.get(name) == digest else "present"
        rows.append(dict(kind=kind, path=str(path), state=state))
    return dict(identity, status="INPUTS_OBSERVED", input_states=rows,
                all_inputs_present=all(r["state"] != "missing" for r in rows),
                all_inputs_bundled_sample_identical=all(r["state"] == "bundled_sample_identical" for r in rows),
                input_format_validated=False, formal_report_ready=False,
                provider_requests=0, work_messages_sent=0)


def require_report_inputs(project, meta, expected):
    result = inputs_status(project, meta, expected)
    if not result["all_inputs_present"]:
        raise WorkflowBlocked(dict(result, error_code="INPUTS_MISSING"))
    if result["mode"] != "reproduce" and result["all_inputs_bundled_sample_identical"]:
        raise WorkflowBlocked(dict(result, error_code="SAMPLE_INPUTS_REQUIRE_REPRODUCTION"))
    return result


def init(project, day, expected, mode="generate"):
    require(mode in ("generate", "reproduce"), "PROJECT_MODE")
    require(mode != "reproduce" or bool(day), "REPRODUCTION_DATE_REQUIRED")
    date_selection = "explicit" if day else "beijing_today"
    day = day or dt.datetime.now(TZ).date().isoformat()
    publication = dt.date.fromisoformat(day)
    require(
        day == publication.isoformat() and publication <= dt.datetime.now(TZ).date(),
        "PUBLICATION_DATE",
    )
    require(not project.exists(), "PROJECT_EXISTS")
    project.mkdir(parents=True)
    shutil.copytree(ROOT / "upstream", project / "engine")
    verify_tree(project / "engine", expected, False)
    meta = dict(
        schema="energy-daily-session.v1",
        publication_date=day,
        data_date=(publication - dt.timedelta(days=1)).isoformat(),
        mode=mode,
        date_selection=date_selection,
        entry_revision=ENTRY_REVISION,
        archive_sha256=ARCHIVE_SHA,
        created_at=dt.datetime.now(TZ).isoformat(),
        business_code_modified=False,
    )
    save(project / "PROJECT.json", meta)
    return dict(
        status="ORIGINAL_COPIED_INPUTS_REQUIRED",
        original_files=len(expected),
        engine=str(project / "engine"),
        **edition(meta),
    )


def import_market(project, cache, meta):
    # Match the upstream filename contract. Copy only fixed market responses, never credentials.
    from calendar import monthrange

    target = dt.date.fromisoformat(meta["publication_date"]) - dt.timedelta(days=1)
    year, month = divmod(target.year * 12 + target.month - 4, 12)
    first = dt.date(year, month + 1, min(target.day, monthrange(year, month + 1)[1]))
    copies = {}
    for n in range((target - first).days + 1):
        day = (first + dt.timedelta(days=n)).isoformat()
        name = "jkx_new_" + day + ".json"
        payload = read(Path(cache) / name, 256 * 1024)
        obj = json.loads(payload)
        require(
            isinstance(obj, dict)
            and str(obj.get("errcode")) == "0"
            and isinstance(obj.get("data"), list)
            and len(obj["data"]) <= 512,
            "MARKET_CACHE_SHAPE",
        )
        copies[name] = payload
    for name, payload in copies.items():
        destination = project / "engine/app/sources" / name
        require(not destination.is_symlink(), "INPUT_SYMLINK")
        destination.write_bytes(payload)
    # With the full cache present, the unchanged program takes its local-cache branch.
    original_command(
        project, "fetch_market_history.py", ["--date", target.isoformat()], timeout=120
    )
    return dict(edition(meta), status="ORIGINAL_MARKET_SCRIPT_COMPLETED", cache_days=len(copies))


def request_market(day):
    # A fixed internal address, no proxy, redirect, credential or arbitrary URL.
    connection = http.client.HTTPConnection(COLLECTOR_HOST, 8766, timeout=10)
    try:
        connection.request(
            "POST",
            "/prepare/" + day,
            body=b"",
            headers={"Accept": "application/json", "Connection": "close"},
        )
        response = connection.getresponse()
        raw = response.read(4097)
        require(
            response.status in (200, 202) and len(raw) <= 4096,
            "COLLECTOR_RESPONSE_REJECTED",
        )
        data = json.loads(raw)
        require(
            isinstance(data, dict) and data.get("data_date") == day,
            "COLLECTOR_DATE_MISMATCH",
        )
        require(
            data.get("status") in ("READY", "COLLECTING", "WAIT_DATA", "INCOMPLETE"),
            "COLLECTOR_STATUS_REJECTED",
        )
        return data
    finally:
        connection.close()


def collector_summary(observed):
    codes = {"PROVIDER_REQUEST_FAILED", "TARGET_QUOTES_UNAVAILABLE", "TARGET_RESPONSE_INVALID",
             "NETWORK_TARGET_REFUSED", "NETWORK_BUDGET", "NETWORK_BUDGET_INVALID",
             "ORIGINAL_COLLECTION_FAILED"}
    stages = {"original_fetch", "original_import", "resolve_provider", "validate_budget"}
    result = {}
    for key, allowed in (("status", {"READY", "COLLECTING", "WAIT_DATA", "INCOMPLETE"}),
                         ("error_code", codes), ("failure_stage", stages)):
        if key in observed:
            value = observed[key]
            result[key] = value if isinstance(value, str) and value in allowed else "UNKNOWN"
    count = observed.get("connections")
    if type(count) is int and 0 <= count <= 188:
        result["connections"] = count
    validated = observed.get("target_response_validated")
    if type(validated) is bool:
        result["target_response_validated"] = validated
    return result


def refresh_market(project, meta):
    identity = edition(meta)
    target = identity["data_date"]
    deadline = time.monotonic() + 620
    while True:
        try:
            observed = request_market(target)
        except (OSError, http.client.HTTPException):
            raise WorkflowBlocked(dict(identity, error_code="COLLECTOR_UNREACHABLE")) from None
        summary = collector_summary(observed)
        if observed["status"] == "READY":
            result = import_market(project, ROOT.parent / "energy-market-data", meta)
            result.update(identity, credential_in_sandbox=False, collector=summary,
                          collector_connections=summary.get("connections"))
            return result
        if observed["status"] == "WAIT_DATA":
            return dict(
                identity,
                status="WAIT_PROVIDER_DATA",
                blocked_outputs=True,
                formal_report_ready=False,
                collector=summary,
                credential_in_sandbox=False,
                retry_after_seconds=300,
            )
        if observed["status"] == "INCOMPLETE":
            raise WorkflowBlocked(dict(identity, error_code="COLLECTOR_FAILED", collector=summary,
                                       credential_in_sandbox=False))
        if time.monotonic() >= deadline:
            raise WorkflowBlocked(dict(identity, error_code="COLLECTOR_WAIT_TIMEOUT", collector=summary))
        time.sleep(5)


def report_browser(override=None):
    if override:
        candidate = override
    elif sys.platform.startswith("linux"):
        candidate = LINUX_REPORT_BROWSER
    else:
        candidate = shutil.which("chromium") or shutil.which("chromium-browser")
    require(bool(candidate) and Path(candidate).is_file(), "APPROVED_BROWSER_REQUIRED")
    return str(candidate)


def build_report(project, meta, expected, browser):
    require_report_inputs(project, meta, expected)
    day = meta["publication_date"]
    original_command(project, "validate_inputs.py", ["--date", day])
    inputs = input_hashes(project / "engine")
    browser = report_browser(browser)
    original_command(
        project,
        "generate_report.py",
        ["--date", day, "--png"],
        browser=browser,
        timeout=180,
    )
    verify_tree(project / "engine", expected, True)
    require(input_hashes(project / "engine") == inputs, "INPUTS_CHANGED_DURING_RENDER")
    stem = "油气与金融市场日报_" + day
    artifacts = {}
    for suffix in (".html", ".png", ".json"):
        name = stem + suffix
        raw = read(project / "engine/app/output" / name, 12 * 1024 * 1024)
        require(len(raw) > 0, "EMPTY_ARTIFACT")
        artifacts[name] = raw
    snapshot = json.loads(artifacts[stem + ".json"])
    require(
        snapshot["publication_date"] == day
        and snapshot["data_date"]
        == (dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat(),
        "OUTPUT_DATE",
    )
    from PIL import Image

    with Image.open(io.BytesIO(artifacts[stem + ".png"])) as image:
        require(
            image.format == "PNG"
            and image.width == 780
            and 500 <= image.height <= 36000,
            "PNG_SHAPE",
        )
        image.verify()
    build_id = uuid.uuid4().hex
    accepted = project / "deliveries" / build_id
    accepted.mkdir(parents=True)
    for name, raw in artifacts.items():
        (accepted / name).write_bytes(raw)
    receipt = dict(
        schema="energy-daily-original-run.v1",
        **edition(meta),
        entry_revision=ENTRY_REVISION,
        reproduction_only=edition(meta)["mode"] == "reproduce",
        archive_sha256=ARCHIVE_SHA,
        input_sha256=inputs,
        artifacts={n: sha(b) for n, b in artifacts.items()},
        original_files_verified=len(expected),
        business_code_modified=False,
        visual_acceptance_pending=True,
        provider_requests=0,
        work_messages_sent=0,
    )
    save(accepted / "RECEIPT.json", receipt)
    save(
        project / "LATEST.json",
        dict(build_id=build_id, receipt_sha256=sha(read(accepted / "RECEIPT.json"))),
    )
    return inspect(project, expected)


def inspect(project, expected):
    meta = project_meta(project, expected)
    latest = json.loads(read(project / "LATEST.json"))
    run = latest["build_id"]
    require(len(run) == 32 and all(c in "0123456789abcdef" for c in run), "BUILD_ID")
    output = project / "deliveries" / run
    raw = read(output / "RECEIPT.json")
    require(sha(raw) == latest["receipt_sha256"], "RECEIPT_CHANGED")
    receipt = json.loads(raw)
    require(
        receipt["archive_sha256"] == ARCHIVE_SHA
        and receipt["publication_date"] == meta["publication_date"]
        and (receipt.get("mode") or "legacy_unspecified") == edition(meta)["mode"]
        and receipt.get("data_date", edition(meta)["data_date"]) == edition(meta)["data_date"],
        "RECEIPT_IDENTITY",
    )
    require(
        receipt["input_sha256"] == input_hashes(project / "engine"),
        "INPUTS_CHANGED_REBUILD",
    )
    names = {
        "油气与金融市场日报_" + meta["publication_date"] + ext
        for ext in (".html", ".png", ".json")
    }
    require(set(receipt["artifacts"]) == names, "ARTIFACT_SET")
    for name in names:
        require(
            sha(read(output / name, 12 * 1024 * 1024)) == receipt["artifacts"][name],
            "ARTIFACT_CHANGED",
        )
    accepted = output / "VISUAL-ACCEPTANCE.json"
    reviewed = False
    if accepted.exists():
        review = json.loads(read(accepted))
        require(
            review.get("receipt_sha256") == sha(raw)
            and review.get("visual_reviewed") is True,
            "VISUAL_RECEIPT_CHANGED",
        )
        reviewed = True
    return dict(
        edition(meta),
        status="ORIGINAL_REPORT_ACCEPTED"
        if reviewed
        else "ORIGINAL_REPORT_VERIFIED_VISUAL_PENDING",
        files=[str(output / n) for n in sorted(names)],
        business_code_modified=False,
        original_files_verified=len(expected),
        reproduction_only=edition(meta)["mode"] == "reproduce",
        formal_report_ready=reviewed and edition(meta)["mode"] == "generate",
        provider_requests=0,
        work_messages_sent=0,
    )


def complete(project, expected, meta, visually_reviewed):
    require(visually_reviewed, "VISUAL_REVIEW_REQUIRED")
    result = inspect(project, expected)
    if result["status"] == "ORIGINAL_REPORT_ACCEPTED":
        return result
    latest = json.loads(read(project / "LATEST.json"))
    delivery = project / "deliveries" / latest["build_id"]
    receipt = json.loads(read(delivery / "RECEIPT.json"))
    for name, digest in receipt["artifacts"].items():
        require(
            sha(read(project / "engine/app/output" / name, 12 * 1024 * 1024)) == digest,
            "ORIGINAL_OUTPUT_CHANGED",
        )
    day = meta["publication_date"]
    claim = json.loads(
        original_command(project, "workflow_gate.py", ["claim", "--date", day])
    )
    require(claim.get("status") == "CLAIMED", "ORIGINAL_EXECUTION_ALREADY_CLAIMED")
    owner = claim["owner"]
    lockfile = project / "engine/app/state" / (day + ".lock.json")
    require(json.loads(read(lockfile)).get("owner") == owner, "ORIGINAL_OWNER_MISSING")
    completed = json.loads(
        original_command(
            project, "workflow_gate.py", ["complete", "--date", day, "--owner", owner]
        )
    )
    require(completed.get("status") == "COMPLETE", "ORIGINAL_COMPLETION_FAILED")
    inspect(project, expected)
    original_command(
        project, "workflow_gate.py", ["release", "--date", day, "--owner", owner]
    )
    save(
        delivery / "VISUAL-ACCEPTANCE.json",
        dict(
            receipt_sha256=latest["receipt_sha256"],
            visual_reviewed=True,
            original_complete_verified=True,
            accepted_at=dt.datetime.now(TZ).isoformat(),
        ),
    )
    return inspect(project, expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "verify-original",
            "init",
            "status",
            "import-market",
            "refresh-market",
            "check",
            "build",
            "inspect",
            "complete",
        ],
    )
    parser.add_argument("--project")
    parser.add_argument(
        "--date", help="Publication date; omitted means today in Asia/Shanghai"
    )
    parser.add_argument("--market-cache")
    parser.add_argument("--mode", choices=("generate", "reproduce"), help="Init only; default generate")
    parser.add_argument("--browser")
    parser.add_argument("--visual-reviewed", action="store_true")
    args = parser.parse_args()
    result = dict(status="INCOMPLETE", provider_requests=0, work_messages_sent=0)
    lock = None
    identity = {}
    try:
        require(args.action == "init" or (args.date is None and args.mode is None),
                "EDITION_OPTIONS_INIT_ONLY")
        expected = original_files()
        if args.action == "verify-original":
            result.update(
                status="ORIGINAL_PACKAGE_VERIFIED",
                files=len(expected),
                archive_sha256=ARCHIVE_SHA,
            )
        else:
            require(args.project is not None, "PROJECT_REQUIRED")
            project = Path(args.project).absolute()
            require(
                not any(p.is_symlink() for p in [project, *project.parents]),
                "SYMLINK_REFUSED",
            )
            require(
                ROOT.resolve() not in project.resolve().parents
                and project.resolve() != ROOT.resolve(),
                "WRITABLE_SESSION_COPY_REQUIRED",
            )
            if args.action == "init":
                result = init(project, args.date, expected, mode=args.mode or "generate")
            elif args.action == "status":
                meta = project_meta(project, expected)
                identity = edition(meta)
                result = inputs_status(project, meta, expected)
            else:
                candidate = project / ".entry.lock"
                with candidate.open("x", encoding="ascii") as stream:
                    stream.write(str(os.getpid()))
                lock = candidate
                meta = project_meta(project, expected)
                identity = edition(meta)
                if args.action == "import-market":
                    require(args.market_cache is not None, "MARKET_CACHE_REQUIRED")
                    result = import_market(project, args.market_cache, meta)
                elif args.action == "refresh-market":
                    result = refresh_market(project, meta)
                elif args.action == "check":
                    require_report_inputs(project, meta, expected)
                    original_command(
                        project,
                        "validate_inputs.py",
                        ["--date", meta["publication_date"]],
                    )
                    result.update(identity, status="ORIGINAL_VALIDATOR_PASSED", formal_report_ready=False)
                elif args.action == "build":
                    result = build_report(project, meta, expected, args.browser)
                elif args.action == "complete":
                    result = complete(project, expected, meta, args.visual_reviewed)
                else:
                    result = inspect(project, expected)
                verify_tree(project / "engine", expected, True)
        original_files()
    except WorkflowBlocked as exc:
        result = dict(exc.report, error_class="WorkflowBlocked")
    except Exception as exc:
        result = dict(
            identity,
            status="INCOMPLETE",
            error_class=type(exc).__name__,
            provider_requests=0,
            work_messages_sent=0,
        )
        code = str(exc)
        result["error_code"] = (
            code
            if code
            and len(code) < 120
            and all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789" for c in code)
            else "LOCAL_FAILURE_REDACTED"
        )
    finally:
        if lock is not None:
            lock.unlink()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return int(result["status"] == "INCOMPLETE")


if __name__ == "__main__":
    sys.exit(main())
