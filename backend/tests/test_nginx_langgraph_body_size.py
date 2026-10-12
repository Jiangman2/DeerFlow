"""Regression coverage for issue #3952: long chat prompts through nginx's
``/api/langgraph/`` route failing with a raw HTTP 500 (or 413) before the
request ever reaches Gateway's application-level error handling.

Root cause (confirmed by a live nginx reproduction during triage, not just
config reading): nginx's defaults are unfit for a text-chat proxy route.

- ``client_max_body_size`` defaults to ``1m``, well under a long pasted
  prompt -- nginx rejects anything larger with a 413 before the body is
  even read.
- ``proxy_request_buffering`` defaults to ``on``, which spools any request
  body larger than the in-memory ``client_body_buffer_size`` (~16k) to a
  temp file (``client_body_temp``, under the nginx ``-p`` prefix) before
  proxying it upstream. On a non-root local run -- the common ``make dev``
  case, and the same class of problem already documented elsewhere in these
  same config files for *response* buffering -- that temp directory can be
  unwritable, which makes nginx fail the request with a raw
  "500 Internal Server Error" page and a "Permission denied" line in the
  error log, matching the reporter's exact symptom (nginx error page, no
  DeerFlow JSON/SSE error) and reproduced independently while diagnosing
  this issue.

The uploads location (``/api/threads/{id}/uploads``) already carries both
settings for file uploads. This locks the same settings onto
``/api/langgraph/`` for chat prompts, sized for text rather than binary
uploads (see the range check below), across the upstream Docker and Helm
configs plus the enterprise local-PVC production config.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
NGINX_CONFIGS = (
    "docker/nginx/nginx.conf",
    "docker/nginx/nginx.local.conf",
    "deploy/helm/deer-flow/templates/configmap-nginx.yaml",
    "k8s/local-pvc/nginx.conf",
)

# Text prompts never carry binary file attachments (those go through the
# dedicated uploads route), so the ceiling here is intentionally well below
# the uploads route's 100M -- generous for even a very long pasted document,
# while avoiding needlessly letting one chat request force Gateway to buffer
# and JSON-parse an arbitrarily large body in memory.
_MIN_EXPECTED_BODY_SIZE_BYTES = 5 * 1024 * 1024
_MAX_EXPECTED_BODY_SIZE_BYTES = 100 * 1024 * 1024

_SIZE_MULTIPLIERS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}

# Model-bound Gateway requests may legitimately remain silent while a whole
# model call, run, or per-file Skill security scan is in progress.
_MIN_BLOCKING_READ_TIMEOUT_SECONDS = 600

_DURATION_MULTIPLIERS = {"": 1, "s": 1, "m": 60, "h": 3600}


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _extract_location_block(content: str, location_selector: str) -> str:
    """Extract a single nginx ``location <location_selector> { ... }`` block
    by brace-depth matching, so assertions target only that location and
    can't be satisfied by a directive that merely appears elsewhere in the
    file (e.g. the neighboring uploads location, which already has both
    settings and must not make the langgraph-route assertions pass by
    accident)."""
    marker = re.compile(r"location\s+" + re.escape(location_selector) + r"\s*\{")
    match = marker.search(content)
    assert match, f"could not find `location {location_selector}` block"

    start = match.end() - 1  # index of the opening brace
    depth = 0
    for i, ch in enumerate(content[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return content[start : i + 1]

    raise AssertionError(f"unbalanced braces in `location {location_selector}` block")


def _parse_body_size_bytes(block: str) -> int:
    match = re.search(r"client_max_body_size\s+(\d+)\s*([mMkKgG]?)\s*;", block)
    assert match, "client_max_body_size value not found or not parseable"
    value, unit = match.groups()
    return int(value) * _SIZE_MULTIPLIERS[unit.lower()]


def _parse_read_timeout_seconds(block: str) -> int:
    # Anchor at line start so a commented-out directive cannot satisfy the test.
    match = re.search(r"^\s*proxy_read_timeout\s+(\d+)([smh]?)\s*;", block, re.M)
    assert match, "no active proxy_read_timeout directive"
    value, unit = match.groups()
    return int(value) * _DURATION_MULTIPLIERS[unit]


@pytest.mark.parametrize("path", NGINX_CONFIGS)
def test_langgraph_route_disables_request_buffering(path):
    content = _read(path)
    block = _extract_location_block(content, "/api/langgraph/")

    assert "proxy_request_buffering off;" in block, (
        f"{path}: /api/langgraph/ does not disable request buffering, so nginx "
        "spools long chat-prompt request bodies to a temp file before proxying "
        "them to Gateway, which can 500 with a permission error on non-root "
        "local runs -- see issue #3952"
    )


@pytest.mark.parametrize("path", NGINX_CONFIGS)
def test_langgraph_route_raises_body_size_limit_for_text_prompts(path):
    content = _read(path)
    block = _extract_location_block(content, "/api/langgraph/")

    size_bytes = _parse_body_size_bytes(block)

    assert _MIN_EXPECTED_BODY_SIZE_BYTES <= size_bytes <= _MAX_EXPECTED_BODY_SIZE_BYTES, (
        f"{path}: /api/langgraph/ client_max_body_size is {size_bytes} bytes, "
        f"expected between {_MIN_EXPECTED_BODY_SIZE_BYTES} and "
        f"{_MAX_EXPECTED_BODY_SIZE_BYTES} bytes -- comfortably above nginx's 1m "
        "default (the actual bug) but below the uploads route's 100M, since "
        "this route only ever carries JSON chat text, never binary files"
    )


@pytest.mark.parametrize("path", NGINX_CONFIGS)
def test_uploads_route_still_has_its_own_body_size_settings(path):
    """Non-regression guard: confirms the extraction helper is precise (the
    uploads location has both directives with its own, larger value) and that
    fixing the langgraph route does not accidentally touch the upload route's
    existing configuration."""
    content = _read(path)
    block = _extract_location_block(content, r"~ ^/api/threads/[^/]+/uploads")

    assert "client_max_body_size 100M;" in block
    assert "proxy_request_buffering off;" in block


@pytest.mark.parametrize("path", NGINX_CONFIGS)
def test_threads_route_outlasts_blocking_gateway_calls(path):
    """Thread compaction, suggestions, and run waits can exceed 60 seconds."""
    content = _read(path)
    block = _extract_location_block(content, "~ ^/api/threads")

    timeout_seconds = _parse_read_timeout_seconds(block)

    assert timeout_seconds >= _MIN_BLOCKING_READ_TIMEOUT_SECONDS, f"{path}: the generic /api/threads location allows {timeout_seconds}s, expected at least {_MIN_BLOCKING_READ_TIMEOUT_SECONDS}s"


@pytest.mark.parametrize("path", NGINX_CONFIGS)
def test_skills_prefix_outlasts_the_llm_security_scan(path):
    """Skill install/edit/rollback can run one LLM security scan per file."""
    content = _read(path)
    block = _extract_location_block(content, "/api/skills")

    timeout_seconds = _parse_read_timeout_seconds(block)

    assert timeout_seconds >= _MIN_BLOCKING_READ_TIMEOUT_SECONDS, f"{path}: /api/skills allows {timeout_seconds}s, expected at least {_MIN_BLOCKING_READ_TIMEOUT_SECONDS}s"


@pytest.mark.parametrize("path", NGINX_CONFIGS)
def test_api_catchall_outlasts_blocking_gateway_calls(path):
    """Run waits and input-polish model calls fall through to this route."""
    content = _read(path)
    block = _extract_location_block(content, "/api/")

    timeout_seconds = _parse_read_timeout_seconds(block)

    assert timeout_seconds >= _MIN_BLOCKING_READ_TIMEOUT_SECONDS, f"{path}: the /api/ catch-all allows {timeout_seconds}s, expected at least {_MIN_BLOCKING_READ_TIMEOUT_SECONDS}s"
