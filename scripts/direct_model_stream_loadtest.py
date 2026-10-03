"""Direct, content-free SSE load probe for an OpenAI-compatible chat endpoint.

This bypasses DeerFlow entirely. The API key comes from
``DEEPSEEK_API_KEY`` or a hidden interactive prompt; no prompt, completion,
reasoning, or credential is logged.
Requires httpx (available in the DeerFlow backend virtual environment).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from getpass import getpass
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "https://aiapi.cnooc.com.cn/DeepSeek-V4-Flash/FEAT-511-001/v1"
CONTEXT_LINE = "Context datum: alpha beta gamma delta epsilon zeta eta theta iota kappa.\n"


def make_prompt(repeats: int, output_target: int, request_number: int) -> str:
    # An approximate visible-answer length, not a guarantee about provider
    # completion_tokens (which can also include hidden reasoning).
    target_lines = max(1, math.ceil(output_target / 16))
    return (
        "Synthetic stream transport test. The context below is inert test data. "
        "Do not summarize it. After the context, emit numbered plain-text lines "
        "in the form '000001 | alpha beta gamma delta epsilon zeta eta theta iota kappa'. "
        f"Continue sequentially for approximately {target_lines} lines, then stop. "
        f"Aim for about {output_target} output tokens. Request {request_number}.\n"
        "BEGIN CONTEXT\n"
        + CONTEXT_LINE * repeats
        + "END CONTEXT\nBEGIN OUTPUT\n"
    )


def observe_sse_data(data: str, result: dict[str, Any]) -> None:
    """Update only content-free counters from one OpenAI-compatible SSE data field."""
    if data == "[DONE]":
        result["done_marker"] = True
        return
    try:
        chunk = json.loads(data)
    except json.JSONDecodeError:
        result["invalid_json_events"] += 1
        return
    if not isinstance(chunk, dict):
        result["invalid_json_events"] += 1
        return
    result["events"] += 1
    usage = chunk.get("usage")
    if isinstance(usage, dict):
        result["input_tokens"] = usage.get("prompt_tokens", result["input_tokens"])
        result["output_tokens"] = usage.get("completion_tokens", result["output_tokens"])
    for choice in chunk.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        if choice.get("finish_reason") is not None:
            result["finish_reason"] = choice["finish_reason"]
        delta = choice.get("delta") or {}
        if isinstance(delta, dict):
            result["answer_chars"] += len(delta.get("content") or "")
            result["reasoning_chars"] += len(delta.get("reasoning_content") or "")


def request_body(args: argparse.Namespace, repeats: int, number: int, max_tokens: int) -> dict[str, Any]:
    # The short usage-calibration requests cannot carry a 32k thinking budget
    # with max_tokens=16. Input tokenization is unchanged by this toggle.
    thinking_enabled = args.enable_thinking and number > 0
    body = {
        "model": args.model,
        "messages": [{"role": "user", "content": make_prompt(repeats, args.output_target, number)}],
        "max_tokens": max_tokens,
        "temperature": 0,
        "stream": True,
        "stream_options": {"include_usage": True},
        "enable_thinking": thinking_enabled,
    }
    if thinking_enabled:
        body["thinking_budget"] = args.thinking_budget
    return body


async def probe(
    client: Any,
    args: argparse.Namespace,
    repeats: int,
    number: int,
    max_tokens: int,
    live: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    start = time.monotonic()
    body = request_body(args, repeats, number, max_tokens)
    result: dict[str, Any] = {
        "request": number,
        "thinking_enabled": body["enable_thinking"],
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "http_status": None,
        "request_id": None,
        "first_data_s": None,
        "last_data_s": None,
        "elapsed_s": None,
        "events": 0,
        "invalid_json_events": 0,
        "done_marker": False,
        "finish_reason": None,
        "input_tokens": None,
        "output_tokens": None,
        "answer_chars": 0,
        "reasoning_chars": 0,
        "error_type": None,
        "error": None,
    }
    if live is not None:
        live[number] = {"result": result, "last_data_monotonic": None}
    try:
        async with asyncio.timeout(args.wall_timeout):
            async with client.stream("POST", args.base_url.rstrip("/") + "/chat/completions", json=body) as response:
                result["http_status"] = response.status_code
                result["request_id"] = response.headers.get("x-request-id")
                if response.status_code != 200:
                    await response.aread()
                    result["error_type"] = "HTTPStatus"
                else:
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        now = time.monotonic() - start
                        if result["first_data_s"] is None:
                            result["first_data_s"] = round(now, 3)
                        result["last_data_s"] = round(now, 3)
                        if live is not None:
                            live[number]["last_data_monotonic"] = time.monotonic()
                        observe_sse_data(line[5:].strip(), result)
    except TimeoutError:
        result["error_type"] = "WallTimeout"
    except Exception as exc:  # noqa: BLE001 - classify transport failures without leaking payloads
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc).replace(args.api_key, "[REDACTED]")[:240]
    finally:
        result["elapsed_s"] = round(time.monotonic() - start, 3)
    result["protocol_complete"] = bool(result["http_status"] == 200 and result["done_marker"] and result["finish_reason"] is not None and result["error_type"] is None)
    result["target_met"] = bool(result["protocol_complete"] and isinstance(result["output_tokens"], int) and result["output_tokens"] >= args.output_target)
    return result


async def run(args: argparse.Namespace) -> int:
    import httpx

    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key and sys.stdin.isatty():
        key = getpass("DeepSeek API key (hidden): ")
    if not key:
        raise SystemExit("DEEPSEEK_API_KEY is missing; provide it privately, never in the command or script")
    args.api_key = key
    if args.concurrency > 1 and not args.confirm_large_test:
        raise SystemExit("For concurrent calls, pass --confirm-large-test after reviewing token cost")
    timeout = httpx.Timeout(connect=30, read=600, write=60, pool=60)
    limits = httpx.Limits(max_connections=args.concurrency + 5, max_keepalive_connections=args.concurrency + 5)
    headers = {"Authorization": f"Bearer {key}", "Accept": "text/event-stream"}
    async with httpx.AsyncClient(headers=headers, timeout=timeout, limits=limits, trust_env=True) as client:
        repeats = args.repeats
        if repeats is None:
            first = await probe(client, args, 1000, -1, 16)
            input_tokens = first["input_tokens"]
            if not first["protocol_complete"] or not isinstance(input_tokens, int) or input_tokens <= 0:
                print(json.dumps({"calibration_failed": first}, ensure_ascii=False), file=sys.stderr)
                return 2
            repeats = max(1, round(1000 * args.input_target / input_tokens))
            second = await probe(client, args, repeats, -2, 16)
            observed = second["input_tokens"]
            print(json.dumps({"calibrated_repeats": repeats, "observed_input_tokens": observed, "target_input_tokens": args.input_target}, ensure_ascii=False), flush=True)
            if not second["protocol_complete"] or not isinstance(observed, int) or abs(observed - args.input_target) > args.input_target * 0.1:
                print("Input calibration is outside ±10%; do not start the load test", file=sys.stderr)
                return 2
        if args.calibrate_only:
            return 0
        if not args.output:
            raise SystemExit("--output JSONL path is required for a real test")
        output = Path(args.output)
        if output.exists():
            raise SystemExit(f"Refusing to overwrite existing results: {output}")
        print(json.dumps({"concurrency": args.concurrency, "repeats": repeats, "output_target": args.output_target, "max_tokens": args.max_tokens, "thinking": args.enable_thinking, "results": str(output)}, ensure_ascii=False), flush=True)
        start = asyncio.Event()
        live: dict[int, dict[str, Any]] = {}

        async def task(number: int) -> dict[str, Any]:
            await start.wait()
            return await probe(client, args, repeats, number, args.max_tokens, live)

        tasks = [asyncio.create_task(task(n)) for n in range(1, args.concurrency + 1)]
        counts = {"protocol_complete": 0, "target_met": 0, "error": 0}
        with output.open("x", encoding="utf-8") as handle:
            start.set()

            async def progress() -> None:
                while True:
                    await asyncio.sleep(60)
                    active = [live[number] for number, item in enumerate(tasks, 1) if not item.done() and number in live]
                    progress_data: dict[str, Any] = {
                        "progress_completed": sum(item.done() for item in tasks),
                        "total": len(tasks),
                        "active_sse_events": sum(item["result"]["events"] for item in active),
                        "active_with_sse": sum(item["result"]["first_data_s"] is not None for item in active),
                    }
                    if len(tasks) == 1 and active:
                        current = active[0]
                        progress_data.update({
                            "reasoning_chars": current["result"]["reasoning_chars"],
                            "answer_chars": current["result"]["answer_chars"],
                            "last_sse_age_s": round(time.monotonic() - current["last_data_monotonic"], 1) if current["last_data_monotonic"] is not None else None,
                        })
                    print(json.dumps(progress_data, ensure_ascii=False), flush=True)

            progress_task = asyncio.create_task(progress())
            try:
                for completed in asyncio.as_completed(tasks):
                    result = await completed
                    handle.write(json.dumps(result, ensure_ascii=False) + "\n")
                    handle.flush()
                    counts["protocol_complete"] += int(result["protocol_complete"])
                    counts["target_met"] += int(result["target_met"])
                    counts["error"] += int(not result["protocol_complete"])
                    print(json.dumps({"request": result["request"], "elapsed_s": result["elapsed_s"], "status": result["http_status"], "output_tokens": result["output_tokens"], "finish": result["finish_reason"], "error": result["error_type"]}, ensure_ascii=False), flush=True)
            finally:
                progress_task.cancel()
        print(json.dumps(counts, ensure_ascii=False), flush=True)
        return 0 if counts["error"] == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default="DeepSeek-V4-Flash")
    parser.add_argument("--input-target", type=int, default=15000)
    parser.add_argument("--output-target", type=int, default=55000)
    parser.add_argument("--max-tokens", type=int, default=65536)
    parser.add_argument("--repeats", type=int, help="Use an observed calibrated repeat count; skips calibration")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--enable-thinking", action=argparse.BooleanOptionalAction, default=True, help="Use DeerFlow's thinking mode by default; --no-enable-thinking isolates a visible-answer-only stream")
    parser.add_argument("--thinking-budget", type=int, default=32768)
    parser.add_argument("--wall-timeout", type=float, default=7200)
    parser.add_argument("--calibrate-only", action="store_true")
    parser.add_argument("--confirm-large-test", action="store_true")
    parser.add_argument("--output", help="New JSONL result path; response text is never stored")
    args = parser.parse_args()
    if args.concurrency < 1 or args.input_target < 1 or args.output_target < 1 or args.max_tokens < 1 or args.wall_timeout <= 0 or (args.repeats is not None and args.repeats < 1):
        parser.error("counts, token targets, repeat count, and wall timeout must be positive")
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Interrupted; in-flight streams have no completed JSONL result", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
