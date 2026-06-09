import argparse
import base64
import json
import os

import requests

MINIMAX_DEFAULT_HOST = "https://api.minimaxi.com"
DASHSCOPE_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
DASHSCOPE_DEFAULT_MUSIC_MODEL = "fun-music-v1"


def _check_base_resp(payload: dict) -> None:
    base = payload.get("base_resp") or {}
    if base.get("status_code", 0) != 0:
        raise Exception(f"MiniMax error {base.get('status_code')}: {base.get('status_msg')}")


def _resolve_provider() -> str:
    provider = os.getenv("MUSIC_GENERATION_PROVIDER")
    if provider:
        provider = provider.strip().lower()
    elif os.getenv("DASHSCOPE_API_KEY"):
        provider = "dashscope"
    elif os.getenv("MINIMAX_API_KEY"):
        provider = "minimax"
    else:
        raise ValueError(
            "No credentials found. Set DASHSCOPE_API_KEY for dashscope or "
            "MINIMAX_API_KEY for minimax (optionally force with "
            "MUSIC_GENERATION_PROVIDER)."
        )
    if provider in ("bailian", "qwen"):
        provider = "dashscope"
    if provider not in ("dashscope", "minimax"):
        raise ValueError(f"Unknown music provider: {provider!r} (use 'dashscope' or 'minimax')")
    return provider


def _dashscope_base_url() -> str:
    return os.getenv("DASHSCOPE_BASE_URL", DASHSCOPE_DEFAULT_BASE_URL).rstrip("/")


def _write_output(output_file: str, data: bytes) -> None:
    output_dir = os.path.dirname(output_file)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(output_file, "wb") as f:
        f.write(data)


def _download_audio(url: str) -> bytes:
    response = requests.get(url, timeout=300)
    response.raise_for_status()
    return response.content


def _load_spec(prompt_file: str) -> dict:
    with open(prompt_file, "r", encoding="utf-8") as f:
        spec = json.load(f)
    prompt = (spec.get("prompt") or "").strip()
    lyrics = (spec.get("lyrics") or "").strip()
    if not prompt and not lyrics:
        raise ValueError("`prompt` or `lyrics` is required in the music spec")
    return spec


def generate_music(prompt_file: str, output_file: str) -> str:
    """Generate a song from a JSON spec via DashScope or MiniMax.

    Spec JSON: {"title": str, "prompt": str, "lyrics"?: str, "is_instrumental"?: bool}
    - lyrics given        -> use them (supports [Verse]/[Chorus] structure tags, \\n lines)
    - is_instrumental true -> pure music, no lyrics needed
    - otherwise           -> lyrics_optimizer auto-writes lyrics from prompt
    """
    spec = _load_spec(prompt_file)
    try:
        provider = _resolve_provider()
    except ValueError as e:
        if "No credentials found" in str(e):
            return "DASHSCOPE_API_KEY or MINIMAX_API_KEY is not set"
        raise
    if provider == "dashscope":
        return _generate_music_dashscope(spec, output_file)
    return _generate_music_minimax(spec, output_file)


def _generate_music_minimax(spec: dict, output_file: str) -> str:
    """Generate music via MiniMax /v1/music_generation."""

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

    _write_output(output_file, bytes.fromhex(audio_hex))
    return f"Successfully generated music to {output_file}"


def _generate_music_dashscope(spec: dict, output_file: str) -> str:
    """Generate music via DashScope Fun-Music.

    Fun-Music does not expose MiniMax's lyrics_optimizer/is_instrumental flags.
    When instrumental mode is requested, keep the user's prompt and add a short
    no-vocal instruction so the API still receives a valid prompt.
    """
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        return "DASHSCOPE_API_KEY is not set"

    prompt = (spec.get("prompt") or "").strip()
    lyrics = (spec.get("lyrics") or "").strip()
    is_instrumental = bool(spec.get("is_instrumental", False))
    input_body = {
        "gender": spec.get("gender") or os.getenv("DASHSCOPE_MUSIC_GENDER", "female"),
        "format": os.getenv("DASHSCOPE_MUSIC_FORMAT", "mp3"),
        "enable_aigc_watermark": os.getenv("DASHSCOPE_MUSIC_AIGC_WATERMARK", "false").lower()
        == "true",
    }
    if lyrics:
        input_body["lyrics"] = lyrics
    else:
        if is_instrumental:
            prompt = f"{prompt}，纯音乐，无人声，无歌词".strip("，")
        input_body["prompt"] = prompt

    response = requests.post(
        f"{_dashscope_base_url()}/services/audio/music/generation",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": os.getenv("DASHSCOPE_MUSIC_MODEL", DASHSCOPE_DEFAULT_MUSIC_MODEL),
            "input": input_body,
        },
        timeout=300,
    )
    response.raise_for_status()
    payload = response.json()
    audio = ((payload.get("output") or {}).get("audio") or {})
    audio_data = audio.get("data")
    if audio_data:
        data = base64.b64decode(audio_data)
    else:
        audio_url = audio.get("url")
        if not audio_url:
            raise Exception(f"DashScope returned no audio data or URL: {payload}")
        data = _download_audio(audio_url)
    _write_output(output_file, data)
    return f"Successfully generated music to {output_file}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate music using MiniMax API")
    parser.add_argument("--prompt-file", required=True,
                        help="Absolute path to JSON spec file {title, prompt, lyrics?, is_instrumental?}")
    parser.add_argument("--output-file", required=True, help="Output path for generated MP3")
    args = parser.parse_args()

    try:
        print(generate_music(args.prompt_file, args.output_file))
    except Exception as e:
        print(f"Error while generating music: {e}")
