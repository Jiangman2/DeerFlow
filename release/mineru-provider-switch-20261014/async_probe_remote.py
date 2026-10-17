"""One synthetic async PDF through the candidate client, in an isolated exec process.

No production file writes, config changes or service startup. No real documents.
The controller supplies reviewed source bytes and a hidden-input key over stdin.
"""
import base64
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import resource
import signal
import stat
import sys
import time
import types

BASE = 'https://aiapi.cnooc.com.cn/MinerU2.5-2605-1.2B/FC-02'
TASKS = BASE + '/dynamic/member1/mineru-for-zy-agent/tasks'
SOURCE = '/app/deerflow_mineru_adapter.py'
SOURCE_SHA = '1c54fbea077137f39a4ce66b1cc309d7e643541808f4b9670fe0fb8037eb6b39'
CA_SHA = '84c58afec9e48e1bd7b41d832ff6986b74a6565b4473f7cc77b868900b4d76af'
MAX_BODY = 16 * 1024 * 1024
FILENAME = 'mineru-async-synthetic.pdf'


class ProbeFailure(RuntimeError):
    pass


def require(value, label):
    if not value:
        raise ProbeFailure(label)


def emit(value):
    print(json.dumps(value, sort_keys=True), flush=True)


def on_alarm(signum, frame):
    raise ProbeFailure('probe_wall_deadline')


def validate_token(token):
    require(isinstance(token, str) and 8 <= len(token) <= 4096 and token.isascii()
            and all(32 < ord(c) < 127 for c in token), 'invalid_hidden_key')


def error_class(exc):
    import requests
    if isinstance(exc, ProbeFailure): return str(exc)
    if isinstance(exc, requests.exceptions.SSLError): return 'TLS_VERIFICATION_FAILED'
    if isinstance(exc, (requests.exceptions.Timeout, TimeoutError)): return 'NETWORK_OR_TASK_TIMEOUT'
    if isinstance(exc, requests.exceptions.ConnectionError): return 'NETWORK_CONNECTION_FAILED'
    if isinstance(exc, requests.exceptions.HTTPError): return 'PROVIDER_HTTP_ERROR'
    return 'CLIENT_OR_RESULT_COMPATIBILITY_FAILURE'


class BoundedTransport:
    def __init__(self, token, ca_file):
        import requests
        self.token = token
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.verify = ca_file
        self.session.mount('https://', requests.adapters.HTTPAdapter(max_retries=0))
        self.posts, self.status_gets, self.result_gets = 0, 0, 0
        self.task_id = None
        self.last = {}
        self.deadline = time.monotonic() + 300

    def _request(self, method, url, headers, timeout, **kwargs):
        import requests
        require(headers.get('Authorization') == 'Bearer ' + self.token, 'credential_binding')
        require(time.monotonic() < self.deadline, 'probe_wall_deadline')
        require(kwargs.pop('allow_redirects', None) is False, 'redirect_policy_required')
        operation = 'submit' if method == 'POST' else ('result' if url.endswith('/result') else 'status')
        self.last = {'operation': operation}
        emit({'provider_request': operation, 'state': 'START', 'post_attempts': self.posts,
              'status_requests': self.status_gets, 'result_requests': self.result_gets})
        with self.session.request(method, url, headers=headers, timeout=(8, min(30, timeout)),
                                  allow_redirects=False, stream=True, **kwargs) as response:
            self.last['http_status'] = response.status_code
            require(not 300 <= response.status_code < 400, 'redirect_refused')
            length = response.headers.get('Content-Length')
            cap = MAX_BODY if operation == 'result' else 65536
            if length is not None:
                require(length.isdigit() and int(length) <= cap, 'response_size_limit')
            parts, size = [], 0
            for chunk in response.iter_content(chunk_size=65536):
                require(time.monotonic() < self.deadline, 'probe_wall_deadline')
                size += len(chunk)
                require(size <= cap, 'response_size_limit')
                parts.append(chunk)
            body = b''.join(parts)
            self.last['response_bytes'] = size
            try:
                envelope = json.loads(body)
                if isinstance(envelope, dict):
                    code = envelope.get('code')
                    if type(code) in (str, int) and re.fullmatch(r'-?\d{1,12}', str(code)):
                        self.last['provider_code'] = str(code)
                    fields = envelope.get('data') if isinstance(envelope.get('data'), dict) else envelope
                    if operation == 'submit' and response.status_code in (200, 202) and envelope.get('success') is not False:
                        candidate_id = str(fields.get('task_id', '')).strip()
                        if re.fullmatch(r'[A-Za-z0-9._-]{1,128}', candidate_id) and candidate_id not in ('.', '..'):
                            self.task_id = candidate_id
                            emit({'accepted_task_id': self.task_id, 'post_attempts': self.posts,
                                  'resubmission_permitted': False})
                    if operation == 'status':
                        status = fields.get('status')
                        self.last['task_status'] = status if status in ('pending', 'queued', 'processing', 'completed', 'failed', 'error') else 'OTHER_OR_ABSENT'
            except (ValueError, UnicodeError):
                self.last['json_response'] = False
            emit(dict(provider_response=operation, **self.last))
            bounded = requests.Response()
            bounded.status_code = response.status_code
            bounded._content = body
            bounded.encoding = 'utf-8'
            bounded.url = url
            return bounded

    def post(self, url, *, headers, files, data, timeout, allow_redirects):
        require(self.posts == 0 and url == BASE + '/tasks', 'repeat_or_unapproved_submission')
        require(set(files) == {'files'}, 'unexpected_multipart')
        filename, content, media_type = files['files']
        require(filename == FILENAME and media_type == 'application/pdf'
                and content.startswith(b'%PDF-') and len(content) <= 2097152, 'synthetic_pdf_required')
        require(data.get('backend') == 'pipeline' and data.get('return_md') == 'true'
                and data.get('return_images') == 'true', 'required_result_contract')
        self.posts += 1
        return self._request('POST', url, headers, timeout, files=files, data=data, allow_redirects=allow_redirects)

    def get(self, url, *, headers, timeout, allow_redirects):
        require(self.task_id is not None, 'query_without_accepted_task')
        prefix = TASKS + '/' + self.task_id
        require(url in (prefix, prefix + '/result'), 'unapproved_query_destination')
        if url == prefix:
            require(self.status_gets < 100, 'status_request_budget')
            self.status_gets += 1
        else:
            require(self.result_gets < 10, 'result_request_budget')
            self.result_gets += 1
        return self._request('GET', url, headers, timeout, allow_redirects=allow_redirects)

    def close(self):
        self.session.close()


def load_candidate(source, expected_sha):
    path = Path(SOURCE)
    s = path.lstat()
    require(stat.S_ISREG(s.st_mode) and not path.is_symlink() and s.st_size < 2097152, 'baseline_source_file_changed')
    require(hashlib.sha256(path.read_bytes()).hexdigest() == SOURCE_SHA, 'baseline_source_hash_changed')
    require(isinstance(source, str) and len(source.encode()) < 2097152, 'candidate_size_limit')
    require(hashlib.sha256(source.encode()).hexdigest() == expected_sha, 'candidate_source_hash_changed')
    module = types.ModuleType('mineru_candidate_smoke')
    module.__file__ = '<reviewed-candidate-in-memory>'
    sys.modules[module.__name__] = module
    exec(compile(source, module.__file__, 'exec'), module.__dict__)
    return module


def synthetic_pdf():
    from PIL import Image, ImageDraw, ImageFont
    pages = []
    for marker in ('8246', '7734'):
        page = Image.new('RGB', (1200, 1600), 'white')
        draw = ImageDraw.Draw(page)
        lines = ['MINERU TEST 2605', 'Synthetic only - no business data',
                 'Verification number ' + marker, 'Item         Quantity', 'Alpha        12', 'Beta         34']
        for i, line in enumerate(lines):
            tile = Image.new('RGB', (560, 24), 'white')
            ImageDraw.Draw(tile).text((0, 2), line, fill='black', font=ImageFont.load_default())
            page.paste(tile.resize((1120, 48)), (40, 60 + i * 72))
        draw.rectangle((80, 600, 1120, 1250), outline='black', width=4)
        for x, top, color in ((200, 1000, '#2266bb'), (500, 850, '#dd8833'), (800, 700, '#339977')):
            draw.rectangle((x, top, x + 140, 1180), fill=color)
        pages.append(page)
    out = io.BytesIO()
    pages[0].save(out, format='PDF', save_all=True, append_images=pages[1:], resolution=144)
    for page in pages: page.close()
    require(len(out.getvalue()) <= 2097152, 'synthetic_pdf_size_limit')
    return out.getvalue()


def check_result(adapter, payload):
    markdown, images = adapter.mineru_pages_to_markdown(payload, filename=FILENAME)
    converted = adapter.weknora_response(markdown, images)
    require(bool(converted['results']['document']['md_content'].strip()), 'empty_converted_markdown')
    compact = re.sub(r'\s+', '', markdown)
    require(all(marker in compact for marker in ('8246', '7734')), 'two_page_ocr_markers_not_found')
    from PIL import Image
    size = 0
    require(len(images) <= 100, 'decoded_image_count_limit')
    for encoded in images.values():
        raw = base64.b64decode(encoded, validate=True)
        size += len(raw)
        require(size <= MAX_BODY, 'decoded_images_size_limit')
        with Image.open(io.BytesIO(raw)) as image:
            require(image.width * image.height <= 20000000, 'decoded_image_dimensions_limit')
            image.verify()
    return {'two_page_ocr_markers': 'PASS', 'adapter_response_conversion': 'PASS',
            'markdown_characters': len(markdown), 'decoded_images': len(images),
            'visual_return': 'PRESENT' if images else 'NOT_CONFIRMED',
            'business_document_quality': 'NOT_TESTED'}


def entry(token, source, source_sha):
    transport = None
    started = time.monotonic()
    result = {'async_smoke': 'FAIL', 'production_configuration_modified': False,
              'service_restarted': False, 'credentials_saved': False, 'automatic_resubmission': False,
              'ready_for_rollout': False, 'load_acceptance': 'NOT_TESTED'}
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 ** 2, 768 * 1024 ** 2))
        resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
        signal.signal(signal.SIGALRM, on_alarm)
        signal.alarm(340)
        sys.dont_write_bytecode = True
        logging.disable(logging.CRITICAL)
        validate_token(token)
        adapter = load_candidate(source, source_sha)
        ca_file = (os.getenv('MINERU_CA_BUNDLE', '').strip() or os.getenv('REQUESTS_CA_BUNDLE', '').strip()
                   or os.getenv('SSL_CERT_FILE', '').strip())
        require(bool(ca_file), 'reviewed_ca_not_configured')
        ca = Path(ca_file)
        require(ca.is_file() and ca.stat().st_size <= 4194304, 'configured_ca_unavailable')
        ca_sha = hashlib.sha256(ca.read_bytes()).hexdigest()
        require(ca_sha == CA_SHA, 'reviewed_ca_hash_changed')
        pdf = synthetic_pdf()
        emit({'remote_preflight': 'PASS', 'candidate_source_sha256': source_sha,
              'ca_sha256': ca_sha, 'tls_verification': True, 'proxy_inherited': False,
              'maximum_submissions': 1, 'synthetic_pages': 2, 'sample_bytes': len(pdf)})
        transport = BoundedTransport(token, ca_file)
        client = adapter.EnterpriseMinerUClient(api_url=BASE, task_query_url=TASKS, api_token=token,
            request_mode='async', timeout_seconds=240, poll_interval_seconds=3,
            transient_poll_retries=2, sync_transient_retries=0, session=transport)
        payload = client.parse_pdf(pdf, FILENAME)
        result.update(check_result(adapter, payload))
        result['async_smoke'] = 'PASS'
    except Exception as exc:
        result.update(error_class=error_class(exc), raw_error_printed=False)
    finally:
        signal.alarm(0)
        if transport:
            result.update(post_attempts=transport.posts, status_requests=transport.status_gets,
                          result_requests=transport.result_gets, accepted_task_id=transport.task_id,
                          last_request=transport.last)
            transport.close()
        else:
            result['post_attempts'] = 0
        result['elapsed_seconds'] = round(time.monotonic() - started, 2)
        if result['async_smoke'] != 'PASS': result['instruction'] = 'SEND_REPORT_DO_NOT_RESUBMIT'
        emit(result)
    return 0 if result['async_smoke'] == 'PASS' else 1
