import asyncio
import json
import tempfile
from pathlib import Path
from typing import Literal

from langchain.tools import tool

from deerflow.config import get_app_config
from deerflow.utils.file_conversion import CONVERTIBLE_EXTENSIONS, convert_file_to_markdown
from minio import Minio


def _get_minio_client(tool_name: str) -> tuple[Minio, str]:
    config = get_app_config().get_tool_config(tool_name)
    if config is None:
        raise ValueError(f"Tool config not found: {tool_name}")
    extra = config.model_extra or {}
    endpoint = extra.get("endpoint")
    access_key = extra.get("access_key")
    secret_key = extra.get("secret_key")
    bucket = extra.get("bucket")
    secure = extra.get("secure")
    if endpoint is None or access_key is None or secret_key is None or bucket is None:
        raise ValueError("MinIO tool requires endpoint/access_key/secret_key/bucket")
    if not str(endpoint).strip() or not str(access_key).strip() or not str(secret_key).strip() or not str(bucket).strip():
        raise ValueError("MinIO tool requires non-empty endpoint/access_key/secret_key/bucket")
    if isinstance(secure, str):
        secure = secure.strip().lower() in ("1", "true", "yes", "y")
    endpoint_str = str(endpoint).strip()
    inferred_secure: bool | None = None
    if endpoint_str.startswith("https://"):
        inferred_secure = True
        endpoint_str = endpoint_str.removeprefix("https://")
    elif endpoint_str.startswith("http://"):
        inferred_secure = False
        endpoint_str = endpoint_str.removeprefix("http://")
    endpoint_str = endpoint_str.split("/")[0]
    if secure is None:
        secure = inferred_secure if inferred_secure is not None else False
    client = Minio(endpoint_str, access_key=access_key, secret_key=secret_key, secure=secure)
    return client, str(bucket).strip()


@tool("minio_list", parse_docstring=True)
def minio_list_tool(prefix: str = "", suffix: str | None = None, max_results: int = 50) -> str:
    """List objects in a MinIO bucket.

    Args:
        prefix: Object key prefix to search under (e.g. "docs/2026/").
        suffix: Optional file suffix filter (e.g. ".pdf", ".docx", ".md").
        max_results: Maximum number of results to return.
    """
    client, bucket = _get_minio_client("minio_list")
    results: list[dict] = []
    for obj in client.list_objects(bucket, prefix=prefix or "", recursive=True):
        key = str(obj.object_name or "")
        if suffix and not key.lower().endswith(str(suffix).lower()):
            continue
        last_modified_dt = getattr(obj, "last_modified", None)
        results.append(
            {
                "key": key,
                "size": obj.size,
                "last_modified": last_modified_dt.isoformat() if last_modified_dt else None,
                "etag": obj.etag,
                "content_type": getattr(obj, "content_type", None),
            }
        )
        if len(results) >= max_results:
            break
    return json.dumps(results, ensure_ascii=False, indent=2)


@tool("minio_fetch", parse_docstring=True)
def minio_fetch_tool(
    key: str,
    return_format: str = "markdown",
    max_chars: int = 12000,
    pdf_fallback: Literal["auto", "docling", "native", "none"] = "native",
    docling_do_ocr: Literal["auto", "off", "on"] = "auto",
    docling_max_pages: int | None = 80,
    docling_pages_per_part: int = 20,
    docling_images_scale: float = 2.0,
    max_media: int = 40,
) -> str:
    """Fetch an object from MinIO and return its contents.

    Args:
        key: Object key in the bucket (e.g. "docs/report.pdf").
        return_format: "markdown" or "text". For PDF/DOC/DOCX/PPT/XLS it will be converted to Markdown automatically.
        max_chars: Maximum characters to return.
        pdf_fallback: Reserved for backward compatibility. Docling is disabled in this build.
        docling_do_ocr: Reserved for backward compatibility.
        docling_max_pages: For PDF return_format="text", limits pages extracted via pypdf.
        docling_pages_per_part: Reserved for backward compatibility.
        docling_images_scale: Reserved for backward compatibility.
        max_media: Reserved for backward compatibility.
    """
    _ = (pdf_fallback, docling_do_ocr, docling_pages_per_part, docling_images_scale, max_media)
    client, bucket = _get_minio_client("minio_fetch")

    def read_text_best_effort(path: Path) -> str:
        data = path.read_bytes()
        for enc in ("utf-8", "utf-8-sig", "gb18030", "utf-16", "latin-1"):
            try:
                text = data.decode(enc)
            except Exception:
                continue
            if enc.startswith("utf-8") and text.count("\ufffd") > max(20, len(text) // 50):
                continue
            return text
        return data.decode("utf-8", errors="replace")

    def is_probably_text_content_type(content_type: str | None) -> bool:
        if not content_type:
            return False
        ct = str(content_type).lower()
        if ct.startswith("text/"):
            return True
        return ct in {
            "application/json",
            "application/xml",
            "application/x-yaml",
            "application/yaml",
            "application/javascript",
        }

    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        filename = Path(key).name or "object"
        local_path = tmp_dir / filename

        stat = client.stat_object(bucket, key)
        content_type = getattr(stat, "content_type", None)

        response = client.get_object(bucket, key)
        try:
            with local_path.open("wb") as f:
                for chunk in response.stream(1024 * 1024):
                    f.write(chunk)
        finally:
            response.close()
            response.release_conn()

        ext = local_path.suffix.lower()
        want_md = return_format.lower() == "markdown"

        if want_md and ext in CONVERTIBLE_EXTENSIONS:
            md_path = asyncio.run(convert_file_to_markdown(local_path))
            if md_path is None:
                text = f"[Conversion failed] Unable to convert '{filename}' ({ext}) to markdown."
            else:
                text = md_path.read_text(encoding="utf-8", errors="replace")
        else:
            text_exts = {".txt", ".md", ".csv", ".log", ".json", ".yaml", ".yml", ".xml", ".html", ".htm"}
            if ext in text_exts or is_probably_text_content_type(content_type):
                text = read_text_best_effort(local_path)
            elif ext == ".pdf" and return_format.lower() == "text":
                from pypdf import PdfReader

                reader = PdfReader(str(local_path))
                parts: list[str] = []
                total = len(reader.pages)
                limit = min(total, int(docling_max_pages)) if docling_max_pages is not None and int(docling_max_pages) > 0 else total
                for i in range(limit):
                    parts.append(reader.pages[i].extract_text() or "")
                text = "\n\n".join(parts)
            else:
                text = f"[Unsupported direct read] '{filename}' ({ext or 'noext'}) is binary. Use return_format='markdown'."

    if len(text) > max_chars:
        text = text[:max_chars]
    return text
