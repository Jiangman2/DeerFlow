"""Build one linked report text from host context; never send or infer a URL."""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from urllib.parse import urlsplit

from common import Failure, digest, load, now
from market import freight_reference_note

MAX_PROJECTION_BYTES = 65536
MAX_TEXT_BYTES = 262144
EXTENSIONS = {'.png', '.html', '.json', '.txt'}
CONVERSATION_ENV = 'DEERFLOW_CONVERSATION_URL'


def require(ok, code):
    if not ok:
        raise Failure(code)


def public_conversation_url(value, thread_id=None):
    """Validate the platform chat route; no origin or task id is invented."""
    if not isinstance(value, str) or len(value) > 2048 or any(char.isspace() for char in value):
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if (parsed.scheme != 'https' or not host or parsed.username is not None
                or parsed.password is not None or parsed.port not in (None, 443)
                or parsed.query or parsed.fragment or host in ('localhost', 'localhost.localdomain')
                or host.endswith(('.localhost', '.local')) or '.' not in host
                or not re.fullmatch(r'[A-Za-z0-9.-]+', host)):
            return None
        try:
            ipaddress.ip_address(host)
            return None  # Public conversation URLs use the host's DNS origin.
        except ValueError:
            pass
        # Match DeerFlow's validate_thread_id, including API-created task ids.
        # Percent escapes, slash separators, dots, query and fragment stay out.
        match = re.fullmatch(r'/workspace/chats/([A-Za-z0-9_-]{1,64})', parsed.path)
        if not match:
            return None
        if thread_id is not None and thread_id != match.group(1):
            return None
        return value
    except (ValueError, TypeError):
        return None


def host_conversation_context():
    """Read the current command's host-injected value, never shell history."""
    raw = os.environ.get(CONVERSATION_ENV)
    url = public_conversation_url(raw)
    return {'conversation_url': url, 'conversation_thread_id': urlsplit(url).path.rsplit('/', 1)[1] if url else None,
            'conversation_link_available': bool(url), 'conversation_context_source': 'HOST_INJECTED_ENVIRONMENT' if url else None,
            'conversation_context_error': None if url else 'HOST_CONVERSATION_URL_INVALID' if raw else 'HOST_CONVERSATION_CONTEXT_UNAVAILABLE'}


def require_same_conversation(receipt, context, exact_url=False):
    """A previously linked project must not silently become another task."""
    previous = receipt.get('conversation_url')
    if previous is None:
        return
    require(public_conversation_url(previous) is not None, 'RESULT_CONVERSATION_INVALID')
    current = context.get('conversation_url')
    require(current is not None, 'HOST_CONVERSATION_CONTEXT_REQUIRED')
    require(urlsplit(previous).path == urlsplit(current).path, 'PROJECT_CONVERSATION_MISMATCH')
    if exact_url:
        require(previous == current, 'REPORT_CONVERSATION_CONTEXT_CHANGED')


def report_text(data, reply_text, context):
    """The normal generated TXT is also the single eventual robot reply."""
    require(isinstance(reply_text, str) and reply_text.startswith('油气与金融市场日报'), 'DELIVERY_TEXT_INVALID')
    require(len(reply_text.encode()) <= MAX_TEXT_BYTES, 'DELIVERY_TEXT_SIZE_LIMIT')
    require('发布日期：' + data['publication_date'] + '；行情日期：' + data['data_date'] in reply_text
            and '报告状态：' + data['quality'] in reply_text, 'DELIVERY_TEXT_REPORT_MISMATCH')
    url = context.get('conversation_url')
    if not url:
        return reply_text
    require(public_conversation_url(url) is not None, 'HOST_CONVERSATION_URL_INVALID')
    footer = '[点击查看完整日报](' + url + ')'
    # A 2.1.2 accepted TXT already has this exact last footer; compatibility
    # finalization must not repeat it or add unverified publication claims.
    if reply_text.rstrip().endswith(footer):
        return reply_text
    require(not re.search(r'^\[点击查看完整日报\]\(', reply_text, re.M), 'DELIVERY_TEXT_LINK_CONFLICT')
    return reply_text.rstrip() + '\n\n' + footer + '\n'


def conversation_summary(projection):
    """Keep a host-returned conversation link independent of file publication.

    This checks a transcription of the current present_files ToolMessage. It
    cannot authenticate the host or establish current-thread identity itself.
    Never construct an origin or a thread URL from local paths or report data.
    """
    result = {'conversation_url': None, 'conversation_link_available': False,
              'projection_basis': 'CALLER_SUPPLIED_TOOLMESSAGE_NOT_INDEPENDENT_HOST_ATTESTATION',
              'current_thread_identity_independently_verified': False,
              'conversation_reason': 'HOST_CONVERSATION_URL_UNAVAILABLE'}
    if not isinstance(projection, dict):
        return result
    artifact = projection.get('artifact')
    content = projection.get('content')
    if (artifact is not None and not isinstance(artifact, dict)) or not isinstance(content, str) or len(content.encode()) > MAX_PROJECTION_BYTES:
        return result
    if projection.get('name') != 'present_files' or projection.get('status') not in (None, 'success', 'error'):
        return result
    call_id = projection.get('tool_call_id')
    if not isinstance(call_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,256}', call_id):
        result['conversation_reason'] = 'HOST_CONVERSATION_IDENTITY_INCOMPLETE'
        return result
    # These are the host's success and failure result formats. Error status,
    # partial file sets and a missing publication run id do not remove a link.
    if not content.startswith(('Successfully presented files', 'Error:', 'File delivery incomplete')):
        return result
    prefixes = ('conversation_url: ', 'File delivery incomplete. conversation_url: ')
    receipt_urls = []
    for line in content.splitlines():
        for prefix in prefixes:
            if line.startswith(prefix):
                receipt_urls.append(line[len(prefix):])
                break
    if len(receipt_urls) > 1:
        result['conversation_reason'] = 'HOST_CONVERSATION_URL_CONFLICT'
        return result
    supplied = list(receipt_urls)
    if isinstance(artifact, dict) and 'conversation_url' in artifact:
        supplied.append(artifact['conversation_url'])
    if not supplied:
        return result
    valid = [public_conversation_url(value, projection.get('thread_id')) for value in supplied]
    if any(value is None for value in valid):
        result['conversation_reason'] = 'HOST_CONVERSATION_URL_INVALID'
        return result
    if len(set(valid)) != 1:
        result['conversation_reason'] = 'HOST_CONVERSATION_URL_CONFLICT'
        return result
    result.update(conversation_url=valid[0], conversation_link_available=True,
                  conversation_reason=None, tool_call_id_sha256=digest(call_id.encode()))
    return result


def publication_summary(projection, files):
    """Check publication separately; its status must not discard a host URL."""
    result = dict(conversation_summary(projection), confirmed=False, reason='HOST_PUBLICATION_NOT_CONFIRMED')
    if not isinstance(projection, dict):
        return result
    artifact = projection.get('artifact')
    content = projection.get('content')
    if (artifact is not None and not isinstance(artifact, dict)) or not isinstance(content, str) or len(content.encode()) > MAX_PROJECTION_BYTES:
        return result
    if (projection.get('name') != 'present_files' or projection.get('status') != 'success'
            or not content.startswith('Successfully presented files')
            or isinstance(artifact, dict) and artifact.get('publication_status') == 'failed'):
        return result
    call_id = projection.get('tool_call_id')
    if not isinstance(call_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,256}', call_id):
        result['reason'] = 'HOST_PUBLICATION_IDENTITY_INCOMPLETE'
        return result
    has_artifact = isinstance(artifact, dict)
    if has_artifact:
        published = artifact.get('presented_files')
        run_id = artifact.get('publication_run_id')
        if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', run_id):
            result['reason'] = 'HOST_PUBLICATION_IDENTITY_INCOMPLETE'
            return result
        result['file_binding_basis'] = 'ARTIFACT_PUBLISHED_FILE_NAMES'
        result['publication_run_id_sha256'] = digest(run_id.encode())
    else:
        # Standard model serialization may expose only ToolMessage content and
        # its call id. Do not fabricate a hidden artifact or publication run id.
        published = projection.get('requested_files')
        result['file_binding_basis'] = 'VISIBLE_TOOLMESSAGE_AND_ITS_REQUESTED_FILES'
        result['publication_run_binding_not_independently_verified'] = True
    if not isinstance(published, list) or len(published) != 4 or not all(isinstance(path, str) for path in published):
        result['reason'] = 'HOST_PUBLICATION_FILE_SET_INCOMPLETE'
        return result
    names = []
    for value in published:
        path = PurePosixPath(value.replace('\\', '/'))
        if len(value) > 8192 or not (path.is_absolute() or Path(value).is_absolute()) or '..' in path.parts or path.suffix not in EXTENSIONS:
            result['reason'] = 'HOST_PUBLICATION_FILE_SET_INCOMPLETE'
            return result
        names.append(path.name)
    expected = {Path(item['path']).name for item in files}
    if len(set(published)) != 4 or len(set(names)) != 4 or set(names) != expected:
        result['reason'] = 'HOST_PUBLICATION_FILE_SET_INCOMPLETE'
        return result
    if not has_artifact and {str(Path(path).resolve()) for path in published} != {str(Path(item['path']).resolve()) for item in files}:
        result['reason'] = 'HOST_PUBLICATION_REQUESTED_FILES_MISMATCH'
        return result
    result.update(confirmed=True, reason=None, tool_call_id_sha256=digest(call_id.encode()),
                  published_files_sha256=digest(json.dumps(published, ensure_ascii=False, separators=(',', ':')).encode()))
    return result


def quality_note(data):
    groups = [('原油', 'oil'), ('天然气', 'gas'), ('航运', 'freight'), ('股票', 'stocks'), ('汇率', 'fx')]
    missing, holidays = [], []
    for label, key in groups:
        quotes = data.get(key)
        require(isinstance(quotes, list), 'DELIVERY_REPORT_SCHEMA_INVALID')
        absent = sum(isinstance(item, dict) and item.get('status') == 'missing' for item in quotes)
        closed = sum(isinstance(item, dict) and item.get('status') == 'non_trading' for item in quotes)
        if absent:
            missing.append('%s%d项' % (label, absent))
        if closed:
            holidays.append('%s%d项' % (label, closed))
    parts = []
    if missing:
        parts.append('数据说明：%s数据未取得，相关字段保留缺值标注；未用旧价冒充目标日行情。' % '、'.join(missing))
    if holidays:
        parts.append('休市说明：%s按已核实来源标注休市，相关目标日报价留空。' % '、'.join(holidays))
    references = [quote for quote in data['freight'] if isinstance(quote, dict) and quote.get('status') == 'latest_available']
    if references:
        parts.append('航运参考报价说明（均非目标日报价）：' + '；'.join(
            quote['name'] + '：' + freight_reference_note(quote) for quote in references) + '。')
        if data.get('freight_reference_search_complete') is False:
            parts.append('运费回溯仍有日期未完成核验；以上为本次已取得的最近有效报价，不保证未核验日期没有更新报价。')
    return '\n'.join(parts)


def compose(data, reply_text, publication):
    """Compatibility composition; file publication is never predicted in prose."""
    return report_text(data, reply_text, publication)


def atomic_bytes(path, raw):
    descriptor, temporary = tempfile.mkstemp(prefix='.delivery-', dir=str(path.parent))
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def finalize(project, receipt, validate_result, projection_path=None):
    project = Path(project).resolve()
    require(receipt.get('status') == 'REPORT_ACCEPTED'
            and (receipt.get('visual_reviewed') is True or receipt.get('version') == '2.1.3-portable'
                 and receipt.get('acceptance_basis') == 'PROGRAMMATIC_FIXED_TEMPLATE_CHECKS'
                 and receipt.get('programmatic_checks_passed') is True)
            and receipt.get('publication_allowed') is True, 'DELIVERY_REPORT_NOT_ACCEPTED')
    validate_result(receipt, project)
    outputs = {Path(item['path']).suffix: Path(item['path']) for item in receipt['files']}
    require(set(outputs) == EXTENSIONS and len(receipt['files']) == 4, 'DELIVERY_REPORT_FILE_SET_INVALID')
    require(Path(receipt.get('reply_text_file', '')).resolve() == outputs['.txt'].resolve(), 'DELIVERY_REPLY_TEXT_IDENTITY')
    data = load(outputs['.json'])
    require(data.get('publication_date') == receipt.get('publication_date')
            and data.get('data_date') == receipt.get('data_date')
            and data.get('quality') == receipt.get('quality'), 'DELIVERY_REPORT_IDENTITY')
    raw_text = outputs['.txt'].read_bytes()
    require(len(raw_text) <= MAX_TEXT_BYTES, 'DELIVERY_TEXT_SIZE_LIMIT')
    text = raw_text.decode('utf-8')
    projection = None; projection_sha = None
    if projection_path:
        source = Path(projection_path)
        require(not source.is_symlink(), 'DELIVERY_PROJECTION_PATH_INVALID')
        source = source.resolve()
        try:
            source.relative_to(project)
        except ValueError:
            raise Failure('DELIVERY_PROJECTION_OUTSIDE_PROJECT') from None
        require(source.is_file() and source.stat().st_size <= MAX_PROJECTION_BYTES, 'DELIVERY_PROJECTION_SIZE_LIMIT')
        raw_projection = source.read_bytes()
        require(len(raw_projection) <= MAX_PROJECTION_BYTES, 'DELIVERY_PROJECTION_SIZE_LIMIT')
        projection = json.loads(raw_projection.decode('utf-8-sig')); projection_sha = digest(raw_projection)
    publication = publication_summary(projection, receipt['files'])
    current = host_conversation_context()
    projected_url = publication.get('conversation_url')
    if current['conversation_url'] and projected_url:
        require(current['conversation_url'] == projected_url, 'HOST_CONVERSATION_LINK_CONFLICT')
    if current['conversation_url']:
        publication.update(conversation_url=current['conversation_url'], conversation_link_available=True,
                           conversation_reason=None, conversation_context_source='HOST_INJECTED_ENVIRONMENT')
    else:
        # A missing-context compatibility call must not overwrite an existing
        # linked result or create a successful, externally sendable draft.
        return {'status': 'WAITING_FOR_CONVERSATION_LINK', 'reply_text_ready': False,
                'external_delivery_ready': False, 'ready_after_successful_publication': False,
                'conversation_link_available': False, 'final_text_file': None,
                'conversation_reason': current['conversation_context_error'],
                'caller_projection_cannot_supply_current_identity': True,
                'accepted_files_preserved': True, 'existing_final_text_preserved': True,
                'next_action': 'stop_and_report_host_context_unavailable', 'messages_sent': 0}
    require_same_conversation(receipt, publication, exact_url=True)
    final = compose(data, text, publication).encode()
    directory = project / 'delivery'
    require(not directory.is_symlink() and directory.resolve().parent == project, 'DELIVERY_DIRECTORY_INVALID')
    directory.mkdir(exist_ok=True)
    final_path, manifest_path = directory / 'final.md', directory / 'MANIFEST.json'
    require(not final_path.is_symlink() and not manifest_path.is_symlink(), 'DELIVERY_PATH_INVALID')
    identity = {'schema': 'energy-daily-final-text.v1', 'formatter_version': '2.1.2-portable', 'accepted_result_sha256': digest((project / 'RESULT.json').read_bytes()),
                'accepted_files': [{'name': Path(item['path']).name, 'bytes': item['bytes'], 'sha256': item['sha256']} for item in receipt['files']],
                'outputs_generation': receipt.get('outputs_generation'), 'publication_date': data['publication_date'], 'data_date': data['data_date'],
                'quality': data['quality'], 'publication_projection_sha256': projection_sha, 'publication': publication,
                'final_text_sha256': digest(final), 'final_text_bytes': len(final),
                'caller_supplied_projection_is_not_independent_host_attestation': True,
                'same_file_for_web_and_explicitly_authorized_external_delivery': True,
                'external_delivery_requested': False, 'messages_sent': 0}
    reused = final_path.is_file() and final_path.read_bytes() == final
    if not reused:
        atomic_bytes(final_path, final)
    old = load(manifest_path) if manifest_path.is_file() else None
    comparable = {key: value for key, value in old.items() if key != 'composed_at'} if isinstance(old, dict) else None
    if comparable != identity:
        manifest = dict(identity, composed_at=now().isoformat())
        atomic_bytes(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False).encode())
    # Reconcile accepted inputs after composition; this does not re-acquire sources.
    validate_result(receipt, project)
    require(digest((project / 'RESULT.json').read_bytes()) == identity['accepted_result_sha256'], 'DELIVERY_ACCEPTANCE_CHANGED')
    return {'status': 'FINAL_TEXT_READY', 'final_text_file': str(final_path), 'manifest_file': str(manifest_path),
            'final_text_sha256': identity['final_text_sha256'], 'final_text_bytes': len(final), 'reused': reused,
            'reply_text_ready': True, 'ready_after_successful_publication': True,
            'external_delivery_ready_by_caller_projection': publication['confirmed'],
            'delivery_requires_successful_publication': True,
            'publication_confirmed_by_caller_projection': publication['confirmed'], 'conversation_url': publication['conversation_url'],
            'conversation_link_available': publication['conversation_link_available'], 'conversation_reason': publication['conversation_reason'],
            'caller_supplied_projection_is_not_independent_host_attestation': True,
            'same_file_for_web_and_explicitly_authorized_external_delivery': True, 'messages_sent': 0}
