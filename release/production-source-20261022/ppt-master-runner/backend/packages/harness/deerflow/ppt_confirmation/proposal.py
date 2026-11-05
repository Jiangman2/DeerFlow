"""Capture a proposal from the HASH-PINNED official page, never submit it.

The admitted isolated-PPT transport obtains an owner-authenticated, version-consistent
snapshot. A fresh browser context renders the upstream UI using in-memory HTTP
replies; its Confirm request is intercepted, not sent to a server. No choice
schema, template ranking, field defaults or receipt writer is reimplemented.

This proves only what the official page would submit. Consent, server-side
countdown, durable claim, conditional official-owner dispatch and receipt
reconciliation are separate requirements. Neither this result nor simulated
HTTP success is a confirmation. Never automatically POST this payload directly
to an unversioned owner endpoint.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlsplit

ORIGIN = "https://ppt-confirmation.invalid"
MAX_BYTES = 2 * 1024 * 1024
MAX_PAYLOAD_BYTES = 256 * 1024
CAPTURE_DEADLINE_SECONDS = 20
CLOSE_DEADLINE_SECONDS = 5
_PINS = {
    "server.py": "7f263892dd49af2f6c48463ab5aa51367e888640f14517ad93730237e5ebd786",
    "conditional_protocol.py": "935b44c27a064e00d20b52ab9831b94811f40839fea779cfb5bf9b4ed1c1c71b",
    "static/index.html": "f905e0d24ed2abfaeb409215068375468bea526d6fe95ed8faa34a355a226b9a",
    "static/app.js": "79f0f4f67f16fd18cfac780a6f1993ac1e0a698748ec9cb9d4cb27ce106f33e2",
    "static/style.css": "03596a728440a186cb8d417dbbaa552e2e8f1983ead8de951dae2bb5701478ad",
}
_VERSIONS = ("recommendation_version", "result_version", "template_selection_version", "template_install_version")


class ProposalError(ValueError):
    """Allowlisted labels only; don't expose project data or browser messages."""


def _require(ok, label):
    if not ok:
        raise ProposalError(label)


def _json(value, limit=MAX_BYTES):
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise ProposalError("invalid_snapshot_json") from None
    _require(len(raw) <= limit, "snapshot_size_limit")
    return raw


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def load_assets(source_root: Path):
    """Read immutable installed official assets only, never project HTML/JS."""
    contents = {}
    try:
        for relative, expected in _PINS.items():
            path = source_root / relative
            _require(all(not part.is_symlink() for part in [path, *path.parents]), "unsafe_official_source")
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            try:
                before = os.fstat(fd)
                _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= MAX_BYTES, "unsafe_official_source")
                with os.fdopen(fd, "rb", closefd=False) as handle:
                    raw = handle.read(MAX_BYTES + 1)
                after = os.fstat(fd)
                _require((before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) == (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), "official_source_changed")
            finally:
                os.close(fd)
            normalized = raw.replace(b"\r\n", b"\n")
            _require(_digest(normalized) == expected, "official_source_changed")
            contents[relative] = normalized
    except OSError:
        raise ProposalError("official_source_unavailable") from None
    return MappingProxyType({"/": contents["static/index.html"], "/static/app.js": contents["static/app.js"], "/static/style.css": contents["static/style.css"]}), _digest(_json(_PINS))


def _health_binding(health, expected_project):
    _require(isinstance(health, dict) and health.get("project") == expected_project, "owner_project_mismatch")
    session = health.get("session")
    _require(
        health.get("service") == "confirm_ui"
        and health.get("status") == "ok"
        and health.get("recommendations") is True
        and isinstance(session, dict)
        and session.get("phase") == "strategist"
        and session.get("status") == "ready_user"
        and not session.get("recommendation_error")
        and not session.get("template_error"),
        "official_owner_not_ready",
    )
    stage = session.get("recommendation_stage_number")
    _require(
        type(stage) is int
        and stage in {1, 2}
        and type(health.get("stage")) is int
        and health["stage"] == stage
        and session.get("current_stage") == f"stage{stage}"
        and type(session.get("expected_stage_number")) is int
        and session["expected_stage_number"] == stage,
        "official_stage_mismatch",
    )
    _require(type(health.get("pid")) is int and health["pid"] > 0, "invalid_owner_process")
    for key in _VERSIONS:
        value = session.get(key)
        _require(value is None and key != "recommendation_version" or isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value), "invalid_owner_version")
    return {"project": expected_project, "pid": health.get("pid"), "stage": stage, **{key: session.get(key) for key in _VERSIONS}}


@dataclass(frozen=True, repr=False)
class PageSnapshot:
    stage: int
    version: str
    source_digest: str
    recommendations_json: bytes = field(repr=False)
    replies: MappingProxyType = field(repr=False)
    assets: MappingProxyType = field(repr=False)
    owner_version: str | None = None


def prepare_snapshot(*, before: dict, recommendations: dict, catalogs: dict, after: dict, expected_project: str, source_root: Path) -> PageSnapshot:
    """Inputs come from the trusted owner adapter, NOT browser/user JSON.

    Stable native version hashes bind Stage 1/2, prior result and installation.
    Full served recommendation and catalog hashes bind computed UI defaults.
    A server restart/version change requires a new observation, not stale reuse.
    """
    _require(isinstance(expected_project, str) and 0 < len(expected_project) <= 4096 and "\x00" not in expected_project, "invalid_project_identity")
    binding = _health_binding(before, expected_project)
    _require(binding == _health_binding(after, expected_project), "owner_changed_during_snapshot")
    _require(isinstance(recommendations, dict) and type(recommendations.get("stage")) in (int, str) and recommendations["stage"] in (binding["stage"], f"stage{binding['stage']}"), "official_stage_mismatch")
    _require(isinstance(catalogs, dict) and bool(catalogs), "official_catalogs_missing")
    rec_raw, catalog_raw = _json(recommendations), _json(catalogs)
    protocol = recommendations.get("confirmation_protocol")
    owner_version = None
    if protocol is not None:
        _require(isinstance(protocol, dict) and type(protocol.get("protocol")) is int and protocol["protocol"] == 1
                 and isinstance(protocol.get("version"), str) and re.fullmatch(r"[a-f0-9]{64}", protocol["version"]), "invalid_owner_protocol")
        owner_version = protocol["version"]
    assets, source_digest = load_assets(source_root)
    version = _digest(_json({**binding, "recommendations": _digest(rec_raw), "catalogs": _digest(catalog_raw), "source": source_digest}))
    replies = MappingProxyType({"/api/session": _json(before["session"]), "/api/recommendations": rec_raw, "/api/catalogs": catalog_raw, "/static/catalogs.json": catalog_raw, "/api/icon-previews": b"{}", "/api/ai-image-comparison": b"{}"})
    return PageSnapshot(binding["stage"], version, source_digest, rec_raw, replies, assets, owner_version)


@dataclass(frozen=True, repr=False)
class CapturedProposal:
    version: str
    source_digest: str
    payload_digest: str
    payload_json: bytes = field(repr=False)
    page_title: str
    console_errors: int
    request_count: int
    console_warnings: int = 0
    confirmation_submitted: bool = False
    owner_version: str | None = None

    @property
    def payload(self):
        return json.loads(self.payload_json)

    def conditional_payload(self, operation_id: str):
        """Called only AFTER a durable consent claim, never by capture itself."""
        _require(isinstance(self.owner_version, str) and re.fullmatch(r"[a-f0-9]{64}", self.owner_version), "conditional_owner_required")
        _require(isinstance(operation_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", operation_id), "invalid_operation_identity")
        return {**self.payload, "_confirmation_request": {"protocol": 1, "version": self.owner_version, "operation_id": operation_id}}


async def capture_proposal(browser, snapshot: PageSnapshot, *, screenshot_path: Path | None = None, viewport: dict | None = None) -> CapturedProposal:
    """Use a fresh context with no cookies/storage/real HTTP and one UI click.

    Browser lifecycle belongs to a bounded Host pool. Discard the owned browser
    after any capture failure/timeout, including uncertain context creation.
    Caller must re-observe the
    official owner before claiming/submitting; time spent here is NOT countdown
    consent. Auxiliary image previews are inert placeholders in this calculator.
    A missing/invalid default returns a blocker, never an invented selection.
    """
    _require(isinstance(snapshot, PageSnapshot), "invalid_snapshot")
    future = asyncio.get_running_loop().create_future()
    errors, console_errors, console_warnings = [], [], []
    count = 0
    context = None

    async def websocket(route):
        errors.append("unexpected_websocket")
        await route.close()

    async def route_request(route):
        nonlocal count
        count += 1
        request = route.request
        url = urlsplit(request.url)
        if f"{url.scheme}://{url.netloc}" != ORIGIN or count > 96 or url.query:
            errors.append("unexpected_page_request")
            await route.abort()
            return
        path = url.path
        if request.method == "POST" and path == "/api/confirm" and not future.done():
            try:
                body = request.post_data_buffer or b""
                _require(len(body) <= MAX_PAYLOAD_BYTES, "proposal_size_limit")
                payload = json.loads(body)
                _require(isinstance(payload, dict) and payload.get("stage") == ("stage1" if snapshot.stage == 1 else "final"), "proposal_stage_mismatch")
                metadata = payload.pop("_confirmation_request", None)
                if snapshot.owner_version is not None:
                    _require(isinstance(metadata, dict) and metadata.get("protocol") == 1 and metadata.get("version") == snapshot.owner_version
                             and isinstance(metadata.get("operation_id"), str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", metadata["operation_id"]), "proposal_protocol_mismatch")
                else:
                    _require(metadata is None, "proposal_protocol_mismatch")
                raw = _json(payload, MAX_PAYLOAD_BYTES)
                # In-memory acknowledgement ONLY. No official endpoint is called.
                await route.fulfill(status=200, content_type="application/json", body='{"status":"ok"}')
                if not future.done():
                    future.set_result(raw)
            except (ValueError, UnicodeError, RecursionError):
                errors.append("invalid_page_proposal")
                await route.abort()
            return
        if request.method == "POST" and path == "/api/shutdown" and future.done():
            await route.fulfill(status=200, content_type="application/json", body='{"status":"capture_only"}')
            return
        if request.method != "GET":
            errors.append("unexpected_page_mutation")
            await route.abort()
            return
        if path in snapshot.replies:
            await route.fulfill(status=200, content_type="application/json", body=snapshot.replies[path])
        elif path in snapshot.assets:
            mime = {"/": "text/html", "/static/app.js": "application/javascript", "/static/style.css": "text/css"}[path]
            await route.fulfill(status=200, content_type=mime, body=snapshot.assets[path])
        elif request.resource_type == "image":
            # No remote/template files are fetched just to compute choices.
            await route.fulfill(status=200, content_type="image/svg+xml", body='<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>')
        elif path == "/favicon.ico":
            await route.fulfill(status=204)
        else:
            errors.append("unexpected_page_resource")
            await route.abort()

    async def capture():
        nonlocal context
        context = await browser.new_context(viewport=viewport or {"width": 1280, "height": 900}, service_workers="block", accept_downloads=False, proxy={"server": "http://127.0.0.1:9"})
        await context.route("**/*", route_request)
        await context.route_web_socket("**/*", websocket)
        page = await context.new_page()
        page.on("pageerror", lambda _: console_errors.append(True))
        page.on("console", lambda event: console_errors.append(True) if event.type == "error" else None)
        page.on("console", lambda event: console_warnings.append(True) if event.type == "warning" else None)
        await page.goto(ORIGIN + "/", wait_until="domcontentloaded", timeout=10000)
        await page.locator("#sections").wait_for(state="visible", timeout=8000)
        _require(page.url == ORIGIN + "/" and len(await page.locator("#sections").inner_text()) > 20, "official_page_not_rendered")
        title = await page.title()
        _require(bool(title) and not await page.locator("#error").is_visible(), "official_page_error")
        if screenshot_path is not None:
            await page.screenshot(path=str(screenshot_path), full_page=False)
        await page.locator("#btn-confirm").click(timeout=3000)
        raw = await asyncio.wait_for(asyncio.shield(future), timeout=3000 / 1000)
        _require(not errors and not console_errors, "official_page_capture_errors")
        return CapturedProposal(snapshot.version, snapshot.source_digest, _digest(raw), raw, title, len(console_errors), count,
                                len(console_warnings), owner_version=snapshot.owner_version)

    try:
        # Python 3.10 is still deployed in the Agent Host. Keep the whole
        # operation (including context creation) inside its supported deadline.
        return await asyncio.wait_for(capture(), timeout=CAPTURE_DEADLINE_SECONDS)
    except ProposalError:
        raise
    except Exception:
        raise ProposalError("official_page_capture_failed") from None
    finally:
        if not future.done():
            future.cancel()
        if context is not None:
            try:
                await asyncio.wait_for(context.close(), timeout=CLOSE_DEADLINE_SECONDS)
            except Exception:
                # Caller must discard its owned browser on cleanup failure;
                # never claim the context was reclaimed or expose raw errors.
                raise ProposalError("official_page_cleanup_failed") from None
