import argparse
import json
import os
import sys

import requests

MINIMAX_DEFAULT_HOST = "https://api.minimaxi.com"
DASHSCOPE_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"


def _check_base_resp(payload: dict) -> None:
    base = payload.get("base_resp") or {}
    if base.get("status_code", 0) != 0:
        raise Exception(f"MiniMax error {base.get('status_code')}: {base.get('status_msg')}")


def _dashscope_base_url() -> str:
    return os.getenv("DASHSCOPE_BASE_URL", DASHSCOPE_DEFAULT_BASE_URL).rstrip("/")


def _ensure_output_dir(output_file: str) -> None:
    output_dir = os.path.dirname(output_file)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)


def _download(url: str, output_file: str) -> None:
    response = requests.get(url, timeout=300)
    response.raise_for_status()
    _ensure_output_dir(output_file)
    with open(output_file, "wb") as f:
        f.write(response.content)


def _raise_dashscope_music_error(response: requests.Response) -> None:
    if response.status_code < 400:
        return
    try:
        payload = response.json()
    except (ValueError, TypeError):
        payload = {}
    code = payload.get("code") or payload.get("Code")
    message = payload.get("message") or payload.get("Message")
    request_id = payload.get("request_id") or payload.get("RequestId")
    if response.status_code == 403:
        details = ", ".join(
            str(value)
            for value in (code, message, f"request_id={request_id}" if request_id else None)
            if value
        )
        raise PermissionError(
            "DashScope fun-music-v1 access was denied. Enable the model in the "
            "Alibaba Cloud Model Studio console for the China (Beijing) region"
            + (f" ({details})" if details else "")
        )
    response.raise_for_status()


def _generate_music_dashscope(spec: dict, output_file: str) -> str:
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        return "DASHSCOPE_API_KEY is not set"

    prompt = (spec.get("prompt") or "").strip()
    lyrics = (spec.get("lyrics") or "").strip()
    if not prompt and not lyrics:
        raise ValueError("`prompt` or `lyrics` is required in the music spec")

    input_data = {
        "format": os.getenv("DASHSCOPE_MUSIC_FORMAT", "mp3"),
        "enable_aigc_watermark": False,
    }
    if prompt:
        input_data["prompt"] = prompt
    if lyrics:
        input_data["lyrics"] = lyrics
    if spec.get("is_instrumental", False):
        input_data["is_instrumental"] = True
    else:
        input_data["gender"] = os.getenv("DASHSCOPE_MUSIC_GENDER", "female")

    response = requests.post(
        f"{_dashscope_base_url()}/services/audio/music/generation",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": os.getenv("DASHSCOPE_MUSIC_MODEL", "fun-music-v1"),
            "input": input_data,
        },
        timeout=600,
    )
    _raise_dashscope_music_error(response)
    payload = response.json()
    audio_url = (((payload.get("output") or {}).get("audio") or {}).get("url"))
    if not audio_url:
        raise Exception(
            f"DashScope returned no audio URL (request_id={payload.get('request_id')})"
        )
    _download(audio_url, output_file)
    return f"Successfully generated music to {output_file}"


def generate_music(prompt_file: str, output_file: str) -> str:
    """Generate a song from a JSON spec via DashScope or MiniMax.

    Spec JSON: {"title": str, "prompt": str, "lyrics"?: str, "is_instrumental"?: bool}
    - lyrics given        -> use them (supports [Verse]/[Chorus] structure tags, \\n lines)
    - is_instrumental true -> pure music, no lyrics needed
    - otherwise           -> lyrics_optimizer auto-writes lyrics from prompt
    """
    with open(prompt_file, "r", encoding="utf-8") as f:
        spec = json.load(f)

    provider = os.getenv("MUSIC_GENERATION_PROVIDER", "").strip().lower()
    if not provider:
        provider = "dashscope" if os.getenv("DASHSCOPE_API_KEY") else "minimax"
    if provider in ("dashscope", "bailian"):
        return _generate_music_dashscope(spec, output_file)
    if provider != "minimax":
        raise ValueError(
            f"Unknown music provider: {provider!r} (use 'dashscope' or 'minimax')"
        )

    api_key = os.getenv("MINIMAX_API_KEY")
    if not api_key:
        return "MINIMAX_API_KEY is not set"

    prompt = (spec.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("`prompt` is required in the music spec")
    lyrics = spec.get("lyrics") or None  # treat empty string the same as absent
    is_instrumental = bool(spec.get("is_instrumental", False))

    body = {
        "model": os.getenv("MINIMAX_MUSIC_MODEL", "music-2.6-free"),
        "prompt": prompt,
        "output_format": "hex",
        "audio_setting": {"sample_rate": 44100, "bitrate": 256000, "format": "mp3"},
    }
    if lyrics:
        body["lyrics"] = lyrics
    elif is_instrumental:
        body["is_instrumental"] = True
    else:
        body["lyrics_optimizer"] = True

    host = os.getenv("MINIMAX_API_HOST", MINIMAX_DEFAULT_HOST).rstrip("/")
    response = requests.post(
        f"{host}/v1/music_generation",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body,
        timeout=300,
    )
    response.raise_for_status()
    payload = response.json()
    _check_base_resp(payload)
    audio_hex = (payload.get("data") or {}).get("audio")
    if not audio_hex:
        raise Exception("MiniMax returned no audio data")

    _ensure_output_dir(output_file)
    with open(output_file, "wb") as f:
        f.write(bytes.fromhex(audio_hex))
    return f"Successfully generated music to {output_file}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate music using DashScope or MiniMax API"
    )
    parser.add_argument("--prompt-file", required=True,
                        help="Absolute path to JSON spec file {title, prompt, lyrics?, is_instrumental?}")
    parser.add_argument("--output-file", required=True, help="Output path for generated MP3")
    args = parser.parse_args()

    try:
        print(generate_music(args.prompt_file, args.output_file))
    except Exception as e:
        print(f"Error while generating music: {e}", file=sys.stderr)
        raise SystemExit(1) from e
