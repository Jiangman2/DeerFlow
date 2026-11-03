import base64
import json
import os
import sys
import time

import requests

MINIMAX_DEFAULT_HOST = "https://api.minimaxi.com"
DASHSCOPE_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"


def _resolve_provider(override_env: str, existing_provider: str, has_existing_creds: bool) -> str:
    """Pick the provider: <SKILL>_PROVIDER override > existing creds > MiniMax fallback."""
    override = os.getenv(override_env)
    if override:
        return override.strip().lower()
    if has_existing_creds:
        return existing_provider
    if os.getenv("MINIMAX_API_KEY"):
        return "minimax"
    raise ValueError(
        f"No credentials found. Set GEMINI_API_KEY for {existing_provider}, "
        f"or MINIMAX_API_KEY for minimax (optionally force with {override_env})."
    )


def _minimax_host() -> str:
    return os.getenv("MINIMAX_API_HOST", MINIMAX_DEFAULT_HOST).rstrip("/")


def _dashscope_base_url() -> str:
    return os.getenv("DASHSCOPE_BASE_URL", DASHSCOPE_DEFAULT_BASE_URL).rstrip("/")


def _ensure_output_dir(output_file: str) -> None:
    """Create the output file's parent directory so nested paths don't fail."""
    output_dir = os.path.dirname(output_file)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)


def _video_task_state_path(output_file: str) -> str:
    """Return a hidden state path that will not be presented as a user artifact."""
    output_dir = os.path.dirname(output_file) or "."
    return os.path.join(
        output_dir,
        ".video-generation-jobs",
        f"{os.path.basename(output_file)}.json",
    )


def _write_video_task_state(path: str, state: dict) -> None:
    """Atomically persist non-secret asynchronous task metadata."""
    state_dir = os.path.dirname(path)
    if state_dir:
        os.makedirs(state_dir, exist_ok=True)
    temporary_path = f"{path}.tmp.{os.getpid()}"
    with open(temporary_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, sort_keys=True)
        f.write("\n")
    os.replace(temporary_path, path)


def _load_video_task_state(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        state = json.load(f)
    if not isinstance(state, dict) or state.get("version") != 1:
        raise ValueError(f"Unsupported or invalid video task state: {path}")
    return state


def _check_base_resp(payload: dict) -> None:
    base = payload.get("base_resp") or {}
    if base.get("status_code", 0) != 0:
        raise Exception(f"MiniMax error {base.get('status_code')}: {base.get('status_msg')}")


def _guess_mime(image_path: str) -> str:
    ext = os.path.splitext(image_path)[1].lower()
    return {
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }.get(ext, "image/jpeg")


def _to_data_url(image_path: str) -> str:
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{_guess_mime(image_path)};base64,{b64}"


def _poll_video_task(host: str, auth: str, task_id: str,
                     max_attempts: int = 120, interval: int = 3) -> str:
    for _ in range(max_attempts):
        response = requests.get(
            f"{host}/v1/query/video_generation",
            headers={"Authorization": auth},
            params={"task_id": task_id},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        status = payload.get("status")
        if status == "Success":
            return payload["file_id"]
        if status == "Fail":
            base = payload.get("base_resp") or {}
            raise Exception(
                f"MiniMax video task {task_id} failed: "
                f"{base.get('status_code')} {base.get('status_msg')}"
            )
        # Surface query-level errors (bad task_id, auth) that arrive as a non-zero
        # base_resp without a terminal status, then keep polling.
        _check_base_resp(payload)
        time.sleep(interval)
    raise Exception(f"MiniMax video task {task_id} timed out after {max_attempts} polls")


def _retrieve_file_url(host: str, auth: str, file_id: str) -> str:
    response = requests.get(
        f"{host}/v1/files/retrieve",
        headers={"Authorization": auth},
        params={"file_id": file_id},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    _check_base_resp(payload)
    return payload["file"]["download_url"]


def _download(url: str, output_file: str) -> None:
    response = requests.get(url, timeout=300)
    response.raise_for_status()
    _ensure_output_dir(output_file)
    with open(output_file, "wb") as f:
        f.write(response.content)


def _poll_dashscope_video_task(
    task_id: str, *, max_attempts: int = 180, interval: int = 5
) -> str:
    for _ in range(max_attempts):
        payload, output = _query_dashscope_video_task(task_id)
        status = output.get("task_status")
        if status == "SUCCEEDED":
            video_url = output.get("video_url")
            if not video_url:
                raise Exception(
                    f"DashScope video task {task_id} succeeded without a video URL"
                )
            return video_url
        if status in {"FAILED", "CANCELED", "UNKNOWN"}:
            raise Exception(
                f"DashScope video task {task_id} failed: "
                f"{output.get('code') or payload.get('code')} "
                f"{output.get('message') or payload.get('message')}"
            )
        time.sleep(interval)
    raise Exception(
        f"DashScope video task {task_id} timed out after {max_attempts} polls"
    )


def _query_dashscope_video_task(task_id: str) -> tuple[dict, dict]:
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise ValueError("DASHSCOPE_API_KEY is not set")
    response = requests.get(
        f"{_dashscope_base_url()}/tasks/{task_id}",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    return payload, payload.get("output") or {}


def _submit_dashscope_video_task(
    prompt: str,
    reference_images: list[str],
    aspect_ratio: str,
) -> tuple[str, str | None]:
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise ValueError("DASHSCOPE_API_KEY is not set")
    if reference_images:
        raise ValueError(
            "DashScope wan2.7-t2v is text-to-video and does not accept reference "
            "images; omit --reference-images or configure an image-to-video model."
        )
    try:
        duration = int(os.getenv("DASHSCOPE_VIDEO_DURATION", "5"))
    except ValueError:
        duration = 5
    duration = max(2, min(15, duration))
    ratio = aspect_ratio if aspect_ratio in {"16:9", "9:16", "1:1", "4:3", "3:4"} else "16:9"
    response = requests.post(
        f"{_dashscope_base_url()}/services/aigc/video-generation/video-synthesis",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        },
        json={
            "model": os.getenv("DASHSCOPE_VIDEO_MODEL", "wan2.7-t2v"),
            "input": {"prompt": prompt},
            "parameters": {
                "resolution": os.getenv("DASHSCOPE_VIDEO_RESOLUTION", "720P"),
                "ratio": ratio,
                "duration": duration,
                "prompt_extend": True,
                "watermark": False,
            },
        },
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    task_id = (payload.get("output") or {}).get("task_id")
    if not task_id:
        raise Exception(
            f"DashScope returned no task ID (request_id={payload.get('request_id')})"
        )
    return task_id, payload.get("request_id")


def _generate_video_dashscope(
    prompt: str,
    reference_images: list[str],
    output_file: str,
    aspect_ratio: str,
) -> str:
    task_id, _ = _submit_dashscope_video_task(
        prompt, reference_images, aspect_ratio
    )
    video_url = _poll_dashscope_video_task(task_id)
    _download(video_url, output_file)
    return f"The video has been generated successfully to {output_file}"


def _generate_video_minimax(
    prompt: str, reference_images: list[str], output_file: str
) -> str:
    api_key = os.getenv("MINIMAX_API_KEY")
    if not api_key:
        return "MINIMAX_API_KEY is not set"
    host = _minimax_host()
    auth = f"Bearer {api_key}"
    body = {"model": os.getenv("MINIMAX_VIDEO_MODEL", "MiniMax-Hailuo-2.3"), "prompt": prompt}
    if reference_images:
        body["first_frame_image"] = _to_data_url(reference_images[0])
    response = requests.post(
        f"{host}/v1/video_generation",
        headers={"Authorization": auth, "Content-Type": "application/json"},
        json=body,
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    _check_base_resp(payload)
    task_id = payload["task_id"]
    file_id = _poll_video_task(host, auth, task_id)
    download_url = _retrieve_file_url(host, auth, file_id)
    _download(download_url, output_file)
    return f"The video has been generated successfully to {output_file}"


def download(url: str, output_file: str) -> None:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set")
    response = requests.get(url, headers={"x-goog-api-key": api_key}, timeout=300)
    response.raise_for_status()
    _ensure_output_dir(output_file)
    with open(output_file, "wb") as f:
        f.write(response.content)


def _generate_video_gemini(
    prompt: str, reference_images: list[str], output_file: str
) -> str:
    reference_payload = []
    request_json = {"instances": [{"prompt": prompt}]}
    for reference_image in reference_images:
        with open(reference_image, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("utf-8")
        reference_payload.append(
            {"image": {"mimeType": "image/jpeg", "bytesBase64Encoded": image_b64},
             "referenceType": "asset"}
        )
    if reference_payload:
        request_json["instances"][0]["referenceImages"] = reference_payload
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return "GEMINI_API_KEY is not set"
    response = requests.post(
        "https://generativelanguage.googleapis.com/v1beta/models/veo-3.1-generate-preview:predictLongRunning",
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json=request_json,
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    operation_name = data["name"]
    while True:
        response = requests.get(
            f"https://generativelanguage.googleapis.com/v1beta/{operation_name}",
            headers={"x-goog-api-key": api_key},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("done", False):
            sample = data["response"]["generateVideoResponse"]["generatedSamples"][0]
            download(sample["video"]["uri"], output_file)
            break
        time.sleep(3)
    return f"The video has been generated successfully to {output_file}"


def _selected_video_provider() -> str:
    provider = os.getenv("VIDEO_GENERATION_PROVIDER", "").strip().lower()
    if provider:
        return provider
    if os.getenv("DASHSCOPE_API_KEY"):
        return "dashscope"
    return _resolve_provider(
        "VIDEO_GENERATION_PROVIDER",
        "gemini",
        bool(os.getenv("GEMINI_API_KEY")),
    )


def generate_video(
    prompt_file: str,
    reference_images: list[str],
    output_file: str,
    aspect_ratio: str = "16:9",
) -> str:
    with open(prompt_file, "r", encoding="utf-8") as f:
        prompt = f.read()
    provider = _selected_video_provider()
    if provider in ("dashscope", "bailian"):
        return _generate_video_dashscope(
            prompt, reference_images, output_file, aspect_ratio
        )
    if provider == "minimax":
        # MiniMax video uses resolution/duration, not aspect_ratio; aspect_ratio ignored.
        return _generate_video_minimax(prompt, reference_images, output_file)
    if provider in ("gemini", "google"):
        return _generate_video_gemini(prompt, reference_images, output_file)
    raise ValueError(
        f"Unknown video provider: {provider!r} "
        "(use 'dashscope', 'gemini', or 'minimax')"
    )


def submit_video_task(
    prompt_file: str,
    reference_images: list[str],
    output_file: str,
    aspect_ratio: str = "16:9",
    task_state_file: str | None = None,
) -> str:
    """Submit one DashScope task and persist its ID without waiting for completion.

    The state file is created before the provider call. If the command channel is
    interrupted after DashScope accepted the request, a subsequent invocation will
    refuse to submit another potentially billable task under the same output path.
    """
    state_path = task_state_file or _video_task_state_path(output_file)
    if os.path.exists(state_path):
        state = _load_video_task_state(state_path)
        if state.get("output_file") != output_file:
            raise ValueError(
                f"Video task state belongs to a different output: {state_path}"
            )
        return (
            "VIDEO_TASK_ALREADY_SUBMITTED "
            f"status={state.get('status', 'UNKNOWN')} "
            f"task_id={state.get('task_id', 'UNAVAILABLE')} "
            f"state_file={state_path}"
        )

    # This async entry point is the enterprise execution contract. It remains
    # pinned to DashScope even if a stale legacy provider variable is present.
    if not os.getenv("DASHSCOPE_API_KEY"):
        raise ValueError("DASHSCOPE_API_KEY is not set")

    state = {
        "version": 1,
        "provider": "dashscope",
        "status": "SUBMITTING",
        "output_file": output_file,
        "model": os.getenv("DASHSCOPE_VIDEO_MODEL", "wan2.7-t2v"),
        "created_at": int(time.time()),
    }
    _write_video_task_state(state_path, state)

    try:
        with open(prompt_file, "r", encoding="utf-8") as f:
            prompt = f.read()
        task_id, request_id = _submit_dashscope_video_task(
            prompt,
            reference_images,
            aspect_ratio,
        )
    except Exception as exc:
        state.update(
            {
                "status": "SUBMISSION_FAILED",
                "error_type": type(exc).__name__,
                "updated_at": int(time.time()),
            }
        )
        _write_video_task_state(state_path, state)
        raise

    state.update(
        {
            "status": "PENDING",
            "task_id": task_id,
            "request_id": request_id,
            "updated_at": int(time.time()),
        }
    )
    _write_video_task_state(state_path, state)
    return (
        "VIDEO_TASK_SUBMITTED "
        f"task_id={task_id} state_file={state_path} "
        "next_action=run_status_only"
    )


def check_video_task(
    output_file: str,
    task_state_file: str | None = None,
) -> str:
    """Perform exactly one remote status check and download only when ready."""
    state_path = task_state_file or _video_task_state_path(output_file)
    if not os.path.exists(state_path):
        raise FileNotFoundError(
            f"No submitted video task exists for {output_file}; "
            "run with --submit-only exactly once first"
        )
    state = _load_video_task_state(state_path)
    if state.get("output_file") != output_file:
        raise ValueError(
            f"Video task state belongs to a different output: {state_path}"
        )
    if state.get("provider") != "dashscope":
        raise ValueError(
            f"Unsupported asynchronous video provider: {state.get('provider')!r}"
        )
    if state.get("status") == "SUCCEEDED" and os.path.isfile(output_file):
        return (
            "VIDEO_TASK_SUCCEEDED "
            f"task_id={state.get('task_id')} output_file={output_file}"
        )

    task_id = state.get("task_id")
    if not task_id:
        raise RuntimeError(
            "The task state has no provider task ID. Submission may have been "
            "interrupted; do not automatically resubmit because the provider may "
            f"already have accepted a billable task. Inspect {state_path}."
        )

    payload, output = _query_dashscope_video_task(task_id)
    status = output.get("task_status") or "UNKNOWN"
    state.update({"status": status, "updated_at": int(time.time())})

    if status == "SUCCEEDED":
        video_url = output.get("video_url")
        if not video_url:
            state["status"] = "READY_WITHOUT_URL"
            _write_video_task_state(state_path, state)
            raise RuntimeError(
                f"DashScope video task {task_id} succeeded without a video URL"
            )
        _write_video_task_state(state_path, state)
        _download(video_url, output_file)
        state.update({"status": "SUCCEEDED", "completed_at": int(time.time())})
        state.pop("error_code", None)
        state.pop("error_message", None)
        _write_video_task_state(state_path, state)
        return (
            "VIDEO_TASK_SUCCEEDED "
            f"task_id={task_id} output_file={output_file}"
        )

    if status in {"FAILED", "CANCELED", "UNKNOWN"}:
        error_code = output.get("code") or payload.get("code")
        error_message = output.get("message") or payload.get("message")
        state.update(
            {
                "status": status,
                "error_code": error_code,
                "error_message": error_message,
            }
        )
        _write_video_task_state(state_path, state)
        raise RuntimeError(
            f"DashScope video task {task_id} failed: "
            f"{error_code} {error_message}"
        )

    _write_video_task_state(state_path, state)
    return (
        "VIDEO_TASK_PENDING "
        f"task_id={task_id} status={status} state_file={state_path}"
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Submit or check one platform-managed DashScope video task"
    )
    parser.add_argument("--prompt-file", required=True, help="Absolute path to JSON prompt file")
    parser.add_argument("--reference-images", nargs="*", default=[],
                        help="Absolute paths to reference images (space-separated)")
    parser.add_argument("--output-file", required=True, help="Output path for generated video")
    parser.add_argument("--aspect-ratio", required=False, default="16:9",
                        help="Aspect ratio of the generated video")
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument(
        "--submit-only",
        action="store_true",
        help="Submit one DashScope task, persist its ID, and return immediately",
    )
    operation.add_argument(
        "--status-only",
        action="store_true",
        help="Check one persisted DashScope task once; download when complete",
    )
    parser.add_argument(
        "--task-state-file",
        required=False,
        help="Optional task-state path (defaults to a hidden path beside the output)",
    )
    args = parser.parse_args()

    try:
        if args.submit_only:
            result = submit_video_task(
                args.prompt_file,
                args.reference_images,
                args.output_file,
                args.aspect_ratio,
                args.task_state_file,
            )
        elif args.status_only:
            result = check_video_task(
                args.output_file,
                args.task_state_file,
            )
        else:
            parser.error(
                "one of --submit-only or --status-only is required; "
                "foreground polling is disabled"
            )
        print(result)
    except Exception as e:
        print(f"Error while generating video: {e}", file=sys.stderr)
        raise SystemExit(1) from e
