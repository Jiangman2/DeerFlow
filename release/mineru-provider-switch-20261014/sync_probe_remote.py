"""Bounded, memory-only FC-01 smoke probe. Loaded through kubectl exec stdin.

No production settings, provider task polling, retry, file upload from disk,
service startup, or writes. The exact installed adapter runs in a new process.
"""
import base64
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import signal
import stat
import sys
import time
import types

ENDPOINT = 'https://aiapi.cnooc.com.cn/MinerU2.5-2605-1.2B/FC-01/file_parse'
SOURCE = '/app/deerflow_mineru_adapter.py'
SOURCE_SHA = '1c54fbea077137f39a4ce66b1cc309d7e643541808f4b9670fe0fb8037eb6b39'
MAX_BODY = 16 * 1024 * 1024
CASE_SECONDS = 300


class ProbeFailure(RuntimeError):
    pass


def require(value, label):
    if not value:
        raise ProbeFailure(label)


def emit(value):
    print(json.dumps(value, sort_keys=True), flush=True)


def alarm_handler(signum, frame):
    raise ProbeFailure('bounded_case_deadline')


def error_class(exc):
    # Never emit exception text, provider messages, URLs or response bodies.
    if isinstance(exc, ProbeFailure):
        return str(exc)
    import requests
    if isinstance(exc, requests.exceptions.SSLError):
        return 'TLS_VERIFICATION_FAILED'
    if isinstance(exc, requests.exceptions.Timeout):
        return 'NETWORK_TIMEOUT_OUTCOME_UNCERTAIN'
    if isinstance(exc, requests.exceptions.ConnectionError):
        return 'NETWORK_CONNECTION_FAILED_OUTCOME_UNCERTAIN'
    if isinstance(exc, requests.exceptions.HTTPError):
        return 'UPSTREAM_HTTP_ERROR'
    return 'CLIENT_OR_RESULT_COMPATIBILITY_FAILURE'


class BoundedTransport:
    """Inject limits into the unmodified client, not a replacement protocol."""
    def __init__(self, token, ca_file):
        import requests
        self.token = token
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.verify = ca_file or True
        self.session.mount('https://', requests.adapters.HTTPAdapter(max_retries=0))
        self.attempts = 0
        self.last = {}

    def post(self, url, *, headers, files, data, timeout):
        import requests
        require(self.attempts == 0, 'repeat_submission_refused')
        require(url == ENDPOINT, 'unapproved_endpoint')
        require(headers.get('Authorization') == 'Bearer ' + self.token, 'credential_binding')
        require(set(files) == {'files'}, 'unexpected_multipart')
        filename, content, media_type = files['files']
        require(filename in ('mineru-synthetic.pdf', 'mineru-synthetic.png'), 'synthetic_only')
        require(media_type in ('application/pdf', 'image/png'), 'synthetic_type')
        require(0 < len(content) <= 2 * 1024 * 1024, 'synthetic_size')
        self.attempts += 1
        started = time.monotonic()
        with self.session.post(url, headers=headers, files=files, data=data,
                               timeout=(8, min(240, timeout)), allow_redirects=False,
                               stream=True) as response:
            self.last['http_status'] = response.status_code
            require(not 300 <= response.status_code < 400, 'redirect_refused')
            length = response.headers.get('Content-Length')
            if length is not None:
                require(length.isdigit() and int(length) <= MAX_BODY, 'response_size_limit')
            parts, size = [], 0
            for chunk in response.iter_content(chunk_size=65536):
                require(time.monotonic() - started <= CASE_SECONDS, 'bounded_case_deadline')
                size += len(chunk)
                require(size <= MAX_BODY, 'response_size_limit')
                parts.append(chunk)
            body = b''.join(parts)
            self.last['response_bytes'] = size
            try:
                envelope = json.loads(body)
                if isinstance(envelope, dict):
                    code = envelope.get('code')
                    self.last['provider_code'] = (str(code) if isinstance(code, (str, int))
                                                   and re.fullmatch(r'-?\d{1,12}', str(code)) else None)
                    self.last['provider_success'] = envelope.get('success') if type(envelope.get('success')) is bool else None
            except (ValueError, UnicodeError):
                self.last['json_response'] = False
            # Existing client's status, envelope and result checks run unchanged.
            bounded = requests.Response()
            bounded.status_code = response.status_code
            bounded._content = body
            bounded.encoding = 'utf-8'
            bounded.url = ENDPOINT
            return bounded

    def close(self):
        self.session.close()


def load_adapter():
    path = Path(SOURCE)
    st = path.lstat()
    require(stat.S_ISREG(st.st_mode) and not path.is_symlink() and st.st_size < 2 * 1024 * 1024,
            'unexpected_adapter_source_file')
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == SOURCE_SHA, 'adapter_source_changed')
    module = types.ModuleType('mineru_sync_probe_baseline')
    module.__file__ = SOURCE
    sys.modules[module.__name__] = module
    # __name__ is not __main__: no HTTP listener or dispatcher is started.
    exec(compile(raw, SOURCE, 'exec'), module.__dict__)
    return module


def fixtures():
    from PIL import Image, ImageDraw, ImageFont
    def sheet(marker, number):
        image = Image.new('RGB', (1200, 1600), 'white')
        draw = ImageDraw.Draw(image)
        try:
            font = ImageFont.truetype('DejaVuSans.ttf', 40)
            small = ImageFont.truetype('DejaVuSans.ttf', 28)
        except OSError:
            # Make the bundled bitmap font legible without installing fonts.
            font = small = None
        def text(x, y, value, title=False):
            chosen = font if title else small
            if chosen:
                draw.text((x, y), value, fill='black', font=chosen)
            else:
                tile = Image.new('RGB', (600, 20), 'white')
                ImageDraw.Draw(tile).text((0, 0), value, fill='black', font=ImageFont.load_default())
                image.paste(tile.resize((1800, 60)), (x, y))
        text(70, 75, 'MINERU TEST 2605', True)
        text(70, 170, 'Synthetic document - no business data')
        text(70, 255, 'Verification number ' + marker, True)
        text(70, 355, 'Item                      Quantity')
        text(70, 420, 'Alpha                     12')
        text(70, 485, 'Beta                      34')
        draw.rectangle((70, 600, 1080, 1220), outline='black', width=3)
        draw.rectangle((170, 980, 350, 1160), fill='#2266bb')
        draw.rectangle((450, 830, 630, 1160), fill='#dd8833')
        draw.rectangle((730, 690, 910, 1160), fill='#339977')
        text(70, 1270, 'Figure 1. Synthetic chart for image-return inspection')
        text(70, 1470, 'Page ' + str(number))
        return image
    one, two = sheet('8246', 1), sheet('7734', 2)
    pdf, png = io.BytesIO(), io.BytesIO()
    one.save(pdf, format='PDF', save_all=True, append_images=[two], resolution=144.0)
    one.save(png, format='PNG')
    return [('PDF_2_PAGES', 'mineru-synthetic.pdf', pdf.getvalue(), ('8246', '7734')),
            ('PNG_1_IMAGE', 'mineru-synthetic.png', png.getvalue(), ('8246',))]


def result_summary(adapter, payload, filename, markers):
    markdown, images = adapter.mineru_pages_to_markdown(payload, filename=filename)
    wrapped = adapter.weknora_response(markdown, images)
    require(bool(wrapped['results']['document']['md_content'].strip()), 'empty_converted_markdown')
    compact = re.sub(r'\s+', '', markdown)
    marker_hits = sum(marker in compact for marker in markers)
    require(marker_hits == len(markers), 'synthetic_content_check_failed')
    from PIL import Image
    valid_images = 0
    total_image_bytes = 0
    for encoded in images.values():
        raw = base64.b64decode(encoded, validate=True)
        total_image_bytes += len(raw)
        require(total_image_bytes <= MAX_BODY, 'decoded_images_limit')
        with Image.open(io.BytesIO(raw)) as image:
            require(image.width * image.height <= 20000000, 'decoded_image_dimensions_limit')
            image.verify()
        valid_images += 1
    return {'adapter_response_conversion': 'PASS', 'synthetic_markers': marker_hits,
            'markdown_characters': len(markdown), 'decoded_images': valid_images,
            'image_bytes': total_image_bytes,
            'visual_return': 'PRESENT' if valid_images else 'NOT_CONFIRMED',
            'business_document_quality': 'NOT_TESTED'}


def run(token):
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # These resource limits affect only this disposable exec process.
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 ** 2, 768 * 1024 ** 2))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    sys.dont_write_bytecode = True
    logging.disable(logging.CRITICAL)
    require(isinstance(token, str) and 8 <= len(token) <= 4096 and token.isascii()
            and all(32 < ord(c) < 127 for c in token), 'invalid_credential_input')
    signal.signal(signal.SIGALRM, alarm_handler)
    signal.alarm(40)
    try:
        adapter = load_adapter()
        cases = fixtures()
        ca_file = (os.getenv('MINERU_CA_BUNDLE', '').strip()
                   or os.getenv('REQUESTS_CA_BUNDLE', '').strip()
                   or os.getenv('SSL_CERT_FILE', '').strip())
        ca_sha = None
        if ca_file:
            path = Path(ca_file)
            require(path.is_file() and path.stat().st_size <= 4 * 1024 ** 2, 'configured_ca_missing_or_oversized')
            ca_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    finally:
        signal.alarm(0)
    emit({'remote_preflight': 'PASS', 'source_sha256': SOURCE_SHA,
          'endpoint': ENDPOINT, 'ca_sha256': ca_sha, 'tls_verification': True,
          'proxy_inherited': False, 'automatic_retry': False, 'maximum_posts': 2,
          'production_configuration_modified': False, 'service_restarted': False,
          'samples': 'SYNTHETIC_IN_MEMORY_ONLY', 'credentials_printed': False})
    total = 0
    for case, filename, content, markers in cases:
        transport = BoundedTransport(token, ca_file)
        started = time.monotonic()
        row = {'case': case, 'sample_bytes': len(content), 'result': 'FAIL'}
        emit({'provider_case': case, 'state': 'START', 'max_wall_seconds': CASE_SECONDS,
              'automatic_retry': False})
        try:
            signal.alarm(CASE_SECONDS)
            client = adapter.EnterpriseMinerUClient(
                api_url=ENDPOINT, api_token=token, timeout_seconds=240,
                request_mode='sync', sync_transient_retries=0,
                poll_interval_seconds=3, transient_poll_retries=0,
                session=transport)
            payload = (client.parse_pdf(content, filename) if filename.endswith('.pdf')
                       else client.parse_image(content, filename, '.png'))
            row.update(result_summary(adapter, payload, filename, markers))
            row['result'] = 'PASS'
        except Exception as exc:
            row['error_class'] = error_class(exc)
            row['raw_error_printed'] = False
        finally:
            signal.alarm(0)
            total += transport.attempts
            row.update(transport.last)
            row['post_attempts'] = transport.attempts
            row['elapsed_seconds'] = round(time.monotonic() - started, 2)
            transport.close()
        emit(row)
        if row['result'] != 'PASS':
            emit({'sync_smoke': 'FAIL', 'post_attempts': total, 'automatic_retry': False,
                  'ready_for_config_switch': False,
                  'instruction': 'SEND_REPORT_DO_NOT_AUTOMATICALLY_REPEAT'})
            return 1
    emit({'sync_smoke': 'PASS', 'post_attempts': total, 'automatic_retry': False,
          'ready_for_config_switch': False, 'large_document_acceptance': 'NOT_TESTED',
          'concurrency_acceptance': 'NOT_TESTED', 'weknora_ingestion': 'NOT_TESTED',
          'production_configuration_modified': False, 'credentials_printed': False,
          'note': 'SMOKE_ONLY_NOT_RELEASE_ACCEPTANCE'})
    return 0


def entry(token):
    try:
        return run(token)
    except Exception as exc:
        emit({'sync_smoke': 'INCOMPLETE', 'error_class': error_class(exc),
              'raw_error_printed': False, 'automatic_retry': False,
              'production_configuration_modified': False, 'ready_for_config_switch': False})
        return 1
