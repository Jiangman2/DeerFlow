"""Offline tests for count-only task inspection in the activation child."""

import contextlib
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import re
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


STAMP = datetime(2026, 10, 22, 8, 30, 0, tzinfo=timezone.utc)
PRIVATE_SENTINEL = "synthetic-private-prompt-and-credential-never-export"


def child_source():
    candidate = Path('/candidate.py')
    if candidate.is_file():
        namespace = {'__name__': 'task_inventory_candidate_test'}
        exec(compile(candidate.read_bytes(), str(candidate), 'exec'), namespace)
        return namespace['CHILD']
    return Path(__file__).with_name('oa-compat-live-check.py').read_text(encoding='utf-8')


def group(status, count, *, unexpired=0, expired=0):
    return {
        'status': status,
        'count': count,
        'oldest_created_at': STAMP - timedelta(days=2),
        'oldest_updated_at': STAMP - timedelta(hours=2),
        'latest_updated_at': STAMP - timedelta(seconds=4),
        'lease_missing': count - unexpired - expired,
        'lease_unexpired': unexpired,
        'lease_expired': expired,
        'not_updated_for_one_hour': min(count, 1),
    }


class FakeConnection:
    def __init__(self, *, empty=False, all_expired=False, fail_on_groups=False):
        self.events = []
        self.empty = empty
        self.all_expired = all_expired
        self.fail_on_groups = fail_on_groups
        self.in_transaction = False

    def transaction(self, **kwargs):
        self.events.append(('transaction', kwargs))
        connection = self

        class Transaction:
            async def __aenter__(self):
                connection.in_transaction = True
                connection.events.append(('enter', True))
                return self

            async def __aexit__(self, *args):
                connection.events.append(('exit', args[0]))
                connection.in_transaction = False

        return Transaction()

    def query(self, sql, args):
        if not self.in_transaction:
            raise AssertionError('Queries must run inside the readonly transaction')
        if not sql.lstrip().upper().startswith('SELECT '):
            raise AssertionError('Only SELECT is supported by this fixture')
        self.events.append(('sql', sql, args))

    async def fetch(self, sql, *args):
        self.query(sql, args)
        if 'alembic_version' in sql:
            return [('0020_ppt_confirmation',)]
        if self.fail_on_groups:
            raise RuntimeError(PRIVATE_SENTINEL)
        if self.empty:
            return []
        if 'callback_status' in sql:
            return [group('succeeded', 13, unexpired=4, expired=2),
                    group('failed', 17, unexpired=6, expired=3)]
        if re.search(r'\bFROM\s+runs\b', sql, re.IGNORECASE):
            rows = [group('pending', 2, unexpired=1), group('running', 5, unexpired=2, expired=1)]
        elif 'oa_policy_flow_jobs' in sql:
            rows = [group('queued', 3), group('launching', 7, unexpired=2, expired=1),
                    group('running', 11, unexpired=3, expired=2)]
        else:
            raise AssertionError('Unexpected query')
        if self.all_expired:
            for row in rows:
                row.update(lease_missing=0, lease_unexpired=0, lease_expired=row['count'])
        return rows

    async def fetchval(self, sql, *args):
        self.query(sql, args)
        if sql.strip().upper() == 'SELECT CURRENT_TIMESTAMP':
            return STAMP
        if 'callback_status' in sql:
            return 10
        if 'oa_policy_flow_jobs' in sql:
            return 21
        if re.search(r'\bFROM\s+runs\b', sql, re.IGNORECASE):
            return 7
        raise AssertionError('Unexpected scalar query')

    async def close(self):
        self.events.append(('closed', True))


class TaskInventoryTests(unittest.TestCase):
    def run_child(self, *, action='tasks', connection=None, backend='postgres', connect_failure=False):
        connection = connection or FakeConnection()
        pg = ModuleType('asyncpg')

        async def connect(url, **kwargs):
            connection.events.append(('connect', url, kwargs))
            if connect_failure:
                raise RuntimeError(PRIVATE_SENTINEL)
            return connection

        pg.connect = connect
        cfg = ModuleType('deerflow.config')

        def config():
            # The wrapper must suppress incidental configuration diagnostics.
            print(PRIVATE_SENTINEL)
            print(PRIVATE_SENTINEL, file=sys.stderr)
            return SimpleNamespace(database=SimpleNamespace(
                backend=backend,
                postgres_url='postgresql+asyncpg://synthetic:' + PRIVATE_SENTINEL + '@invalid/db',
            ))

        cfg.get_app_config = config
        output, errors = io.StringIO(), io.StringIO()
        with patch.dict('sys.modules', {'asyncpg': pg, 'deerflow.config': cfg}), \
                patch('sys.argv', ['check', json.dumps({'action': action})]), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            exec(compile(child_source(), '<offline-child>', 'exec'), {'__name__': 'task_inventory_child_test'})
        raw = output.getvalue()
        self.assertEqual(errors.getvalue(), '')
        self.assertEqual(len(raw.splitlines()), 1)
        self.assertNotIn(PRIVATE_SENTINEL, raw)
        self.assertLess(len(raw.encode('utf-8')), 16 * 1024)
        return json.loads(raw), connection, raw

    def test_task_action_returns_aggregate_counts_and_normalized_timestamps(self):
        result, connection, _raw = self.run_child()
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['revisions'], ['0020_ppt_confirmation'])
        self.assertEqual(result['sampled_at_utc'], STAMP.isoformat())
        self.assertEqual(result['active_runs'], 7)
        self.assertEqual(result['active_oa_jobs'], 21)
        self.assertEqual(result['active_callback_leases'], 10)
        self.assertTrue(result['database_readonly'])
        self.assertFalse(result['schema_migrated'])
        self.assertFalse(result['task_content_or_id_exported'])
        self.assertTrue(result['expired_lease_does_not_authorize_rollout'])
        for key in ('run_groups', 'oa_groups', 'callback_groups'):
            for row in result[key]:
                self.assertEqual(row['oldest_created_at'], (STAMP - timedelta(days=2)).isoformat())
                self.assertEqual(row['oldest_updated_at'], (STAMP - timedelta(hours=2)).isoformat())
                self.assertEqual(row['latest_updated_at'], (STAMP - timedelta(seconds=4)).isoformat())
        self.assertEqual(connection.events[-1], ('closed', True))

    def test_inspection_is_one_repeatable_read_readonly_transaction_with_bounded_connection(self):
        _result, connection, _raw = self.run_child()
        transactions = [entry[1] for entry in connection.events if entry[0] == 'transaction']
        self.assertEqual(transactions, [{'readonly': True, 'isolation': 'repeatable_read'}])
        connects = [entry for entry in connection.events if entry[0] == 'connect']
        self.assertEqual(len(connects), 1)
        self.assertTrue(connects[0][1].startswith('postgresql://'))
        self.assertNotIn('postgresql+asyncpg://', connects[0][1])
        for key in ('timeout', 'command_timeout'):
            self.assertGreater(connects[0][2][key], 0)
            self.assertLessEqual(connects[0][2][key], 30)
        queries = [entry[1] for entry in connection.events if entry[0] == 'sql']
        self.assertTrue(queries)
        self.assertTrue(all(sql.lstrip().upper().startswith('SELECT ') for sql in queries))

    def test_status_sets_cover_launching_and_live_terminal_callbacks(self):
        _result, connection, _raw = self.run_child()
        queries = [entry for entry in connection.events if entry[0] == 'sql' and 'GROUP BY' in entry[1]]
        self.assertEqual(len(queries), 3)
        expected = {
            'runs': ['pending', 'running'],
            'oa': ['queued', 'launching', 'running'],
            'callbacks': ['succeeded', 'failed'],
        }
        observed = {}
        for _kind, sql, args in queries:
            key = 'callbacks' if 'callback_status' in sql else ('oa' if 'oa_policy_flow_jobs' in sql else 'runs')
            observed[key] = list(args[0])
            self.assertIn('ANY($1::text[])', sql)
            self.assertIn('count(*)', sql)
            self.assertIn('GROUP BY status', sql)
            if key == 'callbacks':
                self.assertRegex(sql, r"callback_status\s*=\s*'pending'")
        self.assertEqual(observed, expected)

    def test_no_identity_content_or_error_columns_are_selected(self):
        result, connection, raw = self.run_child()
        forbidden = re.compile(r'\b(?:run_id|thread_id|user_id|owner_worker_id|lease_owner|first_human_message|'
                               r'last_ai_message|metadata_json|kwargs_json|document_filename|key_fields|'
                               r'last_error_detail|last_callback_error|error)\b', re.IGNORECASE)
        for entry in connection.events:
            if entry[0] != 'sql':
                continue
            select_list = re.split(r'\bFROM\b', entry[1], maxsplit=1, flags=re.IGNORECASE)[0]
            self.assertNotRegex(select_list, forbidden)
            self.assertNotRegex(select_list, r'^\s*SELECT\s+\*')
        allowed = set(group('pending', 1))
        for key in ('run_groups', 'oa_groups', 'callback_groups'):
            self.assertLessEqual(len(result[key]), 3)
            for row in result[key]:
                self.assertEqual(set(row), allowed)
        self.assertNotIn('postgresql', raw)
        self.assertNotIn('Traceback', raw)

    def test_expired_leases_do_not_subtract_nonterminal_tasks(self):
        result, _connection, _raw = self.run_child(connection=FakeConnection(all_expired=True))
        self.assertEqual(result['active_runs'], 7)
        self.assertEqual(result['active_oa_jobs'], 21)
        for key in ('run_groups', 'oa_groups'):
            self.assertEqual(sum(row['lease_expired'] for row in result[key]), sum(row['count'] for row in result[key]))
        self.assertTrue(result['expired_lease_does_not_authorize_rollout'])

    def test_empty_tables_have_zero_counts_and_empty_groups(self):
        result, _connection, _raw = self.run_child(connection=FakeConnection(empty=True))
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual((result['active_runs'], result['active_oa_jobs'], result['active_callback_leases']), (0, 0, 0))
        self.assertEqual([result[key] for key in ('run_groups', 'oa_groups', 'callback_groups')], [[], [], []])

    def test_query_failure_closes_connection_and_never_exports_raw_error(self):
        result, connection, _raw = self.run_child(connection=FakeConnection(fail_on_groups=True))
        self.assertEqual(result, {'status': 'INCOMPLETE', 'error_class': 'RuntimeError', 'raw_errors_exported': False})
        self.assertEqual(connection.events[-1], ('closed', True))
        self.assertFalse(connection.in_transaction)

    def test_connection_failure_is_redacted(self):
        result, _connection, _raw = self.run_child(connect_failure=True)
        self.assertEqual(result, {'status': 'INCOMPLETE', 'error_class': 'RuntimeError', 'raw_errors_exported': False})

    def test_non_postgres_backend_is_rejected_before_connection(self):
        result, connection, _raw = self.run_child(backend='sqlite')
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertFalse(any(entry[0] == 'connect' for entry in connection.events))

    def test_lightweight_database_gate_also_counts_launching(self):
        result, connection, _raw = self.run_child(action='database')
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['active_callback_leases'], 10)
        query = next(entry[1] for entry in connection.events if entry[0] == 'sql' and 'oa_policy_flow_jobs' in entry[1])
        self.assertRegex(query, r"status\s+IN\s*\(\s*'queued'\s*,\s*'launching'\s*,\s*'running'\s*\)")
        callback_query = next(entry[1] for entry in connection.events if entry[0] == 'sql' and 'callback_status' in entry[1])
        self.assertRegex(callback_query, r"status\s+IN\s*\(\s*'succeeded'\s*,\s*'failed'\s*\)")
        self.assertRegex(callback_query, r"callback_status\s*=\s*'pending'")
        self.assertRegex(callback_query, r'lease_expires_at\s*>\s*CURRENT_TIMESTAMP')
        self.assertIn(('transaction', {'readonly': True}), connection.events)


if __name__ == '__main__':
    unittest.main()
