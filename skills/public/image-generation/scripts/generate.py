import base64
import mimetypes
import os

import requests
from PIL import Image


def _aspect_ratio_to_size(aspect_ratio: str) -> str:
    """Map common aspect ratios to DashScope Wan pixel sizes."""
    explicit = os.getenv("DASHSCOPE_IMAGE_SIZE") or ""
    if explicit:
        return explicit

    normalized = (aspect_ratio or "16:9").strip()
    sizes = {
        "1:1": "2048*2048",
        "16:9": "2560*1440",
        "9:16": "1440*2560",
        "4:3": "2304*1728",
        "3:4": "1728*2304",
        "3:2": "2496*1664",
        "2:3": "1664*2496",
    }
    return sizes.get(normalized, "2560*1440")


def _truthy_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y"}


def _image_to_data_url(image_path: str) -> str:
    mime_type = mimetypes.guess_type(image_path)[0] or "image/jpeg"
    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime_type};base64,{image_b64}"


def _extract_dashscope_image_url(data: dict) -> str | None:
    choices = data.get("output", {}).get("choices", [])
    if not isinstance(choices, list):
        return None

    for choice in choices:
        if not isinstance(choice, dict):
            continue
        content = choice.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "image" and isinstance(part.get("image"), str):
                return part["image"]
            if isinstance(part.get("url"), str):
                return part["url"]
    return None


def _generate_with_dashscope_wan(
    *,
    prompt: str,
    reference_images: list[str],
    output_file: str,
    aspect_ratio: str,
) -> str:
    api_key = os.getenv("DASHSCOPE_API_KEY") or ""
    if not api_key:
        return "DASHSCOPE_API_KEY is not set"

    model_id = os.getenv("DASHSCOPE_IMAGE_MODEL") or "wan2.7-image-pro"
    base_url = os.getenv("DASHSCOPE_BASE_URL") or "https://dashscope.aliyuncs.com/api/v1"
    base_url = base_url.rstrip("/")
    url = f"{base_url}/services/aigc/multimodal-generation/generation"

    content: list[dict] = []
    for image_path in reference_images:
        content.append({"image": _image_to_data_url(image_path)})
    content.append({"text": prompt})

    parameters: dict = {
        "size": _aspect_ratio_to_size(aspect_ratio),
        "watermark": _truthy_env("DASHSCOPE_WATERMARK", False),
        "thinking_mode": _truthy_env("DASHSCOPE_THINKING_MODE", True),
    }

    session = requests.Session()
    # session.trust_env = False # 允许读取环境变量中的代理配置

    payload: dict = {
        "model": model_id,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": content,
                }
            ]
        },
        "parameters": parameters,
    }

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
        return f"Network error calling DashScope Wan image API: {e}"

    if not response.ok:
        msg = response.text.strip()
        return f"DashScope Wan image API request failed (HTTP {response.status_code}). {msg}"

    try:
        data = response.json()
    except Exception:
        return "DashScope Wan image API returned non-JSON response"

    image_url = _extract_dashscope_image_url(data)
    if not image_url:
        return f"DashScope Wan image API returned unexpected response: {data}"

    if image_url.startswith("data:"):
        try:
            _, encoded = image_url.split(",", 1)
            img_bytes = base64.b64decode(encoded)
        except Exception:
            return "Failed to decode DashScope Wan image data URL"
        with open(output_file, "wb") as f:
            f.write(img_bytes)
        return f"Successfully generated image to {output_file}"

    try:
        r = session.get(image_url, timeout=300)
        r.raise_for_status()
        with open(output_file, "wb") as f:
            f.write(r.content)
        return f"Successfully generated image to {output_file}"
    except Exception as e:
        return f"Failed to download DashScope Wan image url: {e}"


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

    # Filter out invalid reference images
    valid_reference_images = []
    for ref_img in reference_images:
        if validate_image(ref_img):
            valid_reference_images.append(ref_img)
        else:
            print(f"Skipping invalid reference image: {ref_img}")
    
    if len(valid_reference_images) < len(reference_images):
        print(f"Note: {len(reference_images) - len(valid_reference_images)} reference image(s) were skipped due to validation failure.")
    
    if output_file.lower().endswith(".bin"):
        output_file = output_file[:-4] + ".jpg"
    return _generate_with_dashscope_wan(
        prompt=prompt,
        reference_images=valid_reference_images,
        output_file=output_file,
        aspect_ratio=aspect_ratio,
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate images using DashScope Wan API")
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
