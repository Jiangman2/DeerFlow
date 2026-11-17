"""Read-only YAML/source qualification in an existing Gateway; never import app."""
import base64
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import stat
import sys

CONFIG = '/app/backend/config.yaml'
SOURCE_PATHS = ('/app/backend/app/channels/manager.py', '/app/backend/app/gateway/routers/uploads.py', '/app/backend/app/gateway/services.py', '/app/backend/app/gateway/upload_document_memory_guard.py', '/app/backend/app/integrations/work/notifications.py', '/app/backend/app/scheduler/service.py', '/app/backend/app/scheduler/work_notification_delivery.py', '/app/backend/app/scheduler/work_notifications.py', '/app/backend/packages/harness/deerflow/agents/lead_agent/agent.py', '/app/backend/packages/harness/deerflow/config/app_config.py', '/app/backend/packages/harness/deerflow/config/run_ownership_config.py', '/app/backend/packages/harness/deerflow/config/scheduler_config.py', '/app/backend/packages/harness/deerflow/persistence/scheduled_task_runs/sql.py', '/app/backend/packages/harness/deerflow/sandbox/tools.py', '/app/backend/packages/harness/deerflow/tools/builtins/present_file_tool.py', '/app/backend/packages/harness/deerflow/utils/conversation_url.py', '/etc/deerflow-openssl/openssl.cnf')
PACKAGES = {'langchain': '1.2.15', 'langchain-core': '1.3.3', 'langgraph': '1.1.9'}


class Refused(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise Refused(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def audit(event, args):
    if event == 'open':
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 else 0
        need(not (isinstance(mode, str) and any(x in mode for x in 'wax+')),
             'CHILD_WRITE_BLOCKED')
        need(not (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)),
             'CHILD_WRITE_BLOCKED')
    if event.startswith(('socket.', 'subprocess.', 'os.exec', 'os.spawn')) or event in (
        'os.system', 'os.remove', 'os.rename', 'os.mkdir', 'os.rmdir', 'os.chmod', 'os.chown', 'os.link', 'os.symlink',
    ):
        raise Refused('CHILD_SIDE_EFFECT_BLOCKED')


def bounded_read(path, limit):
    need(path == CONFIG or path in SOURCE_PATHS, 'CHILD_PATH_SCOPE')
    target = Path(path)
    # A directory-projected ConfigMap has Kubernetes' own ..data symlink.
    # It is permitted only inside this fixed OpenSSL projection, not elsewhere.
    if path == '/etc/deerflow-openssl/openssl.cnf':
        resolved = target.resolve(strict=True)
        need(Path('/etc/deerflow-openssl') in resolved.parents, 'OPENSSL_LINK_SCOPE')
        target = resolved
    else:
        need(not target.is_symlink(), 'FIXED_FILE_SYMLINK')
    fd = os.open(str(target), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= limit, 'FIXED_FILE_BOUND')
        raw = os.read(fd, limit + 1)
        after = os.fstat(fd)
        signature = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        need(len(raw) == before.st_size and signature(before) == signature(after) == signature(target.stat()),
             'FIXED_FILE_CHANGED')
        return raw
    finally:
        os.close(fd)


def parse_yaml(raw):
    import yaml

    class StrictLoader(yaml.SafeLoader):
        pass

    def mapping(loader, node, deep=False):
        need(node.tag == 'tag:yaml.org,2002:map', 'YAML_MAPPING_TAG')
        result = {}
        for key_node, value_node in node.value:
            need(key_node.tag != 'tag:yaml.org,2002:merge', 'YAML_MERGE_REFUSED')
            key = loader.construct_object(key_node, deep=deep)
            need(isinstance(key, str) and key not in result, 'YAML_DUPLICATE_OR_NONSTRING_KEY')
            result[key] = loader.construct_object(value_node, deep=deep)
        return result

    StrictLoader.add_constructor('tag:yaml.org,2002:map', mapping)
    # No aliases: avoids cyclic comparison and implicit edits through shared nodes.
    class NoAliases(StrictLoader):
        def compose_node(self, parent, index):
            need(not self.check_event(yaml.AliasEvent), 'YAML_ALIAS_REFUSED')
            return super().compose_node(parent, index)

    parsed = yaml.load(raw.decode('utf-8'), Loader=NoAliases)
    need(isinstance(parsed, dict), 'YAML_ROOT_MAPPING_REQUIRED')
    return parsed


def check_config(raw, expected):
    need(len(raw) == expected['bytes'] and digest(raw) == expected['sha256'], 'CONFIG_HASH_CHANGED')
    parsed = parse_yaml(raw)
    scheduler = parsed.get('scheduler')
    need(isinstance(scheduler, dict), 'SCHEDULER_MAPPING_REQUIRED')
    need(type(scheduler.get('recursion_limit')) is int and scheduler['recursion_limit'] == 10000, 'SCHEDULER_BUDGET_CHANGED')
    need(type(parsed.get('max_recursion_limit')) is int and parsed['max_recursion_limit'] == 10000, 'MAX_RECURSION_LIMIT_CHANGED')
    need(type(scheduler.get('max_concurrent_runs')) is int and scheduler['max_concurrent_runs'] == expected['max_concurrent_runs'], 'CONCURRENCY_CHANGED')
    need(type(scheduler.get('multi_instance')) is bool and scheduler['multi_instance'] is expected['multi_instance'], 'MULTI_INSTANCE_MODE_CHANGED')
    ownership = parsed.get('run_ownership')
    need(isinstance(ownership, dict) and ownership.get('heartbeat_enabled') is True, 'RUN_OWNERSHIP_HEARTBEAT_REQUIRED')
    return parsed



def qualify_functions(code):
    """Exercise isolated candidate functions with immediate, in-memory doubles."""
    import ast
    from dataclasses import dataclass, replace
    from datetime import UTC, date, datetime
    import re
    import types
    from urllib.parse import urlsplit
    from zoneinfo import ZoneInfo
    from sqlalchemy import JSON, Column, String, Text, and_, func, select
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.orm import declarative_base

    # Query-role adapters for isolated compilation, not application models.
    # Real repository/ORM behavior is separately covered by 13 SQLite tests.
    # Importing application packages here would execute scheduler/config
    # package initializers, outside this child's read-only source boundary.
    Base = declarative_base()

    class RunRow(Base):
        __tablename__ = 'runs'
        run_id = Column(String, primary_key=True)
        thread_id = Column(String)
        user_id = Column(String)
        status = Column(String)
        metadata_json = Column(JSON)
        kwargs_json = Column(JSON)
        last_ai_message = Column(Text)

    class ScheduledTaskRow(Base):
        __tablename__ = 'scheduled_tasks'
        id = Column(String, primary_key=True)
        user_id = Column(String)
        prompt = Column(Text)

    class ScheduledTaskRunRow(Base):
        __tablename__ = 'scheduled_task_runs'
        id = Column(String, primary_key=True)
        task_id = Column(String)
        run_id = Column(String)
        thread_id = Column(String)
        status = Column(String)

    def extract(source, names, scope):
        nodes = [n for n in ast.parse(source).body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name in names]
        need({n.name for n in nodes} == names, 'CANDIDATE_FUNCTION_SET_CHANGED')
        future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
        module = ast.fix_missing_locations(ast.Module(body=[future] + nodes, type_ignores=[]))
        exec(compile(module, '<isolated-notification-functions>', 'exec'), scope)

    def finish(coroutine):
        try:
            coroutine.send(None)
        except StopIteration as stop:
            return stop.value
        finally:
            coroutine.close()
        raise Refused('SYNTHETIC_UNEXPECTED_EXTERNAL_WAIT')

    origin = 'https://cnoocclaw.cnooc.com.cn'
    thread = 'a5f059b0-1858-4687-8d16-2de8f853c418'
    link = '[点击查看日报网页](' + origin + '/reports/energy-daily/' + thread + '/2026-11-16.html)'
    prompt = '/energy-market-daily synthetic, never executed'
    row = [prompt, 'synthetic-run', link + '\n\nsynthetic body', prompt]
    statements, sent = [], []
    database_failure = [False]

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, statement):
            if database_failure[0]:
                raise RuntimeError('synthetic unavailable')
            statements.append(statement)
            return types.SimpleNamespace(first=lambda: tuple(row))

    class WorkError(Exception):
        uncertain = True
        stage = 'synthetic-uncertain'

    async def wait_for(coroutine, timeout):
        need(timeout == 3, 'PRE_SEND_DEADLINE_CHANGED')
        return await coroutine

    async def send(*args, **kwargs):
        sent.append((args, kwargs))

    module = types.ModuleType('_personal_notification_isolated_qualification')
    need(module.__name__ not in sys.modules, 'QUALIFICATION_NAMESPACE_ALREADY_USED')
    sys.modules[module.__name__] = module
    scope = module.__dict__
    scope.update(dict(asyncio=types.SimpleNamespace(wait_for=wait_for), replace=replace, dataclass=dataclass, os=os,
        re=re, date=date, datetime=datetime, UTC=UTC, ZoneInfo=ZoneInfo, urlsplit=urlsplit,
        and_=and_, func=func, select=select, RunRow=RunRow, ScheduledTaskRunRow=ScheduledTaskRunRow,
        ScheduledTaskRow=ScheduledTaskRow, validate_thread_id=lambda value:value,
        WorkNotificationError=WorkError, get_session_factory=lambda:Session,
        work_notifications_enabled=lambda:True,
        WorkSettings=types.SimpleNamespace(from_env=lambda:types.SimpleNamespace(profile='production')),
        _platform_origin=lambda profile:origin, send_work_notification=send,
        WorkNotificationDispatcher=lambda sessions, **kwargs:types.SimpleNamespace(deliver=kwargs['send'])))
    try:
        extract(bounded_read('/app/backend/app/scheduler/work_notifications.py', 65536), {'WorkNotification', '_notification_summary'}, scope)
        extract(code, {'_message', '_daily_reader_url', '_is_reader_message', '_ready_daily_message', 'build_work_notification_dispatcher'}, scope)
        note = scope['WorkNotification'](owner_user_id='synthetic-owner', connection_id='synthetic-connection',
            binding_key='synthetic-binding', task_id='synthetic-task', task_run_id='synthetic-occurrence',
            title='油气与金融市场日报（个人）', result_text='', thread_id=thread,
            delivery_id='11111111-1111-4111-8111-111111111111', task_status='enabled')
        ready = scope['_ready_daily_message']
        need(finish(ready(note, origin, Session)) == ('ready', link), 'FINAL_READER_REFRESH_FAILED')
        compiled = statements[-1].compile(dialect=postgresql.dialect())
        sql = str(compiled)
        need(all(value in sql for value in ('LEFT OUTER JOIN runs', 'runs.user_id', 'scheduled_tasks.user_id',
                 'scheduled_task_runs.thread_id', 'metadata_json', 'kwargs_json', 'LIMIT', 'substr'))
             and set(compiled.params.values()).issuperset({'synthetic-owner', 'synthetic-task', 'synthetic-occurrence', thread, 1, 2000, 2048}),
             'EXACT_OWNER_RUN_QUERY_COMPILE_FAILED')
        row[2] = None
        need(finish(ready(note, origin, Session)) == ('retry', None), 'EMPTY_DAILY_MUST_WAIT')
        row[2] = link.replace(origin, 'https://untrusted.invalid')
        need(finish(ready(note, origin, Session)) == ('retry', None), 'UNTRUSTED_READER_ACCEPTED')
        row[2] = link.replace(thread, 'other-thread')
        need(finish(ready(note, origin, Session)) == ('retry', None), 'OTHER_THREAD_READER_ACCEPTED')
        row[2], row[3] = None, 'ordinary synthetic task'
        need(finish(ready(note, origin, Session)) == ('unchanged', None), 'ORDINARY_NOTIFICATION_CHANGED')
        row[1], row[3] = None, prompt
        need(finish(ready(note, origin, Session)) == ('failed', None), 'UNASSOCIATED_RUN_ACCEPTED')
        row[1], row[2] = 'synthetic-run', link
        deliver = scope['build_work_notification_dispatcher']().deliver
        need(finish(deliver(note)) == 'sent' and len(sent) == 1 and sent[0][0][2] == link
             and sent[0][0][3] == note.delivery_id and sent[0][1]['markdown'] is True, 'LINK_ONLY_SEND_CHANGED')

        async def uncertain(*args, **kwargs):
            raise WorkError()

        scope['send_work_notification'] = uncertain
        need(finish(deliver(note)) == 'uncertain', 'UNCERTAIN_SEND_RETRIED')
        database_failure[0] = True
        need(finish(deliver(note)) == 'retry', 'PRESEND_DATABASE_FAILURE_CHANGED')
        scope['send_work_notification'] = send
        queued = replace(note, result_text='油气与金融市场日报\n\n' + link)
        need(finish(deliver(queued)) == 'sent' and len(sent) == 2, 'VALID_READER_FAST_PATH_CHANGED')
        return 10
    finally:
        sys.modules.pop(module.__name__, None)

def observe(payload):
    need(isinstance(payload, dict) and payload.get('mode') in ('qualify', 'verify'), 'CHILD_MODE')
    need(set(payload) == ({'mode', 'config', 'candidate', 'sources'} if payload['mode'] == 'qualify'
                          else {'mode', 'config', 'sources'}), 'CHILD_PAYLOAD_KEYS')
    expected = payload['sources']
    need(isinstance(expected, dict) and set(expected) == set(SOURCE_PATHS), 'SOURCE_SET_CHANGED')
    raw = bounded_read(CONFIG, 65536)
    check_config(raw, payload['config'])
    need(payload['config']['max_concurrent_runs'] == 32 and payload['config']['multi_instance'] is True, 'CURRENT_CONCURRENCY_NOT_PRESERVED')
    versions = {name: importlib.metadata.version(name) for name in PACKAGES}
    need(versions == PACKAGES, 'GATEWAY_LIBRARY_VERSIONS_CHANGED')
    rows, total = {}, 0
    for path in sorted(SOURCE_PATHS):
        content = bounded_read(path, 1048576)
        total += len(content)
        need(total <= 4194304, 'SOURCE_TOTAL_BOUND')
        row = {'bytes': len(content), 'sha256': digest(content)}
        need(row == expected[path], 'FIXED_SOURCE_HASH_CHANGED')
        rows[path] = row
    target = '/app/backend/app/scheduler/work_notification_delivery.py'
    code = bounded_read(target, 65536)
    if payload['mode'] == 'qualify':
        asset = payload['candidate']
        need(set(asset) == {'base64', 'bytes', 'sha256'}, 'CANDIDATE_KEYS')
        code = base64.b64decode(asset['base64'], validate=True)
        need(0 < len(code) <= 65536 and len(code) == asset['bytes'] and digest(code) == asset['sha256'], 'CANDIDATE_HASH')
    compile(code, '<notification-candidate>', 'exec')
    cases = qualify_functions(code)
    need(bounded_read(CONFIG, 65536) == raw, 'CONFIG_READBACK_CHANGED')
    return {'schema': 'personal-notification-qualification-child.v1', 'status': 'PERSONAL_NOTIFICATION_READONLY_QUALIFIED',
            'application_imported': False, 'database_access': False, 'persistent_files_modified': False,
            'provider_requests': 0, 'work_messages_sent': 0, 'credentials_exported': False,
            'raw_configuration_exported': False, 'config_sha256': digest(raw),
            'scheduler_recursion_limit': 10000, 'max_recursion_limit': 10000, 'max_concurrent_runs': 32,
            'scheduler_multi_instance': True, 'versions': versions, 'source_files_verified': len(rows),
            'source_hashes': rows, 'candidate_sha256': digest(code), 'isolated_notification_cases': cases,
            'postgres_query_compiled': True, 'loaded_application_modules_verified': False, 'stable_readback': True}


def main():
    result = {'schema': 'personal-notification-qualification-child.v1', 'status': 'INCOMPLETE',
              'application_imported': False, 'provider_requests': 0, 'database_access': False,
              'persistent_files_modified': False, 'credentials_exported': False, 'raw_errors_exported': False}
    try:
        raw = sys.stdin.buffer.read(262145)
        need(0 < len(raw) <= 262144, 'CHILD_INPUT_BOUND')
        payload = json.loads(raw)
        sys.addaudithook(audit)
        result = observe(payload)
    except Exception as exc:
        result['error_class'] = type(exc).__name__
        result['error_code'] = str(exc) if isinstance(exc, Refused) else 'CHILD_QUALIFICATION_FAILED'
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result['status'] == 'PERSONAL_NOTIFICATION_READONLY_QUALIFIED' else 1


if __name__ == '__main__':
    sys.exit(main())
