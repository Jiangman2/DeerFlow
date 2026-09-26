"""Fail-closed preview-only edits for the three audited live image trees.

This script runs during an offline image build. It never edits host files.
"""

import argparse
import ast
import hashlib
from pathlib import Path


BASELINES = {
    "gateway": (
        "app/gateway/routers/artifacts.py",
        "25db8c3d4b98686aea770e45f22a750354d4af2b70d32f1501b08f05c113b12f",
    ),
    "runner": (
        "app/gateway/routers/artifacts.py",
        "8c05d60d67b259e3286fcea33e558d4325c401f66c25c90aa7d847eb782d40e0",
    ),
    "frontend": (
        "src/components/workspace/messages/markdown-link.tsx",
        "a11c882b53936ee34489a5ffeda5db3daec1df3b6c9be95381d775e0aa2c3164",
    ),
}


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise ValueError(f"{label}: expected exactly one source occurrence, found {count}")
    return source.replace(old, new, 1)


def patch_backend(source: str) -> str:
    source = replace_once(
        source,
        "from app.gateway.path_utils import resolve_thread_virtual_path\n",
        "from app.gateway.path_utils import resolve_thread_virtual_path\n"
        "from deerflow.config.app_config import get_app_config\n",
        "config import",
    )
    source = replace_once(
        source,
        "    forward_headers: dict[str, str] = dict(sandbox_headers)\n"
        "    for header_name in (\"accept\", \"content-type\", \"if-none-match\", \"if-modified-since\"):\n",
        "    forward_headers: dict[str, str] = dict(sandbox_headers)\n"
        "    if sandbox_headers:\n"
        "        # Authenticate to the AIO Nginx with the server-owned key, never\n"
        "        # with a browser credential. The preview port remains fixed.\n"
        "        sandbox_api_key = get_app_config().sandbox.sandbox_api_key\n"
        "        if not sandbox_api_key:\n"
        "            raise HTTPException(status_code=503, detail=\"Live Preview authentication is not configured\")\n"
        "        forward_headers[\"authorization\"] = f\"Bearer {sandbox_api_key}\"\n"
        "    for header_name in (\"accept\", \"content-type\", \"if-none-match\", \"if-modified-since\"):\n",
        "sandbox bearer",
    )
    old = "async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:"
    if source.count(old) != 2:
        raise ValueError("Expected exactly two Live Preview httpx clients")
    return source.replace(old, "async with httpx.AsyncClient(follow_redirects=False, timeout=timeout, trust_env=False) as client:")


def patch_frontend(source: str) -> str:
    source = replace_once(
        source,
        "  if (!href || !threadId) {\n    return null;\n  }\n  let parsed: URL;\n  try {\n    parsed = new URL(href);\n",
        "  if (!href || !threadId) {\n    return null;\n  }\n"
        "  // Autolinkers can absorb Chinese prose after an exact loopback origin.\n"
        "  const previewOrigin =\n"
        "    /^https?:\\/\\/(?:127\\.0\\.0\\.1|localhost|\\[::1\\]):5050(?=$|[/?#。！？；，])/i.exec(\n"
        "      href,\n"
        "    )?.[0];\n"
        "  const suffix = previewOrigin ? href.slice(previewOrigin.length) : \"\";\n"
        "  const candidate =\n"
        "    previewOrigin && /^[。！？；，]/.test(suffix) ? `${previewOrigin}/` : href;\n"
        "  let parsed: URL;\n  try {\n    parsed = new URL(candidate);\n",
        "loopback resolver",
    )
    source = replace_once(
        source,
        "  return `${prefix}${parsed.pathname || \"/\"}${parsed.search}`;",
        "  return `${prefix}${parsed.pathname || \"/\"}${parsed.search}${parsed.hash}`;",
        "preview URL fragment",
    )
    source = replace_once(
        source,
        "    if (href !== undefined && !isSafeHref(href)) {",
        "    const livePreviewURL = resolveLivePreviewURL(href, threadId);\n"
        "    if (href !== undefined && !isSafeHref(href) && !livePreviewURL) {",
        "allowlisted preview before generic URL rejection",
    )
    source = replace_once(
        source,
        "    const livePreviewURL = resolveLivePreviewURL(href, threadId);\n"
        "    if (livePreviewURL) {",
        "    if (livePreviewURL) {",
        "remove duplicate resolver",
    )
    source = replace_once(
        source,
        "    if (livePreviewURL) {\n      const { className, target, rel, children, ...rest } = props;\n"
        "      const visibleChildren =",
        "    if (livePreviewURL) {\n      const { className, target, rel, children, ...rest } = props;\n"
        "      const trailingProse =\n"
        "        typeof children === \"string\"\n"
        "          ? /^https?:\\/\\/(?:127\\.0\\.0\\.1|localhost|\\[::1\\]):5050([。！？；，].+)$/i.exec(\n"
        "              children,\n"
        "            )?.[1]\n"
        "          : undefined;\n"
        "      const visibleChildren =",
        "preserve Chinese trailing prose",
    )
    source = replace_once(
        source,
        "      return (\n        <a\n          {...rest}\n          href={livePreviewURL}",
        "      const previewLink = (\n        <a\n          {...rest}\n          href={livePreviewURL}",
        "preview anchor",
    )
    source = replace_once(
        source,
        "          {visibleChildren}\n        </a>\n      );\n    }",
        "          {visibleChildren}\n        </a>\n      );\n"
        "      return trailingProse ? (\n"
        "        <>\n          {previewLink}\n          {trailingProse}\n        </>\n"
        "      ) : (\n        previewLink\n      );\n    }",
        "preview prose outside anchor",
    )
    return source


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("component", choices=BASELINES)
    parser.add_argument("root", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    relative, baseline = BASELINES[args.component]
    root = args.root.resolve(strict=True)
    target = (root / relative).resolve(strict=True)
    if root not in target.parents or not target.is_file():
        raise ValueError("Unsafe or missing target")
    raw = target.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != baseline:
        raise ValueError(f"{args.component}: audited source mismatch: {actual}")
    if b"\r" in raw:
        raise ValueError("Unexpected CRLF source")
    source = raw.decode("utf-8")
    patched = patch_frontend(source) if args.component == "frontend" else patch_backend(source)
    if args.component != "frontend":
        ast.parse(patched, filename=relative)
    if patched == source:
        raise ValueError("No changes produced")
    output = patched.encode("utf-8")
    if not args.check_only:
        target.write_bytes(output)
    print(
        f"preview_delta=PASS component={args.component} source={actual} "
        f"target={hashlib.sha256(output).hexdigest()} mode={'check' if args.check_only else 'apply'}"
    )


if __name__ == "__main__":
    main()
