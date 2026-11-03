"""Reject known raster images before a text-read request or sandbox startup.

This is a format guard, not an authorization check or an image decoder. SVG
remains readable source text. A renamed binary still needs the decoder guard.
"""

import posixpath

RASTER_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif", ".heic", ".heif", ".ico"})
IMAGE_READ_GUIDANCE = (
    "If view_image is available in this run, use it for supported images. "
    "If it is unavailable, use an existing text/OCR companion or the original source data; "
    "when visual inspection is essential, explain the limitation and request a vision-capable model. "
    "Do not call unavailable tools, interpret cat/base64 output as an image, or claim visual verification."
)


def image_text_read_error(path: str) -> str | None:
    if posixpath.splitext(path.replace("\\", "/"))[1].lower() not in RASTER_IMAGE_SUFFIXES:
        return None
    return "Error: READ_FILE_IMAGE_UNSUPPORTED. read_file reads UTF-8 text, not raster images. Do not retry read_file on this image; no file content was read. " + IMAGE_READ_GUIDANCE
