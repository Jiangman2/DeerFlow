"""Send one accepted, published daily-reader link to an explicitly requested bot."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import Failure, digest, load, now
from delivery_text import host_conversation_context, html_report_url, require_same_conversation

HOSTS = frozenset(('open.w.cnooc.com.cn', 'open.feishu.cn', 'open.larksuite.com'))
LABEL = '点击查看日报网页'
MAX_RESPONSE = 16384
TIMEOUT = 20


def require(ok, code):
    if not ok:
        raise Failure(code)


def endpoint(value):
    require(isinstance(value, str) and len(value) <= 2048 and not any(c.isspace() for c in value), 'WEBHOOK_URL_INVALID')
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == 'https' and parsed.hostname in HOSTS and parsed.port in (None, 443)
                 and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
                 and re.fullmatch(r'/open-apis/bot/v2/hook/[A-Za-z0-9_-]{16,256}', parsed.path))
    except ValueError:
        valid = False
    require(valid, 'WEBHOOK_URL_NOT_SUPPORTED')
    return value


def bounded_file(path, limit):
    require(not path.is_symlink(), 'WEBHOOK_FILE_SYMLINK_REJECTED')
    with path.open('rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size <= limit, 'WEBHOOK_FILE_BOUND')
        value = stream.read(limit + 1)
    require(len(value) <= limit, 'WEBHOOK_FILE_BOUND')
    return value


def request_url(project, path=None):
    target = Path(path) if path else project / 'delivery/webhook-request.json'
    require(not target.is_symlink(), 'WEBHOOK_REQUEST_PATH_INVALID')
    target = target.resolve()
    require(project in target.parents and 'outputs' not in target.relative_to(project).parts, 'WEBHOOK_REQUEST_PATH_INVALID')
    try:
        request = json.loads(bounded_file(target, 8192).decode('utf-8-sig'))
    except (ValueError, UnicodeError):
        raise Failure('WEBHOOK_REQUEST_JSON_INVALID') from None
    require(isinstance(request, dict) and set(request) == {'webhook_url'}, 'WEBHOOK_EXPLICIT_URL_REQUIRED')
    url = endpoint(request['webhook_url'])
    os.chmod(target, 0o600)
    return url


def published_report(project):
    import daily
    receipt = load(project / 'RESULT.json')
    require(receipt.get('status') == 'REPORT_ACCEPTED' and receipt.get('publication_allowed') is True,
            'WEBHOOK_REPORT_NOT_ACCEPTED')
    daily.validate_result(receipt, project)
    context = host_conversation_context()
    require(context['conversation_link_available'], 'WEBHOOK_HOST_CONTEXT_REQUIRED')
    require_same_conversation(receipt, context, exact_url=True)
    reader = html_report_url(context, receipt['publication_date'])
    require(reader and receipt.get('html_report_url') == reader, 'WEBHOOK_READER_IDENTITY_INVALID')
    workspaces = [p for p in (project, *project.parents) if p.name == 'workspace']
    require(bool(workspaces), 'WEBHOOK_PUBLISHED_OUTPUTS_UNAVAILABLE')
    output = workspaces[0].parent / 'outputs'
    require(output.is_dir() and not output.is_symlink(), 'WEBHOOK_PUBLISHED_OUTPUTS_UNAVAILABLE')
    # present_files copies each accepted workspace artifact to this thread's
    # persistent outputs root. The check verifies those real copies, avoiding a
    # model-authored publication receipt. It is not an authenticated ToolMessage.
    for item in receipt['files']:
        target = output / Path(item['path']).name
        require(target.is_file(), 'WEBHOOK_FILES_NOT_PUBLISHED')
        try:
            value = bounded_file(target, 64 * 1024 * 1024)
        except PermissionError as exc:
            raise Failure('WEBHOOK_PUBLISHED_FILE_PERMISSION_DENIED', error_class='PermissionError',
                          stage='verify_published_outputs', file_extension=target.suffix,
                          provider_request_started=False, errno=exc.errno) from None
        require(len(value) == item['bytes'] and digest(value) == item['sha256'], 'WEBHOOK_PUBLISHED_FILE_CHANGED')
    text_path = next(Path(item['path']) for item in receipt['files'] if item['path'].endswith('.txt'))
    text = text_path.read_text(encoding='utf-8')
    require(text.splitlines()[0] == '[' + LABEL + '](' + reader + ')', 'WEBHOOK_TEXT_READER_MISMATCH')
    return receipt, reader


def message(reader):
    # Feishu-compatible rich text gives a real mobile hyperlink, with no report
    # body, unsupported markdown message type, or second correction message.
    return {'msg_type': 'post', 'content': {'post': {'zh_cn': {'content': [[
        {'tag': 'a', 'text': LABEL, 'href': reader}]]}}}}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def post_once(url, payload):
    endpoint(url)
    opener = urllib.request.build_opener(NoRedirect())  # Preserve approved proxy/CA configuration.
    request = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type': 'application/json; charset=utf-8', 'User-Agent': 'EnergyDaily/2.1.8'}, method='POST')
    try:
        with opener.open(request, timeout=15) as response:
            status = response.status
            raw = response.read(MAX_RESPONSE + 1)
        if status != 200 or len(raw) > MAX_RESPONSE:
            return {'outcome': 'UNKNOWN', 'reason': 'WEBHOOK_RESPONSE_UNCONFIRMED', 'http_status': status}
        value = json.loads(raw)
        if not isinstance(value, dict):
            return {'outcome': 'UNKNOWN', 'reason': 'WEBHOOK_RESPONSE_UNCONFIRMED', 'http_status': status}
        codes = [value[key] for key in ('code', 'StatusCode') if key in value]
        if not codes or any(type(code) is not int for code in codes):
            return {'outcome': 'UNKNOWN', 'reason': 'WEBHOOK_RESPONSE_UNCONFIRMED', 'http_status': status}
        accepted = all(code == 0 for code in codes)
        return {'outcome': 'SENT' if accepted else 'FAILED', 'http_status': status,
                'reason': None if accepted else 'WEBHOOK_PROVIDER_REJECTED', 'provider_codes': codes}
    except urllib.error.HTTPError as exc:
        return {'outcome': 'FAILED' if 400 <= exc.code < 500 else 'UNKNOWN',
                'reason': 'WEBHOOK_HTTP_REJECTED' if 400 <= exc.code < 500 else 'WEBHOOK_RESPONSE_UNCONFIRMED',
                'http_status': exc.code}
    except Exception:
        return {'outcome': 'UNKNOWN', 'reason': 'WEBHOOK_TRANSPORT_OUTCOME_UNKNOWN'}


def child_post(url, payload):
    try:
        result = subprocess.run([sys.executable, '-I', '-B', str(Path(__file__).resolve()), '__post'],
            input=json.dumps({'url': url, 'payload': payload}).encode(), capture_output=True, timeout=TIMEOUT)
        value = json.loads(result.stdout) if result.returncode == 0 and len(result.stdout) <= 2048 else None
        require(isinstance(value, dict) and value.get('outcome') in ('SENT', 'FAILED', 'UNKNOWN'), 'WEBHOOK_CHILD_RESULT_INVALID')
        if value['outcome'] == 'SENT':
            require(value.get('http_status') == 200 and isinstance(value.get('provider_codes'), list)
                    and bool(value['provider_codes']) and all(type(code) is int and code == 0 for code in value['provider_codes']),
                    'WEBHOOK_CHILD_RESULT_INVALID')
        return value
    except Exception:
        # The request may have reached the bot. Never resubmit an uncertain POST.
        return {'outcome': 'UNKNOWN', 'reason': 'WEBHOOK_TRANSPORT_OUTCOME_UNKNOWN'}


def write_record(path, value, exclusive=False):
    raw = (json.dumps(value, sort_keys=True, ensure_ascii=False) + '\n').encode()
    if exclusive:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        return
    temporary = path.with_name(path.name + '.update-' + os.urandom(8).hex())
    try:
        write_record(temporary, value, exclusive=True)
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if os.name != 'nt' or attempt == 4:
                    raise
                # Windows readers may briefly deny deletion of the old journal.
                # Retrying this local rename never repeats the outbound POST.
                time.sleep(.01 * (attempt + 1))
    finally:
        if temporary.exists():
            temporary.unlink()


def send(project, request_file=None):
    progress = {'stage': 'verify_report', 'provider_request_started': False}
    try:
        return _send(project, request_file, progress)
    except PermissionError as exc:
        raise Failure('WEBHOOK_LOCAL_PERMISSION_DENIED', error_class='PermissionError', errno=exc.errno,
                      retry_allowed=False, **progress) from None


def _send(project, request_file, progress):
    project = Path(project).expanduser().resolve()
    receipt, reader = published_report(project)
    progress['stage'] = 'read_explicit_request'
    url = request_url(project, request_file)
    payload = message(reader)
    key = digest((url + '\n' + reader).encode())
    directory = project / 'delivery'
    progress['stage'] = 'prepare_send_journal'
    require(not directory.is_symlink(), 'WEBHOOK_JOURNAL_PATH_INVALID')
    directory.mkdir(exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    path = directory / ('webhook-' + key + '.json')
    record = {'schema': 'energy-webhook-attempt.v1', 'status': 'SENDING', 'created_at': now().isoformat(),
              'target_sha256': digest(url.encode()), 'reader_url': reader, 'conversation_url': receipt['conversation_url'],
              'publication_date': receipt['publication_date'], 'payload_sha256': digest(json.dumps(payload, sort_keys=True).encode()),
              'published_files_verified': True, 'publication_basis': 'MATCHING_CURRENT_THREAD_OUTPUT_COPIES',
              'automatic_retry': False, 'webhook_url_exported': False}
    try:
        progress['stage'] = 'claim_send_journal'
        write_record(path, record, exclusive=True)
    except FileExistsError:
        try:
            prior = json.loads(bounded_file(path, 16384))
        except (ValueError, UnicodeError):
            raise Failure('WEBHOOK_PREVIOUS_OUTCOME_UNKNOWN', retry_allowed=False) from None
        require(prior.get('target_sha256') == record['target_sha256'] and prior.get('reader_url') == reader
                and prior.get('payload_sha256') == record['payload_sha256'], 'WEBHOOK_JOURNAL_IDENTITY_INVALID')
        if prior.get('status') == 'SENT':
            return {'status': 'WEBHOOK_ALREADY_SENT', 'messages_sent_this_call': 0, 'reader_url': reader,
                    'receipt_file': str(path), 'final_text_file': receipt['final_text_file'], 'automatic_retry': False}
        raise Failure('WEBHOOK_PREVIOUS_SEND_FAILED' if prior.get('status') == 'FAILED' else 'WEBHOOK_PREVIOUS_OUTCOME_UNKNOWN',
                      retry_allowed=False, receipt_file=str(path))
    progress.update(stage='send', provider_request_started=True)
    result = child_post(url, payload)
    record.update(status=result['outcome'], finished_at=now().isoformat(),
                  reason=result.get('reason'), http_status=result.get('http_status'), provider_codes=result.get('provider_codes'))
    progress['stage'] = 'persist_send_receipt'
    write_record(path, record)
    if result['outcome'] != 'SENT':
        raise Failure(result.get('reason') or 'WEBHOOK_SEND_FAILED', retry_allowed=False,
                      delivery_status=result['outcome'], report_generated=True, receipt_file=str(path), reader_url=reader,
                      http_status=result.get('http_status'), provider_codes=result.get('provider_codes'))
    return {'status': 'WEBHOOK_SENT', 'messages_sent_this_call': 1, 'reader_url': reader,
            'receipt_file': str(path), 'final_text_file': receipt['final_text_file'], 'automatic_retry': False}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ['__post']:
        try:
            raw = sys.stdin.buffer.read(8193)
            require(len(raw) <= 8192, 'WEBHOOK_CHILD_INPUT_INVALID')
            data = json.loads(raw)
            require(isinstance(data, dict) and set(data) == {'url', 'payload'}, 'WEBHOOK_CHILD_INPUT_INVALID')
            anchor = data['payload']['content']['post']['zh_cn']['content'][0][0]
            reader = anchor['href']
            match = re.search(r'/([0-9]{4}-[0-9]{2}-[0-9]{2})\.html$', reader)
            context = host_conversation_context()
            require(context['conversation_link_available'] and match
                    and html_report_url(context, match.group(1)) == reader
                    and data['payload'] == message(reader), 'WEBHOOK_CHILD_PAYLOAD_INVALID')
            result = post_once(data['url'], data['payload'])
        except Exception:
            result = {'outcome': 'UNKNOWN', 'reason': 'WEBHOOK_TRANSPORT_OUTCOME_UNKNOWN'}
        print(json.dumps(result))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--request-file')
    args = parser.parse_args(argv)
    try:
        result = send(args.workspace, args.request_file)
        code = 0
    except Failure as exc:
        result, code = {'status': 'WEBHOOK_FAILED', 'error_code': exc.code,
                        'recovery_scope': 'operator', 'retry_allowed': False, **exc.details}, 1
    except Exception as exc:
        result, code = {'status': 'WEBHOOK_FAILED', 'error_code': 'WEBHOOK_LOCAL_FAILURE',
                        'error_class': type(exc).__name__, 'recovery_scope': 'operator', 'retry_allowed': False}, 1
    print(json.dumps(result, ensure_ascii=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
