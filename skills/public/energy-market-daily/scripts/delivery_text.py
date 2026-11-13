"""Compose a single final reply; never send messages or infer a platform URL."""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from urllib.parse import urlsplit
import uuid

from common import Failure, digest, load, now

MAX_PROJECTION_BYTES = 65536
MAX_TEXT_BYTES = 262144
EXTENSIONS = {'.png', '.html', '.json', '.txt'}


def require(ok, code):
    if not ok:
        raise Failure(code)


def public_conversation_url(value, thread_id=None):
    """Validate only a caller-supplied host receipt URL; no origin is invented."""
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
        match = re.fullmatch(r'/workspace/chats/([0-9a-f-]{36})', parsed.path)
        if not match or str(uuid.UUID(match.group(1))) != match.group(1):
            return None
        if thread_id is not None and thread_id != match.group(1):
            return None
        return value
    except (ValueError, TypeError):
        return None


def publication_summary(projection, files):
    """Check the projection's consistency, not the caller's platform identity."""
    result = {'confirmed': False, 'conversation_url': None,
              'projection_basis': 'CALLER_SUPPLIED_TOOLMESSAGE_NOT_INDEPENDENT_HOST_ATTESTATION',
              'current_thread_identity_independently_verified': False,
              'reason': 'HOST_PUBLICATION_NOT_CONFIRMED'}
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
    receipt_urls = [line[len('conversation_url: '):] for line in content.splitlines() if line.startswith('conversation_url: ')]
    if not has_artifact and {str(Path(path).resolve()) for path in published} != {str(Path(item['path']).resolve()) for item in files}:
        result['reason'] = 'HOST_PUBLICATION_REQUESTED_FILES_MISMATCH'
        return result
    supplied_url = artifact.get('conversation_url') if has_artifact else receipt_urls[0] if len(receipt_urls) == 1 else None
    url = public_conversation_url(supplied_url, projection.get('thread_id'))
    if url is None or receipt_urls != [url]:
        result['reason'] = 'HOST_CONVERSATION_URL_UNAVAILABLE'
        return result
    result.update(confirmed=True, conversation_url=url, reason=None, tool_call_id_sha256=digest(call_id.encode()),
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
        parts.append('休市说明：%s按已核实来源标注休市，未补入旧价。' % '、'.join(holidays))
    return '\n'.join(parts)


def compose(data, reply_text, publication):
    """Preserve accepted report text byte-for-byte before appending delivery text."""
    require(isinstance(reply_text, str) and reply_text.startswith('油气与金融市场日报'), 'DELIVERY_TEXT_INVALID')
    require(len(reply_text.encode()) <= MAX_TEXT_BYTES, 'DELIVERY_TEXT_SIZE_LIMIT')
    require('发布日期：' + data['publication_date'] + '；行情日期：' + data['data_date'] in reply_text
            and '报告状态：' + data['quality'] in reply_text, 'DELIVERY_TEXT_REPORT_MISMATCH')
    # Only new suffix content is authored here. The accepted TXT is untouched.
    body = reply_text + ('' if reply_text.endswith('\n') else '\n') + '\n'
    note = quality_note(data)
    if note:
        body += note + '\n\n'
    if publication['confirmed']:
        body += 'PNG长图、HTML、JSON和完整文字已在本次对话发布。\n\n'
        body += '[点击查看完整日报](' + publication['conversation_url'] + ')\n'
    else:
        body += '日报文件已生成，平台发布尚未确认；当前没有已确认的完整日报链接。\n'
    return body


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
    require(receipt.get('status') == 'REPORT_ACCEPTED' and receipt.get('visual_reviewed') is True
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
    final = compose(data, text, publication).encode()
    directory = project / 'delivery'
    require(not directory.is_symlink() and directory.resolve().parent == project, 'DELIVERY_DIRECTORY_INVALID')
    directory.mkdir(exist_ok=True)
    final_path, manifest_path = directory / 'final.md', directory / 'MANIFEST.json'
    require(not final_path.is_symlink() and not manifest_path.is_symlink(), 'DELIVERY_PATH_INVALID')
    identity = {'schema': 'energy-daily-final-text.v1', 'formatter_version': '2.1.0-portable', 'accepted_result_sha256': digest((project / 'RESULT.json').read_bytes()),
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
            'publication_confirmed_by_caller_projection': publication['confirmed'], 'conversation_url': publication['conversation_url'],
            'caller_supplied_projection_is_not_independent_host_attestation': True,
            'same_file_for_web_and_explicitly_authorized_external_delivery': True, 'messages_sent': 0}
