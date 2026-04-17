import base64
import os

import requests
from PIL import Image


def _generate_with_ark(
    *,
    prompt: str,
    output_file: str,
    aspect_ratio: str,
) -> str:
    api_key = os.getenv("ARK_API_KEY") or os.getenv("VOLCENGINE_API_KEY") or ""
    if not api_key:
        return "ARK_API_KEY is not set"

    model_id = os.getenv("ARK_IMAGE_MODEL") or os.getenv("ARK_MODEL") or ""
    if not model_id:
        return "ARK_IMAGE_MODEL is not set"

    base_url = os.getenv("ARK_BASE_URL") or os.getenv("VOLCENGINE_ARK_BASE_URL") or "https://ark.cn-beijing.volces.com/api/v3"
    base_url = base_url.rstrip("/")
    url = f"{base_url}/images/generations"

    size = os.getenv("ARK_IMAGE_SIZE") or "2K"
    output_format = os.getenv("ARK_OUTPUT_FORMAT") or os.getenv("ARK_IMAGE_FORMAT") or ""
    response_format = os.getenv("ARK_RESPONSE_FORMAT") or ""
    sequential = os.getenv("ARK_SEQUENTIAL_IMAGE_GENERATION") or ""
    stream = os.getenv("ARK_STREAM") or ""
    watermark = os.getenv("ARK_WATERMARK")
    watermark_flag = False if watermark is None else watermark.strip().lower() in {"1", "true", "yes", "y"}

    session = requests.Session()
    # session.trust_env = False # 允许读取环境变量中的代理配置

    payload: dict = {
        "model": model_id,
        "prompt": prompt,
        "size": size,
        "watermark": watermark_flag,
    }
    if output_format:
        payload["output_format"] = output_format
    if response_format:
        payload["response_format"] = response_format
    if sequential:
        payload["sequential_image_generation"] = sequential
    if stream:
        payload["stream"] = stream.strip().lower() in {"1", "true", "yes", "y"}

    try:
        response = session.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=300,
        )
    except requests.RequestException as e:
        return f"Network error calling Ark image API: {e}"

    if not response.ok:
        msg = response.text.strip()
        return f"Ark image API request failed (HTTP {response.status_code}). {msg}"

    try:
        data = response.json()
    except Exception:
        return "Ark image API returned non-JSON response"

    items = data.get("data")
    if not isinstance(items, list) or not items:
        return f"Ark image API returned unexpected response: {data}"

    first = items[0] if isinstance(items[0], dict) else None
    if not isinstance(first, dict):
        return f"Ark image API returned unexpected response: {data}"

    if isinstance(first.get("b64_json"), str) and first["b64_json"]:
        try:
            img_bytes = base64.b64decode(first["b64_json"])
        except Exception:
            return "Failed to decode Ark image base64 content"
        with open(output_file, "wb") as f:
            f.write(img_bytes)
        return f"Successfully generated image to {output_file}"

    if isinstance(first.get("url"), str) and first["url"]:
        try:
            r = session.get(first["url"], timeout=300)
            r.raise_for_status()
            with open(output_file, "wb") as f:
                f.write(r.content)
            return f"Successfully generated image to {output_file}"
        except Exception as e:
            return f"Failed to download Ark image url: {e}"

    return f"Ark image API returned unexpected response: {data}"


def validate_image(image_path: str) -> bool:
    """
    Validate if an image file can be opened and is not corrupted.
    
    Args:
        image_path: Path to the image file
        
    Returns:
        True if the image is valid and can be opened, False otherwise
    """
    try:
        with Image.open(image_path) as img:
            img.verify()  # Verify that it's a valid image
        # Re-open to check if it can be fully loaded (verify() may not catch all issues)
        with Image.open(image_path) as img:
            img.load()  # Force load the image data
        return True
    except Exception as e:
        print(f"Warning: Image '{image_path}' is invalid or corrupted: {e}")
        return False


def generate_image(
    prompt_file: str,
    reference_images: list[str],
    output_file: str,
    aspect_ratio: str = "16:9",
) -> str:
    with open(prompt_file, "r", encoding="utf-8") as f:
        prompt = f.read()
    parts = []
    i = 0
    
    # Filter out invalid reference images
    valid_reference_images = []
    for ref_img in reference_images:
        if validate_image(ref_img):
            valid_reference_images.append(ref_img)
        else:
            print(f"Skipping invalid reference image: {ref_img}")
    
    if len(valid_reference_images) < len(reference_images):
        print(f"Note: {len(reference_images) - len(valid_reference_images)} reference image(s) were skipped due to validation failure.")
    
    for reference_image in valid_reference_images:
        i += 1
        with open(reference_image, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("utf-8")
        parts.append(
            {
                "inlineData": {
                    "mimeType": "image/jpeg",
                    "data": image_b64,
                }
            }
        )

    if os.getenv("ARK_API_KEY") or os.getenv("VOLCENGINE_API_KEY"):
        if output_file.lower().endswith(".bin"):
            output_file = output_file[:-4] + ".jpg"
        return _generate_with_ark(prompt=prompt, output_file=output_file, aspect_ratio=aspect_ratio)

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return "GEMINI_API_KEY is not set"
    session = requests.Session()
    # session.trust_env = False # 允许读取环境变量中的代理配置
    try:
        response = session.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-image-preview:generateContent",
            headers={
                "x-goog-api-key": api_key,
                "Content-Type": "application/json",
            },
            json={
                "generationConfig": {"imageConfig": {"aspectRatio": aspect_ratio}},
                "contents": [{"parts": [*parts, {"text": prompt}]}],
            },
            timeout=120,
        )
    except requests.RequestException as e:
        return f"Network error calling Gemini API: {e}"

    if not response.ok:
        try:
            payload = response.json()
            msg = payload.get("error", {}).get("message") or response.text
        except Exception:
            msg = response.text
        msg = (msg or "").strip()
        if response.status_code == 429:
            return f"Gemini API quota exceeded (HTTP 429). {msg}"
        return f"Gemini API request failed (HTTP {response.status_code}). {msg}"

    json = response.json()
    parts: list[dict] = json["candidates"][0]["content"]["parts"]
    image_parts = [part for part in parts if part.get("inlineData", False)]
    if len(image_parts) == 1:
        base64_image = image_parts[0]["inlineData"]["data"]
        # Save the image to a file
        with open(output_file, "wb") as f:
            f.write(base64.b64decode(base64_image))
        return f"Successfully generated image to {output_file}"
    else:
        raise Exception("Failed to generate image")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate images using Gemini API")
    parser.add_argument(
        "--prompt-file",
        required=True,
        help="Absolute path to JSON prompt file",
    )
    parser.add_argument(
        "--reference-images",
        nargs="*",
        default=[],
        help="Absolute paths to reference images (space-separated)",
    )
    parser.add_argument(
        "--output-file",
        required=True,
        help="Output path for generated image",
    )
    parser.add_argument(
        "--aspect-ratio",
        required=False,
        default="16:9",
        help="Aspect ratio of the generated image",
    )

    args = parser.parse_args()

    try:
        print(
            generate_image(
                args.prompt_file,
                args.reference_images,
                args.output_file,
                args.aspect_ratio,
            )
        )
    except Exception as e:
        print(f"Error while generating image: {e}")
