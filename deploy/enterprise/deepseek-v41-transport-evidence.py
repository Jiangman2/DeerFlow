"""Synthetic-only transport evidence. Never exports payloads or credentials."""

import asyncio
import datetime
import hashlib
import ipaddress
import json
import re
import time

import httpx

ENDPOINT = "https://aiapi.cnooc.com.cn/DeepSeek-V4.1-Flash/FCV-41/v1/chat/completions"
STAGES = ("raw_flash", "flash", "thinking", "tool_call", "tool_resume", "multi_image")


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def error_features(raw):
    text = raw.decode("utf-8", "replace").lower()
    result = dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), categories=[])
    for name, words in {
        "no_fallback": ("未配置兜底", "no fallback"),
        "unavailable": ("service unavailable", "服务不可用"),
        "overloaded": ("overload", "过载", "capacity"),
        "dns": ("dns_fail", "dns failure", "name resolution"),
        "upstream": ("upstream",),
        "proxy": ("squid", "proxy error"),
        "timeout": ("timed out", "timeout", "超时"),
        "unauthorized": ("unauthorized", "没有模型功能", "认证失败"),
        "html": ("<html", "<!doctype html"),
    }.items():
        if any(w in text for w in words):
            result["categories"].append(name)
    try:
        value = json.loads(raw)
        if isinstance(value, dict):
            result["known_fields"] = sorted(set(value) & {"code", "data", "message", "error", "detail"})
            for k in ("code", "status", "status_code"):
                if type(value.get(k)) is int:
                    result[k] = value[k]
    except (ValueError, UnicodeError):
        pass
    return result


def safe_headers(headers, key):
    result = {}
    for name in ("x-request-id", "request-id", "x-correlation-id", "traceparent", "x-b3-traceid", "x-amzn-trace-id"):
        value = headers.get(name, "")
        if value and key not in value and re.fullmatch(r"[A-Za-z0-9_.:;=/-]{1,128}", value):
            result[name] = value
    value = headers.get("server", "").lower()
    result["server_family"] = next((x for x in ("nginx", "envoy", "squid", "apache", "uvicorn", "cloudflare") if x in value), "OTHER_OR_ABSENT")
    value = headers.get("content-type", "").split(";")[0].strip().lower()
    result["content_type"] = value if value in ("application/json", "text/html", "text/event-stream", "text/plain") else "OTHER_OR_ABSENT"
    result["via_present"] = "via" in headers
    result["retry_after_present"] = "retry-after" in headers
    return result


def shape(body):
    messages = body.get("messages", [])
    calls = [c.get("id") for m in messages if m.get("role") == "assistant" for c in (m.get("tool_calls") or [])]
    results = [m.get("tool_call_id") for m in messages if m.get("role") == "tool"]
    return dict(
        model_matches=body.get("model") == "DeepSeek-V4.1-Flash",
        stream=body.get("stream"), enable_thinking=body.get("enable_thinking"),
        max_tokens=body.get("max_tokens"),
        roles=[m.get("role") if m.get("role") in ("user", "assistant", "tool", "system", "developer") else "OTHER" for m in messages],
        tools_count=len(body.get("tools") or []),
        tool_choice=body.get("tool_choice") if body.get("tool_choice") in (None, "auto", "none", "required") else "STRUCTURED",
        tool_result_ids_match=bool(results) and results == calls,
        tool_results_count=len(results),
        image_count=sum(1 for m in messages if isinstance(m.get("content"), list) for p in m["content"] if p.get("type") == "image_url"),
        unexpected_parameter_names=sorted(set(body) - {"model", "messages", "tools", "tool_choice", "stream", "stream_options", "max_tokens", "temperature", "enable_thinking", "n", "stop"}),
    )


class EvidenceStream(httpx.AsyncByteStream):
    def __init__(self, inner, row):
        self.inner, self.row = inner, row
        self.buffer = b""
        self.error = bytearray()

    def line(self, line):
        if not line.startswith(b"data:"):
            return
        data = line[5:].strip()
        if data == b"[DONE]":
            self.row["done_seen"] = True
            return
        try:
            obj = json.loads(data)
            for choice in obj.get("choices", []):
                reason = choice.get("finish_reason")
                if reason is not None:
                    self.row["terminal_reasons"].append(reason if reason in ("stop", "tool_calls", "length", "content_filter") else "OTHER")
        except (ValueError, AttributeError, TypeError):
            self.row["invalid_sse_json"] = True

    async def __aiter__(self):
        try:
            async for data in self.inner:
                self.row["response_bytes"] += len(data)
                if self.row["response_bytes"] > 2097152:
                    raise ValueError("RESPONSE_LIMIT")
                if self.row["http_status"] >= 400:
                    if len(self.error) < 65536:
                        self.error.extend(data[:65536 - len(self.error)])
                elif self.row["stream_requested"]:
                    self.buffer += data
                    if len(self.buffer) > 262144:
                        raise ValueError("SSE_LINE_LIMIT")
                    while b"\n" in self.buffer:
                        line, self.buffer = self.buffer.split(b"\n", 1)
                        self.line(line.rstrip(b"\r"))
                yield data
            if self.buffer:
                self.line(self.buffer)
        finally:
            if self.row["http_status"] >= 400:
                self.row["error_body_features"] = error_features(bytes(self.error))
                self.row["error_body_truncated"] = self.row["response_bytes"] > len(self.error)
            self.row["stream_observation_ended_utc"] = utc()

    async def aclose(self):
        await self.inner.aclose()


class Recorder:
    def __init__(self, key, operation):
        self.key, self.operation, self.rows = key, operation, []

    async def request(self, request):
        if str(request.url) != ENDPOINT or request.method != "POST" or len(self.rows) >= 6:
            raise ValueError("FIXED_ENDPOINT_REQUEST_BUDGET")
        raw = request.content
        body = json.loads(raw)
        i = len(self.rows)
        correlation = "portal-v41-" + self.operation + "-" + str(i + 1)
        request.headers["X-Request-ID"] = correlation
        row = dict(stage=STAGES[i], client_request_id=correlation, request_started_utc=utc(), request_sha256=hashlib.sha256(raw).hexdigest(), request_bytes=len(raw), request_shape=shape(body), stream_requested=body.get("stream") is True, done_seen=False, terminal_reasons=[], response_bytes=0)
        self.rows.append(row)
        request.extensions["evidence_index"] = i
        request.extensions["evidence_started"] = time.monotonic()

        async def trace(name, info):
            if name in ("connection.connect_tcp.started", "connection.connect_tcp.complete", "connection.connect_tcp.failed", "connection.start_tls.started", "connection.start_tls.complete", "connection.start_tls.failed", "http11.receive_response_headers.failed", "http11.receive_response_body.failed"):
                entries = row.setdefault('transport_events', [])
                if len(entries) < 16:
                    item = dict(event=name, elapsed_seconds=round(time.monotonic()-request.extensions['evidence_started'], 3))
                    if 'exception' in info:
                        item['error_class'] = type(info['exception']).__name__
                    entries.append(item)

        request.extensions['trace'] = trace

    async def response(self, response):
        row = self.rows[response.request.extensions["evidence_index"]]
        row.update(http_status=response.status_code, response_headers_utc=utc(), time_to_headers_seconds=round(time.monotonic() - response.request.extensions["evidence_started"], 3), headers=safe_headers(response.headers, self.key))
        stream = response.extensions.get("network_stream")
        try:
            address = stream.get_extra_info("server_addr")
            if not isinstance(address, tuple):
                sock = stream.get_extra_info('socket')
                address = sock.getpeername() if sock is not None else None
            if isinstance(address, tuple):
                row["connected_peer"] = dict(address=str(ipaddress.ip_address(address[0])), port=address[1])
            tls = stream.get_extra_info('ssl_object')
            if tls is not None:
                row['tls_version'] = tls.version()
                row['peer_certificate_sha256'] = hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest()
        except (AttributeError, TypeError, ValueError, OSError):
            pass
        response.stream = EvidenceStream(response.stream, row)


async def diagnose(key, profile, run_probe, client, recorder):
    baseline = dict(status="INCOMPLETE")
    try:
        response = await asyncio.wait_for(client.post(ENDPOINT, headers={"Authorization": "Bearer " + key}, json=dict(model="DeepSeek-V4.1-Flash", messages=[dict(role="user", content="Reply exactly V41_PROBE_OK and nothing else.")], max_tokens=1000, stream=False, enable_thinking=False)), timeout=45)
        response.raise_for_status()
        data = response.json()
        choice = data["choices"][0]
        baseline["status"] = "PASS" if choice.get("finish_reason") == "stop" and choice["message"].get("content", "").strip() == "V41_PROBE_OK" and not choice["message"].get("reasoning_content") else "CONTENT_CONTRACT_FAIL"
    except Exception as exc:
        baseline["error_class"] = type(exc).__name__
    # Deliberately independent application-path comparison, not an automatic
    # replay of a failed request. It has the original five-stage/no-retry budget.
    app = await run_probe(key, client, profile, request_deadline=90)
    streams = recorder.rows[1:]
    complete = len(streams) == 5 and all(r.get("http_status") == 200 and r["done_seen"] and r["terminal_reasons"] and not r.get("invalid_sse_json") for r in streams)
    shapes = [r['request_shape'] for r in streams]
    contract = len(shapes) == 5 and all(s['model_matches'] and s['stream'] is True for s in shapes) and [s['enable_thinking'] for s in shapes] == [False, True, False, False, True] and shapes[3]['tool_result_ids_match'] and shapes[3]['tool_results_count'] == 1 and shapes[4]['image_count'] == 2
    accepted = baseline["status"] == "PASS" and app.get("status") == "PROVIDER_ACCEPTED_NOT_DEPLOYED" and complete and contract
    return dict(status="SAMPLE_ACCEPTED_NOT_DEPLOYED" if accepted else "DIAGNOSIS_INCOMPLETE", raw_baseline=baseline, application=app, stream_terminal_and_done=complete, wire_contract_accepted=contract, requests=recorder.rows, provider_requests_attempted=len(recorder.rows), maximum_provider_calls=6, automatic_retry=False, trust_env=False, environment_proxy_used=False, tls_verification=True, redirects_followed=False, business_tools_executed=0, raw_payloads_exported=False, credentials_exported=False)
