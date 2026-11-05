"""Opt-in paid PDF diagnostic: seven DeepSeek fetches and four control fetches.

Uses the real registered adapters. Public origin probes never receive API keys.
No model calls, automatic retries, production changes, or provider fallback.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import time

import httpx

from benchmark_search_fetch_adapters import build_node, invoke, provider_config
from deerflow.config.app_config import pop_current_app_config, push_current_app_config

PDF_PATH = "/tzzgx/dqbg/nb/202503/P020250327605146009602.pdf"
HTML_PATH = "/xwzx/gsxw/2025/202503/t20250327_114400.html"
CASES = [
    ("cnooc_ltd_pdf", "https://ltd.cnooc.com.cn" + PDF_PATH),
    ("cnooc_www_pdf", "https://www.cnoocltd.com" + PDF_PATH),
    ("cnooc_ltd_html", "https://ltd.cnooc.com.cn" + HTML_PATH),
    ("cnooc_www_html", "https://www.cnoocltd.com" + HTML_PATH),
    (
        "w3c_dummy_pdf",
        "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf",
    ),
    ("arxiv_pdf", "https://arxiv.org/pdf/1706.03762"),
    ("arxiv_abstract_html", "https://arxiv.org/abs/1706.03762"),
]
CONTROLS = {"cnooc_ltd_pdf", "cnooc_www_pdf"}


def contains_energy_values(content):
    # Markdown may escape decimal points; this is a lexical marker, not fact scoring.
    text = content.replace("\\.", ".")
    return "726.8" in text and "7.2" in text


def origin_probe(case):
    """Fixed public URL inventory only; no auth, proxy env, redirects or retries."""
    sample_id, url = case
    started = time.monotonic()
    record = {
        "sample_id": sample_id,
        "url": url,
        "layer": "origin-direct-no-redirect",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    digest = hashlib.sha256()
    size = 0
    prefix = bytearray()
    try:
        with httpx.Client(
            timeout=httpx.Timeout(20, connect=8),
            trust_env=False,
            follow_redirects=False,
        ) as client:
            with client.stream("GET", url) as response:
                record.update(
                    http_status=response.status_code,
                    content_type=response.headers.get("content-type"),
                    content_length=response.headers.get("content-length"),
                    location=response.headers.get("location"),
                )
                for chunk in response.iter_bytes(65536):
                    if size + len(chunk) > 24 * 1024 * 1024:
                        record["stop_reason"] = "24MiB_limit"
                        break
                    if time.monotonic() - started > 30:
                        record["stop_reason"] = "30s_deadline"
                        break
                    digest.update(chunk)
                    size += len(chunk)
                    if len(prefix) < 16:
                        prefix.extend(chunk[: 16 - len(prefix)])
                else:
                    record.update(complete=True, sha256=digest.hexdigest())
        record.update(body_bytes=size, pdf_magic=bytes(prefix).startswith(b"%PDF-"))
    except Exception as exc:
        record["error_class"] = type(exc).__name__
    record["elapsed_s"] = round(time.monotonic() - started, 3)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    keys = json.loads(args.key_file.read_text(encoding="utf-8-sig"))
    secrets = {
        p: keys[p.upper() + "_API_KEY"].strip() for p in ("deepseek", "tavily", "exa")
    }
    if not all(secrets.values()):
        raise ValueError("All three provider credentials are required")
    args.output.mkdir(parents=True, exist_ok=False)

    def save(filename, row):
        encoded = json.dumps(row, ensure_ascii=False, indent=2)
        for secret in secrets.values():
            encoded = encoded.replace(secret, "[REDACTED]")
        (args.output / filename).write_text(encoded, encoding="utf-8")

    records = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for row in pool.map(origin_probe, CASES):
            save("origin-" + row["sample_id"] + ".json", row)
            print(json.dumps(row), flush=True)
    for provider, key in secrets.items():
        config = provider_config(provider, key)
        push_current_app_config(config)
        try:
            node = build_node(config)
            for sample_id, url in CASES:
                if provider != "deepseek" and sample_id not in CONTROLS:
                    continue
                row = invoke(node, provider, "fetch", sample_id, url)
                content = row.get("content", "")
                row["contains_energy_values"] = contains_energy_values(content)
                if content.startswith("Error: "):
                    try:
                        row["structured_error"] = json.loads(
                            content.removeprefix("Error: ")
                        )
                    except ValueError:
                        pass
                save(f"{provider}-{sample_id}.json", row)
                summary = {
                    k: row.get(k)
                    for k in (
                        "provider",
                        "sample_id",
                        "success",
                        "elapsed_s",
                        "output_chars",
                        "contains_energy_values",
                        "structured_error",
                        "error",
                    )
                }
                records.append(summary)
                print(json.dumps(summary), flush=True)
        finally:
            pop_current_app_config()
    save("summary.json", records)


if __name__ == "__main__":
    main()
