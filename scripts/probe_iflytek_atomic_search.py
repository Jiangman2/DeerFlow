"""Small opt-in probe of documented /biz/search behavior; not a Fetch adapter.

Python 3.10+, standard library only. No endpoint guessing or automatic retries.
Credentials are read from environment variables or hidden interactive prompts.
"""

import argparse
import base64
from datetime import datetime, timezone
from email.utils import formatdate
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import ssl
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)
import uuid

HOSTS = {
    "bearer": "https://search-api-open.cn-huabei-1.xf-yun.com",
    "hmac": "https://cbm-search-api.cn-huabei-1.xf-yun.com",
}
TARGET = "https://ltd.cnooc.com.cn/xwzx/gsxw/2025/202503/t20250327_114400.html"
PYTHON_DOC = "https://docs.python.org/3/library/asyncio-task.html"
MAX_BYTES = 2 * 1024 * 1024
PRIVATE = {
    "appid",
    "uid",
    "apikey",
    "apisecret",
    "apipassword",
    "password",
    "secret",
    "authorization",
    "signature",
    "token",
    "accesstoken",
    "sid",
    "sessionid",
    "traceid",
    "logtraceid",
    "think",
    "reasoning",
    "reasoningcontent",
}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def redact(value, secrets, depth=0):
    if depth > 30:
        return "[OMITTED: depth limit]"
    if isinstance(value, dict):
        return {
            k: "[REDACTED]"
            if re.sub(r"[^a-z0-9]", "", k.lower()) in PRIVATE
            else redact(v, secrets, depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v, secrets, depth + 1) for v in value]
    if isinstance(value, str):
        for secret in sorted(set(secrets), key=len, reverse=True):
            if secret:
                value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)Bearer\s+[^\s\"'<>]+", "Bearer [REDACTED]", value)
        value = re.sub(
            r"(?:https?|wss?)://[^\s\"'<>]*[?&](?:authorization|signature|token)=[^\s\"'<>]*",
            "[REDACTED_SIGNED_URL]",
            value,
            flags=re.I,
        )
    return value


def authentication(mode, password, key, secret):
    endpoint = HOSTS[mode] + "/biz/search"
    if mode == "bearer":
        return endpoint, {"Authorization": "Bearer " + password}, [password]
    host = urlsplit(endpoint).hostname
    date = formatdate(usegmt=True)
    text = f"host: {host}\ndate: {date}\nPOST /biz/search HTTP/1.1"
    signature = base64.b64encode(
        hmac.new(secret.encode(), text.encode(), hashlib.sha256).digest()
    ).decode()
    auth = f'api_key="{key}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature}"'
    authorization = base64.b64encode(auth.encode()).decode()
    return (
        endpoint
        + "?"
        + urlencode({"host": host, "date": date, "authorization": authorization}),
        {},
        [key, secret, signature, authorization],
    )


def cases(probe_url=None):
    keyword = "中国海油 2024 年年度业绩"
    missing = (
        "https://ltd.cnooc.com.cn/__fetch_probe_missing_"
        + uuid.uuid4().hex[:12]
        + ".html"
    )
    result = [
        ("keyword_summary", keyword, False, None),
        ("keyword_content", keyword, True, None),
        ("url_cnooc", TARGET, True, TARGET),
        ("url_python", PYTHON_DOC, True, PYTHON_DOC),
        ("url_negative_candidate", missing, True, missing),
    ]
    if probe_url:
        result.append(("url_controlled", probe_url, True, probe_url))
    return result


def analyze(data, target=None, marker=None):
    if not isinstance(data, dict):
        return {"response_schema_valid": False}
    payload = data.get("data")
    documents = payload.get("documents") if isinstance(payload, dict) else None
    valid = isinstance(documents, list) and all(isinstance(d, dict) for d in documents)
    result = {
        "response_schema_valid": valid,
        "success_value": data.get("success"),
        "err_code": data.get("err_code"),
        "business_success": data.get("success") is True
        and str(data.get("err_code")) == "0",
        "document_count": len(documents) if valid else None,
    }
    if not valid:
        return result
    exact = [d for d in documents if target and d.get("url") == target]

    def has_content(d):
        return isinstance(d.get("content"), str) and bool(d["content"].strip())

    result.update(
        content_document_count=sum(has_content(d) for d in documents),
        exact_url_hits=len(exact),
        exact_url_content_hits=sum(has_content(d) for d in exact),
        sources=[
            {
                "title": d.get("name"),
                "url": d.get("url"),
                "content_chars": len(d["content"])
                if isinstance(d.get("content"), str)
                else 0,
                "summary_chars": len(d["summary"])
                if isinstance(d.get("summary"), str)
                else 0,
            }
            for d in documents
        ],
    )
    if marker:
        result["marker_in_exact_url_content"] = any(
            marker in d.get("content", "")
            for d in exact
            if isinstance(d.get("content"), str)
        )
    result["interpretation"] = (
        "Exact URL plus content is only candidate evidence; search may return cached or partial text. No independent Fetch endpoint was called."
    )
    return result


def request_once(opener, request, timeout):
    started = time.monotonic()
    record = {}
    try:
        try:
            response = opener.open(request, timeout=min(timeout, 15))
        except HTTPError as exc:
            response = exc
        with response:
            record["http_status"] = response.code
            body = bytearray()
            while True:
                if time.monotonic() - started > timeout:
                    raise TimeoutError
                chunk = (
                    response.read1(32768)
                    if hasattr(response, "read1")
                    else response.read(32768)
                )
                if not chunk:
                    break
                if len(body) + len(chunk) > MAX_BYTES:
                    raise ValueError("response size limit")
                body.extend(chunk)
            record["response_bytes"] = len(body)
            try:
                record["response"] = json.loads(body)
            except (ValueError, UnicodeError, RecursionError):
                record["response_not_json"] = True
    except Exception as exc:
        # Raw HTTP exceptions can contain a signed URL or credential diagnostics.
        record["error_class"] = type(exc).__name__
    record["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return record


def self_test():
    from io import BytesIO
    from urllib.error import HTTPError as FakeHTTPError
    from urllib.parse import parse_qs

    class Response(BytesIO):
        code = 200

    class Opener:
        def __init__(self, body=None, code=200):
            self.body = body
            self.code = code

        def open(self, request, **kwargs):
            if self.code != 200:
                raise FakeHTTPError(
                    request.full_url,
                    self.code,
                    "private message",
                    {},
                    BytesIO(b'{"error":"denied"}'),
                )
            return Response(self.body)

    data = {
        "success": True,
        "err_code": "0",
        "data": {
            "documents": [
                {
                    "name": "fixture",
                    "url": TARGET,
                    "summary": "short",
                    "content": "private-marker body",
                }
            ]
        },
    }
    assert analyze(data, TARGET, "private-marker")["marker_in_exact_url_content"]
    assert analyze(data, PYTHON_DOC)["exact_url_content_hits"] == 0
    assert not analyze(
        {"success": "false", "err_code": "1", "data": {"documents": []}}
    )["business_success"]
    assert not analyze({"data": {"documents": "wrong"}})["response_schema_valid"]
    assert (
        analyze({"success": True, "err_code": "0", "data": {"documents": []}}, TARGET)[
            "exact_url_hits"
        ]
        == 0
    )
    assert redact({"sid": "id", "content": "dummy-password"}, ["dummy-password"]) == {
        "sid": "[REDACTED]",
        "content": "[REDACTED]",
    }
    url, headers, secrets = authentication("hmac", "", "dummy-key", "dummy-secret")
    q = parse_qs(urlsplit(url).query)
    expected = base64.b64encode(
        hmac.new(
            b"dummy-secret",
            f"host: {urlsplit(url).hostname}\ndate: {q['date'][0]}\nPOST /biz/search HTTP/1.1".encode(),
            hashlib.sha256,
        ).digest()
    ).decode()
    assert expected in base64.b64decode(q["authorization"][0]).decode()
    assert "authorization=" not in redact(url, secrets)
    request = Request("https://example.com", data=b"{}", method="POST")
    assert (
        request_once(Opener(json.dumps(data).encode()), request, 30)["response"] == data
    )
    rejected = request_once(Opener(code=401), request, 30)
    assert rejected["http_status"] == 401 and "private message" not in json.dumps(
        rejected
    )
    assert request_once(Opener(b"not JSON"), request, 30)["response_not_json"]
    assert (
        request_once(Opener(b"x" * (MAX_BYTES + 1)), request, 30)["error_class"]
        == "ValueError"
    )
    assert (
        NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com")
        is None
    )
    assert all(len(query) <= 100 for _, query, _, _ in cases())
    assert all(
        "private-marker" not in query
        for _, query, _, _ in cases("https://example.com/test")
    )
    print("Offline self-test passed; no network requests")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--auth", choices=list(HOSTS), default="bearer")
    parser.add_argument("--app-id", default=os.environ.get("IFLYTEK_SEARCH_APP_ID"))
    parser.add_argument("--business", default="deerflow")
    parser.add_argument("--pipeline", default="fl_agg_search_pro")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--ca-file")
    parser.add_argument(
        "--probe-url", help="Optional public controlled HTML URL, <=100 characters"
    )
    parser.add_argument(
        "--expected-marker", help="Marker in controlled page body; never sent to API"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            f"iflytek-atomic-probe-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
        ),
    )
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if (
        not args.app_id
        or not re.fullmatch(r"[A-Za-z]+", args.business)
        or not 1 <= args.timeout <= 60
    ):
        parser.error("Require app-id, alphabetic business, and timeout 1-60 seconds")
    if args.probe_url:
        p = urlsplit(args.probe_url)
        if (
            len(args.probe_url) > 100
            or p.scheme not in ("http", "https")
            or not p.hostname
            or p.username
            or p.password
        ):
            parser.error(
                "probe-url must be a public HTTP(S) URL without credentials, <=100 characters"
            )
    if args.expected_marker and not args.probe_url:
        parser.error("expected-marker requires probe-url")
    if args.output.exists():
        parser.error("Output directory already exists; select a new output path")
    password = (
        (
            os.environ.get("IFLYTEK_SEARCH_API_PASSWORD")
            or getpass.getpass("Atomic service APIPASSWORD (hidden): ")
        )
        if args.auth == "bearer"
        else ""
    )
    key = (
        (
            os.environ.get("IFLYTEK_SEARCH_API_KEY")
            or getpass.getpass("Atomic service APIKey (hidden): ")
        )
        if args.auth == "hmac"
        else ""
    )
    secret = (
        (
            os.environ.get("IFLYTEK_SEARCH_API_SECRET")
            or getpass.getpass("Atomic service APISecret (hidden): ")
        )
        if args.auth == "hmac"
        else ""
    )
    if (args.auth == "bearer" and not password) or (
        args.auth == "hmac" and not (key and secret)
    ):
        parser.error("Atomic service credentials must not be empty")
    context = ssl.create_default_context(cafile=args.ca_file)
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), NoRedirect())
    args.output.mkdir(parents=True, mode=0o700)
    records = []
    local_uid = uuid.uuid4().hex

    def save(name, value, sensitive):
        with os.fdopen(
            os.open(args.output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
            "w",
            encoding="utf-8",
        ) as stream:
            json.dump(redact(value, sensitive), stream, ensure_ascii=False, indent=2)

    for sample_id, query, full_text, target in cases(args.probe_url):
        url, headers, sensitive = authentication(args.auth, password, key, secret)
        sensitive += [args.app_id, local_uid]
        body = {
            "appId": args.app_id,
            "uId": local_uid,
            "business": args.business,
            "pipeline_name": args.pipeline,
            "name": query,
            "limit": 5,
            "full_text": full_text,
            "open_rerank": True,
        }
        request = Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode(),
            headers={
                **headers,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
            method="POST",
        )
        row = {
            "sample_id": sample_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "endpoint": HOSTS[args.auth] + "/biz/search",
            "request": body,
            "target_url": target,
            "url_is_keyword_query": target is not None,
        }
        row.update(request_once(opener, request, args.timeout))
        row["analysis"] = analyze(
            row.get("response"),
            target,
            args.expected_marker if sample_id == "url_controlled" else None,
        )
        if sample_id == "url_negative_candidate":
            row["negative_control_note"] = (
                "Random missing-path candidate; origin status not independently verified by this script"
            )
        save(sample_id + ".json", row, sensitive)
        summary = {
            "sample_id": sample_id,
            "http_status": row.get("http_status"),
            "elapsed_ms": row["elapsed_ms"],
            "error_class": row.get("error_class"),
            "analysis": row["analysis"],
        }
        records.append(summary)
        print(
            json.dumps(
                {k: v for k, v in summary.items() if k != "analysis"},
                ensure_ascii=False,
            ),
            flush=True,
        )
        if row.get("http_status") in (401, 402, 403, 429):
            print(
                "Stopped after authentication, access, billing or rate-limit response; no retry."
            )
            break
        time.sleep(0.25)  # Sequential diagnostic calls, below the documented 5 QPS.
    save("summary.json", records, [password, key, secret, args.app_id, local_uid])
    print(
        f"Saved sanitized results: {args.output}; review business content before sharing."
    )
    print(
        "This probes URL-as-query behavior; it does not establish an independent Fetch API contract."
    )
    return (
        0
        if records
        and all(
            r.get("http_status") == 200
            and r["analysis"].get("business_success")
            and r["analysis"].get("response_schema_valid")
            for r in records
        )
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
