"""Deterministic launcher; immutable upstream plus explicit missing-market adapter."""

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
from urllib.parse import quote as quote_url, urlsplit

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_SHA = "eb66843bbe6170d86d33b65efa593be263e778275c6f7d491e1fd0c3c6d98f99"
TZ = dt.timezone(dt.timedelta(hours=8))
MUTABLE = ("app/data/", "app/sources/", "app/output/", "app/state/", "reproduced/")
COLLECTOR_HOST = "energy-market-collector.deer-flow.svc.cluster.local"
# Qualified on the existing ARM64 document-runtime1 image. Full Chrome's GPU
# process crashes on that runtime; this bundled headless executable passed the
# same launch/new-page/screenshot path. No browser download or upstream edit.
LINUX_REPORT_BROWSER = "/root/.cache/ms-playwright/chromium_headless_shell-1228/chrome-linux/headless_shell"
ENTRY_REVISION = "20261111-efficient-r2"
MARKET_WAIT_SECONDS = 300
RUN_BUDGET_SECONDS = 540  # AIO command hard limit is 600s; leave cleanup/transport time.
EXECUTION_DEADLINE = None


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


def original_command(project, script, args, browser=None, timeout=120, *, successor=False):
    # Use a private subprocess, original argv and the existing upstream browser override.
    env_names = (
        "PATH",
        "SystemRoot",
        "WINDIR",
        "SystemDrive",
        "ProgramData",
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
    if successor:
        require(script == "nullable_market.py", "SUCCESSOR_ENTRY_REQUIRED")
        command = [sys.executable, "-B", str(ROOT / "adapters" / script),
                   "--engine", str(engine)] + args
    else:
        command = [sys.executable, "-B", str(engine / "app" / script)] + args
    if EXECUTION_DEADLINE is not None:
        timeout = min(timeout, EXECUTION_DEADLINE - time.monotonic())
        require(timeout > 0, "RUN_TIME_BUDGET_EXHAUSTED")
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
            raise ValueError("SUCCESSOR_COMMAND_TIMEOUT" if successor else "ORIGINAL_COMMAND_TIMEOUT") from None
        size = log.tell()
        require(size <= 256 * 1024, "ORIGINAL_OUTPUT_BOUND")
        log.seek(0)
        output = log.read()
    if child.returncode != 0 and script == "validate_inputs.py":
        # Only exact static validator diagnostics, never echoed source text/logs.
        repairs = {
            "Snapshot date mismatch": "correct_input_dates",
            "History window mismatch": "restore_original_market_input",
            "Incomplete API set": "wait_for_required_target_market_quotes",
            "Invalid target API quote": "wait_for_required_target_market_quotes",
            "Invalid API previous value": "restore_original_market_input",
            "API change mismatch": "restore_original_market_input",
            "Incomplete financial set": "supply_all_seven_financial_entries",
            "Financial identity/unit/date mismatch": "correct_financial_identity_unit_date",
            "Missing source URL/name": "complete_source_evidence",
            "Missing retrieval time": "complete_source_evidence",
            "Missing evidence archive": "complete_source_evidence",
            "Evidence missing or outside app directory": "complete_source_evidence",
            "Unexplained financial missing quote": "record_financial_missing_status_reason_and_evidence",
            "Invalid financial quote/basis": "correct_financial_quote_basis",
            "Unexplained missing change": "record_missing_previous_when_applicable",
            "Invalid reported change": "correct_financial_change_evidence",
            "Invalid financial previous": "correct_financial_change_evidence",
            "Financial change mismatch": "correct_financial_change_evidence",
            "News cutoff must include timezone": "correct_news_cutoff",
            "News cutoff after publication": "correct_news_cutoff",
            "News counts fail": "complete_required_independent_news",
            "Duplicate article across sections": "replace_duplicate_news",
            "Empty article": "complete_verified_news_text",
            "News outside time window": "replace_out_of_window_news",
            "News published after cutoff": "replace_out_of_window_news",
        }
        final_line = output.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
        issue = final_line[0].removeprefix("ValueError: ")
        if final_line[0].startswith("ValueError: ") and issue in repairs:
            raise WorkflowBlocked(dict(error_code="ORIGINAL_COMMAND_FAILED_VALIDATE_INPUTS",
                                       validation_issue=issue, next_action=repairs[issue]))
    require(child.returncode == 0,
            ("SUCCESSOR_COMMAND_FAILED_" if successor else "ORIGINAL_COMMAND_FAILED_")
            + script.replace(".py", "").upper())
    return output


def successor_command(project, action, day, *, extra=(), browser=None, timeout=120):
    output = original_command(project, "nullable_market.py", ["--action", action, "--date", day, *extra],
                              browser=browser, timeout=timeout, successor=True)
    result = json.loads(output)
    require(isinstance(result, dict), "SUCCESSOR_RESULT_SHAPE")
    return result


def validate_quality(quality):
    require(isinstance(quality, dict) and quality.get("quality") in ("holiday", "incomplete")
            and quality.get("policy_version") == "energy-market-nullable.v2"
            and quality.get("validator_id") == "energy-market-nullable-validator.v2"
            and type(quality.get("formal_report_ready")) is bool
            and quality["formal_report_ready"] == (quality["quality"] == "holiday"), "REPORT_QUALITY")
    return quality


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
    if result["mode"] == "generate" and meta.get("news_as_of"):
        news = json.loads(read(project / "engine/app/data" / ("news_" + meta["publication_date"] + ".json")))
        actual = dt.datetime.fromisoformat(news["news_as_of"])
        require(actual.tzinfo is not None and actual == dt.datetime.fromisoformat(meta["news_as_of"]),
                "NEWS_CUTOFF_CHANGED")
    return result


def news_cutoff(publication, explicit=None):
    now = dt.datetime.now(TZ)
    if explicit:
        cutoff = dt.datetime.fromisoformat(explicit)
        require(cutoff.tzinfo is not None, "NEWS_CUTOFF_TIMEZONE_REQUIRED")
        cutoff = cutoff.astimezone(TZ)
        require(cutoff <= now and cutoff.date() <= publication, "NEWS_CUTOFF_AFTER_EDITION")
    else:
        cutoff = min(now, dt.datetime.combine(publication, dt.time(23, 59, 59), TZ))
    return cutoff.isoformat()


def init(project, day, expected, mode="generate", news_as_of=None):
    require(mode in ("generate", "reproduce"), "PROJECT_MODE")
    require(mode != "reproduce" or bool(day), "REPRODUCTION_DATE_REQUIRED")
    require(mode != "reproduce" or news_as_of is None, "REPRODUCTION_CUTOFF_FROM_INPUT")
    date_selection = "explicit" if day else "beijing_today"
    day = day or dt.datetime.now(TZ).date().isoformat()
    publication = dt.date.fromisoformat(day)
    require(
        day == publication.isoformat() and publication <= dt.datetime.now(TZ).date(),
        "PUBLICATION_DATE",
    )
    cutoff = news_cutoff(publication, news_as_of)
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
    if mode == "generate":
        meta.update(news_as_of=cutoff, news_cutoff_basis="explicit" if news_as_of else "task_start_or_historical_day_end")
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
    result = dict(edition(meta), status="ORIGINAL_MARKET_SCRIPT_COMPLETED", cache_days=len(copies))
    save(project / "MARKET-READY.json", dict(
        edition(meta), archive_sha256=ARCHIVE_SHA,
        energy_sha256=sha(read(project / "engine/app/data" / ("energy_" + target.isoformat() + ".json"))),
    ))
    return result


def import_annotated_market(project, meta, observed):
    """Import a sealed collector batch, or an explicit local acquisition failure.

    The successor owns normalization/quality rules. This launcher never infers a
    holiday, substitutes an older target price or reads the provider credential.
    """
    observation = project / "engine/app/sources" / ("market-observation_" + edition(meta)["data_date"] + ".json")
    save(observation, observed)
    result = successor_command(project, "import-market", meta["publication_date"],
                               extra=("--cache", str(ROOT.parent / "energy-market-data"),
                                      "--observation", str(observation)))
    save(project / "MARKET-READY.json", dict(
        edition(meta), archive_sha256=ARCHIVE_SHA,
        energy_sha256=sha(read(project / "engine/app/data" / ("energy_" + edition(meta)["data_date"] + ".json"))),
        report_quality=result.get("report_quality"), acquisition_observation_sha256=sha(read(observation)),
    ))
    return dict(result, **edition(meta), ready=True)


def request_market(day, *, start=True):
    # A fixed internal address, no proxy, redirect, credential or arbitrary URL.
    connection = http.client.HTTPConnection(COLLECTOR_HOST, 8766, timeout=10)
    try:
        connection.request(
            "POST" if start else "GET",
            ("/prepare/" if start else "/status/") + day,
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
            data.get("status") in ("READY", "ANNOTATED_READY", "INCOMPLETE_DATA", "COLLECTING", "WAIT_DATA", "INCOMPLETE")
            or (not start and data.get("status") == "NOT_REQUESTED"),
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
    for key, allowed in (("status", {"READY", "ANNOTATED_READY", "INCOMPLETE_DATA", "COLLECTING", "WAIT_DATA", "INCOMPLETE", "NOT_REQUESTED"}),
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


def collector_observation(observed, meta):
    """The only producer fields crossing into session evidence; no raw errors."""
    result = dict(collector_summary(observed), data_date=edition(meta)["data_date"])
    if "manifest_file" in observed or "manifest_sha256" in observed:
        name, digest = observed.get("manifest_file"), observed.get("manifest_sha256")
        prefix = "market-state_" + result["data_date"] + "_"
        require(isinstance(name, str) and name.startswith(prefix) and name.endswith(".json")
                and len(name[len(prefix):-5]) == 32
                and all(c in "0123456789abcdef" for c in name[len(prefix):-5]), "COLLECTOR_MANIFEST_NAME")
        require(isinstance(digest, str) and len(digest) == 64
                and all(c in "0123456789abcdef" for c in digest), "COLLECTOR_MANIFEST_HASH")
        result.update(manifest_file=name, manifest_sha256=digest)
    return result


def refresh_market(project, meta):
    identity = edition(meta)
    target = identity["data_date"]
    deadline = time.monotonic() + MARKET_WAIT_SECONDS
    first = True
    while True:
        try:
            observed = request_market(target) if first else request_market(target, start=False)
            first = False
        except (OSError, http.client.HTTPException):
            raise WorkflowBlocked(dict(identity, error_code="COLLECTOR_UNREACHABLE")) from None
        summary = collector_summary(observed)
        if observed["status"] in ("ANNOTATED_READY", "INCOMPLETE_DATA") or (
                observed["status"] == "READY" and "manifest_file" in observed):
            return import_annotated_market(project, meta, collector_observation(observed, meta))
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


def market_once(project, meta):
    """Start once, then use read-only status. Never retry a provider failure here."""
    identity = edition(meta)
    if identity["mode"] == "reproduce":
        return dict(identity, status="REPRODUCTION_LOCAL_INPUTS", ready=True)
    target = identity["data_date"]
    receipt = project / "MARKET-READY.json"
    energy = project / "engine/app/data" / ("energy_" + target + ".json")
    if receipt.exists():
        saved = json.loads(read(receipt))
        require(all(saved.get(k) == v for k, v in identity.items())
                and saved.get("archive_sha256") == ARCHIVE_SHA, "MARKET_RECEIPT_IDENTITY")
        if energy.exists() and saved.get("energy_sha256") == sha(read(energy)):
            return dict(identity, status="PROJECT_MARKET_REUSED", ready=True)
    attempt = project / "MARKET-REQUEST.json"
    start = not attempt.exists()
    if start:
        # Persist before dispatch: a lost response must not resubmit the collection.
        save(attempt, dict(identity, requested_at=dt.datetime.now(TZ).isoformat()))
    else:
        saved = json.loads(read(attempt))
        require(all(saved.get(k) == v for k, v in identity.items()), "MARKET_REQUEST_IDENTITY")
    try:
        observed = request_market(target, start=start)
    except (OSError, http.client.HTTPException):
        return dict(identity, status="COLLECTOR_UNREACHABLE", ready=False,
                    next_action="resume_same_project_readonly_status", automatic_retry=False)
    except (ValueError, UnicodeError) as exc:
        # This boundary is only the bounded internal HTTP status envelope.
        # Manifest/hash/evidence errors below remain hard failures.
        if not isinstance(exc, (json.JSONDecodeError, UnicodeError)) and str(exc) not in (
                "COLLECTOR_RESPONSE_REJECTED", "COLLECTOR_DATE_MISMATCH", "COLLECTOR_STATUS_REJECTED"):
            raise
        return dict(identity, status="COLLECTOR_INVALID_RESPONSE", ready=False,
                    next_action="generate_explicit_incomplete_report", automatic_retry=False)
    summary = collector_summary(observed)
    if observed["status"] in ("ANNOTATED_READY", "INCOMPLETE_DATA") or (
            observed["status"] == "READY" and "manifest_file" in observed):
        result = import_annotated_market(project, meta, collector_observation(observed, meta))
        return dict(result, collector=summary)
    if observed["status"] == "READY":
        result = import_market(project, ROOT.parent / "energy-market-data", meta)
        return dict(result, ready=True, collector=summary)
    statuses = {"COLLECTING": "MARKET_COLLECTING", "WAIT_DATA": "WAIT_PROVIDER_DATA",
                "INCOMPLETE": "COLLECTOR_FAILED", "NOT_REQUESTED": "COLLECTOR_STATE_UNAVAILABLE"}
    return dict(identity, status=statuses[observed["status"]], ready=False,
                collector=summary, automatic_retry=False,
                next_action="resume_same_project_readonly_status" if observed["status"] == "COLLECTING"
                else "report_blocker_keep_project")


def prepare_report(project, meta, expected):
    # Research proceeds while the dedicated collector works; no 620-second wait.
    market = market_once(project, meta)
    inputs = inputs_status(project, meta, expected)
    cutoff = meta.get("news_as_of")
    cutoff_basis = meta.get("news_cutoff_basis", "existing_input_contract")
    if not cutoff:
        news_path = project / "engine/app/data" / ("news_" + meta["publication_date"] + ".json")
        if news_path.exists():
            news = json.loads(read(news_path))
            candidate = news.get("news_as_of")
            try:
                parsed = dt.datetime.fromisoformat(candidate)
                if parsed.tzinfo is not None and parsed.astimezone(TZ).date() <= dt.date.fromisoformat(meta["publication_date"]):
                    cutoff = candidate
            except (TypeError, ValueError):
                pass  # The unchanged validator will report an invalid input.
    return dict(inputs, status="PREPARED", market=market,
                next_action="research_financial_and_news", automatic_retry=False,
                research_required=meta.get("mode") != "reproduce",
                news_as_of=cutoff, news_cutoff_basis=cutoff_basis,
                paths=dict(project=str(project), engine=str(project / "engine"),
                           evidence=str(project / "engine/app/sources"),
                           evidence_relative_to=str(project / "engine/app"),
                           deliveries=str(project / "deliveries")),
                reference_documents=[str(ROOT / "upstream/docs" / name) for name in
                                     ("03-ACQUISITION.md", "05-DATA-CONTRACT.md", "01-STYLE.md")])


def run_report(project, meta, expected, browser):
    global EXECUTION_DEADLINE
    previous = EXECUTION_DEADLINE
    EXECUTION_DEADLINE = time.monotonic() + RUN_BUDGET_SECONDS
    try:
        return _run_report(project, meta, expected, browser)
    finally:
        EXECUTION_DEADLINE = previous


def _run_report(project, meta, expected, browser):
    """Reuse a verified build or execute the original validate/render sequence once."""
    if (project / "LATEST.json").exists():
        try:
            result = inspect(project, expected)
        except ValueError as exc:
            if str(exc) != "INPUTS_CHANGED_REBUILD":
                raise
        else:
            return dict(result, existing_delivery_reused=True)
    inputs = inputs_status(project, meta, expected)
    missing = [r["kind"] for r in inputs["input_states"]
               if r["kind"] != "energy" and r["state"] == "missing"]
    if missing:
        raise WorkflowBlocked(dict(inputs, error_code="INPUTS_MISSING",
                                   next_action="research_financial_and_news", missing_inputs=missing))
    deadline = time.monotonic() + MARKET_WAIT_SECONDS
    market = market_once(project, meta)
    while market["status"] == "MARKET_COLLECTING":
        if time.monotonic() >= deadline:
            market = dict(market, status="MARKET_WAIT_TIMEOUT", next_action="report_blocker_keep_project")
            break
        time.sleep(min(5, max(0, deadline - time.monotonic())))
        # market_once persisted dispatch before its first POST; subsequent checks
        # use GET and cannot start another collection or retry provider failures.
        market = market_once(project, meta)
    if not market["ready"]:
        # User-approved unattended policy: a technical failure produces an
        # unmistakably incomplete image, never a fabricated holiday/price.
        allowed = {"MARKET_WAIT_TIMEOUT", "COLLECTOR_UNREACHABLE", "WAIT_PROVIDER_DATA",
                   "COLLECTOR_FAILED", "COLLECTOR_STATE_UNAVAILABLE", "COLLECTOR_INVALID_RESPONSE"}
        require(market["status"] in allowed, "MARKET_FAILURE_STATUS")
        import_annotated_market(project, meta, dict(
            data_date=edition(meta)["data_date"], status="LOCAL_ACQUISITION_FAILURE",
            failure_code=market["status"], collector=market.get("collector", {}),
            observed_at=dt.datetime.now(TZ).isoformat(), target_response_validated=False,
        ))
    return build_report(project, meta, expected, browser)


def reply_text(snapshot, meta):
    """Format the accepted snapshot, without recalc, selection or generated claims."""
    def text(value):
        # Source material stays text, never reply markup or an instruction.
        value = " ".join(str(value).split())
        for character in ("\\", "`", "*", "_", "[", "]", "<", ">"):
            value = value.replace(character, "\\" + character)
        return value

    def source_url(value):
        parsed = urlsplit(value)
        require(parsed.scheme in ("http", "https") and bool(parsed.hostname)
                and parsed.username is None and parsed.password is None
                and not any(ord(c) < 32 for c in value), "REPLY_SOURCE_URL")
        # An autolink keeps underscores/query strings intact; encode only unsafe
        # delimiter characters instead of applying prose Markdown escapes.
        return "<" + quote_url(value, safe="/:?#[]@!$&'()*+,;=%~.-_") + ">"

    lines = ["油气与金融市场日报", "发布日期：" + meta["publication_date"],
             "行情日期：" + edition(meta)["data_date"]]
    quality = snapshot.get("report_quality")
    if quality and quality.get("formal_report_ready") is False:
        lines.insert(0, "【数据不完整：本期部分数据未取得，不作为完整正式日报】")
    elif quality and quality.get("quality") == "holiday":
        lines.append("供应商明确返回休市的报价显示“—／休市”，不使用旧价替代。")
    if meta.get("mode") == "reproduce":
        lines.append("历史/样例复现，不代表最新行情。")
    elif meta.get("mode") != "generate":
        lines.append("历史项目模式未记录，不宣称最新日报。")
    for key, title in (("oil", "原油市场"), ("gas", "天然气市场"),
                       ("freight", "航运运费"), ("stocks", "石油公司与股市"), ("fx", "汇率与美元")):
        lines.extend(["", title])
        for quote in snapshot[key]:
            value, change = quote["value"], quote["change"]
            formatted = "—" if value is None else f"{value:,.{quote.get('decimals', 2)}f}"
            if quote.get("status") == "non_trading":
                delta = "休市"
            elif quote.get("status") == "missing":
                delta = "待核实"
            elif quote.get("status") in ("transport_unavailable", "transport_failed", "invalid_response", "provider_missing", "unavailable"):
                delta = "数据未取得" if quote.get("status") != "provider_missing" else "供应商未提供"
            elif change is None:
                delta = "口径变化，不比涨跌" if key == "freight" else "前值待补" if value is not None else "未接入"
            else:
                delta = ("▲ " if change > 0 else "▼ " if change < 0 else "— ") + f"{abs(change):.2f}%"
            line = text(quote["name"]) + "：" + formatted + " " + text(quote["unit"]) + "；" + delta
            if quote.get("missing_reason"):
                line += "（" + text(quote["missing_reason"]) + "）"
            lines.append(line)
    for key, title in (("market_news", "油气市场重要新闻"), ("company_news", "石油公司动态")):
        lines.extend(["", title])
        for index, item in enumerate(snapshot[key], 1):
            lines.append(f"{index}. " + text(item["title"]) + "：" + text(item["text"]))
            lines.append("来源：" + text(item["source"]) + " " + text(item["published"]) + " " + source_url(item["url"]))
    sources = list(dict.fromkeys(q.get("source", "") for k in ("oil", "gas", "freight", "stocks", "fx")
                                for q in snapshot[k] if q.get("source")))
    lines.extend(["", "行情来源：" + "、".join(text(s) for s in sources) + "。",
                  "走势图不对非交易日数据补值，未更新数据留空；详细口径与曲线见本次文件。"])
    result = "\n".join(lines)
    require(len(result) <= 50000, "REPLY_TEXT_BOUND")
    return result


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
    energy = json.loads(read(project / "engine/app/data" / ("energy_" + edition(meta)["data_date"] + ".json")))
    successor = "policy_version" in energy
    validation = successor_command(project, "validate", day) if successor else None
    if not successor:
        original_command(project, "validate_inputs.py", ["--date", day])
    inputs = input_hashes(project / "engine")
    browser = report_browser(browser)
    if successor:
        successor_command(project, "generate", day, extra=("--png",), browser=browser, timeout=180)
    else:
        original_command(project, "generate_report.py", ["--date", day, "--png"], browser=browser, timeout=180)
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
    quality = snapshot.get("report_quality")
    if successor:
        validate_quality(quality)
        require(quality == validation.get("report_quality"), "OUTPUT_QUALITY_MISMATCH")
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
    reply_name = stem + "-文字版.txt"
    reply = reply_text(snapshot, meta).encode("utf-8")
    (accepted / reply_name).write_bytes(reply)
    receipt = dict(
        schema="energy-daily-annotated-run.v2" if successor else "energy-daily-original-run.v1",
        **edition(meta),
        entry_revision=ENTRY_REVISION,
        reproduction_only=edition(meta)["mode"] == "reproduce",
        archive_sha256=ARCHIVE_SHA,
        input_sha256=inputs,
        artifacts={n: sha(b) for n, b in artifacts.items()},
        reply_file=dict(name=reply_name, sha256=sha(reply), bytes=len(reply)),
        original_files_verified=len(expected),
        business_code_modified=False,
        visual_acceptance_pending=True,
        provider_requests=0,
        work_messages_sent=0,
    )
    if successor:
        receipt.update(report_quality=quality, successor_adapter_used=True,
                       original_validator_passed=False, original_business_files_modified=False)
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
    reply_path = None
    if "reply_file" in receipt:
        reply_row = receipt["reply_file"]
        reply_name = "油气与金融市场日报_" + meta["publication_date"] + "-文字版.txt"
        require(reply_row.get("name") == reply_name, "REPLY_FILE_IDENTITY")
        reply_path = output / reply_name
        reply_raw = read(reply_path, 256 * 1024)
        require(len(reply_raw) == reply_row["bytes"] and sha(reply_raw) == reply_row["sha256"], "REPLY_FILE_CHANGED")
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
    successor = receipt.get("successor_adapter_used") is True
    quality = receipt.get("report_quality")
    if successor:
        validate_quality(quality)
        snapshot_quality = json.loads(read(output / ("油气与金融市场日报_" + meta["publication_date"] + ".json"))).get("report_quality")
        require(snapshot_quality == quality, "RECEIPT_QUALITY_CHANGED")
    result = dict(
        edition(meta),
        status="ORIGINAL_REPORT_ACCEPTED"
        if reviewed
        else "ORIGINAL_REPORT_VERIFIED_VISUAL_PENDING",
        files=[str(output / n) for n in sorted(names)],
        business_code_modified=False,
        original_files_verified=len(expected),
        reproduction_only=edition(meta)["mode"] == "reproduce",
        formal_report_ready=reviewed and edition(meta)["mode"] == "generate"
        and (not successor or quality["formal_report_ready"]),
        provider_requests=0,
        work_messages_sent=0,
    )
    if successor:
        result.update(status=("ANNOTATED_REPORT_ACCEPTED" if quality["formal_report_ready"] else "INCOMPLETE_DATA_REPORT_ACCEPTED")
                      if reviewed else "ANNOTATED_REPORT_VERIFIED_VISUAL_PENDING",
                      report_quality=quality, original_validator_passed=False,
                      successor_adapter_used=True, report_generated=True)
    result["next_action"] = "present_files_then_reply" if reviewed else "view_png_then_complete"
    if reviewed:
        snapshot = json.loads(read(output / ("油气与金融市场日报_" + meta["publication_date"] + ".json")))
        reply = reply_text(snapshot, meta)
        if reply_path:
            require(reply_raw.decode("utf-8") == reply, "REPLY_SNAPSHOT_MISMATCH")
            result["reply_text_file"] = str(reply_path)
            result["reply_text_sha256"] = sha(reply_raw)
            result["files"].append(str(reply_path))
        if len(json.dumps(reply, ensure_ascii=False)) <= 10000:
            result["reply_text"] = reply
        else:
            result["reply_text_inline"] = False
            result["next_action"] = "read_complete_reply_file_then_present_files" if reply_path else "read_legacy_report_json_then_present_files"
            if not reply_path:
                result["reply_snapshot_file"] = str(output / ("油气与金融市场日报_" + meta["publication_date"] + ".json"))
        result["conversation_link_source"] = "present_files_host_receipt_or_work_delivery"
    return result


def complete(project, expected, meta, visually_reviewed):
    require(visually_reviewed, "VISUAL_REVIEW_REQUIRED")
    result = inspect(project, expected)
    if result["status"] in ("ORIGINAL_REPORT_ACCEPTED", "ANNOTATED_REPORT_ACCEPTED", "INCOMPLETE_DATA_REPORT_ACCEPTED"):
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
    progress = delivery / "COMPLETION.json"
    if progress.exists():
        completion = json.loads(read(progress))
        require(completion.get("receipt_sha256") == latest["receipt_sha256"]
                and completion.get("phase") in ("claimed", "completed"), "COMPLETION_IDENTITY")
        owner = completion["owner"]
        require(isinstance(owner, str) and len(owner) == 32
                and all(c in "0123456789abcdef" for c in owner), "COMPLETION_OWNER")
    else:
        claim = json.loads(
            original_command(project, "workflow_gate.py", ["claim", "--date", day])
        )
        require(claim.get("status") == "CLAIMED", "ORIGINAL_EXECUTION_ALREADY_CLAIMED")
        owner = claim["owner"]
        completion = dict(owner=owner, receipt_sha256=latest["receipt_sha256"], phase="claimed")
        save(progress, completion)
    lockfile = project / "engine/app/state" / (day + ".lock.json")
    if lockfile.exists():
        require(json.loads(read(lockfile)).get("owner") == owner, "ORIGINAL_OWNER_MISSING")
    else:
        require(completion["phase"] == "completed", "ORIGINAL_OWNER_MISSING")
    if completion["phase"] == "claimed":
        if receipt.get("successor_adapter_used"):
            validation = successor_command(project, "validate", day)
            require(validation.get("report_quality") == receipt["report_quality"], "SUCCESSOR_COMPLETION_QUALITY")
            save(delivery / "SUCCESSOR-COMPLETION.json", dict(
                schema="energy-daily-annotated-completion.v1", date=day, owner=owner,
                receipt_sha256=latest["receipt_sha256"], outputs=receipt["artifacts"],
                report_quality=receipt["report_quality"], original_validator_passed=False,
            ))
        else:
            completed = json.loads(original_command(
                project, "workflow_gate.py", ["complete", "--date", day, "--owner", owner]))
            require(completed.get("status") == "COMPLETE", "ORIGINAL_COMPLETION_FAILED")
        completion["phase"] = "completed"
        save(progress, completion)
    original_completion = json.loads(read(delivery / "SUCCESSOR-COMPLETION.json"
                                         if receipt.get("successor_adapter_used")
                                         else project / "engine/app/state" / (day + ".complete.json")))
    require(original_completion.get("date") == day
            and original_completion.get("outputs") == receipt["artifacts"], "ORIGINAL_COMPLETION_CHANGED")
    if receipt.get("successor_adapter_used"):
        require(original_completion.get("owner") == owner
                and original_completion.get("receipt_sha256") == latest["receipt_sha256"]
                and original_completion.get("report_quality") == receipt["report_quality"]
                and original_completion.get("original_validator_passed") is False, "SUCCESSOR_COMPLETION_CHANGED")
    inspect(project, expected)
    if lockfile.exists():
        original_command(
            project, "workflow_gate.py", ["release", "--date", day, "--owner", owner]
        )
    save(
        delivery / "VISUAL-ACCEPTANCE.json",
        dict(
            receipt_sha256=latest["receipt_sha256"],
            visual_reviewed=True,
            original_complete_verified=not receipt.get("successor_adapter_used", False),
            successor_complete_verified=receipt.get("successor_adapter_used", False),
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
            "prepare",
            "run",
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
    parser.add_argument("--news-as-of", help="Optional explicit timezone-aware news cutoff, fixed at prepare/init")
    parser.add_argument("--visual-reviewed", action="store_true")
    args = parser.parse_args()
    result = dict(status="INCOMPLETE", provider_requests=0, work_messages_sent=0)
    lock = None
    identity = {}
    try:
        require(args.action in ("init", "prepare") or (args.date is None and args.mode is None),
                "EDITION_OPTIONS_INIT_ONLY")
        require(args.action in ("init", "prepare") or args.news_as_of is None, "NEWS_CUTOFF_INIT_ONLY")
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
                result = init(project, args.date, expected, mode=args.mode or "generate", news_as_of=args.news_as_of)
            elif args.action == "status":
                meta = project_meta(project, expected)
                identity = edition(meta)
                result = inputs_status(project, meta, expected)
            else:
                if args.action == "prepare" and not project.exists():
                    init(project, args.date, expected, mode=args.mode or "generate", news_as_of=args.news_as_of)
                candidate = project / ".entry.lock"
                with candidate.open("x", encoding="ascii") as stream:
                    stream.write(str(os.getpid()))
                lock = candidate
                meta = project_meta(project, expected)
                identity = edition(meta)
                if args.action == "prepare":
                    require((args.date is None or args.date == meta["publication_date"])
                            and (args.mode is None or args.mode == meta.get("mode")), "PROJECT_EDITION_CHANGED")
                    require(args.news_as_of is None or news_cutoff(dt.date.fromisoformat(meta["publication_date"]), args.news_as_of)
                            == meta.get("news_as_of"), "NEWS_CUTOFF_CHANGED")
                    result = prepare_report(project, meta, expected)
                elif args.action == "run":
                    result = run_report(project, meta, expected, args.browser)
                elif args.action == "import-market":
                    require(args.market_cache is not None, "MARKET_CACHE_REQUIRED")
                    result = import_market(project, args.market_cache, meta)
                elif args.action == "refresh-market":
                    result = refresh_market(project, meta)
                elif args.action == "check":
                    require_report_inputs(project, meta, expected)
                    energy = json.loads(read(project / "engine/app/data" / ("energy_" + identity["data_date"] + ".json")))
                    if "policy_version" in energy:
                        checked = successor_command(project, "validate", meta["publication_date"])
                        result.update(identity, status="SUCCESSOR_VALIDATOR_PASSED", formal_report_ready=False,
                                      report_quality=validate_quality(checked["report_quality"]), original_validator_passed=False)
                    else:
                        original_command(project, "validate_inputs.py", ["--date", meta["publication_date"]])
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
        result = dict(identity, **dict(exc.report, error_class="WorkflowBlocked"))
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
