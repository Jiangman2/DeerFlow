import base64
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from deerflow.community.media_generation import tools as media_tools


def _runtime(tmp_path: Path) -> SimpleNamespace:
    outputs = tmp_path / "outputs"
    uploads = tmp_path / "uploads"
    workspace = tmp_path / "workspace"
    for path in (outputs, uploads, workspace):
        path.mkdir()
    return SimpleNamespace(
        state={
            "thread_data": {
                "outputs_path": str(outputs),
                "uploads_path": str(uploads),
                "workspace_path": str(workspace),
            }
        }
    )


def _payload(command) -> dict:
    return json.loads(command.update["messages"][0].content)


@pytest.mark.asyncio
async def test_generate_image_uses_bounded_platform_contract(tmp_path, monkeypatch) -> None:
    image = b"\xff\xd8\xffgenerated\xff\xd9"
    observed: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["url"] = str(request.url)
        observed["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(image).decode("ascii")}]},
        )

    monkeypatch.setenv(media_tools.IMAGE_API_URL_ENV, "http://10.57.2.43:30008/v1/images/generations")
    monkeypatch.setattr(
        media_tools,
        "_async_client",
        lambda *, read_timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    result = await media_tools.generate_image_tool.coroutine(
        runtime=_runtime(tmp_path),
        tool_call_id="image-call",
        prompt="A clean offshore wind platform at sunrise",
        filename="wind-platform",
        size="1024x1024",
        output_format="jpg",
        seed=42,
    )

    assert observed["url"] == "http://10.57.2.43:30008/v1/images/generations"
    assert observed["body"] == {
        "prompt": "A clean offshore wind platform at sunrise",
        "response_format": "b64_json",
        "size": "1024x1024",
        "output_format": "jpg",
        "seed": 42,
        "n": 1,
    }
    payload = _payload(result)
    assert payload["status"] == "completed"
    assert payload["artifact"] == "/mnt/user-data/outputs/wind-platform.jpg"
    assert (tmp_path / "outputs" / "wind-platform.jpg").read_bytes() == image
    assert result.update["artifacts"] == [payload["artifact"]]


@pytest.mark.asyncio
async def test_generate_image_rejects_mismatched_payload(tmp_path, monkeypatch) -> None:
    png = b"\x89PNG\r\n\x1a\ngenerated"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(png).decode("ascii")}]},
        )

    monkeypatch.setenv(media_tools.IMAGE_API_URL_ENV, "http://10.57.2.43:30008/v1/images/generations")
    monkeypatch.setattr(
        media_tools,
        "_async_client",
        lambda *, read_timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    result = await media_tools.generate_image_tool.coroutine(
        runtime=_runtime(tmp_path),
        tool_call_id="image-call",
        prompt="probe",
        output_format="jpg",
    )

    payload = _payload(result)
    assert payload["status"] == "error"
    assert payload["code"] == "invalid_response"
    assert not list((tmp_path / "outputs").iterdir())


@pytest.mark.asyncio
async def test_generate_video_persists_job_and_publishes_mp4(tmp_path, monkeypatch) -> None:
    observed_methods: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed_methods.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(200, json={"id": "job-123", "status": "queued"})
        return httpx.Response(200, json={"id": "job-123", "status": "completed", "progress": 100})

    def fake_download(_url, _headers, outputs_path, output_name):
        payload = b"\x00\x00\x00\x18ftypisomgenerated-video"
        destination = outputs_path / output_name
        destination.write_bytes(payload)
        return destination, len(payload)

    monkeypatch.setenv(media_tools.VIDEO_API_URL_ENV, "http://10.57.2.43:30009/v1/videos")
    monkeypatch.setattr(
        media_tools,
        "_async_client",
        lambda *, read_timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(media_tools, "_download_video_to_output", fake_download)

    runtime = _runtime(tmp_path)
    result = await media_tools.generate_video_tool.coroutine(
        runtime=runtime,
        tool_call_id="video-call",
        prompt="A slow aerial shot above an offshore platform",
        filename="offshore-flight",
        wait_seconds=1,
    )

    assert observed_methods == [("POST", "/v1/videos"), ("GET", "/v1/videos/job-123")]
    payload = _payload(result)
    assert payload["status"] == "completed"
    assert payload["job_id"] == "job-123"
    assert payload["artifact"] == "/mnt/user-data/outputs/offshore-flight.mp4"
    assert result.update["artifacts"] == [payload["artifact"]]

    observed_methods.clear()
    repeated = await media_tools.generate_video_tool.coroutine(
        runtime=runtime,
        tool_call_id="video-call-repeat",
        prompt="A slow aerial shot above an offshore platform",
        filename="offshore-flight",
        wait_seconds=1,
    )
    assert observed_methods == []
    assert _payload(repeated)["artifact"] == payload["artifact"]


@pytest.mark.asyncio
async def test_generate_video_rejects_foreign_job(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(media_tools.VIDEO_API_URL_ENV, "http://10.57.2.43:30009/v1/videos")

    result = await media_tools.generate_video_tool.coroutine(
        runtime=_runtime(tmp_path),
        tool_call_id="video-call",
        job_id="job-from-another-thread",
    )

    payload = _payload(result)
    assert payload["status"] == "error"
    assert payload["code"] == "unknown_job"


@pytest.mark.asyncio
async def test_generate_video_reports_remote_job_loss_without_resubmitting(tmp_path, monkeypatch) -> None:
    observed_methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed_methods.append(request.method)
        if request.method == "POST":
            return httpx.Response(200, json={"id": "job-lost", "status": "queued"})
        return httpx.Response(404, json={"detail": "not found"})

    monkeypatch.setenv(media_tools.VIDEO_API_URL_ENV, "http://10.57.2.43:30009/v1/videos")
    monkeypatch.setattr(
        media_tools,
        "_async_client",
        lambda *, read_timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    runtime = _runtime(tmp_path)
    first = await media_tools.generate_video_tool.coroutine(
        runtime=runtime,
        tool_call_id="video-call",
        prompt="A stable offshore platform shot",
        wait_seconds=0,
    )
    assert _payload(first)["code"] == "video_job_lost"
    assert observed_methods == ["POST", "GET"]

    observed_methods.clear()
    second = await media_tools.generate_video_tool.coroutine(
        runtime=runtime,
        tool_call_id="video-call-repeat",
        prompt="A stable offshore platform shot",
        wait_seconds=0,
    )
    assert _payload(second)["code"] == "video_job_lost"
    assert observed_methods == ["GET"]


def test_configured_tools_are_opt_in(monkeypatch) -> None:
    monkeypatch.delenv(media_tools.IMAGE_API_URL_ENV, raising=False)
    monkeypatch.delenv(media_tools.VIDEO_API_URL_ENV, raising=False)
    assert media_tools.get_configured_media_generation_tools() == []

    monkeypatch.setenv(media_tools.IMAGE_API_URL_ENV, "http://10.57.2.43:30008/v1/images/generations")
    assert [tool.name for tool in media_tools.get_configured_media_generation_tools()] == ["generate_image"]

    monkeypatch.setenv(media_tools.VIDEO_API_URL_ENV, "http://10.57.2.43:30009/v1/videos")
    assert [tool.name for tool in media_tools.get_configured_media_generation_tools()] == [
        "generate_image",
        "generate_video",
    ]
