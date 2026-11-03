import base64
import json
import os
import ssl
import sys

import requests
from requests.adapters import HTTPAdapter

MINIMAX_DEFAULT_HOST = "https://api.minimaxi.com"
DASHSCOPE_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
# MiniMax image-01 caps the prompt at 1500 characters and rejects longer requests
# with a generic "invalid params" error, so validate before calling the API.
MINIMAX_PROMPT_MAX_CHARS = 1500


class _DashScopeTLSAdapter(HTTPAdapter):
    """Use one verified SSL context for direct and proxied DashScope HTTPS."""

    def __init__(self, ssl_context: ssl.SSLContext, *args, **kwargs):
        self._dashscope_ssl_context = ssl_context
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = self._dashscope_ssl_context
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        proxy_kwargs["ssl_context"] = self._dashscope_ssl_context
        return super().proxy_manager_for(proxy, **proxy_kwargs)


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _dashscope_http_client():
    """Return requests or a scoped verified session for a legacy TLS proxy.

    The compatibility path lowers only OpenSSL's RSA security level for
    DashScope traffic. CA-chain and hostname verification remain enabled.
    """

    if not _truthy_env("DASHSCOPE_TLS_COMPAT"):
        return requests

    ca_file = next(
        (
            value
            for name in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE")
            if (value := os.getenv(name, "").strip()) and os.path.isfile(value)
        ),
        None,
    )
    context = (
        ssl.create_default_context(cafile=ca_file)
        if ca_file
        else ssl.create_default_context()
    )
    context.set_ciphers("DEFAULT:@SECLEVEL=1")

    session = requests.Session()
    session.mount("https://", _DashScopeTLSAdapter(context))
    return session


def _resolve_provider() -> str:
    """Resolve image generation to the enterprise-supported provider.

    Bailian/DashScope is the platform default. MiniMax remains available only
    when explicitly selected for compatibility; it is never an automatic
    fallback. Gemini aliases are rejected so a stale environment cannot route
    image data to an unavailable provider.
    """
    configured = os.getenv("IMAGE_GENERATION_PROVIDER", "dashscope").strip().lower()
    provider = {"qwen": "dashscope", "bailian": "dashscope"}.get(
        configured, configured
    )
    if provider in {"gemini", "google"}:
        raise ValueError(
            "Gemini image generation is disabled. "
            "Set IMAGE_GENERATION_PROVIDER=dashscope and configure DASHSCOPE_API_KEY."
        )
    if provider not in {"dashscope", "minimax"}:
        raise ValueError(
            f"Unknown image provider: {configured!r} "
            "(use 'dashscope'/'bailian' or explicitly select 'minimax')"
        )
    return provider


def _minimax_host() -> str:
    return os.getenv("MINIMAX_API_HOST", MINIMAX_DEFAULT_HOST).rstrip("/")


def _dashscope_base_url() -> str:
    return os.getenv("DASHSCOPE_BASE_URL", DASHSCOPE_DEFAULT_BASE_URL).rstrip("/")


def _check_base_resp(payload: dict) -> None:
    base = payload.get("base_resp") or {}
    if base.get("status_code", 0) != 0:
        raise Exception(
            f"MiniMax error {base.get('status_code')}: {base.get('status_msg')}"
        )


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


def _ensure_output_dir(output_file: str) -> None:
    """Create the output file's parent directory so nested paths don't fail."""
    output_dir = os.path.dirname(output_file)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)


def _parse_prompt_spec(raw: str) -> tuple[str, str | None]:
    """Return the text and optional negative prompt from a shared prompt file."""
    text = raw.strip()
    try:
        data = json.loads(text)
    except (ValueError, json.JSONDecodeError):
        return text, None
    if not isinstance(data, dict):
        return text, None
    prompt = data.get("prompt")
    negative_prompt = data.get("negative_prompt")
    return (
        prompt.strip() if isinstance(prompt, str) and prompt.strip() else text,
        negative_prompt.strip()
        if isinstance(negative_prompt, str) and negative_prompt.strip()
        else None,
    )


def _dashscope_image_size(aspect_ratio: str) -> str:
    return {
        "16:9": "2688*1536",
        "9:16": "1536*2688",
        "1:1": "2048*2048",
        "4:3": "2368*1728",
        "3:4": "1728*2368",
    }.get(aspect_ratio, "2048*2048")


def _download_result(url: str, output_file: str, *, http_client=requests) -> None:
    response = http_client.get(url, timeout=300)
    response.raise_for_status()
    _ensure_output_dir(output_file)
    with open(output_file, "wb") as f:
        f.write(response.content)


def _generate_image_dashscope(
    prompt: str, reference_images: list[str], output_file: str, aspect_ratio: str
) -> str:
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        return "DASHSCOPE_API_KEY is not set"

    text, negative_prompt = _parse_prompt_spec(prompt)
    content = [{"image": _to_data_url(path)} for path in reference_images]
    content.append({"text": text})
    parameters = {
        "size": _dashscope_image_size(aspect_ratio),
        "n": 1,
        "prompt_extend": True,
        "watermark": False,
    }
    if negative_prompt:
        parameters["negative_prompt"] = negative_prompt

    http_client = _dashscope_http_client()
    response = http_client.post(
        f"{_dashscope_base_url()}/services/aigc/multimodal-generation/generation",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": os.getenv("DASHSCOPE_IMAGE_MODEL", "qwen-image-2.0-pro"),
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": parameters,
        },
        timeout=300,
    )
    response.raise_for_status()
    payload = response.json()
    choices = (payload.get("output") or {}).get("choices") or []
    message_content = (
        ((choices[0].get("message") or {}).get("content") or []) if choices else []
    )
    image_url = next(
        (
            item.get("image")
            for item in message_content
            if isinstance(item, dict) and item.get("image")
        ),
        None,
    )
    if not image_url:
        raise Exception(
            f"DashScope returned no image URL (request_id={payload.get('request_id')})"
        )
    _download_result(image_url, output_file, http_client=http_client)
    return f"Successfully generated image to {output_file}"


def _minimax_prompt(raw: str) -> str:
    """Extract the single text prompt MiniMax image-01 expects.

    The shared prompt file is structured JSON (a consolidated ``prompt`` plus
    provider-neutral fields like ``style`` / ``composition`` / ``negative_prompt``),
    but MiniMax consumes one string and expands it via ``prompt_optimizer``. The
    provider adapts the input itself — the caller never needs to know MiniMax is
    active. Use the JSON ``prompt`` field; fall back to the raw text for plain-text
    prompt files or JSON without a ``prompt`` field.
    """
    text = raw.strip()
    try:
        data = json.loads(text)
    except (ValueError, json.JSONDecodeError):
        return text
    if isinstance(data, dict):
        core = data.get("prompt")
        if isinstance(core, str) and core.strip():
            return core.strip()
    return text


def _generate_image_minimax(
    prompt: str, reference_images: list[str], output_file: str, aspect_ratio: str
) -> str:
    api_key = os.getenv("MINIMAX_API_KEY")
    if not api_key:
        return "MINIMAX_API_KEY is not set"
    prompt = _minimax_prompt(prompt)
    if len(prompt) > MINIMAX_PROMPT_MAX_CHARS:
        return (
            f"Prompt is {len(prompt)} characters but MiniMax image-01 accepts at most "
            f"{MINIMAX_PROMPT_MAX_CHARS}. Shorten the prompt to stay within the limit; "
            f"reference images plus a tighter description usually recover the detail."
        )
    body = {
        "model": os.getenv("MINIMAX_IMAGE_MODEL", "image-01"),
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "response_format": "base64",
        "n": 1,
        "prompt_optimizer": True,
    }
    if reference_images:
        # Reference images are passed as character subjects as-is. Invalid files
        # surface as a MiniMax API error.
        body["subject_reference"] = [
            {"type": "character", "image_file": _to_data_url(p)} for p in reference_images
        ]
    response = requests.post(
        f"{_minimax_host()}/v1/image_generation",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body,
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    _check_base_resp(payload)
    images = (payload.get("data") or {}).get("image_base64") or []
    if not images:
        raise Exception("MiniMax returned no image data")
    _ensure_output_dir(output_file)
    with open(output_file, "wb") as f:
        f.write(base64.b64decode(images[0]))
    return f"Successfully generated image to {output_file}"


def generate_image(
    prompt_file: str,
    reference_images: list[str],
    output_file: str,
    aspect_ratio: str = "16:9",
) -> str:
    with open(prompt_file, encoding="utf-8") as f:
        prompt = f.read()
    provider = _resolve_provider()
    if provider == "dashscope":
        return _generate_image_dashscope(
            prompt, reference_images, output_file, aspect_ratio
        )
    if provider == "minimax":
        return _generate_image_minimax(prompt, reference_images, output_file, aspect_ratio)
    raise AssertionError(f"Unhandled image provider: {provider}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate images using Bailian/DashScope or MiniMax API"
    )
    parser.add_argument("--prompt-file", required=True, help="Absolute path to JSON prompt file")
    parser.add_argument("--reference-images", nargs="*", default=[],
                        help="Absolute paths to reference images (space-separated)")
    parser.add_argument("--output-file", required=True, help="Output path for generated image")
    parser.add_argument("--aspect-ratio", required=False, default="16:9",
                        help="Aspect ratio of the generated image")
    args = parser.parse_args()

    try:
        print(generate_image(args.prompt_file, args.reference_images,
                             args.output_file, args.aspect_ratio))
    except Exception as e:
        print(f"Error while generating image: {e}", file=sys.stderr)
        raise SystemExit(1) from e
