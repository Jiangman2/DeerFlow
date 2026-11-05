import importlib.util
import sys
from pathlib import Path

import pytest
import requests

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "public"
    / "systematic-literature-review"
    / "scripts"
    / "arxiv_search.py"
)
SPEC = importlib.util.spec_from_file_location(
    "systematic_literature_review_arxiv",
    SCRIPT_PATH,
)
arxiv = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = arxiv
SPEC.loader.exec_module(arxiv)


class Response:
    def __init__(self, status_code=200, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


EMPTY_FEED = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"></feed>
"""


def test_rate_limit_retry_is_bounded_and_caps_retry_after(monkeypatch):
    responses = [
        Response(429, headers={"Retry-After": "120"}),
        Response(200, text=EMPTY_FEED),
    ]
    sleeps = []
    monkeypatch.setattr(arxiv.requests, "get", lambda *args, **kwargs: responses.pop(0))
    monkeypatch.setattr(arxiv.time, "sleep", sleeps.append)

    result = arxiv.search("graph neural networks", max_attempts=3)

    assert result == []
    assert sleeps == [arxiv.BACKOFF_MAX_SECONDS]


def test_rate_limit_stops_after_final_attempt(monkeypatch):
    calls = []
    sleeps = []

    def always_limited(*args, **kwargs):
        calls.append(kwargs)
        return Response(429, headers={"Retry-After": "1"})

    monkeypatch.setattr(arxiv.requests, "get", always_limited)
    monkeypatch.setattr(arxiv.time, "sleep", sleeps.append)

    with pytest.raises(requests.HTTPError, match="HTTP 429"):
        arxiv.search("retrieval augmented generation", max_attempts=3)

    assert len(calls) == 3
    assert sleeps == [1.0, 1.0]


def test_timeout_stops_after_final_attempt(monkeypatch):
    calls = []
    sleeps = []

    def always_times_out(*args, **kwargs):
        calls.append(kwargs)
        raise requests.Timeout("arXiv timed out")

    monkeypatch.setattr(arxiv.requests, "get", always_times_out)
    monkeypatch.setattr(arxiv.time, "sleep", sleeps.append)

    with pytest.raises(requests.Timeout, match="timed out"):
        arxiv.search("foundation models", max_attempts=3, timeout_seconds=7)

    assert len(calls) == 3
    assert [call["timeout"] for call in calls] == [7, 7, 7]
    assert sleeps == [2.0, 4.0]
