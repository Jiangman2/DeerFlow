"""Collect one CNOOC web-agent response as sanitized JSONL; no automatic retry.

Requires websocket-client for live use. --self-test is offline and stdlib-only.
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
import sys
import tempfile
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit
import uuid

ENDPOINT = "wss://agents.cnooc.com.cn/openapi/flames/api/v2/chat"
INPUT_NODE = "a16d12e1fad"
PRIVATE_FIELDS = {
    "appid",
    "appsecret",
    "apikey",
    "secret",
    "password",
    "token",
    "accesstoken",
    "authorization",
    "signature",
    "sessionid",
    "sid",
    "traceid",
    "bodyid",
    "think",
    "thinking",
    "reasoning",
    "reasoningcontent",
}


def signed_url(app_id, secret, body_id):
    parts = urlsplit(ENDPOINT)
    date = formatdate(usegmt=True)
    text = f"host: {parts.hostname}\ndate: {date}\nGET {parts.path} HTTP/1.1"
    signature = base64.b64encode(
        hmac.new(secret.encode(), text.encode(), hashlib.sha256).digest()
    ).decode()
    auth = f'hmac api_key="{app_id}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature}"'
    authorization = base64.b64encode(auth.encode()).decode()
    url = (
        ENDPOINT
        + "?"
        + urlencode(
            {
                "authorization": authorization,
                "date": date,
                "host": parts.hostname,
                "bodyId": body_id,
            }
        )
    )
    return url, (secret, app_id, body_id, signature, authorization)


def scrub(value, secrets, depth=0):
    if depth > 30:
        return "[OMITTED: nesting limit]"
    if isinstance(value, dict):
        return {
            k: "[REDACTED]"
            if re.sub(r"[^a-z0-9]", "", k.lower()) in PRIVATE_FIELDS
            else scrub(v, secrets, depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [scrub(v, secrets, depth + 1) for v in value]
    if not isinstance(value, str):
        return value
    # Some source fields are JSON encoded as strings; retain that outer type.
    if value.lstrip().startswith(("{", "[")):
        try:
            return json.dumps(
                scrub(json.loads(value), secrets, depth + 1), ensure_ascii=False
            )
        except (ValueError, RecursionError):
            pass
    for secret in sorted(set(secrets), key=len, reverse=True):
        if secret:
            value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"(?i)Bearer\s+[^\s\"'<>]+", "Bearer [REDACTED]", value)
    return re.sub(
        r"(?:https?|wss?)://[^\s\"'<>]+",
        lambda m: (
            "[REDACTED_SIGNED_URL]"
            if any(
                k.lower() in {"authorization", "signature", "token", "access_token"}
                for k in parse_qs(urlsplit(m.group()).query)
            )
            else m.group()
        ),
        value,
    )


def collect(
    module,
    app_id,
    secret,
    body_id,
    query,
    deepseek,
    output,
    timeout,
    ca_file=None,
    max_frames=5000,
):
    url, secrets = signed_url(app_id, secret, body_id)
    started = time.monotonic()
    deadline = started + timeout
    context = ssl.create_default_context(cafile=ca_file)
    request = {
        "header": {
            "traceId": uuid.uuid4().hex,
            "appId": app_id,
            "bodyId": body_id,
            "mode": 0,
        },
        "payload": {
            "input": {INPUT_NODE: {"query": query, "deepseek": deepseek, "history": ""}}
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    sock = None
    total_bytes = 0
    # Refuse overwrite; file permissions apply on POSIX, Windows uses its ACLs.
    with os.fdopen(
        os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
        "w",
        encoding="utf-8",
    ) as stream:

        def write(row):
            stream.write(json.dumps(scrub(row, secrets), ensure_ascii=False) + "\n")
            stream.flush()

        write(
            {
                "event": "start",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "endpoint": ENDPOINT,
                "request": request,
                "timeout_seconds": timeout,
                "max_frames": max_frames,
                "redaction": "known credentials, signed URLs, session identifiers and reasoning fields; review business text before sharing",
            }
        )
        try:
            module.enableTrace(False)
            sock = module.create_connection(
                url,
                timeout=min(10, timeout),
                sslopt={
                    "context": context,
                    "cert_reqs": ssl.CERT_REQUIRED,
                    "check_hostname": True,
                },
            )
            sock.settimeout(max(0.001, deadline - time.monotonic()))
            sock.send(json.dumps(request, ensure_ascii=False))
            for seq in range(1, max_frames + 1):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                sock.settimeout(remaining)
                raw = sock.recv()
                if raw in ("", b""):
                    raise EOFError
                total_bytes += len(raw.encode("utf-8") if isinstance(raw, str) else raw)
                if total_bytes > 4 * 1024 * 1024:
                    write({"event": "stop", "reason": "received_content_exceeds_4MiB"})
                    return 2
                try:
                    message = json.loads(raw)
                except (ValueError, UnicodeError, RecursionError):
                    write({"event": "stop", "reason": "invalid_json", "seq": seq})
                    return 2
                write(
                    {
                        "seq": seq,
                        "elapsed_ms": round((time.monotonic() - started) * 1000),
                        "message": message,
                    }
                )
                if not isinstance(message, dict) or not isinstance(
                    message.get("header"), dict
                ):
                    write({"event": "stop", "reason": "missing_header"})
                    return 2
                header = message["header"]
                code, status = header.get("code"), header.get("status")
                if type(code) is not int or type(status) is not int:
                    write({"event": "stop", "reason": "invalid_code_or_status_type"})
                    return 2
                if code != 0:
                    write(
                        {
                            "event": "stop",
                            "reason": "provider_business_error",
                            "code": code,
                        }
                    )
                    return 2
                if status not in (0, 1, 2):
                    write({"event": "stop", "reason": "invalid_status"})
                    return 2
                if status == 2:
                    write(
                        {
                            "event": "complete",
                            "frames": seq,
                            "received_bytes": total_bytes,
                        }
                    )
                    return 0
            write({"event": "stop", "reason": "frame_limit", "limit": max_frames})
            return 2
        except Exception as exc:
            # Exception messages can contain the signed connection URL.
            write({"event": "stop", "error_class": type(exc).__name__})
            return 2
        finally:
            if sock is not None:
                try:
                    sock.close(timeout=1)
                except Exception:
                    pass


def self_test():
    class Socket:
        def __init__(self, frames):
            self.frames = iter(frames)
            self.closed = False

        def settimeout(self, value):
            pass

        def send(self, value):
            assert json.loads(value)["payload"]["input"][INPUT_NODE]["history"] == ""

        def recv(self):
            return next(self.frames)

        def close(self, **kwargs):
            self.closed = True

    url, _ = signed_url("offline-app", "offline-secret", "offline-body")
    auth = base64.b64decode(parse_qs(urlsplit(url).query)["authorization"][0]).decode()
    assert (
        'api_key="offline-app"' in auth and 'headers="host date request-line"' in auth
    )
    signed_date = parse_qs(urlsplit(url).query)["date"][0]
    signing_text = f"host: agents.cnooc.com.cn\ndate: {signed_date}\nGET /openapi/flames/api/v2/chat HTTP/1.1"
    expected_signature = base64.b64encode(
        hmac.new(b"offline-secret", signing_text.encode(), hashlib.sha256).digest()
    ).decode()
    assert f'signature="{expected_signature}"' in auth
    with tempfile.TemporaryDirectory() as directory:
        cases = [
            (
                [
                    {
                        "header": {"code": 0, "status": 1},
                        "payload": {
                            "think": "hidden reasoning",
                            "netSearch": '[{"url":"https://example.com","snippet":"public"}]',
                        },
                    },
                    {"header": {"code": 0, "status": 2}},
                ],
                0,
            ),
            ([{"header": {"code": 23, "status": 2}}], 2),
            ([{"header": {"code": 0, "status": 2}}], 0),
            ([{"payload": {"text": "missing header"}}], 2),
            ([{"header": {"code": "0", "status": 2}}], 2),
            ([{"header": {"code": 0, "status": 1}}], 2),
            (
                [{"header": {"code": 0, "status": 1}}] * 500
                + [{"header": {"code": 0, "status": 2}}],
                0,
            ),
        ]
        for index, (frames, expected) in enumerate(cases):
            sock = Socket([json.dumps(f) for f in frames])

            def connect(*args, **kwargs):
                assert kwargs["sslopt"]["context"].verify_mode == ssl.CERT_REQUIRED
                assert kwargs["sslopt"]["context"].check_hostname
                return sock

            module = SimpleNamespace(
                create_connection=connect, enableTrace=lambda _: None
            )
            output = Path(directory) / f"{index}.jsonl"
            assert (
                collect(
                    module,
                    "offline-app",
                    "offline-secret",
                    "offline-body",
                    "public query",
                    False,
                    output,
                    10,
                )
                == expected
            )
            text = output.read_text(encoding="utf-8")
            assert (
                sock.closed
                and "offline-secret" not in text
                and "hidden reasoning" not in text
            )
            assert all(json.loads(line) for line in text.splitlines())
        assert scrub(
            {"netSearch": '{"token":"hidden-token","url":"https://example.com"}'}, ()
        ) == {"netSearch": '{"token": "[REDACTED]", "url": "https://example.com"}'}
        assert (
            scrub("https://example.com?authorization=hidden", ())
            == "[REDACTED_SIGNED_URL]"
        )
    print("Offline self-test passed; no network requests")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--app-id", default=os.environ.get("CNOOC_WEB_APP_ID"))
    parser.add_argument("--body-id", default=os.environ.get("CNOOC_WEB_BODY_ID"))
    parser.add_argument(
        "--query",
        default="请搜索 Python asyncio.TaskGroup 官方文档，返回来源标题、完整URL和相关摘要。",
    )
    parser.add_argument(
        "--deepseek",
        action="store_true",
        help="Use the task chain's modelFlag=true; not a search-provider selector",
    )
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument(
        "--max-frames",
        type=int,
        default=5000,
        help="Maximum received data messages (1-20000, default 5000)",
    )
    parser.add_argument("--ca-file", help="Optional trusted enterprise CA bundle")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            f"cnooc-web-probe-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}.jsonl"
        ),
    )
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if (
        not args.app_id
        or not args.body_id
        or not args.query.strip()
        or not 1 <= args.timeout <= 300
        or not 1 <= args.max_frames <= 20000
    ):
        parser.error(
            "Require app-id, body-id, a nonblank query, timeout 1-300 seconds, and max-frames 1-20000"
        )
    try:
        import websocket
    except ImportError:
        print(
            "Install dependency: python3 -m pip install websocket-client",
            file=sys.stderr,
        )
        return 2
    if not hasattr(websocket, "create_connection"):
        print(
            "The required package is websocket-client, not websocket", file=sys.stderr
        )
        return 2
    secret = os.environ.get("CNOOC_WEB_APP_SECRET") or getpass.getpass(
        "App Secret (hidden input): "
    )
    if not secret:
        parser.error("App Secret is required")
    try:
        result = collect(
            websocket,
            args.app_id,
            secret,
            args.body_id,
            args.query,
            args.deepseek,
            args.output,
            args.timeout,
            args.ca_file,
            max_frames=args.max_frames,
        )
    except Exception as exc:
        print(
            f"Probe could not start ({type(exc).__name__}); no error details printed",
            file=sys.stderr,
        )
        return 2
    print(f"{'Completed' if result == 0 else 'Failed or incomplete'}: {args.output}")
    print(
        "Review business content in the sanitized JSONL before sharing; no automatic retries were made."
    )
    return result


if __name__ == "__main__":
    raise SystemExit(main())
