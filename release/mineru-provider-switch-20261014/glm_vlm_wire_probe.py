"""Isolated stdlib wire-contract probe, NOT the running Go VLM client.

Two distinct synthetic requests at most; no retries, redirects, raw output,
business documents, files, or environment credentials. Linux Python 3.7+.
"""
import base64
import hashlib
import http.client
import json
import os
import re
import signal
import socket
import ssl
import struct
import time
import zlib

HOST = 'aiapi.cnooc.com.cn'
PATH = '/GLM-5.3-Flash/FCGLM-001/v1/chat/completions'
MODEL = 'GLM-5.3-Flash'
LIMIT = 2 * 1024 * 1024
DEADLINE = 180


class ProbeFailure(Exception):
    pass


def need(ok, label):
    if not ok:
        raise ProbeFailure(label)


def red_png():
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 32, 32, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\x00' + b'\xff\x00\x00' * 32) * 32)) + chunk(b'IEND', b''))


def payload(mode):
    need(mode in ('image_first_stream', 'native_text_first_nonstream'), 'unknown_mode')
    image = {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(red_png()).decode(), 'detail': 'auto'}}
    text = {'type': 'text', 'text': 'Name the dominant colour in this image in English. Reply with only one colour word.'}
    parts = [image, text] if mode == 'image_first_stream' else [text, image]
    # Same defaults as reviewed remote_api.go; vary only order and transport.
    out = {'model': MODEL, 'messages': [{'role': 'user', 'content': parts}], 'max_tokens': 5000, 'temperature': .1}
    if mode == 'image_first_stream':
        out['stream'] = True
    return out


def visible_result(text):
    need(isinstance(text, str) and 0 < len(text.strip()) <= 65536, 'visible_content_missing_or_oversize')
    return {'visible_characters': len(text), 'synthetic_red_check': bool(re.fullmatch(r'\s*red[.!。]?\s*', text, re.I))}


class SSE:
    def __init__(self):
        self.buffer = b''
        self.event = []
        self.text = []
        self.chars = 0
        self.total = 0
        self.done = False
        self.finish = None
        self.reasoning_seen = False
        self.frames = 0

    def dispatch(self):
        if not self.event:
            return
        data = b'\n'.join(self.event).decode('utf-8')
        self.event = []
        need(not self.done, 'data_after_done')
        if data.strip() == '[DONE]':
            self.done = True
            return
        row = json.loads(data)
        need(isinstance(row, dict) and 'error' not in row, 'sse_error_or_invalid_object')
        self.frames += 1
        choices = row.get('choices') or []
        need(isinstance(choices, list) and len(choices) <= 1, 'sse_choices_shape')
        if not choices:
            return
        c = choices[0]
        need(isinstance(c, dict) and c.get('index', 0) == 0, 'sse_choice_shape')
        delta = c.get('delta') or {}
        need(isinstance(delta, dict) and not delta.get('tool_calls'), 'unexpected_tool_call')
        self.reasoning_seen = self.reasoning_seen or bool(delta.get('reasoning_content'))
        value = delta.get('content') or ''
        need(isinstance(value, str), 'sse_content_shape')
        if value:
            need(self.finish is None, 'content_after_finish')
            self.chars += len(value)
            need(self.chars <= 65536, 'visible_content_oversize')
            self.text.append(value)
        if c.get('finish_reason'):
            need(c['finish_reason'] == 'stop', 'non_stop_finish')
            self.finish = 'stop'

    def feed(self, data):
        self.total += len(data)
        need(self.total <= LIMIT, 'response_oversize')
        self.buffer += data
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            line = line.rstrip(b'\r')
            need(len(line) <= 65536, 'sse_line_oversize')
            if not line:
                self.dispatch()
            elif line.startswith(b'data:'):
                self.event.append(line[5:].lstrip(b' '))
                need(sum(map(len, self.event)) <= 131072, 'sse_event_oversize')
            elif line.startswith(b'event:'):
                need(line.strip() != b'event: error', 'sse_error_event')
        need(len(self.buffer) <= 65536, 'sse_line_oversize')

    def result(self):
        need(self.done and self.finish == 'stop', 'stream_incomplete_no_done_or_stop')
        out = visible_result(''.join(self.text))
        return dict(out, done=True, finish_stop=True, reasoning_discarded=self.reasoning_seen, frames=self.frames)


def nonstream(raw):
    need(len(raw) <= LIMIT, 'response_oversize')
    row = json.loads(raw)
    need(isinstance(row, dict) and 'error' not in row, 'json_error_or_invalid_object')
    choices = row.get('choices')
    need(isinstance(choices, list) and len(choices) == 1, 'json_choices_shape')
    c = choices[0]
    need(isinstance(c, dict) and c.get('finish_reason') == 'stop', 'non_stop_finish')
    msg = c.get('message')
    need(isinstance(msg, dict) and not msg.get('tool_calls'), 'unexpected_message')
    return dict(visible_result(msg.get('content')), finish_stop=True, reasoning_discarded=bool(msg.get('reasoning_content')))


def context():
    # Use the existing app container trust store, not an imported bypass CA.
    ctx = ssl.create_default_context()
    need(ctx.check_hostname and ctx.verify_mode == ssl.CERT_REQUIRED, 'tls_verification_disabled')
    configured = os.environ.get('SSL_CERT_FILE')
    if configured:
        with open(configured, 'rb') as f:
            raw = f.read(1048577)
        need(0 < len(raw) <= 1048576, 'ca_size')
        ca_sha = hashlib.sha256(raw).hexdigest()
    else:
        ca_sha = None
    return ctx, {'tls_verification': True, 'trust': 'EXISTING_APP_DEFAULT_SSL_CONTEXT', 'configured_ca_sha256': ca_sha,
                 'proxy_inherited': False, 'go_transport_executed': False}


def timeout_handler(signum, frame):
    raise ProbeFailure('total_deadline_outcome_uncertain')


def one(mode, token, ctx):
    out = {'mode': mode, 'post_attempts': 0, 'result': 'FAIL', 'raw_response_saved': False}
    conn = None
    started = time.monotonic()
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, DEADLINE)
    try:
        conn = http.client.HTTPSConnection(HOST, timeout=DEADLINE, context=ctx)
        body = json.dumps(payload(mode), separators=(',', ':')).encode()
        # Increment BEFORE the uncertain send boundary, never retry.
        out['post_attempts'] = 1
        conn.request('POST', PATH, body, {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                     'Accept': 'text/event-stream' if mode == 'image_first_stream' else 'application/json',
                     'Connection': 'close'})
        response = conn.getresponse()
        out['http_status'] = response.status
        if response.status != 200:
            out['failure'] = 'redirect_refused' if 300 <= response.status < 400 else 'http_non_200'
            return out
        mime = response.getheader('Content-Type', '').split(';')[0].strip().lower()
        if mode == 'image_first_stream':
            need(mime == 'text/event-stream', 'unexpected_sse_content_type')
            parser = SSE()
            while not parser.done:
                chunk = response.read1(8192)
                if not chunk:
                    break
                parser.feed(chunk)
            out.update(parser.result())
        else:
            need(mime == 'application/json', 'unexpected_json_content_type')
            out.update(nonstream(response.read(LIMIT + 1)))
        out['result'] = 'PASS' if out['synthetic_red_check'] else 'VISION_CHECK_FAILED'
    except ProbeFailure as exc:
        out['failure'] = str(exc)  # Only fixed labels raised in this module.
    except ssl.SSLCertVerificationError:
        out['failure'] = 'certificate_verification_failed'
    except socket.gaierror:
        out['failure'] = 'dns_failed'
    except (socket.timeout, TimeoutError):
        out['failure'] = 'transport_timeout_outcome_uncertain'
    except Exception:
        out['failure'] = 'transport_or_protocol_failure_no_raw_detail'
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        if conn is not None:
            conn.close()
        out['elapsed_seconds'] = round(time.monotonic() - started, 3)
    return out


def run(token):
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    need(isinstance(token, str) and re.fullmatch(r'[!-~]{8,4096}', token), 'credential_shape')
    ctx, transport = context()
    results = []
    for mode in ('image_first_stream', 'native_text_first_nonstream'):
        row = one(mode, token, ctx)
        results.append(row)
        # The comparison is a second distinct experiment, not a retry.
        # Do not proceed after failure or uncertainty of the positive control.
        if row['result'] != 'PASS':
            break
    return {'wire_probe': 'COMPLETE', 'scope': 'SYNTHETIC_PYTHON_WIRE_EMULATION_NOT_NATIVE_GO_VLM',
            'maximum_submissions': 2, 'results': results, 'transport': transport,
            'credentials_saved': False, 'production_model_changed': False, 'native_app_acceptance': 'NOT_TESTED'}
