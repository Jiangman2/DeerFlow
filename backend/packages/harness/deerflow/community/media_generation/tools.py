"""Bounded image and video generation through operator-owned internal APIs."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import os
import re
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Annotated, Any
from urllib.parse import quote, urlsplit

import httpx
from langchain.tools import BaseTool, InjectedToolCallId, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from deerflow.community.url_safety import resolve_host_addresses, validate_public_http_url
from deerflow.config.paths import VIRTUAL_PATH_PREFIX
from deerflow.tools.types import Runtime

logger = logging.getLogger(__name__)

IMAGE_API_URL_ENV = "DEERFLOW_IMAGE_GENERATION_API_URL"
VIDEO_API_URL_ENV = "DEERFLOW_VIDEO_GENERATION_API_URL"
MEDIA_API_KEY_ENV = "DEERFLOW_MEDIA_GENERATION_API_KEY"
VIDEO_DOWNLOAD_HOSTS_ENV = "DEERFLOW_VIDEO_GENERATION_DOWNLOAD_HOSTS"

_OUTPUTS_VIRTUAL_PREFIX = f"{VIRTUAL_PATH_PREFIX}/outputs"
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_DIMENSIONS_RE = re.compile(r"^(?P<width>[1-9][0-9]{1,4})x(?P<height>[1-9][0-9]{1,4})$")
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,256}$")
_IMAGE_FORMATS = {"jpg": "jpg", "jpeg": "jpg", "png": "png", "webp": "webp"}
_IMAGE_MIME = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
_MAX_PROMPT_CHARS = 12_000
_MAX_NEGATIVE_PROMPT_CHARS = 6_000
_MAX_IMAGE_BYTES = 32 * 1024 * 1024
_MAX_IMAGE_RESPONSE_BYTES = 48 * 1024 * 1024
_MAX_JSON_RESPONSE_BYTES = 1024 * 1024
_MAX_REFERENCE_IMAGE_BYTES = 20 * 1024 * 1024
_MAX_VIDEO_BYTES = 1024 * 1024 * 1024
_VIDEO_JOB_DIR = ".media-generation-jobs"
_PENDING_VIDEO_STATUSES = frozenset({"queued", "pending", "submitted", "processing", "running", "in_progress"})
_FAILED_VIDEO_STATUSES = frozenset({"failed", "error", "canceled", "cancelled"})
_COMPLETE_VIDEO_STATUSES = frozenset({"completed", "complete", "succeeded", "success"})


class MediaGenerationError(RuntimeError):
    """A safe, user-visible media generation error."""

    def __init__(self, message: str, *, code: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _bounded_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _configured_endpoint(env_name: str) -> str | None:
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return None
    parsed = urlsplit(raw)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise MediaGenerationError(
            f"{env_name} is not a valid operator-owned HTTP endpoint",
            code="invalid_configuration",
        )
    return raw.rstrip("/")


def _api_headers(*, request_key: str | None = None) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    api_key = os.getenv(MEDIA_API_KEY_ENV, "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if request_key:
        headers["Idempotency-Key"] = request_key
    return headers


def _async_client(*, read_timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=read_timeout, write=60.0, pool=10.0),
        trust_env=False,
        follow_redirects=False,
    )


def _raise_for_status(response: httpx.Response, *, operation: str) -> None:
    if not response.is_error:
        return
    raise MediaGenerationError(
        f"{operation} was rejected with HTTP {response.status_code}",
        code=f"http_{response.status_code}",
        retryable=response.status_code == 429 or response.status_code >= 500,
    )


async def _read_limited_response(response: httpx.Response, maximum: int) -> bytes:
    declared = response.headers.get("content-length", "").strip()
    if declared:
        try:
            if int(declared) > maximum:
                raise MediaGenerationError("Media API response exceeded the configured size limit", code="response_too_large")
        except ValueError:
            pass
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > maximum:
            raise MediaGenerationError("Media API response exceeded the configured size limit", code="response_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def _response_json(raw: bytes, *, operation: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MediaGenerationError(f"{operation} returned invalid JSON", code="invalid_response") from exc
    if not isinstance(payload, dict):
        raise MediaGenerationError(f"{operation} returned an invalid response envelope", code="invalid_response")
    return payload


def _outputs_path(runtime: Runtime) -> Path:
    state = getattr(runtime, "state", None)
    if not isinstance(state, dict):
        raise MediaGenerationError("Thread runtime state is not available", code="missing_runtime")
    thread_data = state.get("thread_data")
    if not isinstance(thread_data, dict) or not thread_data.get("outputs_path"):
        raise MediaGenerationError("Thread outputs path is not available", code="missing_runtime")
    return Path(str(thread_data["outputs_path"])).resolve()


def _safe_output_name(filename: str | None, *, prefix: str, extension: str) -> str:
    if filename:
        stem = Path(filename).stem
    else:
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        stem = f"{prefix}-{timestamp}"
    safe_stem = _SAFE_FILENAME_RE.sub("_", stem).strip("._-") or prefix
    return f"{safe_stem[:96]}.{extension}"


def _dedupe_output_path(outputs_path: Path, output_name: str) -> Path:
    candidate = outputs_path / output_name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for index in range(1, 1001):
        alternative = outputs_path / f"{stem}-{index}{suffix}"
        if not alternative.exists():
            return alternative
    return outputs_path / f"{stem}-{uuid.uuid4().hex}{suffix}"


def _atomic_write_bytes(outputs_path: Path, output_name: str, content: bytes) -> Path:
    outputs_path.mkdir(parents=True, exist_ok=True)
    destination = _dedupe_output_path(outputs_path, output_name)
    fd, temporary_name = tempfile.mkstemp(prefix=".media-generation-", dir=outputs_path)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, destination)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return destination


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _dimensions(value: str, *, multiple: int, minimum: int, maximum: int, label: str) -> str:
    match = _DIMENSIONS_RE.fullmatch(value.strip())
    if match is None:
        raise MediaGenerationError(f"{label} must use WIDTHxHEIGHT", code="invalid_parameters")
    width = int(match.group("width"))
    height = int(match.group("height"))
    if not (minimum <= width <= maximum and minimum <= height <= maximum):
        raise MediaGenerationError(
            f"{label} dimensions must be between {minimum} and {maximum} pixels",
            code="invalid_parameters",
        )
    if width % multiple or height % multiple:
        raise MediaGenerationError(f"{label} dimensions must be multiples of {multiple}", code="invalid_parameters")
    return f"{width}x{height}"


def _validate_prompt(prompt: str) -> str:
    normalized = prompt.strip()
    if not normalized:
        raise MediaGenerationError("prompt is required", code="invalid_parameters")
    if len(normalized) > _MAX_PROMPT_CHARS:
        raise MediaGenerationError(f"prompt exceeds {_MAX_PROMPT_CHARS} characters", code="invalid_parameters")
    return normalized


def _validate_negative_prompt(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > _MAX_NEGATIVE_PROMPT_CHARS:
        raise MediaGenerationError(
            f"negative_prompt exceeds {_MAX_NEGATIVE_PROMPT_CHARS} characters",
            code="invalid_parameters",
        )
    return normalized


def _image_format(value: str) -> str:
    normalized = value.strip().lower()
    output_format = _IMAGE_FORMATS.get(normalized)
    if output_format is None:
        raise MediaGenerationError("output_format must be png, jpg, jpeg, or webp", code="invalid_parameters")
    return output_format


def _detected_image_format(content: bytes) -> str | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if content.startswith(b"\xff\xd8\xff") and content.endswith(b"\xff\xd9"):
        return "jpg"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp"
    return None


def _decode_generated_image(payload: dict[str, Any], expected_format: str) -> bytes:
    data = payload.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise MediaGenerationError("Image API returned no single b64_json result", code="invalid_response")
    encoded = data[0].get("b64_json")
    if not isinstance(encoded, str) or not encoded:
        raise MediaGenerationError("Image API returned no b64_json payload", code="invalid_response")
    if len(encoded) > ((_MAX_IMAGE_BYTES + 2) // 3) * 4 + 16:
        raise MediaGenerationError("Generated image exceeded the configured size limit", code="response_too_large")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MediaGenerationError("Image API returned invalid base64 data", code="invalid_response") from exc
    if not content or len(content) > _MAX_IMAGE_BYTES:
        raise MediaGenerationError("Generated image exceeded the configured size limit", code="response_too_large")
    detected = _detected_image_format(content)
    if detected != expected_format:
        raise MediaGenerationError(
            f"Image API returned {detected or 'unknown'} bytes for requested {expected_format}",
            code="invalid_response",
        )
    return content


def _success_command(
    tool_name: str,
    tool_call_id: str,
    payload: dict[str, Any],
    artifact: str | None = None,
) -> Command:
    message_kwargs: dict[str, Any] = {
        "tool_call_id": tool_call_id,
        "name": tool_name,
    }
    if artifact:
        message_kwargs["artifact"] = {"presented_files": [artifact]}
    update: dict[str, Any] = {
        "messages": [
            ToolMessage(
                json.dumps(payload, ensure_ascii=False),
                **message_kwargs,
            )
        ]
    }
    if artifact:
        update["artifacts"] = [artifact]
    return Command(update=update)


def _error_command(tool_name: str, tool_call_id: str, error: Exception) -> Command:
    if isinstance(error, MediaGenerationError):
        payload = {"status": "error", "code": error.code, "message": str(error), "retryable": error.retryable}
    elif isinstance(error, httpx.TimeoutException):
        payload = {"status": "error", "code": "timeout", "message": "Media generation service timed out", "retryable": True}
    elif isinstance(error, httpx.HTTPError):
        payload = {"status": "error", "code": "transport_error", "message": "Media generation service could not be reached", "retryable": True}
    else:
        logger.exception("Unexpected %s failure", tool_name)
        payload = {"status": "error", "code": "internal_error", "message": "Media generation failed unexpectedly", "retryable": False}
    return Command(
        update={
            "messages": [
                ToolMessage(
                    json.dumps(payload, ensure_ascii=False),
                    tool_call_id=tool_call_id,
                    name=tool_name,
                    status="error",
                )
            ]
        }
    )


@tool("generate_image", parse_docstring=True)
async def generate_image_tool(
    runtime: Runtime,
    tool_call_id: Annotated[str, InjectedToolCallId],
    prompt: str,
    filename: str | None = None,
    size: str = "1024x1024",
    output_format: str = "jpg",
    seed: int = 1024,
    num_inference_steps: int | None = None,
    negative_prompt: str | None = None,
) -> Command:
    """Generate one image with the platform-managed model and publish it.

    Use this tool for text-to-image requests. It returns a ready artifact; do
    not call bash, provider SDKs, or present_files for the same image.

    Args:
        prompt: Complete visual prompt for the image.
        filename: Optional output filename; directories and extensions are ignored.
        size: Image size as WIDTHxHEIGHT. Both dimensions must be multiples of 32.
        output_format: png, jpg, jpeg, or webp.
        seed: Reproducibility seed from 0 through 2147483647.
        num_inference_steps: Optional diffusion-step count from 1 through 200.
        negative_prompt: Optional description of visual properties to avoid.
    """

    try:
        endpoint = _configured_endpoint(IMAGE_API_URL_ENV)
        if endpoint is None:
            raise MediaGenerationError("Image generation is not configured", code="not_configured")
        prompt = _validate_prompt(prompt)
        size = _dimensions(size, multiple=32, minimum=256, maximum=4096, label="image size")
        output_format = _image_format(output_format)
        if isinstance(seed, bool) or not 0 <= seed <= 2_147_483_647:
            raise MediaGenerationError("seed must be between 0 and 2147483647", code="invalid_parameters")
        if num_inference_steps is not None and (isinstance(num_inference_steps, bool) or not 1 <= num_inference_steps <= 200):
            raise MediaGenerationError("num_inference_steps must be between 1 and 200", code="invalid_parameters")
        negative_prompt = _validate_negative_prompt(negative_prompt)

        body: dict[str, Any] = {
            "prompt": prompt,
            "response_format": "b64_json",
            "size": size,
            "output_format": output_format,
            "seed": seed,
            "n": 1,
        }
        if num_inference_steps is not None:
            body["num_inference_steps"] = num_inference_steps
        if negative_prompt is not None:
            body["negative_prompt"] = negative_prompt

        timeout = float(_bounded_int_env("DEERFLOW_IMAGE_GENERATION_TIMEOUT_SECONDS", 600, 30, 1800))
        async with _async_client(read_timeout=timeout) as client:
            async with client.stream("POST", endpoint, headers=_api_headers(), json=body) as response:
                _raise_for_status(response, operation="Image generation")
                raw = await _read_limited_response(response, _MAX_IMAGE_RESPONSE_BYTES)
        content = _decode_generated_image(_response_json(raw, operation="Image generation"), output_format)
        outputs_path = _outputs_path(runtime)
        output_name = _safe_output_name(filename, prefix="generated-image", extension=output_format)
        destination = await asyncio.to_thread(_atomic_write_bytes, outputs_path, output_name, content)
        virtual_path = f"{_OUTPUTS_VIRTUAL_PREFIX}/{destination.name}"
        return _success_command(
            "generate_image",
            tool_call_id,
            {
                "status": "completed",
                "artifact": virtual_path,
                "mime_type": _IMAGE_MIME[output_format],
                "bytes": len(content),
                "size": size,
                "seed": seed,
            },
            virtual_path,
        )
    except Exception as exc:
        return _error_command("generate_image", tool_call_id, exc)


def _bounded_number(value: int | float | None, *, name: str, minimum: float, maximum: float) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not minimum <= value <= maximum:
        raise MediaGenerationError(f"{name} must be between {minimum:g} and {maximum:g}", code="invalid_parameters")
    return value


def _resolve_reference_image(runtime: Runtime, virtual_path: str) -> tuple[str, bytes, str]:
    normalized = PurePosixPath(virtual_path)
    if not normalized.is_absolute() or ".." in normalized.parts:
        raise MediaGenerationError("reference_image must be an absolute current-thread virtual path", code="invalid_parameters")
    prefixes = {
        f"{VIRTUAL_PATH_PREFIX}/uploads/": "uploads_path",
        f"{VIRTUAL_PATH_PREFIX}/workspace/": "workspace_path",
        f"{VIRTUAL_PATH_PREFIX}/outputs/": "outputs_path",
    }
    selected = next(((prefix, key) for prefix, key in prefixes.items() if virtual_path.startswith(prefix)), None)
    if selected is None:
        raise MediaGenerationError("reference_image must be under uploads, workspace, or outputs", code="invalid_parameters")
    state = getattr(runtime, "state", None)
    thread_data = state.get("thread_data") if isinstance(state, dict) else None
    if not isinstance(thread_data, dict) or not thread_data.get(selected[1]):
        raise MediaGenerationError("Thread file paths are not available", code="missing_runtime")
    root = Path(str(thread_data[selected[1]])).resolve()
    relative = PurePosixPath(virtual_path.removeprefix(selected[0]))
    actual = (root / Path(*relative.parts)).resolve()
    try:
        actual.relative_to(root)
    except ValueError as exc:
        raise MediaGenerationError("reference_image escapes the current thread", code="invalid_parameters") from exc
    if not actual.is_file():
        raise MediaGenerationError("reference_image does not exist", code="file_not_found")
    if actual.stat().st_size > _MAX_REFERENCE_IMAGE_BYTES:
        raise MediaGenerationError("reference_image exceeds 20 MiB", code="file_too_large")
    content = actual.read_bytes()
    image_format = _detected_image_format(content)
    if image_format is None:
        raise MediaGenerationError("reference_image must be PNG, JPEG, or WebP", code="invalid_file")
    return actual.name, content, _IMAGE_MIME[image_format]


def _video_request_key(body: dict[str, Any], output_name: str) -> str:
    canonical = json.dumps({"body": body, "output": output_name}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _video_request_state_path(outputs_path: Path, request_key: str) -> Path:
    return outputs_path / _VIDEO_JOB_DIR / f"request-{request_key}.json"


def _video_job_state_path(outputs_path: Path, job_id: str) -> Path:
    digest = hashlib.sha256(job_id.encode("utf-8")).hexdigest()
    return outputs_path / _VIDEO_JOB_DIR / f"job-{digest}.json"


def _load_json_state(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MediaGenerationError("Saved video job state is invalid", code="invalid_job_state") from exc
    if not isinstance(value, dict) or value.get("version") != 1:
        raise MediaGenerationError("Saved video job state is invalid", code="invalid_job_state")
    return value


def _existing_video_artifact(outputs_path: Path, state: dict[str, Any]) -> tuple[str, int] | None:
    artifact = state.get("artifact")
    if state.get("status") != "completed" or not isinstance(artifact, str):
        return None
    prefix = f"{_OUTPUTS_VIRTUAL_PREFIX}/"
    if not artifact.startswith(prefix):
        return None
    name = artifact.removeprefix(prefix)
    if not name or PurePosixPath(name).name != name:
        return None
    candidate = (outputs_path / name).resolve()
    try:
        candidate.relative_to(outputs_path)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return artifact, candidate.stat().st_size


def _video_status(payload: dict[str, Any]) -> str:
    status = payload.get("status")
    if not isinstance(status, str) or not status.strip():
        raise MediaGenerationError("Video API returned no status", code="invalid_response")
    return status.strip().lower()


def _video_job_id(payload: dict[str, Any]) -> str:
    value = payload.get("id") or payload.get("video_id")
    if not isinstance(value, str) or not _JOB_ID_RE.fullmatch(value):
        raise MediaGenerationError("Video API returned an invalid job ID", code="invalid_response")
    return value


def _video_error_message(payload: dict[str, Any]) -> str:
    error = payload.get("error") or payload.get("message") or payload.get("detail")
    if isinstance(error, dict):
        error = error.get("message") or error.get("detail") or error.get("code")
    return str(error or "Video generation failed")[:500]


def _allowed_video_download_url(url: str, api_endpoint: str) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return False
    allowed = {urlsplit(api_endpoint).hostname or ""}
    allowed.update(host.strip().lower() for host in os.getenv(VIDEO_DOWNLOAD_HOSTS_ENV, "").split(",") if host.strip())
    return parsed.hostname.lower() in allowed


def _download_video_to_output(
    url: str,
    headers: dict[str, str],
    outputs_path: Path,
    output_name: str,
) -> tuple[Path, int]:
    outputs_path.mkdir(parents=True, exist_ok=True)
    destination = _dedupe_output_path(outputs_path, output_name)
    fd, temporary_name = tempfile.mkstemp(prefix=".media-generation-video-", dir=outputs_path)
    temporary = Path(temporary_name)
    total = 0
    prefix = b""
    try:
        with os.fdopen(fd, "wb") as stream, httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0),
            trust_env=False,
            follow_redirects=False,
        ) as client:
            with client.stream("GET", url, headers=headers) as response:
                _raise_for_status(response, operation="Video download")
                declared = response.headers.get("content-length", "").strip()
                if declared:
                    try:
                        if int(declared) > _MAX_VIDEO_BYTES:
                            raise MediaGenerationError("Generated video exceeded 1 GiB", code="response_too_large")
                    except ValueError:
                        pass
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > _MAX_VIDEO_BYTES:
                        raise MediaGenerationError("Generated video exceeded 1 GiB", code="response_too_large")
                    if len(prefix) < 16:
                        prefix += chunk[: 16 - len(prefix)]
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
        if total < 12 or prefix[4:8] != b"ftyp":
            raise MediaGenerationError("Video API returned a non-MP4 payload", code="invalid_response")
        os.replace(temporary, destination)
        return destination, total
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


async def _video_status_request(client: httpx.AsyncClient, endpoint: str, job_id: str) -> dict[str, Any]:
    url = f"{endpoint}/{quote(job_id, safe='')}"
    async with client.stream("GET", url, headers=_api_headers()) as response:
        if response.status_code == 404:
            raise MediaGenerationError(
                "The video service no longer has this job; do not resubmit it automatically",
                code="video_job_lost",
            )
        _raise_for_status(response, operation="Video status")
        return _response_json(await _read_limited_response(response, _MAX_JSON_RESPONSE_BYTES), operation="Video status")


async def _complete_video_job(
    *,
    endpoint: str,
    outputs_path: Path,
    output_name: str,
    job_id: str,
    payload: dict[str, Any],
) -> tuple[str, int]:
    candidate_url = payload.get("url") or payload.get("download_url")
    download_url = f"{endpoint}/{quote(job_id, safe='')}/content"
    if isinstance(candidate_url, str) and candidate_url.strip():
        candidate_url = candidate_url.strip()
        if _allowed_video_download_url(candidate_url, endpoint):
            download_url = candidate_url
        else:
            logger.warning("Ignoring non-allowlisted video download URL for job %s", job_id)
    destination, total = await asyncio.to_thread(
        _download_video_to_output,
        download_url,
        _api_headers(),
        outputs_path,
        output_name,
    )
    return f"{_OUTPUTS_VIRTUAL_PREFIX}/{destination.name}", total


async def _wait_for_video(
    *,
    endpoint: str,
    outputs_path: Path,
    state: dict[str, Any],
    wait_seconds: int,
) -> tuple[dict[str, Any], str | None, int | None]:
    job_id = str(state["job_id"])
    deadline = time.monotonic() + wait_seconds
    poll_interval = _bounded_int_env("DEERFLOW_VIDEO_GENERATION_POLL_SECONDS", 3, 1, 5)
    last_payload: dict[str, Any] = {"id": job_id, "status": state.get("status") or "queued"}
    async with _async_client(read_timeout=30.0) as client:
        while True:
            last_payload = await _video_status_request(client, endpoint, job_id)
            response_job_id = _video_job_id(last_payload)
            if response_job_id != job_id:
                raise MediaGenerationError("Video API returned a mismatched job ID", code="invalid_response")
            status = _video_status(last_payload)
            state.update({"status": status, "updated_at": int(time.time())})
            progress = last_payload.get("progress")
            if isinstance(progress, (int, float, str)) and not isinstance(progress, bool):
                state["progress"] = progress
            await asyncio.to_thread(_atomic_write_json, _video_job_state_path(outputs_path, job_id), state)
            if status in _COMPLETE_VIDEO_STATUSES:
                artifact, total = await _complete_video_job(
                    endpoint=endpoint,
                    outputs_path=outputs_path,
                    output_name=str(state["output_name"]),
                    job_id=job_id,
                    payload=last_payload,
                )
                state.update({"status": "completed", "artifact": artifact, "bytes": total, "completed_at": int(time.time())})
                await asyncio.to_thread(_atomic_write_json, _video_job_state_path(outputs_path, job_id), state)
                return state, artifact, total
            if status in _FAILED_VIDEO_STATUSES:
                state.update({"error": _video_error_message(last_payload), "failed_at": int(time.time())})
                await asyncio.to_thread(_atomic_write_json, _video_job_state_path(outputs_path, job_id), state)
                raise MediaGenerationError(state["error"], code="video_generation_failed")
            if status not in _PENDING_VIDEO_STATUSES:
                raise MediaGenerationError(f"Video API returned unknown status {status!r}", code="invalid_response")
            if time.monotonic() >= deadline:
                return state, None, None
            await asyncio.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))


@tool("generate_video", parse_docstring=True)
async def generate_video_tool(
    runtime: Runtime,
    tool_call_id: Annotated[str, InjectedToolCallId],
    prompt: str = "",
    job_id: str | None = None,
    filename: str | None = None,
    size: str = "832x480",
    fps: int = 24,
    num_frames: int | None = 81,
    seconds: int | None = None,
    seed: int = 1024,
    num_inference_steps: int | None = None,
    guidance_scale: float | None = None,
    negative_prompt: str | None = None,
    reference_image: str | None = None,
    reference_url: str | None = None,
    wait_seconds: int = 30,
) -> Command:
    """Submit or resume one platform-managed video-generation job.

    On the first call, provide prompt and optional generation settings. If the
    result is pending, call this tool again with only its job_id (and optionally
    wait_seconds). A completed call publishes the MP4 artifact automatically.

    Args:
        prompt: Complete text prompt. Required only when submitting a new job.
        job_id: Existing job ID returned by an earlier call; never invent one.
        filename: Optional MP4 filename for a new job.
        size: Video size as WIDTHxHEIGHT, with dimensions divisible by 16.
        fps: Frames per second from 1 through 60.
        num_frames: Preferred frame count from 1 through 1000; omit when using seconds.
        seconds: Optional duration from 1 through 60; do not combine with num_frames.
        seed: Reproducibility seed from 0 through 2147483647.
        num_inference_steps: Optional inference-step count from 1 through 200.
        guidance_scale: Optional guidance scale from 0 through 30.
        negative_prompt: Optional description of properties to avoid.
        reference_image: Optional current-thread PNG/JPEG/WebP virtual path for image-to-video.
        reference_url: Optional public HTTP(S) reference image URL; mutually exclusive with reference_image.
        wait_seconds: Seconds to wait in this call before returning pending, from 0 through 60.
    """

    try:
        endpoint = _configured_endpoint(VIDEO_API_URL_ENV)
        if endpoint is None:
            raise MediaGenerationError("Video generation is not configured", code="not_configured")
        outputs_path = _outputs_path(runtime)
        wait_seconds = int(_bounded_number(wait_seconds, name="wait_seconds", minimum=0, maximum=60) or 0)

        if job_id is not None:
            job_id = job_id.strip()
            if not _JOB_ID_RE.fullmatch(job_id):
                raise MediaGenerationError("job_id is invalid", code="invalid_parameters")
            state = await asyncio.to_thread(_load_json_state, _video_job_state_path(outputs_path, job_id))
            if state is None or state.get("job_id") != job_id:
                raise MediaGenerationError("job_id is not owned by the current thread", code="unknown_job")
            existing_artifact = await asyncio.to_thread(_existing_video_artifact, outputs_path, state)
            if existing_artifact is not None:
                artifact, total = existing_artifact
                return _success_command(
                    "generate_video",
                    tool_call_id,
                    {"status": "completed", "job_id": job_id, "artifact": artifact, "bytes": total},
                    artifact,
                )
        else:
            prompt = _validate_prompt(prompt)
            size = _dimensions(size, multiple=16, minimum=128, maximum=4096, label="video size")
            _bounded_number(fps, name="fps", minimum=1, maximum=60)
            _bounded_number(seed, name="seed", minimum=0, maximum=2_147_483_647)
            _bounded_number(num_frames, name="num_frames", minimum=1, maximum=1000)
            _bounded_number(seconds, name="seconds", minimum=1, maximum=60)
            _bounded_number(num_inference_steps, name="num_inference_steps", minimum=1, maximum=200)
            _bounded_number(guidance_scale, name="guidance_scale", minimum=0, maximum=30)
            if num_frames is not None and seconds is not None:
                raise MediaGenerationError("Use num_frames or seconds, not both", code="invalid_parameters")
            if reference_image and reference_url:
                raise MediaGenerationError("Use reference_image or reference_url, not both", code="invalid_parameters")
            negative_prompt = _validate_negative_prompt(negative_prompt)
            if reference_url:
                url_error = await asyncio.to_thread(
                    validate_public_http_url,
                    reference_url,
                    action="use as a video reference",
                    resolver=resolve_host_addresses,
                )
                if url_error:
                    raise MediaGenerationError(url_error.removeprefix("Error: "), code="unsafe_reference_url")

            body: dict[str, Any] = {"prompt": prompt, "size": size, "fps": fps, "seed": seed}
            if num_frames is not None:
                body["num_frames"] = num_frames
            if seconds is not None:
                body["seconds"] = seconds
            if num_inference_steps is not None:
                body["num_inference_steps"] = num_inference_steps
            if guidance_scale is not None:
                body["guidance_scale"] = guidance_scale
            if negative_prompt is not None:
                body["negative_prompt"] = negative_prompt
            if reference_url:
                body["reference_url"] = reference_url

            output_name = _safe_output_name(filename, prefix="generated-video", extension="mp4")
            request_key = _video_request_key(body, output_name)
            request_path = _video_request_state_path(outputs_path, request_key)
            existing = await asyncio.to_thread(_load_json_state, request_path)
            if existing and existing.get("job_id"):
                job_id = str(existing["job_id"])
                state = await asyncio.to_thread(_load_json_state, _video_job_state_path(outputs_path, job_id))
                if state is None:
                    raise MediaGenerationError("Saved video job state is incomplete", code="invalid_job_state")
            elif existing:
                raise MediaGenerationError(
                    "A matching video submission has an uncertain result; use a new filename before explicitly resubmitting",
                    code="submission_outcome_unknown",
                )
            else:
                request_state = {
                    "version": 1,
                    "request_key": request_key,
                    "output_name": output_name,
                    "status": "submitting",
                    "created_at": int(time.time()),
                }
                await asyncio.to_thread(_atomic_write_json, request_path, request_state)
                files = None
                data = None
                json_body = body
                if reference_image:
                    reference_name, reference_content, reference_mime = await asyncio.to_thread(
                        _resolve_reference_image,
                        runtime,
                        reference_image,
                    )
                    data = {key: str(value) for key, value in body.items()}
                    files = {"input_reference": (reference_name, reference_content, reference_mime)}
                    json_body = None
                async with _async_client(read_timeout=60.0) as client:
                    async with client.stream(
                        "POST",
                        endpoint,
                        headers=_api_headers(request_key=request_key),
                        json=json_body,
                        data=data,
                        files=files,
                    ) as response:
                        _raise_for_status(response, operation="Video submission")
                        submit_payload = _response_json(
                            await _read_limited_response(response, _MAX_JSON_RESPONSE_BYTES),
                            operation="Video submission",
                        )
                job_id = _video_job_id(submit_payload)
                status = _video_status(submit_payload)
                if status in _FAILED_VIDEO_STATUSES:
                    raise MediaGenerationError(_video_error_message(submit_payload), code="video_generation_failed")
                if status not in _PENDING_VIDEO_STATUSES | _COMPLETE_VIDEO_STATUSES:
                    raise MediaGenerationError(f"Video API returned unknown status {status!r}", code="invalid_response")
                state = {
                    "version": 1,
                    "request_key": request_key,
                    "job_id": job_id,
                    "output_name": output_name,
                    "status": status,
                    "created_at": int(time.time()),
                }
                request_state.update({"job_id": job_id, "status": status})
                await asyncio.to_thread(_atomic_write_json, _video_job_state_path(outputs_path, job_id), state)
                await asyncio.to_thread(_atomic_write_json, request_path, request_state)
                if status in _COMPLETE_VIDEO_STATUSES:
                    artifact, total = await _complete_video_job(
                        endpoint=endpoint,
                        outputs_path=outputs_path,
                        output_name=output_name,
                        job_id=job_id,
                        payload=submit_payload,
                    )
                    state.update({"status": "completed", "artifact": artifact, "bytes": total, "completed_at": int(time.time())})
                    await asyncio.to_thread(_atomic_write_json, _video_job_state_path(outputs_path, job_id), state)
                    return _success_command(
                        "generate_video",
                        tool_call_id,
                        {"status": "completed", "job_id": job_id, "artifact": artifact, "bytes": total},
                        artifact,
                    )

        existing_artifact = await asyncio.to_thread(_existing_video_artifact, outputs_path, state)
        if existing_artifact is not None:
            artifact, total = existing_artifact
            return _success_command(
                "generate_video",
                tool_call_id,
                {"status": "completed", "job_id": state["job_id"], "artifact": artifact, "bytes": total},
                artifact,
            )

        state, artifact, total = await _wait_for_video(
            endpoint=endpoint,
            outputs_path=outputs_path,
            state=state,
            wait_seconds=wait_seconds,
        )
        if artifact:
            return _success_command(
                "generate_video",
                tool_call_id,
                {"status": "completed", "job_id": state["job_id"], "artifact": artifact, "bytes": total},
                artifact,
            )
        return _success_command(
            "generate_video",
            tool_call_id,
            {
                "status": str(state.get("status") or "pending"),
                "job_id": state["job_id"],
                "progress": state.get("progress"),
                "next_action": "Call generate_video again with this job_id; do not submit a duplicate job.",
            },
        )
    except Exception as exc:
        return _error_command("generate_video", tool_call_id, exc)


def get_configured_media_generation_tools() -> list[BaseTool]:
    """Return only media tools whose operator-owned endpoint is configured."""

    configured: list[BaseTool] = []
    if os.getenv(IMAGE_API_URL_ENV, "").strip():
        configured.append(generate_image_tool)
    if os.getenv(VIDEO_API_URL_ENV, "").strip():
        configured.append(generate_video_tool)
    return configured
