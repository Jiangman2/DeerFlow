#!/usr/bin/env python3
"""Fixed Gateway overlay qualification; run only in an isolated maintenance Pod.

This is not a Gateway entrypoint and never loads its live application config.
The operator supplies the exact four sealed candidate files as read-only mounts.
The host must additionally enforce the image identity, Pod isolation, execution
timeout and output bound. A successful synthetic result is not a rollout permit.
"""

import argparse
import asyncio
import contextlib
import copy
import hashlib
import importlib
import io
import json
import logging
import os
from pathlib import Path
import platform
import ssl
import stat
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


ROOT = Path('/app/backend')
FILES = {'app/channels/manager.py': (94358, '1a782faf4e4cdbd491871972cca3e501d7a3011da3869fac398f994bedef56f1'), 'app/integrations/work/notifications.py': (10025, '2f25958bcb2556685d153bef55f72afebd360deb157cd13164ceb4261001f039'), 'app/scheduler/work_notification_delivery.py': (4297, 'd446868a681754fa98241b62ebfe45a886c6bf21b0da0f68d0d69cd111e8a42e'), 'app/scheduler/work_notifications.py': (14201, 'd3f7e6c4acc4fd0cb7e4f6b31861ddf8b68ff1d2f44761a99da90536a6982645'), 'packages/harness/deerflow/tools/builtins/present_file_tool.py': (19640, 'eccfe1e5975c771465abf96f1da60fdb2a11e0712716aeb2453a2d10fbe30e3b'), 'packages/harness/deerflow/utils/conversation_url.py': (4466, 'ef20ca84241617a489859fca4f660b07ba5923616828e0fad654b6c59d2f4abc')}
ORIGIN = 'https://portal.example.com'
THREAD = 'synthetic-current-thread_123'
URL = ORIGIN + '/workspace/chats/' + THREAD
VIRTUAL_FILE = '/mnt/user-data/outputs/report.txt'
OPENSSL_COMPAT = '/etc/deerflow-openssl/openssl.cnf'
OPENSSL_COMPAT_SHA = '20130c1b277e0a5fb79a27af7dc35be38162a6e086aa91e45b4f249d73c61604'
SAFE_ERRORS = frozenset({
    'APPLICATION_BOOTSTRAP_IMPORTED', 'CANDIDATE_FILE_CHANGED', 'CANDIDATE_FILE_HASH_MISMATCH',
    'CANDIDATE_FILE_NOT_REGULAR', 'CANDIDATE_FILE_SIZE_MISMATCH', 'CANDIDATE_READBACK_CHANGED',
    'CONFIG_EXCEPTION_ESCAPED', 'CONFIGURED_ORIGIN_FAILED', 'DIAGNOSTIC_OUTPUT_BOUND',
    'EXPLICIT_FALLBACK_FAILED', 'FAILED_PUBLICATION_LINKED', 'FILE_OWNER_MISMATCH',
    'FINAL_HOST_LINK_MISMATCH', 'FINAL_REPLY_REQUIRED', 'FOOTER_DEDUP_FAILED', 'FOOTER_NOT_IDEMPOTENT',
    'INVALID_CONFIG_FALLBACK_USED', 'INVALID_ORIGIN_ACCEPTED', 'INVALID_THREAD_ACCEPTED',
    'MISSING_ORIGIN_BREAKS_COMPATIBILITY', 'MISSING_ORIGIN_GUESSED', 'MODEL_CAN_SET_THREAD_LINK',
    'OIDC_ORIGIN_FAILED', 'PRODUCTION_HTTP_ACCEPTED', 'PROGRESS_LINK_LEAKED', 'PUBLICATION_ARTIFACT_FAILED',
    'PUBLICATION_TEXT_FAILED', 'SCHEDULER_DEFAULT_CHANGED', 'SCHEDULER_FOOTER_FAILED', 'SCHEDULER_UAT_CHANGED',
    'SYNTHETIC_EXTERNAL_OPERATION_BLOCKED', 'SYNTHETIC_PRIVATE_READ_BLOCKED',
    'SYNTHETIC_WRITE_OUTSIDE_TEMP_BLOCKED', 'DAILY_SOURCE_LINK_FAILED', 'DAILY_CURRENT_LINK_FAILED', 'FAILED_DELIVERY_FALSE_COMPLETE', 'DAILY_SUMMARY_BOUND', 'PRIVATE_WORK_SEND_CHANGED', 'UNCERTAIN_WORK_RETRIED', 'UAT_COMPATIBILITY_FAILED', 'UNQUALIFIED_REPLY_LINKED',
    'UNTRUSTED_LINK_USED', 'WRONG_IMPORT_ORIGIN', 'OPENSSL_COMPAT_IDENTITY_MISMATCH',
})


class Refused(Exception):
    pass


class QuietBuffer(io.TextIOBase):
    """Discard redacted import/log text, retaining only a bounded byte count."""

    def __init__(self):
        self.bytes = 0

    def write(self, text):
        self.bytes += len(text.encode('utf-8', 'replace'))
        if self.bytes > 65536:
            raise Refused('DIAGNOSTIC_OUTPUT_BOUND')
        return len(text)

    def flush(self):
        pass


def require(condition, code):
    if not condition:
        raise Refused(code)


def verify_files(root):
    result = {}
    for relative, (expected_bytes, expected_sha) in FILES.items():
        path = root / relative
        before = path.lstat()
        require(stat.S_ISREG(before.st_mode), 'CANDIDATE_FILE_NOT_REGULAR')
        require(before.st_size == expected_bytes, 'CANDIDATE_FILE_SIZE_MISMATCH')
        with path.open('rb') as stream:
            data = stream.read(expected_bytes + 1)
        after = path.lstat()
        require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), 'CANDIDATE_FILE_CHANGED')
        require(len(data) == expected_bytes and hashlib.sha256(data).hexdigest() == expected_sha,
                'CANDIDATE_FILE_HASH_MISMATCH')
        compile(data, str(path), 'exec', dont_inherit=True)
        result[relative] = {'bytes': expected_bytes, 'sha256': expected_sha}
    return result


def verified_openssl_environment():
    """Retain only the known, non-secret compatibility config used by Gateway."""
    raw = os.environ.get('OPENSSL_CONF')
    if not raw:
        return {}
    require(raw == OPENSSL_COMPAT, 'OPENSSL_COMPAT_IDENTITY_MISMATCH')
    path = Path(raw)
    # Kubernetes projected ConfigMaps use symlinks. The immutable file payload,
    # not an operator-controlled arbitrary filename, is the acceptance boundary.
    before = path.stat()
    require(stat.S_ISREG(before.st_mode) and before.st_size == 189, 'OPENSSL_COMPAT_IDENTITY_MISMATCH')
    with path.open('rb') as stream:
        data = stream.read(190)
    after = path.stat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) and
            len(data) == 189 and hashlib.sha256(data).hexdigest() == OPENSSL_COMPAT_SHA,
            'OPENSSL_COMPAT_IDENTITY_MISMATCH')
    return {'OPENSSL_CONF': OPENSSL_COMPAT}


def install_audit_guard(workspace):
    """Process-local defense for synthetic tests, not a claim about cluster policy."""
    workspace = workspace.resolve()

    def inside_temporary(path):
        try:
            Path(path).resolve().relative_to(workspace)
            return True
        except (ValueError, TypeError, OSError):
            return False

    def inside_mutation(path, dir_fd=None):
        if not isinstance(path, (str, bytes, os.PathLike)):
            return False
        candidate = Path(os.fsdecode(path))
        if not candidate.is_absolute() and isinstance(dir_fd, int) and dir_fd >= 0:
            try:
                candidate = Path(os.readlink('/proc/self/fd/' + str(dir_fd))) / candidate
            except OSError:
                return False
        return inside_temporary(candidate)

    def audit(event, args):
        if event in {'socket.connect', 'socket.connect_ex', 'socket.getaddrinfo', 'socket.bind', 'socket.sendto',
                     'subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.posix_spawnp', 'sqlite3.connect'}:
            raise Refused('SYNTHETIC_EXTERNAL_OPERATION_BLOCKED')
        if event == 'open' and args and isinstance(args[0], (str, bytes, os.PathLike)):
            path = os.fsdecode(args[0])
            normalized = path.replace('\\', '/').lower()
            name = normalized.rsplit('/', 1)[-1]
            if '/var/run/secrets/' in normalized or name == '.env' or name.startswith('.env.') or name in {
                'config.yaml', 'config.yml', 'extensions_config.json', 'api-credential.private.json', 'admin.conf'
            }:
                raise Refused('SYNTHETIC_PRIVATE_READ_BLOCKED')
            flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            writable = flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
            if writable and os.path.normcase(path) != os.path.normcase(os.devnull) and not inside_temporary(path):
                raise Refused('SYNTHETIC_WRITE_OUTSIDE_TEMP_BLOCKED')
        if event in {'os.remove', 'os.rmdir', 'os.mkdir', 'os.rename', 'os.chmod', 'os.truncate', 'os.chdir'} and args:
            if event == 'os.rename':
                paths = ((args[0], args[2] if len(args) > 2 else None),
                         (args[1], args[3] if len(args) > 3 else None))
            else:
                fd_index = 2 if event in {'os.mkdir', 'os.chmod'} else 1
                paths = ((args[0], args[fd_index] if len(args) > fd_index and event != 'os.truncate' else None),)
            for path, dir_fd in paths:
                if not inside_mutation(path, dir_fd):
                    raise Refused('SYNTHETIC_WRITE_OUTSIDE_TEMP_BLOCKED')

    sys.addaudithook(audit)


def synthetic_config(*, iam=None, oidc=None):
    return SimpleNamespace(auth=SimpleNamespace(
        iam=SimpleNamespace(enabled=iam is not None, frontend_base_url=iam),
        oidc=SimpleNamespace(enabled=oidc is not None, frontend_base_url=oidc)))


def publications():
    return [
        {'type': 'human', 'content': 'synthetic report'},
        {'type': 'ai', 'content': '', 'tool_calls': [{'name': 'present_files', 'args': {'filepaths': [VIRTUAL_FILE]}}]},
        {'type': 'tool', 'name': 'present_files', 'status': 'success', 'content': 'Successfully presented files',
         'artifact': {'presented_files': [VIRTUAL_FILE], 'conversation_url': 'https://untrusted.invalid/other'}},
        {'type': 'ai', 'content': 'synthetic report ready'},
    ]


async def exercise_channel(manager_module, workspace, *, streaming, workspace_source):
    from app.channels.message_bus import InboundMessage, MessageBus, ResolvedAttachment
    from app.channels.store import ChannelStore

    bus = MessageBus()
    bus.publish_outbound = AsyncMock()
    manager = manager_module.ChannelManager(bus=bus, store=ChannelStore(path=workspace / 'store.json'))
    file = workspace / 'report.txt'
    file.write_text('synthetic', encoding='utf-8')
    attachment = ResolvedAttachment(VIRTUAL_FILE, file, 'report.txt', 'text/plain', 9, True)
    owner_reads = []

    def resolve(thread_id, paths, *, user_id=None):
        owner_reads.append((thread_id, user_id))
        return [attachment] if paths == [VIRTUAL_FILE] else []

    result = {'messages': publications()}
    if workspace_source:
        result['messages'][1]['tool_calls'][0]['args']['filepaths'] = ['/mnt/user-data/workspace/report.txt']
    client = MagicMock()
    client.runs.wait = AsyncMock(return_value=result)

    async def stream():
        yield SimpleNamespace(event='messages', data=[{'id': 'ai-1', 'type': 'AIMessageChunk', 'content': 'working'}, {'langgraph_node': 'agent'}])
        yield SimpleNamespace(event='values', data=result)

    client.runs.stream = MagicMock(return_value=stream()) if streaming else MagicMock()
    inbound = InboundMessage(channel_name='feishu', chat_id='synthetic-work-chat', user_id='synthetic-user', text='synthetic report')
    with patch.object(manager, '_resolve_run_params', return_value=('lead_agent', {}, {})), \
            patch.object(manager, '_apply_channel_policy', AsyncMock(return_value=None)), \
            patch.object(manager, '_channel_supports_streaming', return_value=streaming), \
            patch.object(manager_module, '_resolve_attachments', resolve), \
            patch.object(manager_module, 'STREAM_UPDATE_MIN_INTERVAL_SECONDS', 0):
        await manager._handle_chat_on_thread(client, inbound, THREAD, storage_user_id='synthetic-owner')
    replies = [call.args[0] for call in bus.publish_outbound.call_args_list]
    require(bool(replies) and replies[-1].is_final, 'FINAL_REPLY_REQUIRED')
    require(replies[-1].text.endswith('查看完整结果：' + URL) and replies[-1].text.count(URL) == 1,
            'FINAL_HOST_LINK_MISMATCH')
    require('untrusted.invalid' not in replies[-1].text, 'UNTRUSTED_LINK_USED')
    require(all(URL not in reply.text for reply in replies if not reply.is_final), 'PROGRESS_LINK_LEAKED')
    require(bool(owner_reads) and all(item == (THREAD, 'synthetic-owner') for item in owner_reads), 'FILE_OWNER_MISMATCH')


def exercise(root, workspace, result, loop):
    sys.path[:0] = [str(root), str(root / 'packages/harness')]
    result['step'] = 'import_fixed_modules'
    import dotenv

    # AppConfig imports dotenv at module scope. The isolated qualifier must not
    # read even image-baked .env files; it supplies every setting synthetically.
    # croniter asks platform.architecture(), which otherwise runs the external
    # `file` command on Linux. Pointer bitness is exact for this interpreter and
    # avoids granting arbitrary subprocess execution merely for an import probe.
    bits = '64bit' if sys.maxsize > 2**32 else '32bit'
    with patch.object(dotenv, 'load_dotenv', return_value=False), \
            patch.object(platform, 'architecture', return_value=(bits, '')):
        links = importlib.import_module('deerflow.utils.conversation_url')
        present = importlib.import_module('deerflow.tools.builtins.present_file_tool')
        manager = importlib.import_module('app.channels.manager')
        delivery = importlib.import_module('app.scheduler.work_notification_delivery')
        notifications = importlib.import_module('app.integrations.work.notifications')
        outbox = importlib.import_module('app.scheduler.work_notifications')
    require('app.gateway.app' not in sys.modules, 'APPLICATION_BOOTSTRAP_IMPORTED')
    for module, relative in ((links, 'packages/harness/deerflow/utils/conversation_url.py'),
                             (present, 'packages/harness/deerflow/tools/builtins/present_file_tool.py'),
                             (manager, 'app/channels/manager.py'),
                             (delivery, 'app/scheduler/work_notification_delivery.py'),
                             (notifications, 'app/integrations/work/notifications.py'),
                             (outbox, 'app/scheduler/work_notifications.py')):
        require(Path(module.__file__).resolve() == (root / relative).resolve(), 'WRONG_IMPORT_ORIGIN')
    result['imported_candidate_modules'] = len(FILES)
    result['step'] = 'synthetic_link_and_delivery_checks'
    checks = []

    with patch.object(links, 'get_app_config', return_value=synthetic_config(iam=ORIGIN)):
        require(links.current_conversation_url(THREAD) == URL, 'CONFIGURED_ORIGIN_FAILED')
        checks.append('configured_iam_origin')
        for bad in ('', 'http://portal.example.com', 'https://name:private@portal.example.com',
                    ORIGIN + '/path', ORIGIN + '/?token=private', ORIGIN + '/#fragment',
                    ORIGIN + ':bad', ORIGIN + ':65536', 'https://[invalid', ORIGIN + '\\other',
                    ORIGIN + '\n.invalid', '//portal.example.com'):
            require(links.validated_web_origin(bad) == '', 'INVALID_ORIGIN_ACCEPTED')
        checks.append('invalid_origins_rejected')
        for bad in (None, '', '../other', 'other?admin_review=1', 'x' * 65, '中文'):
            require(links.current_conversation_url(bad) == '', 'INVALID_THREAD_ACCEPTED')
        checks.append('invalid_threads_rejected')
        require(links.validated_web_origin('http://localhost:3000', profile='uat') == 'http://localhost:3000', 'UAT_COMPATIBILITY_FAILED')
        require(links.validated_web_origin('http://localhost:3000') == '', 'PRODUCTION_HTTP_ACCEPTED')
        checks.append('uat_loopback_only')
        for existing in (URL, '<' + URL + '>', '[result](' + URL + ')', '查看完整结果：' + URL):
            text = links.append_conversation_link('report\n' + existing + '\nlast detail', URL)
            require(text.endswith('last detail\n\n查看完整结果：' + URL) and text.count(URL) == 1, 'FOOTER_DEDUP_FAILED')
            require(links.append_conversation_link(text, URL) == text, 'FOOTER_NOT_IDEMPOTENT')
        checks.append('single_footer_last_and_idempotent')

        outputs = workspace / 'outputs'
        outputs.mkdir()
        file = outputs / 'report.txt'
        file.write_text('synthetic report', encoding='utf-8')
        runtime = SimpleNamespace(state={'thread_data': {'outputs_path': str(outputs), 'workspace_path': str(workspace / 'workspace')}},
                                  context={'thread_id': THREAD}, config={})
        for use_config in (False, True):
            if use_config:
                runtime.context = {}
                runtime.config = {'configurable': {'thread_id': THREAD}}
            command = present.present_file_tool.func(runtime=runtime, filepaths=[str(file)], tool_call_id='synthetic-success')
            message = command.update['messages'][0]
            require(message.content == 'Successfully presented files\nconversation_url: ' + URL, 'PUBLICATION_TEXT_FAILED')
            require(message.artifact['conversation_url'] == URL and message.artifact['presented_files'] == [VIRTUAL_FILE], 'PUBLICATION_ARTIFACT_FAILED')
        require('conversation_url' not in present.present_file_tool.args and 'thread_id' not in present.present_file_tool.args, 'MODEL_CAN_SET_THREAD_LINK')
        checks.append('present_files_trusted_runtime_context_and_config')
        failed = present.present_file_tool.func(runtime=runtime, filepaths=[str(file) + '.missing'], tool_call_id='synthetic-failed')
        message = failed.update['messages'][0]
        require(message.status == 'error' and message.artifact['publication_status'] == 'failed' and message.artifact['presented_files'] == [] and message.artifact['conversation_url'] == URL, 'FAILED_PUBLICATION_LINKED')
        checks.append('failed_publication_explicit_failure_link')

        for streaming in (False, True):
            for workspace_source in (False, True):
                loop.run_until_complete(exercise_channel(manager, workspace, streaming=streaming, workspace_source=workspace_source))
                checks.append(('streaming' if streaming else 'ordinary') + ('_workspace' if workspace_source else '_outputs') + '_work_final')

        from app.channels.message_bus import ResolvedAttachment
        attachment = ResolvedAttachment(VIRTUAL_FILE, file, 'report.txt', 'text/plain', 16, True)
        with patch.object(manager, '_resolve_attachments', return_value=[]):
            for case in ('failed', 'old_turn', 'missing_receipt', 'missing_file', 'other_channel'):
                messages = copy.deepcopy(publications())
                channel, files = 'feishu', [attachment]
                if case == 'failed':
                    messages[2]['status'] = 'error'
                elif case == 'old_turn':
                    messages.append({'type': 'human', 'content': 'new request'})
                elif case == 'missing_receipt':
                    messages.pop(2)
                elif case == 'missing_file':
                    files = []
                else:
                    channel = 'slack'
                text = manager._append_presented_conversation_link('original', channel_name=channel, thread_id=THREAD,
                                                                   result={'messages': messages}, attachments=files)
                require(text == 'original', 'UNQUALIFIED_REPLY_LINKED')
        checks.append('only_current_successful_work_delivery')

    with patch.object(links, 'get_app_config', return_value=synthetic_config(oidc=ORIGIN + '/')):
        require(links.current_conversation_url(THREAD) == URL, 'OIDC_ORIGIN_FAILED')
    checks.append('oidc_origin')
    with patch.object(links, 'get_app_config', return_value=synthetic_config()):
        require(links.current_conversation_url(THREAD) == '', 'MISSING_ORIGIN_GUESSED')
        message = present.present_file_tool.func(runtime=runtime, filepaths=[str(file)], tool_call_id='synthetic-no-origin').update['messages'][0]
        require(message.content == 'Successfully presented files' and 'conversation_url' not in message.artifact, 'MISSING_ORIGIN_BREAKS_COMPATIBILITY')
        with patch.dict(os.environ, {'DEER_FLOW_WORK_SCHEDULED_NOTIFICATION_WEB_URL': ORIGIN}):
            require(links.current_conversation_url(THREAD) == URL, 'EXPLICIT_FALLBACK_FAILED')
    checks.append('missing_origin_and_explicit_fallback')
    with patch.object(links, 'get_app_config', side_effect=RuntimeError('synthetic hidden detail')):
        require(links.current_conversation_url(THREAD) == '', 'CONFIG_EXCEPTION_ESCAPED')
    checks.append('configuration_error_soft_failure')
    with patch.object(links, 'get_app_config', return_value=synthetic_config(iam=ORIGIN + '/invalid')), \
            patch.dict(os.environ, {'DEER_FLOW_WORK_SCHEDULED_NOTIFICATION_WEB_URL': ORIGIN}):
        require(links.current_conversation_url(THREAD) == '', 'INVALID_CONFIG_FALLBACK_USED')
    checks.append('invalid_primary_cannot_fallback')
    require(delivery._platform_origin('production') == 'https://cnoocclaw.cnooc.com.cn', 'SCHEDULER_DEFAULT_CHANGED')
    require(delivery._platform_origin('uat') == '', 'SCHEDULER_UAT_CHANGED')
    notification = SimpleNamespace(title='synthetic report', result_text='complete ' + URL, finished_at=None, thread_id=THREAD)
    text = delivery._message(notification, ORIGIN)
    require(text.endswith('查看完整结果：' + URL) and text.count(URL) == 1 and '[链接见平台]' in text, 'SCHEDULER_FOOTER_FAILED')
    checks.append('scheduler_existing_message_contract')
    exercise_daily(links, manager, delivery, notifications, outbox, workspace, loop, checks)
    result['checks'] = checks
    result['synthetic_checks'] = len(checks)


def exercise_daily(links, manager, delivery, notifications, outbox, workspace, loop, checks):
    from app.channels.message_bus import ResolvedAttachment
    paths = ['/mnt/user-data/outputs/油气与金融市场日报_2026-11-11.' + ext for ext in ('png', 'html', 'json', 'txt')]
    messages = publications()
    messages[2]['artifact']['presented_files'] = paths
    file = workspace / 'outputs/report.txt'
    attachments = [ResolvedAttachment(path, file, path.rsplit('/', 1)[-1], 'text/plain', 16, True) for path in paths]
    with patch.object(links, 'get_app_config', return_value=synthetic_config(iam=ORIGIN)), patch.object(manager, '_resolve_attachments', return_value=[]):
        text = manager._append_presented_conversation_link('日报\n会话ID：wrong-id\n平台入口：https://wrong.invalid/drive/home/', channel_name='feishu', thread_id=THREAD, result={'messages': messages}, attachments=attachments)
        require(text.endswith('[点击查看完整日报](' + URL + ')') and 'wrong.invalid' not in text and 'wrong-id' not in text, 'DAILY_CURRENT_LINK_FAILED')
        require(links.append_conversation_link(text, URL, daily=True) == text, 'FOOTER_NOT_IDEMPOTENT')
        checks.append('daily_click_label_trusted_current_thread_no_guessed_portal')
        messages[2].update(status='error', artifact={'publication_status': 'failed', 'presented_files': []})
        text = manager._append_presented_conversation_link('日报正文', channel_name='feishu', thread_id=THREAD, result={'messages': messages}, attachments=[])
        require('文件交付未完成。' in text and '[查看本次对话](' + URL + ')' in text and '点击查看完整日报' not in text, 'FAILED_DELIVERY_FALSE_COMPLETE')
        checks.append('failed_delivery_link_not_complete_report')
    body = '油气与金融市场日报\n1. [来源新闻](https://oilprice.com/real.html)\n其他：https://unsafe.invalid/private?token=fixture'
    notification = SimpleNamespace(title='日报', result_text=body, finished_at=None, thread_id=THREAD)
    text = delivery._message(notification, ORIGIN)
    require('[来源新闻](https://oilprice.com/real.html)' in text and 'unsafe.invalid' not in text and text.endswith('[点击查看完整日报](' + URL + ')'), 'DAILY_SOURCE_LINK_FAILED')
    checks.append('scheduled_daily_public_news_links_only')
    require(outbox._notification_summary(body) == body, 'DAILY_SUMMARY_BOUND')
    clipped = outbox._notification_summary('油气与金融市场日报\n' + '一段中文\n' * 2000)
    require(len(clipped.encode()) <= 9000 and clipped.endswith('正文较长，完整内容请点击日报链接查看。'), 'DAILY_SUMMARY_BOUND')
    require(outbox._notification_summary('ordinary' * 300) == ('ordinary' * 300)[:1200], 'DAILY_SUMMARY_BOUND')
    checks.append('daily_summary_whole_lines_bounded_ordinary_unchanged')
    from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
    create = MagicMock(return_value=SimpleNamespace(code=0, raw=SimpleNamespace(status_code=200), data=SimpleNamespace(message_id='om_synthetic_receipt')))
    channel = SimpleNamespace(_api_client=SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(message=SimpleNamespace(create=create)))),
                              is_running=True, _CreateMessageRequest=CreateMessageRequest, _CreateMessageRequestBody=CreateMessageRequestBody)
    target = SimpleNamespace(chat_id='oc_synthetic_private', binding_key='synthetic-binding')
    with patch.object(notifications, '_current_runtime', AsyncMock(return_value=(None, channel))), patch.object(notifications, '_targets', AsyncMock(return_value=[target])):
        receipt = loop.run_until_complete(notifications.send_work_notification('synthetic-owner', 'synthetic-connection', text, 'synthetic-once-id', binding_key='synthetic-binding', markdown=True))
        request = create.call_args.args[0]
        require(receipt == 'om_synthetic_receipt' and create.call_count == 1 and request.body.msg_type == 'interactive' and request.body.receive_id == 'oc_synthetic_private' and request.body.uuid == 'synthetic-once-id' and json.loads(request.body.content)['elements'] == [{'tag': 'markdown', 'content': text}], 'PRIVATE_WORK_SEND_CHANGED')
        checks.append('scheduled_markdown_reuses_private_target_client_and_uuid')
        create.reset_mock()
        create.side_effect = TimeoutError()
        try:
            loop.run_until_complete(notifications.send_work_notification('synthetic-owner', 'synthetic-connection', text, 'synthetic-once-id', binding_key='synthetic-binding', markdown=True))
            raise Refused('UNCERTAIN_WORK_RETRIED')
        except notifications.WorkNotificationError as exc:
            require(exc.uncertain is True and create.call_count == 1, 'UNCERTAIN_WORK_RETRIED')
        checks.append('uncertain_markdown_send_never_retried_or_downgraded')


def qualify(root=ROOT):
    result = {'schema': 'energy-daily-result-link-qualification.v2', 'status': 'INCOMPLETE',
              'application_started': False, 'database_access': False, 'provider_requests': 0,
              'work_messages_sent': 0, 'live_configuration_verified': False, 'ready_for_rollout': False,
              'raw_errors_exported': False, 'persistent_files_modified': False, 'step': 'verify_candidate_files'}
    try:
        sys.dont_write_bytecode = True
        result['candidate_files'] = verify_files(root)
        result['candidate_files_verified'] = len(FILES)
        openssl_environment = verified_openssl_environment()
        result['openssl_compat_verified'] = bool(openssl_environment)
        # Initialize the interpreter's TLS provider before optional native
        # dependency imports; this creates no connection and weakens no TLS rule.
        ssl.create_default_context()
        # Windows asyncio uses a local socket pair for its wake-up pipe. Create
        # it before installing the no-network guard, never during app imports.
        loop = asyncio.new_event_loop()
        with tempfile.TemporaryDirectory(prefix='energy-link-qualification-') as temporary:
            workspace = Path(temporary)
            os.environ.clear()
            os.environ.update({'HOME': str(workspace), 'TMPDIR': str(workspace), 'PYTHONDONTWRITEBYTECODE': '1',
                               'PYTHON_DOTENV_DISABLED': '1', 'LANGCHAIN_TRACING_V2': 'false'})
            os.environ.update(openssl_environment)
            install_audit_guard(workspace)
            logging.disable(logging.CRITICAL)
            silent = QuietBuffer()
            with contextlib.redirect_stdout(silent), contextlib.redirect_stderr(silent):
                try:
                    exercise(root, workspace, result, loop)
                finally:
                    loop.close()
            result['redacted_diagnostic_bytes'] = silent.bytes
            result['step'] = 'stable_candidate_readback'
            require(result['candidate_files'] == verify_files(root), 'CANDIDATE_READBACK_CHANGED')
        result['temporary_files_cleaned'] = True
        result['status'] = 'GATEWAY_CONVERSATION_LINK_SYNTHETIC_ACCEPTED'
    except BaseException as exc:
        result['error_class'] = type(exc).__name__ if isinstance(exc, (Refused, ImportError, SyntaxError, OSError, RuntimeError, ValueError, TypeError, KeyError, AssertionError)) else 'OTHER'
        # Only this module's static Refused messages are eligible for export.
        result['error_code'] = str(exc) if isinstance(exc, Refused) and str(exc) in SAFE_ERRORS else 'SYNTHETIC_QUALIFICATION_FAILED'
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--qualify-fixed-candidate', action='store_true', required=True)
    parser.parse_args()
    if platform.system() != 'Linux' or not hasattr(os, 'geteuid') or os.geteuid() != 0 or sys.version_info < (3, 12):
        result = {'schema': 'energy-daily-result-link-qualification.v2', 'status': 'INCOMPLETE',
                  'error_code': 'ISOLATED_LINUX_ROOT_PYTHON312_REQUIRED', 'application_started': False,
                  'database_access': False, 'provider_requests': 0, 'work_messages_sent': 0,
                  'candidate_files_verified': 0, 'ready_for_rollout': False}
    else:
        result = qualify()
    print(json.dumps(result, sort_keys=True, ensure_ascii=True))
    return 0 if result['status'] == 'GATEWAY_CONVERSATION_LINK_SYNTHETIC_ACCEPTED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
