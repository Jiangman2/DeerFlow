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
