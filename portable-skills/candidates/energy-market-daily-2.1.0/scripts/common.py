"""Portable I/O and private runtime configuration; no platform SDK required."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

ROOT = Path(__file__).resolve().parents[1]
BEIJING = dt.timezone(dt.timedelta(hours=8))


class Failure(Exception):
    def __init__(self, code, **details):
        super().__init__(code)
        self.code, self.details = code, details


def now():
    return dt.datetime.now(BEIJING)


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
    fd, temporary = tempfile.mkstemp(prefix='.daily-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load(path, limit=8 * 1024 * 1024):
    path = Path(path)
    if path.stat().st_size > limit:
        raise Failure('INPUT_SIZE_LIMIT')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def credentials(path=None):
    """Environment wins. Private JSON stays outside public source/release ZIPs."""
    values = {}
    selected = path or os.getenv('ENERGY_DAILY_CREDENTIALS')
    if selected:
        values = load(Path(selected).expanduser(), 16384)
    elif (ROOT / '.private' / 'credentials.json').is_file():
        values = load(ROOT / '.private' / 'credentials.json', 16384)
    if not isinstance(values, dict):
        raise Failure('CREDENTIAL_FORMAT_INVALID')
    def setting(key):
        return os.environ.get(key) or values.get(key)
    url = setting('JKX_API_URL') or setting('JKX_API_ENDPOINT')
    password = setting('JKX_API_PASSWORD')
    if not isinstance(url, str) or not url:
        raise Failure('JKX_API_URL_REQUIRED')
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parsed.query)
    password = password or query.get('p', [None])[0]
    if not isinstance(password, str) or not password:
        raise Failure('JKX_API_PASSWORD_REQUIRED')
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise Failure('JKX_API_URL_INVALID')
    # Existing author JSON uses JKX_API_ENDPOINT; accept both spellings.
    return parsed._replace(query='', fragment='').geturl(), password


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, deadline, timeout=8):
        self.deadline, self.timeout = deadline, timeout
        # Default ProxyHandler respects HTTP_PROXY/HTTPS_PROXY/NO_PROXY.
        # Never silently force direct internet access in enterprise networks.
        dedicated_proxy = os.environ.get('ENERGY_DAILY_HTTP_PROXY')
        handlers = [NoRedirect()]
        if dedicated_proxy:
            parsed = urllib.parse.urlsplit(dedicated_proxy)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname:
                raise Failure('ENERGY_DAILY_PROXY_INVALID')
            handlers.insert(0, urllib.request.ProxyHandler({'http': dedicated_proxy, 'https': dedicated_proxy}))
        self.opener = urllib.request.build_opener(*handlers)
        self.requests = 0

    def get(self, url, *, params=None, limit=4 * 1024 * 1024, redirects=0, allowed_hosts=None, compressed=False):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise Failure('ACQUISITION_DEADLINE')
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            raise Failure('SOURCE_URL_INVALID')
        if allowed_hosts and not any(parsed.hostname == host or parsed.hostname.endswith('.' + host) for host in allowed_hosts):
            raise Failure('SOURCE_HOST_REJECTED')
        if params:
            url += ('&' if parsed.query else '?') + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={'User-Agent': 'EnergyDaily/2.0 (+research)', 'Accept-Encoding': 'gzip' if compressed else 'identity'})
        self.requests += 1
        request_deadline = min(self.deadline, time.monotonic() + self.timeout)
        try:
            with self.opener.open(request, timeout=max(0.2, min(self.timeout, remaining))) as response:
                chunks, size = [], 0
                while True:
                    remaining = request_deadline - time.monotonic()
                    if remaining <= 0:
                        raise Failure('HTTP_REQUEST_DEADLINE')
                    try:
                        response.fp.raw._sock.settimeout(max(0.1, remaining))
                    except AttributeError:
                        pass
                    chunk = response.read1(min(65536, limit + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > limit:
                        raise Failure('SOURCE_RESPONSE_TOO_LARGE')
                raw = b''.join(chunks)
                encoding = response.headers.get('Content-Encoding', '').strip().lower()
                if encoding == 'gzip':
                    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
                    raw = decoder.decompress(raw, limit + 1)
                    if len(raw) > limit or not decoder.eof or decoder.unconsumed_tail:
                        raise Failure('SOURCE_RESPONSE_TOO_LARGE_OR_INVALID')
                elif encoding not in ('', 'identity'):
                    raise Failure('SOURCE_ENCODING_UNSUPPORTED')
                return raw
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308) and redirects:
                destination = urllib.parse.urljoin(url, exc.headers.get('Location', ''))
                target = urllib.parse.urlsplit(destination)
                if target.scheme != 'https' or not target.hostname or target.username or target.password:
                    raise Failure('SOURCE_REDIRECT_REJECTED') from None
                # Credential-bearing provider requests are always redirects=0.
                return self.get(destination, limit=limit, redirects=redirects - 1, allowed_hosts=allowed_hosts, compressed=compressed)
            raise Failure('HTTP_REQUEST_FAILED', http_status=exc.code) from None
        except Failure:
            raise
        except Exception:
            # urllib exception strings can contain the credential-bearing URL.
            raise Failure('TRANSPORT_REQUEST_FAILED') from None


def evidence(project, name, raw, url, **extra):
    path = Path(project) / 'sources' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {'source_url': url, 'retrieved_at': now().isoformat(), 'file': str(path.relative_to(project)).replace('\\', '/'),
            'sha256': digest(raw), **extra}
