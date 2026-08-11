import asyncio
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from _router_auth_helpers import call_unwrapped, make_authed_test_app
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import FileResponse

import app.gateway.routers.artifacts as artifacts_router
from app.gateway.internal_auth import INTERNAL_OWNER_USER_ID_HEADER_NAME, INTERNAL_SYSTEM_ROLE
from deerflow.config.paths import make_safe_user_id

ACTIVE_ARTIFACT_CASES = [
    ("poc.html", "<html><body><script>alert('xss')</script></body></html>"),
    ("page.xhtml", '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>hello</body></html>'),
    ("image.svg", '<svg xmlns="http://www.w3.org/2000/svg"><script>alert("xss")</script></svg>'),
]


def _make_request(query_string: bytes = b"") -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": query_string})


class _FakeLivePreviewStream:
    status_code = 200
    headers = {"content-type": "text/html; charset=utf-8"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def aiter_bytes(self):
        yield b'<script src="/static/app.js"></script>'
        yield b'<script>fetch("/api/slides")</script>'


class _FakeLivePreviewClient:
    def __init__(self, *args, **kwargs):
        self.request_args = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def stream(self, *args, **kwargs):
        self.request_args = (args, kwargs)
        return _FakeLivePreviewStream()


def test_live_preview_proxy_rewrites_sandbox_root_urls(monkeypatch) -> None:
    fake_sandbox = SimpleNamespace(
        id="sandbox-abc12345",
        base_url="http://sandbox-abc12345-svc.deer-flow.svc.cluster.local:8080",
    )
    fake_provider = SimpleNamespace(get_for_thread=lambda _thread_id, user_id=None: fake_sandbox)
    monkeypatch.setattr(artifacts_router, "get_sandbox_provider", lambda: fake_provider)
    monkeypatch.setattr(artifacts_router, "_resolve_artifact_owner_user_id", AsyncMock(return_value="owner-1"))
    monkeypatch.setattr(artifacts_router, "_resolve_thread_runtime_profile", AsyncMock(return_value=None))
    monkeypatch.setattr(artifacts_router.httpx, "AsyncClient", _FakeLivePreviewClient)

    response = asyncio.run(
        call_unwrapped(
            artifacts_router.get_live_preview,
            "thread-1",
            5050,
            "",
            _make_request(),
        )
    )

    body = bytes(response.body).decode("utf-8")
    assert response.status_code == 200
    assert "/api/threads/thread-1/live-preview/5050/static/app.js" in body
    assert "/api/threads/thread-1/live-preview/5050/api/slides" in body
    assert response.headers["cache-control"] == "no-store"


def test_live_preview_for_isolated_ppt_runtime_is_forwarded_to_runner(monkeypatch) -> None:
    expected = artifacts_router.Response(content=b"runner-preview", status_code=200)
    forward = AsyncMock(return_value=expected)
    monkeypatch.delenv("DEER_FLOW_RUNTIME_PROFILE", raising=False)
    monkeypatch.setattr(artifacts_router, "_resolve_artifact_owner_user_id", AsyncMock(return_value="owner-1"))
    monkeypatch.setattr(
        artifacts_router,
        "_resolve_thread_runtime_profile",
        AsyncMock(return_value=artifacts_router.PPT_MASTER_RUNTIME_PROFILE),
    )
    monkeypatch.setattr(artifacts_router, "_forward_live_preview_to_runner", forward)
    monkeypatch.setattr(
        artifacts_router,
        "get_sandbox_provider",
        lambda: pytest.fail("the main Gateway must not adopt the Runner Sandbox"),
    )

    request = _make_request()
    response = asyncio.run(
        call_unwrapped(
            artifacts_router.get_live_preview,
            "thread-1",
            5050,
            "",
            request,
        )
    )

    assert response is expected
    forward.assert_awaited_once_with(request, "thread-1")


def test_live_preview_proxy_rejects_unsupported_port(monkeypatch) -> None:
    request = _make_request()
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            call_unwrapped(
                artifacts_router.get_live_preview,
                "thread-1",
                5051,
                "",
                request,
            )
        )
    assert exc_info.value.status_code == 404


def test_get_artifact_reads_utf8_text_file_on_windows_locale(tmp_path, monkeypatch) -> None:
    artifact_path = tmp_path / "note.txt"
    text = "Curly quotes: \u201cutf8\u201d"
    artifact_path.write_text(text, encoding="utf-8")

    original_read_text = Path.read_text

    def read_text_with_gbk_default(self, *args, **kwargs):
        kwargs.setdefault("encoding", "gbk")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text_with_gbk_default)
    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _thread_id, _path, user_id=None: artifact_path)

    request = _make_request()
    response = asyncio.run(call_unwrapped(artifacts_router.get_artifact, "thread-1", "mnt/user-data/outputs/note.txt", request))

    assert bytes(response.body).decode("utf-8") == text
    assert response.media_type == "text/plain"


def test_get_artifact_serves_pdf_as_binary_even_when_sample_has_no_null_bytes(tmp_path, monkeypatch) -> None:
    artifact_path = tmp_path / "preview.pdf"
    payload = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"
    assert b"\x00" not in payload
    artifact_path.write_bytes(payload)

    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _thread_id, _path, user_id=None: artifact_path)

    response = asyncio.run(call_unwrapped(artifacts_router.get_artifact, "thread-1", "mnt/user-data/outputs/preview.pdf", _make_request()))

    assert response.status_code == 200
    assert response.media_type == "application/pdf"
    assert bytes(response.body) == payload
    assert response.headers.get("content-disposition", "").startswith("inline;")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in response.headers["content-security-policy"]


def test_get_artifact_keeps_benign_page_destination_open_action_inline(tmp_path, monkeypatch) -> None:
    artifact_path = tmp_path / "deck.preview.pdf"
    artifact_path.write_bytes(b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R /OpenAction[3 0 R /XYZ null null 0] >>\nendobj\n%%EOF\n")

    monkeypatch.setattr(
        artifacts_router,
        "resolve_thread_virtual_path",
        lambda _thread_id, _path, user_id=None: artifact_path,
    )

    response = asyncio.run(
        call_unwrapped(
            artifacts_router.get_artifact,
            "thread-1",
            "mnt/user-data/outputs/deck.preview.pdf",
            _make_request(),
        )
    )

    assert response.status_code == 200
    assert response.media_type == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in response.headers["content-security-policy"]


def test_get_pdf_artifact_preview_returns_scriptless_page_gallery(tmp_path, monkeypatch) -> None:
    artifact_path = tmp_path / "deck.preview.pdf"
    artifact_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.setattr(
        artifacts_router,
        "resolve_thread_virtual_path",
        lambda _thread_id, _path, user_id=None: artifact_path,
    )
    monkeypatch.setattr(artifacts_router, "_inspect_pdf_preview", lambda _path: 2)
    monkeypatch.setattr(
        artifacts_router,
        "_render_pdf_preview_pages",
        lambda _path, page_count: [b"page-zero", b"page-one"][:page_count],
    )

    response = asyncio.run(
        call_unwrapped(
            artifacts_router.get_pdf_artifact_preview,
            "thread-1",
            "mnt/user-data/outputs/deck.preview.pdf",
            _make_request(),
            page=None,
        )
    )

    body = bytes(response.body).decode("utf-8")
    assert response.status_code == 200
    assert response.media_type == "text/html"
    assert body.count("<img") == 2
    assert "data:image/png;base64,cGFnZS16ZXJv" in body
    assert "data:image/png;base64,cGFnZS1vbmU=" in body
    assert "?page=" not in body
    assert "<script" not in body
    assert "sandbox" in response.headers["content-security-policy"]
    assert "img-src data:" in response.headers["content-security-policy"]


def test_get_pdf_artifact_preview_renders_requested_page_as_png(tmp_path, monkeypatch) -> None:
    artifact_path = tmp_path / "deck.preview.pdf"
    artifact_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.setattr(
        artifacts_router,
        "resolve_thread_virtual_path",
        lambda _thread_id, _path, user_id=None: artifact_path,
    )
    monkeypatch.setattr(artifacts_router, "_render_pdf_preview_page", lambda _path, page: b"\x89PNG\r\npage" + bytes([page]))

    response = asyncio.run(
        call_unwrapped(
            artifacts_router.get_pdf_artifact_preview,
            "thread-1",
            "mnt/user-data/outputs/deck.preview.pdf",
            _make_request(),
            page=3,
        )
    )

    assert response.status_code == 200
    assert response.media_type == "image/png"
    assert bytes(response.body).startswith(b"\x89PNG")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_get_pdf_artifact_preview_rejects_non_companion_pdf(tmp_path, monkeypatch) -> None:
    artifact_path = tmp_path / "report.pdf"
    artifact_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.setattr(
        artifacts_router,
        "resolve_thread_virtual_path",
        lambda _thread_id, _path, user_id=None: artifact_path,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            call_unwrapped(
                artifacts_router.get_pdf_artifact_preview,
                "thread-1",
                "mnt/user-data/outputs/report.pdf",
                _make_request(),
                page=None,
            )
        )

    assert exc_info.value.status_code == 400


def test_get_artifact_keeps_indirect_open_action_as_attachment(tmp_path, monkeypatch) -> None:
    artifact_path = tmp_path / "unsafe.preview.pdf"
    artifact_path.write_bytes(b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog /OpenAction 2 0 R >>\nendobj\n2 0 obj\n<< /S /GoTo /D [3 0 R /Fit] >>\nendobj\n%%EOF\n")

    monkeypatch.setattr(
        artifacts_router,
        "resolve_thread_virtual_path",
        lambda _thread_id, _path, user_id=None: artifact_path,
    )

    response = asyncio.run(
        call_unwrapped(
            artifacts_router.get_artifact,
            "thread-1",
            "mnt/user-data/outputs/unsafe.preview.pdf",
            _make_request(),
        )
    )

    assert isinstance(response, FileResponse)
    assert response.media_type == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment;")


def test_get_artifact_forces_uploaded_pdf_to_download(tmp_path, monkeypatch) -> None:
    artifact_path = tmp_path / "uploaded.pdf"
    artifact_path.write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n")

    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _thread_id, _path, user_id=None: artifact_path)

    response = asyncio.run(
        call_unwrapped(
            artifacts_router.get_artifact,
            "thread-1",
            "mnt/user-data/uploads/uploaded.pdf",
            _make_request(),
        )
    )

    assert isinstance(response, FileResponse)
    assert response.media_type == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-store"


def test_get_artifact_keeps_safe_uploaded_raster_image_inline(tmp_path, monkeypatch) -> None:
    artifact_path = tmp_path / "image.png"
    payload = b"\x89PNG\r\n\x1a\nprobe"
    artifact_path.write_bytes(payload)

    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _thread_id, _path, user_id=None: artifact_path)

    response = asyncio.run(
        call_unwrapped(
            artifacts_router.get_artifact,
            "thread-1",
            "mnt/user-data/uploads/image.png",
            _make_request(),
        )
    )

    assert response.media_type == "image/png"
    assert bytes(response.body) == payload
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_get_artifact_forces_active_pdf_output_to_download(tmp_path, monkeypatch) -> None:
    artifact_path = tmp_path / "active.pdf"
    artifact_path.write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /OpenAction 2 0 R >>\nendobj\n2 0 obj\n<< /S /JavaScript /JS (app.alert('probe')) >>\nendobj\n%%EOF\n")

    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _thread_id, _path, user_id=None: artifact_path)

    response = asyncio.run(
        call_unwrapped(
            artifacts_router.get_artifact,
            "thread-1",
            "mnt/user-data/outputs/active.pdf",
            _make_request(),
        )
    )

    assert isinstance(response, FileResponse)
    assert response.media_type == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(("filename", "content"), ACTIVE_ARTIFACT_CASES)
def test_get_artifact_forces_download_for_active_content(tmp_path, monkeypatch, filename: str, content: str) -> None:
    artifact_path = tmp_path / filename
    artifact_path.write_text(content, encoding="utf-8")

    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _thread_id, _path, user_id=None: artifact_path)

    response = asyncio.run(call_unwrapped(artifacts_router.get_artifact, "thread-1", f"mnt/user-data/outputs/{filename}", _make_request()))

    assert isinstance(response, FileResponse)
    assert response.media_type == "application/octet-stream"
    assert response.headers.get("content-disposition", "").startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(("filename", "content"), ACTIVE_ARTIFACT_CASES)
def test_get_artifact_forces_download_for_active_content_in_skill_archive(tmp_path, monkeypatch, filename: str, content: str) -> None:
    skill_path = tmp_path / "sample.skill"
    with zipfile.ZipFile(skill_path, "w") as zip_ref:
        zip_ref.writestr(filename, content)

    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _thread_id, _path, user_id=None: skill_path)

    response = asyncio.run(call_unwrapped(artifacts_router.get_artifact, "thread-1", f"mnt/user-data/outputs/sample.skill/{filename}", _make_request()))

    assert response.headers.get("content-disposition", "").startswith("attachment;")
    assert bytes(response.body) == content.encode("utf-8")


def test_get_artifact_download_false_does_not_force_attachment(tmp_path, monkeypatch) -> None:
    artifact_path = tmp_path / "note.txt"
    artifact_path.write_text("hello", encoding="utf-8")

    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _thread_id, _path, user_id=None: artifact_path)

    app = make_authed_test_app()
    app.include_router(artifacts_router.router)

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/artifacts/mnt/user-data/outputs/note.txt?download=false")

    assert response.status_code == 200
    assert response.text == "hello"
    assert "content-disposition" not in response.headers


def test_get_artifact_supports_head_for_preview_discovery(tmp_path, monkeypatch) -> None:
    artifact_path = tmp_path / "deck.preview.pdf"
    artifact_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _thread_id, _path, user_id=None: artifact_path)

    app = make_authed_test_app()
    app.include_router(artifacts_router.router)

    with TestClient(app) as client:
        response = client.head("/api/threads/thread-1/artifacts/mnt/user-data/outputs/deck.preview.pdf")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content == b""


def test_get_artifact_download_true_forces_attachment_for_skill_archive(tmp_path, monkeypatch) -> None:
    skill_path = tmp_path / "sample.skill"
    with zipfile.ZipFile(skill_path, "w") as zip_ref:
        zip_ref.writestr("notes.txt", "hello")

    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _thread_id, _path, user_id=None: skill_path)

    app = make_authed_test_app()
    app.include_router(artifacts_router.router)

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/artifacts/mnt/user-data/outputs/sample.skill/notes.txt?download=true")

    assert response.status_code == 200
    assert response.text == "hello"
    assert response.headers.get("content-disposition", "").startswith("attachment;")


def _make_internal_request(owner: str | None, *, system_role: str = INTERNAL_SYSTEM_ROLE) -> Request:
    """A request as it arrives from a trusted internal caller.

    ``system_role`` is stamped onto ``request.state.user`` the way
    ``AuthMiddleware`` does after validating the internal token. When *owner*
    is given it is carried in the owner-user-id header.
    """
    headers: list[tuple[bytes, bytes]] = []
    if owner is not None:
        headers.append((INTERNAL_OWNER_USER_ID_HEADER_NAME.lower().encode(), owner.encode()))
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": headers, "query_string": b""})
    request.state.user = SimpleNamespace(id="default", system_role=system_role)
    return request


def _capture_resolved_user_id(monkeypatch, tmp_path) -> dict:
    """Patch resolve_thread_virtual_path to record the user_id it is called with."""
    artifact_path = tmp_path / "index.html"
    artifact_path.write_text("<html>", encoding="utf-8")
    seen: dict = {}

    def fake_resolve(_thread_id, _path, user_id=None):
        seen["user_id"] = user_id
        return artifact_path

    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", fake_resolve)
    return seen


def test_get_artifact_scopes_to_trusted_owner_header(tmp_path, monkeypatch) -> None:
    # An internal caller acting for an owner must resolve the artifact under
    # that owner's storage, not the synthetic internal user.
    seen = _capture_resolved_user_id(monkeypatch, tmp_path)
    request = _make_internal_request("owner-123")

    asyncio.run(call_unwrapped(artifacts_router.get_artifact, "thread-1", "mnt/user-data/outputs/index.html", request))

    assert seen["user_id"] == "owner-123"


def test_get_artifact_normalizes_raw_owner_id_from_trusted_header(tmp_path, monkeypatch) -> None:
    # The trusted header carries the raw platform owner id (channel workers
    # send it unsanitized; see ChannelManager._owner_headers), while run files
    # live under the make_safe_user_id bucket — so a raw id with chars outside
    # [A-Za-z0-9_-] must resolve to the normalized bucket, not the raw one.
    seen = _capture_resolved_user_id(monkeypatch, tmp_path)
    raw_owner = "ou_7d8a.6e6d@example:id"
    request = _make_internal_request(raw_owner)

    asyncio.run(call_unwrapped(artifacts_router.get_artifact, "thread-1", "mnt/user-data/outputs/index.html", request))

    assert seen["user_id"] == make_safe_user_id(raw_owner)
    assert seen["user_id"] != raw_owner


def test_get_artifact_without_owner_header_falls_back_to_effective_user(tmp_path, monkeypatch) -> None:
    # No owner header → no override; resolution falls back to the effective user
    # (user_id=None lets resolve_thread_virtual_path apply its default).
    seen = _capture_resolved_user_id(monkeypatch, tmp_path)
    request = _make_internal_request(None)

    asyncio.run(call_unwrapped(artifacts_router.get_artifact, "thread-1", "mnt/user-data/outputs/index.html", request))

    assert seen["user_id"] is None


def test_get_artifact_ignores_owner_header_for_non_internal_caller(tmp_path, monkeypatch) -> None:
    # The owner header is only trusted for internal callers; a normal user
    # carrying it must not be able to read another user's storage.
    seen = _capture_resolved_user_id(monkeypatch, tmp_path)
    request = _make_internal_request("owner-123", system_role="user")

    asyncio.run(call_unwrapped(artifacts_router.get_artifact, "thread-1", "mnt/user-data/outputs/index.html", request))

    assert seen["user_id"] is None


def test_get_artifact_resolves_browser_request_from_thread_owner_metadata(tmp_path, monkeypatch) -> None:
    seen = _capture_resolved_user_id(monkeypatch, tmp_path)
    thread_store = SimpleNamespace(
        get=AsyncMock(
            return_value={
                "thread_id": "thread-1",
                "user_id": "thread.owner@example.com",
            }
        )
    )
    app = SimpleNamespace(state=SimpleNamespace(thread_store=thread_store))
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "query_string": b"",
            "app": app,
        }
    )
    request.state.user = SimpleNamespace(id="reviewing-admin", system_role="admin")

    asyncio.run(
        call_unwrapped(
            artifacts_router.get_artifact,
            "thread-1",
            "mnt/user-data/outputs/index.html",
            request,
        )
    )

    thread_store.get.assert_awaited_once_with("thread-1", user_id=None)
    assert seen["user_id"] == make_safe_user_id("thread.owner@example.com")


def test_thread_metadata_owner_takes_precedence_over_internal_header(tmp_path, monkeypatch) -> None:
    seen = _capture_resolved_user_id(monkeypatch, tmp_path)
    thread_store = SimpleNamespace(get=AsyncMock(return_value={"thread_id": "thread-1", "user_id": "canonical-owner"}))
    app = SimpleNamespace(state=SimpleNamespace(thread_store=thread_store))
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [
                (
                    INTERNAL_OWNER_USER_ID_HEADER_NAME.lower().encode(),
                    b"legacy-header-owner",
                )
            ],
            "query_string": b"",
            "app": app,
        }
    )
    request.state.user = SimpleNamespace(id="default", system_role=INTERNAL_SYSTEM_ROLE)

    asyncio.run(
        call_unwrapped(
            artifacts_router.get_artifact,
            "thread-1",
            "mnt/user-data/outputs/index.html",
            request,
        )
    )

    assert seen["user_id"] == "canonical-owner"


def test_skill_archive_preview_rejects_oversized_member_before_decompression(tmp_path) -> None:
    skill_path = tmp_path / "sample.skill"
    payload = b"A" * (artifacts_router.MAX_SKILL_ARCHIVE_MEMBER_BYTES + 1)
    with zipfile.ZipFile(skill_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zip_ref:
        zip_ref.writestr("SKILL.md", payload)

    assert skill_path.stat().st_size < artifacts_router.MAX_SKILL_ARCHIVE_MEMBER_BYTES

    with pytest.raises(HTTPException) as exc_info:
        artifacts_router._extract_file_from_skill_archive(skill_path, "SKILL.md")

    assert exc_info.value.status_code == 413
