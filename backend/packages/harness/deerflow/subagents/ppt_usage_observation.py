"""Fixed metadata only; never a billing or model callback."""
from typing import Any
import re

def observed_session_usage(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Only fixed, reported root/Worker metadata; never journal/billing input."""

    raw = payload.get("session_usage_observation")
    semantics = "session_cumulative_not_current_turn_or_invoice"
    if not isinstance(raw, dict) or raw.get("semantics") != semantics:
        return None

    def counter(value: object) -> dict[str, Any] | None:
        if not isinstance(value, dict) or value.get("source") != "codex_session_token_count" or value.get("cli_version") != "0.139.0":
            return None
        identifier = value.get("session_id_hash")
        counts = value.get("usage")
        names = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens")
        if (not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{16}", identifier) or not isinstance(counts, dict)
                or any(type(counts.get(name)) is not int or not 0 <= counts[name] < 2 ** 53 for name in names)):
            return None
        if (counts["total_tokens"] != counts["input_tokens"] + counts["output_tokens"]
                or counts["cached_input_tokens"] > counts["input_tokens"] or counts["reasoning_output_tokens"] > counts["output_tokens"]):
            return None
        return {"source": "codex_session_token_count", "cli_version": "0.139.0", "session_id_hash": identifier, "usage": {name: counts[name] for name in names}}

    parent = counter(raw.get("parent"))
    workers = []
    seen = {parent["session_id_hash"]} if parent else set()
    raw_workers = raw.get("workers")
    if isinstance(raw_workers, list):
        for value in raw_workers[:7]:
            normalized = counter(value)
            if normalized is not None and normalized["session_id_hash"] not in seen:
                workers.append(normalized)
                seen.add(normalized["session_id_hash"])
    count = raw.get("worker_count")
    count = count if type(count) is int and 0 <= count <= 64 else None
    return {"semantics": semantics, "parent": parent, "workers": workers, "worker_count": count,
            "partial": raw.get("partial") is True or parent is None or count is None or len(workers) != count}
