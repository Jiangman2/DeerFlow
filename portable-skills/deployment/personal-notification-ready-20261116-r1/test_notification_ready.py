"""Reproduce peer completion with missing text using real SQLite repositories."""
import ast
import asyncio
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
import os
from pathlib import Path
import re
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'backend/packages/harness'))
from deerflow.persistence.base import Base
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.scheduled_task_runs.model import ScheduledTaskRunRow
from deerflow.persistence.scheduled_task_runs.sql import ScheduledTaskRunRepository
from deerflow.persistence.scheduled_tasks.model import ScheduledTaskRow

THREAD = '9a762837-e4c4-4376-a14d-fcefbacd4bbc'
ORIGIN = 'https://cnoocclaw.cnooc.com.cn'
READER = ORIGIN + '/reports/energy-daily/' + THREAD + '/2026-11-16.html'
LINK = '[点击查看日报网页](' + READER + ')'
NOW = datetime(2026, 11, 16, 5, 40, 46, tzinfo=timezone.utc)
PROMPT = '/energy-market-daily 生成今天的油气日报'


class WorkError(Exception):
    def __init__(self, stage='provider_rejected', uncertain=False):
        self.stage, self.uncertain = stage, uncertain


def functions(source, names, scope):
    nodes = [node for node in ast.parse(source).body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in names]
    assert {node.name for node in nodes} == names
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future] + nodes, type_ignores=[]))
    exec(compile(module, '<isolated-production-functions>', 'exec'), scope)


class NotificationReadyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 5.0
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(lambda conn: Base.metadata.create_all(conn, tables=[RunRow.__table__, ScheduledTaskRow.__table__, ScheduledTaskRunRow.__table__]))
        async with self.sessions.begin() as session:
            session.add(ScheduledTaskRow(id='task', user_id='owner', title='油气与金融市场日报（个人）', prompt=PROMPT,
                schedule_type='cron', schedule_spec={'cron': '0 9 * * *'}, timezone='Asia/Shanghai', status='enabled',
                run_context={'work_notification': {'connection_id': 'connection', 'binding_key': 'binding'}}))
            session.add(RunRow(run_id='run', thread_id=THREAD, user_id='owner', status='success', last_ai_message=None,
                owner_worker_id='live-worker', lease_expires_at=NOW + timedelta(seconds=30),
                metadata_json={'scheduled_task_id': 'task', 'scheduled_task_run_id': 'occurrence'},
                kwargs_json={'input': {'messages': [{'role': 'user', 'content': PROMPT}]}}))
            session.add(ScheduledTaskRunRow(id='occurrence', task_id='task', thread_id=THREAD, run_id='run',
                scheduled_for=NOW, trigger='scheduled', status='success'))
        self.send = AsyncMock(return_value='provider-message')
        self.runtime_module = ModuleType('notification_ready_test_functions')
        sys.modules[self.runtime_module.__name__] = self.runtime_module
        self.ns = self.runtime_module.__dict__
        self.ns.update({'asyncio': asyncio, 'replace': replace, 'dataclass': dataclass,
            're': re, 'os': os, 'date': date, 'datetime': datetime, 'UTC': timezone.utc, 'ZoneInfo': ZoneInfo,
            'urlsplit': urlsplit, 'and_': and_, 'func': func, 'select': select, 'RunRow': RunRow,
            'ScheduledTaskRow': ScheduledTaskRow, 'ScheduledTaskRunRow': ScheduledTaskRunRow,
            'validate_thread_id': lambda value:value, 'WorkNotificationError': WorkError,
            'get_session_factory': lambda:self.sessions, 'work_notifications_enabled': lambda:True,
            'WorkSettings': SimpleNamespace(from_env=lambda:SimpleNamespace(profile='production')),
            '_platform_origin': lambda profile:ORIGIN, 'send_work_notification': self.send,
            'WorkNotificationDispatcher': lambda sessions, **kwargs:SimpleNamespace(deliver=kwargs['send'])})
        old = ROOT / 'portable-skills/deployment/energy-reader-delivery-repair-20261115-r2/backend/app/scheduler'
        functions((old / 'work_notifications.py').read_text(encoding='utf-8'), {'WorkNotification', '_notification_summary'}, self.ns)
        functions((HERE / 'backend/app/scheduler/work_notification_delivery.py').read_text(encoding='utf-8'),
            {'_message', '_daily_reader_url', '_is_reader_message', '_ready_daily_message', 'build_work_notification_dispatcher'}, self.ns)
        self.note = self.ns['WorkNotification'](owner_user_id='owner', connection_id='connection', binding_key='binding',
            task_id='task', task_run_id='occurrence', title='油气与金融市场日报（个人）', result_text='', thread_id=THREAD,
            delivery_id='11111111-1111-4111-8111-111111111111', finished_at=NOW.isoformat(), task_status='enabled')
        self.deliver = self.ns['build_work_notification_dispatcher']().deliver

    async def asyncTearDown(self):
        await self.engine.dispose()
        sys.modules.pop(self.runtime_module.__name__, None)

    async def set_result(self, text):
        async with self.sessions.begin() as session:
            await session.execute(update(RunRow).where(RunRow.run_id == 'run').values(last_ai_message=text))

    async def test_empty_queued_snapshot_refreshes_final_reader(self):
        await self.set_result(LINK + '\n\n完整正文。')
        self.assertEqual(await self.deliver(self.note), 'sent')
        self.send.assert_awaited_once()
        self.assertEqual(self.send.call_args.args[2], LINK)
        self.assertTrue(self.send.call_args.kwargs['markdown'])

    async def test_not_ready_waits_before_any_work_request(self):
        self.assertEqual(await self.deliver(self.note), 'retry');self.send.assert_not_awaited()
        await self.set_result(LINK + '\n\n最终正文')
        self.assertEqual(await self.deliver(self.note), 'sent');self.send.assert_awaited_once()
        self.assertEqual(self.send.call_args.args[3], self.note.delivery_id)

    async def test_real_peer_reconciliation_captures_empty_result_before_completion(self):
        async with self.sessions.begin() as session:
            await session.execute(update(ScheduledTaskRunRow).where(ScheduledTaskRunRow.id == 'occurrence').values(status='running'))
        captured = []
        async def enqueue(session, task, occurrence, *, result_text, thread_id, recovered=False):
            captured.append((result_text, thread_id, recovered))
        repository = ScheduledTaskRunRepository(self.sessions)
        self.assertEqual(await repository.reconcile_active_runs(error='synthetic-recovery', now=NOW, on_success=enqueue), 1)
        self.assertEqual(captured, [(None, THREAD, True)])
        old_message = self.ns['_message'](self.note, ORIGIN)
        self.assertTrue(old_message.startswith('【定时任务】油气与金融市场日报（个人）已完成'))
        self.assertNotIn('点击查看日报网页', old_message)
        self.assertIn('/workspace/chats/' + THREAD, old_message)
        await self.set_result(LINK + '\n\n最终正文')
        self.assertEqual(await self.deliver(self.note), 'sent')
        self.assertEqual(self.send.call_args.args[2], LINK)

    async def test_valid_queued_link_keeps_fast_path(self):
        note = replace(self.note, result_text='油气与金融市场日报\n\n' + LINK)
        original = self.ns['_ready_daily_message']
        async def forbidden(*args):self.fail('No refresh needed for an accepted reader link')
        self.ns['_ready_daily_message'] = forbidden
        try:self.assertEqual(await self.deliver(note), 'sent')
        finally:self.ns['_ready_daily_message'] = original
        self.assertEqual(self.send.call_args.args[2], LINK)

    async def test_wrong_owner_run_is_never_read_for_delivery(self):
        await self.set_result(LINK)
        async with self.sessions.begin() as session:
            await session.execute(update(RunRow).where(RunRow.run_id == 'run').values(user_id='other-owner'))
        self.assertEqual(await self.deliver(self.note), 'failed');self.send.assert_not_awaited()

    async def test_wrong_scheduled_occurrence_metadata_is_rejected(self):
        await self.set_result(LINK)
        async with self.sessions.begin() as session:
            await session.execute(update(RunRow).where(RunRow.run_id == 'run').values(metadata_json={'scheduled_task_id': 'task', 'scheduled_task_run_id': 'other'}))
        self.assertEqual(await self.deliver(self.note), 'failed');self.send.assert_not_awaited()

    async def test_wrong_thread_is_rejected(self):
        await self.set_result(LINK)
        async with self.sessions.begin() as session:
            await session.execute(update(RunRow).where(RunRow.run_id == 'run').values(thread_id='other-thread'))
        self.assertEqual(await self.deliver(self.note), 'failed');self.send.assert_not_awaited()

    async def test_foreign_reader_does_not_become_generic_success(self):
        await self.set_result(LINK.replace(THREAD, 'foreign-thread'))
        self.assertEqual(await self.deliver(self.note), 'retry');self.send.assert_not_awaited()

    async def test_untrusted_origin_does_not_become_generic_success(self):
        await self.set_result(LINK.replace(ORIGIN, 'https://evil.invalid'))
        self.assertEqual(await self.deliver(self.note), 'retry');self.send.assert_not_awaited()

    async def test_unrelated_task_keeps_existing_generic_message(self):
        async with self.sessions.begin() as session:
            await session.execute(update(RunRow).where(RunRow.run_id == 'run').values(kwargs_json={'input': {'messages': [{'role': 'user', 'content': '普通任务'}]}}))
            await session.execute(update(ScheduledTaskRow).where(ScheduledTaskRow.id == 'task').values(prompt='普通任务'))
        expected = self.ns['_message'](self.note, ORIGIN)
        self.assertEqual(await self.deliver(self.note), 'sent')
        self.assertEqual(self.send.call_args.args[2], expected)

    async def test_original_daily_input_survives_task_description_edit(self):
        async with self.sessions.begin() as session:
            await session.execute(update(ScheduledTaskRow).where(ScheduledTaskRow.id == 'task').values(prompt='已修改的下次任务'))
        self.assertEqual(await self.deliver(self.note), 'retry');self.send.assert_not_awaited()

    async def test_post_send_uncertainty_retains_no_retry_outcome(self):
        await self.set_result(LINK)
        self.send.side_effect = WorkError(uncertain=True)
        self.assertEqual(await self.deliver(self.note), 'uncertain');self.send.assert_awaited_once()

    async def test_database_error_is_pre_send_retry(self):
        original = self.ns['_ready_daily_message']
        async def unavailable(*args):raise RuntimeError('synthetic database outage')
        self.ns['_ready_daily_message'] = unavailable
        try:self.assertEqual(await self.deliver(self.note), 'retry')
        finally:self.ns['_ready_daily_message'] = original
        self.send.assert_not_awaited()


if __name__ == '__main__':unittest.main()
