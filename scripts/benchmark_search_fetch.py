"""Bounded, paid opt-in smoke comparison of three official Search/Fetch APIs.

Run with --key-file pointing to an ignored JSON file, or set provider key env vars.
Never prints keys, headers, raw errors, or response bodies. Raw results go to outputs/.
This tests provider APIs, not DeerFlow adapters; no model or automatic retry is used.
"""

import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import statistics
import time
from datetime import datetime, timezone

import requests


SEARCHES = [
    ("cn_energy", "中国海油 2024 年报 油气净产量"),
    ("cn_policy", "海上风电开发建设管理办法 国家能源局"),
    ("en_research", "offshore wind turbine predictive maintenance review"),
    ("en_documentation", "Python asyncio TaskGroup cancellation documentation"),
    ("open_source", "bytedance DeerFlow GitHub architecture"),
]
PAGES = [
    ("cn_policy", "https://www.nea.gov.cn/2011-11/21/c_131260222.htm"),
    ("cn_report_pdf", "https://ltd.cnooc.com.cn/tzzgx/dqbg/nb/202503/P020250327605146009602.pdf"),
    ("en_documentation", "https://docs.python.org/3/library/asyncio-task.html"),
    ("en_kubernetes", "https://kubernetes.io/docs/concepts/overview/"),
    ("open_source", "https://github.com/bytedance/deer-flow"),
]
PROVIDERS = {
    "tavily": ("TAVILY_API_KEY", "https://api.tavily.com/search", "https://api.tavily.com/extract"),
    "deepseek": ("DEEPSEEK_API_KEY", "https://api.deepseek.com/v1/tools/search", "https://api.deepseek.com/v1/tools/fetch"),
    "exa": ("EXA_API_KEY", "https://api.exa.ai/search", "https://api.exa.ai/contents"),
}
MAX_BODY = 2 * 1024 * 1024
CONTENT_LIMIT = 20000


def payload(provider, kind, value):
    if kind == "search":
        if provider == "tavily":
            return {"query": value, "max_results": 5, "search_depth": "basic", "include_answer": False,
                    "include_raw_content": False, "include_usage": True}
        if provider == "deepseek":
            return {"query": value, "count": 5, "need_content": False}
        return {"query": value, "numResults": 5, "type": "auto", "contents": {"highlights": {"maxCharacters": 1000}}}
    if provider == "tavily":
        return {"urls": [value], "extract_depth": "basic", "format": "markdown", "include_usage": True}
    if provider == "deepseek":
        return {"url": value}
    return {"urls": [value], "text": {"maxCharacters": CONTENT_LIMIT}}


def normalize(provider, kind, data):
    if kind == "search":
        items = []
        for row in data.get("results", []):
            snippet = row.get("summary") or row.get("snippet") or row.get("content") or "\n".join(row.get("highlights") or [])
            items.append({"title": row.get("title", ""), "url": row.get("url", ""), "snippet": snippet})
        return {"items": items, "result_count": len(items), "nonempty": bool(items)}
    if provider == "deepseek":
        page = data
    else:
        page = next(iter(data.get("results") or []), {})
    content = page.get("raw_content") or page.get("text") or page.get("content") or ""
    # Store raw response separately, but compare a common model-facing character budget.
    return {"title": page.get("title", ""), "content_chars": len(content),
            "returned_content": content[:CONTENT_LIMIT], "truncated": len(content) > CONTENT_LIMIT,
            "nonempty": bool(content.strip()), "over_200_chars": len(content.strip()) >= 200,
            "page_statuses": data.get("statuses"), "failed_results": data.get("failed_results")}


def request_one(session, provider, key, kind, sample_id, value):
    started = time.perf_counter()
    record = {"provider": provider, "kind": kind, "sample_id": sample_id, "input": value,
              "started_at": datetime.now(timezone.utc).isoformat(), "http_status": None}
    endpoint = PROVIDERS[provider][1 if kind == "search" else 2]
    headers = {"Content-Type": "application/json"}
    headers["x-api-key" if provider == "exa" else "Authorization"] = key if provider == "exa" else f"Bearer {key}"
    try:
        with session.post(endpoint, headers=headers, json=payload(provider, kind, value),
                          timeout=(10, 35), allow_redirects=False, stream=True) as response:
            record["http_status"] = response.status_code
            chunks = bytearray()
            for chunk in response.iter_content(16384):
                chunks.extend(chunk)
                if len(chunks) > MAX_BODY:
                    raise ValueError("response_size_limit")
                if time.perf_counter() - started > 60:
                    raise TimeoutError("total_deadline")
            record["response_bytes"] = len(chunks)
            if response.status_code == 200:
                data = json.loads(chunks)
                record["raw_response"] = data
                record.update(normalize(provider, kind, data))
                record["usage"] = data.get("usage")
                record["cost_dollars"] = data.get("costDollars")
            else:
                # Provider error bodies may echo credentials; deliberately do not persist them.
                record["error"] = f"HTTP_{response.status_code}"
    except Exception as exc:
        # Exception messages can include URLs or headers; keep only the safe class name.
        record["error"] = type(exc).__name__
    record["elapsed_s"] = round(time.perf_counter() - started, 3)
    return record


def run_provider(provider, key, output):
    records = []
    session = requests.Session()
    session.trust_env = False
    try:
        for kind, samples in (("search", SEARCHES), ("fetch", PAGES)):
            for sample_id, value in samples:
                row = request_one(session, provider, key, kind, sample_id, value)
                records.append(row)
                # Defensive redaction across every persisted field, including provider content.
                encoded = json.dumps(row, ensure_ascii=False).replace(key, "[REDACTED]")
                (output / f"{provider}-{kind}-{sample_id}.json").write_text(encoded, encoding="utf-8")
                print(json.dumps({k: row.get(k) for k in ("provider", "kind", "sample_id", "http_status",
                      "elapsed_s", "nonempty", "result_count", "content_chars", "error")}), flush=True)
                if row.get("http_status") in (401, 402, 403):
                    return records
    finally:
        session.close()
    return records


def summarize(records):
    summary = []
    for provider in PROVIDERS:
        for kind in ("search", "fetch"):
            rows = [r for r in records if r["provider"] == provider and r["kind"] == kind]
            latencies = [r["elapsed_s"] for r in rows]
            successful_latencies = [r["elapsed_s"] for r in rows if r.get("nonempty")]
            summary.append({"provider": provider, "kind": kind, "planned": 5, "attempted": len(rows),
                            "http_200": sum(r.get("http_status") == 200 for r in rows),
                            "nonempty": sum(bool(r.get("nonempty")) for r in rows),
                            "median_s": round(statistics.median(latencies), 3) if latencies else None,
                            "success_median_s": round(statistics.median(successful_latencies), 3) if successful_latencies else None,
                            "max_s": max(latencies) if latencies else None})
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    keys = json.loads(args.key_file.read_text(encoding="utf-8-sig")) if args.key_file else os.environ
    missing = [name for name, *_ in PROVIDERS.values() if not keys.get(name, "").strip()]
    if missing:
        raise SystemExit("Missing key variable(s): " + ", ".join(missing))
    args.output.mkdir(parents=True, exist_ok=False)
    all_records = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(run_provider, p, keys[v[0]].strip(), args.output) for p, v in PROVIDERS.items()]
        for future in concurrent.futures.as_completed(futures):
            all_records.extend(future.result())
    summary = summarize(all_records)
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("SUMMARY " + json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
