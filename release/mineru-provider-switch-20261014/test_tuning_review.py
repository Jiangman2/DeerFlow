import ast
import base64
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
import tuning_review as m
from test_observe_pipeline import heartbeat, redis_output


class ReviewTests(unittest.TestCase):
    def test_python37(self):
        ast.parse((ROOT / 'tuning_review.py').read_text(encoding='utf-8'), feature_version=(3, 7))

    def test_plan_no_provider_gate(self):
        p = m.proposal()
        self.assertEqual(sum(p['worker_first_step'].values()), 56)
        self.assertEqual(p['max_parse_handlers'], 12)
        self.assertEqual(p['mineru_slots'], 16)
        self.assertEqual(p['deepseek_local_limit_separate_step'], 64)
        self.assertFalse(p['provider_capacity_approval_required'])
        self.assertFalse(p['ready_to_apply'])

    def test_config_db_overrides_env(self):
        s = m.resolve('core', {'value': 7, 'bootstrap': False}, '4')
        self.assertEqual((s['predicted_next_start'], s['source']), (7, 'DATABASE'))

    def test_bootstrap_uses_env(self):
        self.assertEqual(m.resolve('core', {'value': 8, 'bootstrap': True}, '4')['predicted_next_start'], 4)

    def test_absent_uses_default(self):
        self.assertEqual(m.resolve('wiki', None, None)['predicted_next_start'], 8)

    def test_bad_db_env_fallback(self):
        self.assertEqual(m.resolve('core', {'value': None, 'bootstrap': False}, '4')['predicted_next_start'], 4)

    def test_nonpositive_worker_default(self):
        self.assertEqual(m.resolve('core', {'value': 0, 'bootstrap': False}, '4')['predicted_next_start'], 8)

    def test_model_zero_not_worker_fallback(self):
        self.assertEqual(m.resolve('model', {'value': 0, 'bootstrap': False}, '32')['predicted_next_start'], 0)

    def test_int_bool_float_not_valid(self):
        for v in (True, 1.5, '1.2', 'password', 10**30):
            self.assertIsNone(m.integer(v))

    def test_heartbeat_pid_binding(self):
        r = m.redis_projection(redis_output(), 'test-app')
        self.assertEqual(r['process_ids'], [1])
        self.assertNotIn('test-server', json.dumps(r))

    def test_runtime_no_fake_signal_proof(self):
        raw = b'BIN\t' + b'a'*64 + b'\nPID\t1\nSigCgt\t0000000000080000\nMARK\tasynq_package\t1\n'
        r = m.parse_process(raw, 1)
        self.assertTrue(r['tstp_handler_installed'])
        self.assertFalse(r['safe_to_signal_proven'])

    def test_runtime_unknown_lines_fail_closed(self):
        with self.assertRaises(m.obs.CheckFailure):
            m.parse_process(b'SECRET\tbad\n', 1)

    def test_env_unknown_rejected(self):
        with self.assertRaises(m.obs.CheckFailure):
            m.parse_env(b'PASSWORD\tsecret\n')

    def test_env_bad_value_not_printed(self):
        r = m.parse_env(b'WEKNORA_MODEL_MAX_CONCURRENCY\tprivate-value\n')
        self.assertNotIn('private-value', json.dumps(r))

    def test_sql_limited_readonly_no_content(self):
        sql = m.sql_review()
        self.assertIn('BEGIN READ ONLY', sql)
        self.assertIn("statement_timeout", sql)
        self.assertNotIn('SELECT *', sql)
        self.assertNotIn('knowledge_bases', sql)
        self.assertNotIn("parameters::text", sql)
        self.assertNotIn('api_key', sql)
        for word in ('UPDATE ', 'DELETE ', 'INSERT ', 'ALTER ', 'CREATE ', 'FOR UPDATE'):
            self.assertNotIn(word, sql.upper())

    def test_database_shape_and_sensitive_stripping(self):
        r = {'transaction_read_only': 'on', 'settings': [], 'models': [],
             'connections': {'all': 10, 'active': 2, 'max_connections': 100}, 'secret': 'DO_NOT_EXPORT'}
        self.assertNotIn('DO_NOT_EXPORT', json.dumps(m.clean_database(r)))

    def test_database_readonly_required(self):
        with self.assertRaises(m.obs.CheckFailure):
            m.clean_database({'transaction_read_only': 'off'})

    def test_database_unknown_setting_refused(self):
        with self.assertRaises(m.obs.CheckFailure):
            m.clean_database({'transaction_read_only': 'on', 'settings': [{'key': 'api.key'}], 'models': []})

    def test_secret_numeric_setting_refused(self):
        with self.assertRaises(m.obs.CheckFailure):
            m.clean_database({'transaction_read_only': 'on', 'settings': [
                {'key': 'model.max_concurrency', 'value': 32, 'is_secret': True}], 'models': []})

    def test_unknown_model_not_exported(self):
        with self.assertRaises(m.obs.CheckFailure):
            m.clean_database({'transaction_read_only': 'on', 'settings': [],
                              'models': [{'id': 'secret-custom-model', 'max_concurrency': 1}]})

    def test_unset_env_and_positive_numeric(self):
        r = m.parse_env(b'GOMAXPROCS\t\nWEKNORA_ASYNQ_CORE_CONCURRENCY\t4\nAUTO_MIGRATE\tfalse\n')
        self.assertIsNone(r['GOMAXPROCS'])
        self.assertEqual(r['AUTO_MIGRATE'], 'false')

    def test_large_memory_value_redacted(self):
        r = m.parse_env(b'GOMEMLIMIT\thidden-token-value\n')
        self.assertEqual(r['GOMEMLIMIT'], 'INVALID_REDACTED')

    def test_deployment_does_not_export_argv_env(self):
        p = {'metadata': {'uid': 'u'}, 'spec': {'nodeName': 'n'}, 'status': {'containerStatuses': []}}
        d = {'metadata': {'uid': 'd', 'generation': 1}, 'spec': {'replicas': 1, 'template': {'spec': {
             'containers': [{'name': 'app', 'image': 'image', 'args': ['PRIVATE_TOKEN'],
                             'env': [{'name': 'PASSWORD', 'value': 'PRIVATE_TOKEN'}],
                             'lifecycle': {'preStop': {'exec': {'command': ['PRIVATE_TOKEN']}}}}]}}}}
        r = m.deployment_projection(d, p)
        self.assertNotIn('PRIVATE_TOKEN', json.dumps(r))
        self.assertTrue(r['pre_stop_present'])

    def test_collect_does_not_export_full_pods_on_partial(self):
        app = {'metadata': {'name': 'app', 'uid': 'uid'}, 'spec': {'nodeName': 'n', 'containers': [
            {'name': 'app', 'image': 'x', 'env': [{'name': 'PASSWORD', 'value': 'PRIVATE_TOKEN'}]}]},
            'status': {'containerStatuses': []}}
        d = {'metadata': {'uid': 'd', 'generation': 1}, 'spec': {'replicas': 1, 'template': {'spec': app['spec']}}}
        pg = {'metadata': {'name': 'postgres'}, 'spec': {'containers': [{'name': 'postgres',
              'env': [{'name': 'PASSWORD', 'value': 'PRIVATE_TOKEN'}]}]}}
        db = {'transaction_read_only': 'on', 'settings': [], 'models': [], 'connections': {}}
        responses = [b'WEKNORA_ASYNQ_CORE_CONCURRENCY\t4\n', json.dumps(db).encode(), json.dumps(db).encode()]
        with patch.object(m.obs, 'read_expansion'), patch.object(m.obs, 'baseline', return_value=(
                {'weknora-app': d}, {'weknora-app': [app]}, {'signature': 'stable'}, {})), \
             patch.object(m, 'pg_binding', return_value=(pg, {'spec_sha256': 'safe'})), \
             patch.object(m.obs, 'redis_binding', side_effect=ValueError('SECRET')), \
             patch.object(m.obs, 'resource_snapshot', return_value=[]), \
             patch.object(m.obs, 'call', side_effect=responses), patch.object(m.obs, 'emit'):
            r = {}
            m.collect(r)
        self.assertNotIn('PRIVATE_TOKEN', json.dumps(r))
        self.assertNotIn('SECRET', json.dumps(r))
        self.assertFalse(r['restart_strategy_assessment']['direct_rollout_restart_approved'])

    def test_pending_queues_not_empty_gate(self):
        p = m.proposal()
        self.assertFalse(p['queue_empty_required'])
        self.assertTrue(p['old_claimed_tasks_must_finish'])

    def test_no_write_modes(self):
        source = (ROOT / 'tuning_review.py').read_text(encoding='utf-8')
        for flag in ('--patch', '--restart', '--apply', '--scale'):
            self.assertNotIn(flag, source)
        self.assertNotIn('kill -', m.PROCESS_SHELL)

    def test_postgres_password_not_argv(self):
        bash = Path('C:/Program Files/Git/bin/bash.exe')
        if not bash.exists():
            bash = Path('/bin/bash')
        if not bash.exists():
            self.skipTest('bash unavailable')
        mock = '''psql() {
 case "$*" in *TEST_ONLY_SECRET*) return 42;; esac
 test "$PGPASSWORD" = TEST_ONLY_SECRET || return 43
 case "$PGOPTIONS" in *default_transaction_read_only=on*) ;; *) return 44;; esac
 printf '{"transaction_read_only":"on"}\\n'
}
POSTGRES_PASSWORD=TEST_ONLY_SECRET
POSTGRES_USER=postgres
POSTGRES_DB=WeKnora
set -- 'BEGIN READ ONLY; ROLLBACK;'
'''
        p = subprocess.run([str(bash), '--noprofile', '--norc'], input=(mock + m.DB_SHELL).encode(),
                           capture_output=True, timeout=10)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn(b'TEST_ONLY_SECRET', p.stdout + p.stderr)


if __name__ == '__main__':
    unittest.main()
