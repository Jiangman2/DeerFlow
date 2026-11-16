"""Compile the exact refresh query for PostgreSQL without a database connection."""
import ast
import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_notification_ready as fixture
from sqlalchemy.dialects import postgresql

captured = []


class Session:
    async def __aenter__(self):return self
    async def __aexit__(self, *args):return False
    async def execute(self, statement):
        captured.append(statement)
        return SimpleNamespace(first=lambda:None)


source = (Path(__file__).resolve().parent / 'backend/app/scheduler/work_notification_delivery.py').read_text(encoding='utf-8')
scope = {name: getattr(fixture, name) for name in ('and_', 'func', 'select', 'RunRow', 'ScheduledTaskRunRow', 'ScheduledTaskRow')}
fixture.functions(source, {'_ready_daily_message'}, scope)
note = SimpleNamespace(thread_id='thread', task_id='task', task_run_id='occurrence', owner_user_id='owner')
assert asyncio.run(scope['_ready_daily_message'](note, 'https://example.invalid', Session)) == ('failed', None)
compiled = captured[0].compile(dialect=postgresql.dialect())
text = str(compiled)
assert 'LEFT OUTER JOIN runs' in text and 'LIMIT' in text and 'substr' in text
assert all(value in text for value in ('runs.user_id', 'scheduled_tasks.user_id', 'scheduled_task_runs.thread_id', 'metadata_json', 'kwargs_json'))
assert set(compiled.params.values()).issuperset({'thread', 'task', 'occurrence', 'owner', 1, 2000, 2048})
print(json.dumps({'status': 'POSTGRES_PARAMETER_BOUND_REFRESH_QUERY_COMPILED', 'database_connections': 0,
                  'network_requests': 0, 'messages_sent': 0}))
