import ast
import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

PATH = Path(__file__).with_name('observe_pipeline.py')
spec = importlib.util.spec_from_file_location('observe_pipeline', PATH)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def vi(n):
    out = bytearray()
    while n > 127:
        out.append((n & 127) | 128); n >>= 7
    out.append(n)
    return bytes(out)


def field(n, value):
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, bytes):
        return vi((n << 3) | 2) + vi(len(value)) + value
    return vi(n << 3) + vi(value)


def heartbeat(host='test-app', concurrency=4, active=2, queues=None, status='active'):
    qs = queues if queues is not None else {'default': 1, 'chat_attachment': 3}
    return (field(1, host) + field(2, 1) + field(3, 'test-server') + field(4, concurrency)
            + b''.join(field(5, field(1, k) + field(2, v)) for k, v in qs.items())
            + field(7, status) + field(9, active))


def redis_output(blob=None, pending=1, since=1699999980000000000):
    blob = heartbeat() if blob is None else blob
    q = '\t'.join(map(str, [pending, 1, 0, 0, 0, 0, 12, 1, 0, since]))
    return ('T\t1700000000\t1\nQ\tdefault\t' + q + '\nH\t' + str(len(blob)) + '\t'
            + base64.b64encode(blob + b'\n').decode() + '\nEND\n').encode()


class ObserveTests(unittest.TestCase):
    def test_python37_syntax(self):
        ast.parse(PATH.read_text(encoding='utf-8'), feature_version=(3, 7))

    def test_heartbeat_runtime_not_env(self):
        info = m.server_info(heartbeat(concurrency=8, active=5), 'test-app')
        self.assertEqual((info['pool'], info['concurrency'], info['active']), ('core', 8, 5))

    def test_all_six_pool_classifications(self):
        cases = [({'default': 1}, 'core'), ({'postprocess': 1}, 'postprocess'),
                 ({'summary': 2, 'memory': 1}, 'enrichment'), ({'sync': 2, 'low': 1}, 'maintenance'),
                 ({'default': 3, 'summary': 2}, 'shared'), ({'wiki': 1}, 'wiki')]
        for qs, name in cases:
            self.assertEqual(m.server_info(heartbeat(queues=qs), 'test-app')['pool'], name)

    def test_unknown_topology_not_assumed_core(self):
        self.assertEqual(m.pool_kind({'wiki': 1, 'default': 1}), 'UNRECOGNIZED')

    def test_other_host_not_exported(self):
        self.assertIsNone(m.server_info(heartbeat(host='other-app'), 'test-app'))

    def test_bad_capacity_rejected(self):
        for cap, active in [(0, 0), (4, 5), (5000, 1)]:
            with self.assertRaises(m.CheckFailure):
                m.server_info(heartbeat(concurrency=cap, active=active), 'test-app')

    def test_truncated_and_invalid_proto(self):
        for raw in [b'\x80', b'\x0a\xff', b'\x00', b'\x0f', b'\x80' * 11]:
            with self.assertRaises(m.CheckFailure):
                m.protobuf(raw)

    def test_unknown_fields_skipped(self):
        info = m.server_info(heartbeat() + field(45, 'future'), 'test-app')
        self.assertEqual(info['concurrency'], 4)

    def test_duplicate_scalar_rejected(self):
        with self.assertRaises(m.CheckFailure):
            m.server_info(heartbeat() + field(4, 6), 'test-app')

    def test_oldest_pending_nanoseconds(self):
        r = m.parse_redis(redis_output(), 'test-app')
        self.assertEqual(r['queues']['default']['oldest_pending_seconds'], 20)
        self.assertEqual(r['effective_pool_summary']['core']['effective_concurrency'], 4)

    def test_nonempty_missing_timestamp_unknown(self):
        r = m.parse_redis(redis_output(since=0), 'test-app')
        self.assertIsNone(r['queues']['default']['oldest_pending_seconds'])

    def test_empty_pending(self):
        r = m.parse_redis(redis_output(pending=0, since=0), 'test-app')
        self.assertEqual(r['queues']['default']['oldest_pending_seconds'], 0)

    def test_partial_read_rejected(self):
        with self.assertRaises(m.CheckFailure):
            m.parse_redis(redis_output().replace(b'END\n', b''), 'test-app')

    def test_protobuf_newline_retained(self):
        r = m.parse_redis(redis_output(heartbeat(active=3, concurrency=10) + field(100, b'\n')), 'test-app')
        self.assertEqual(r['effective_servers'][0]['concurrency'], 10)

    def test_bad_heartbeat_keeps_queue_evidence(self):
        r = m.parse_redis(redis_output(b'\x0f'), 'test-app')
        self.assertEqual(r['queues']['default']['pending'], 1)
        self.assertTrue(r['heartbeat_unavailable'])
        self.assertIsNone(r['effective_pool_summary']['core']['effective_concurrency'])

    def test_duplicate_pool_does_not_double_capacity(self):
        row = m.server_info(heartbeat(), 'test-app')
        self.assertIsNone(m.summarize_pools([row, row])['core']['effective_concurrency'])

    def test_stopped_heartbeat_not_live_capacity(self):
        row = m.server_info(heartbeat(status='stopped'), 'test-app')
        self.assertIsNone(m.summarize_pools([row])['core']['effective_concurrency'])

    def test_logs_aggregate_only(self):
        raw = (b'2026-10-16T01:00:00Z INFO Parse finished request_id=secret-id filename=\'secret.pdf\' status=completed elapsed_seconds=12.123\n'
               b'INFO Dispatch started request_id=hidden priority=interactive backend=10.1.1.1 wait_seconds=0.123 bytes=123 busy_attempt=0\n'
               b'INFO Dispatch transport failure request_id=hidden backend=10.1.1.1 error=secret-error\n')
        vals, errors = m.log_stats(raw)
        self.assertEqual(vals['parse_completed'], [12.123])
        self.assertEqual(vals['dispatch_wait_interactive'], [.123])
        self.assertEqual(errors, 1)
        self.assertNotIn('secret', json.dumps([vals, errors]))
        self.assertNotIn('10.1.1.1', json.dumps(vals))

    def test_tail_truncation_not_completion(self):
        vals, _ = m.log_stats(b'Parse finished request_id=abc filename=\'x\' status=completed elapsed_seconds=1.')
        self.assertEqual(vals['parse_completed'], [])

    def test_failure_does_not_leak_raw(self):
        self.assertNotIn('password', json.dumps(m.failure(ValueError('password=secret'))))

    def test_dependency_markers_are_counts_only(self):
        result = m.dependency_log_markers(b'ERROR request failed status=429 token=hidden\nERROR embedding failed secret-text\n')
        self.assertEqual(result['rate_limit_or_quota_marker'], 1)
        self.assertEqual(result['embedding_failure_marker'], 1)
        self.assertNotIn('hidden', json.dumps(result))

    def test_missing_dispatcher_no_crash_or_capacity_claim(self):
        result = m.interpret([{'redis': m.parse_redis(redis_output(), 'test-app'),
                              'worker_reads_complete': True, 'dispatcher': None, 'active_workers': 0}])
        self.assertIn('INCOMPLETE_SAMPLES_NO_TUNING_APPROVAL', result['findings'])

    def test_secret_binding_and_unknown_tls(self):
        ref = {'secretKeyRef': {'name': 'redis-secret', 'key': 'password'}}
        app = {'metadata': {'name': 'test-app'}, 'spec': {'containers': [{'name': 'app', 'env': [
            {'name': 'REDIS_ADDR', 'value': 'weknora-redis:6379'}, {'name': 'REDIS_PASSWORD', 'valueFrom': ref}]}]}}
        redis = {'metadata': {'name': 'redis', 'uid': 'redis-uid', 'labels': {'app': 'redis'}},
                 'spec': {'containers': [{'name': 'redis', 'env': [{'name': 'REDIS_PASSWORD', 'valueFrom': ref}]}]}}
        svc = {'spec': {'selector': {'app': 'redis'}}}
        eps = {'items': [{'endpoints': [{'targetRef': {'uid': 'redis-uid'}}]}]}
        with patch.object(m, 'get', side_effect=[svc, eps]):
            self.assertEqual(m.redis_binding({'weknora-app': [app], 'weknora-redis': [redis]})[1:], ('0', 'test-app'))
        app['spec']['containers'][0]['env'].append({'name': 'REDIS_USE_TLS', 'value': 'true'})
        with self.assertRaises(m.CheckFailure):
            m.redis_binding({'weknora-app': [app], 'weknora-redis': [redis]})

    def test_unknown_queue_not_effective_capacity(self):
        r = m.parse_redis(redis_output(heartbeat(queues={'unreviewed': 1})), 'test-app')
        self.assertTrue(r['heartbeat_unavailable'])
        self.assertEqual(r['effective_servers'], [])

    def test_source_no_cluster_writes_or_shell_argv_password(self):
        tree = ast.parse(PATH.read_text(encoding='utf-8'))
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name) or call.func.id != 'call':
                continue
            if call.args and isinstance(call.args[0], ast.List):
                self.assertIn(call.args[0].elts[0].value, ('get', 'exec', 'logs'))
        self.assertNotIn(' -a ', m.REDIS_SHELL)
        self.assertNotIn('redis-cli --pass', m.REDIS_SHELL)

    def test_no_patch_or_create(self):
        for verb in ('patch', 'apply', 'delete', 'scale', 'create'):
            with self.assertRaises(m.CheckFailure):
                m.call([verb])

    def test_no_automatic_tuning(self):
        row = {'worker_reads_complete': True, 'active_workers': 5, 'dispatcher': {'waiting': 0},
               'redis': m.parse_redis(redis_output(), 'test-app')}
        result = m.interpret([row, row])
        self.assertFalse(result['configuration_change_authorized_by_this_report'])
        self.assertEqual(result['all_queue_trends']['default']['counter_deltas_not_document_counts']['processed_total'], 0)

    def test_counter_reset_unknown(self):
        a = m.parse_redis(redis_output(), 'test-app')
        b = m.parse_redis(redis_output().replace(b'\t12\t1\t0\t', b'\t1\t1\t0\t'), 'test-app')
        rows = [{'redis': x, 'worker_reads_complete': False} for x in (a, b)]
        r = m.interpret(rows)
        self.assertIsNone(r['all_queue_trends']['default']['counter_deltas_not_document_counts']['processed_total'])

    def test_percentiles_and_empty(self):
        self.assertIsNone(m.summary_stats([])['p95_seconds'])
        self.assertEqual(m.summary_stats(list(range(1, 21)))['p95_seconds'], 19)

    def test_safe_env_not_unresolved(self):
        with self.assertRaises(m.CheckFailure):
            m.literal_env({'envFrom': [{'secretRef': {'name': 'hidden'}}]})

    def test_shell_readonly_allowlist_and_wire_format(self):
        bash = Path('C:/Program Files/Git/bin/bash.exe')
        if not bash.exists():
            bash = Path('/bin/bash')
        if not bash.exists():
            self.skipTest('bash unavailable')
        blob = heartbeat()
        encoded = base64.b64encode(blob).decode()
        mock = r'''redis-cli() {
 shift 8
 case "$1" in
 TIME) printf '1700000000\n0\n';;
 SCARD) printf '11\n';;
 SISMEMBER) printf '1\n';;
 LLEN) printf '1\n';;
 ZCARD|EXISTS) printf '0\n';;
 LINDEX) printf 'test-id\n';;
 HGET) test "$3" = pending_since || return 80; printf '1699999980000000000\n';;
 ZRANGEBYSCORE) printf 'asynq:servers:{test-app:1:uuid}\n';;
 STRLEN) printf '%s\n' __SIZE__;;
 GET) case "$2" in
 asynq:servers:*) printf '%s' '__BLOB__' | base64 -d; printf '\n';;
 *:processed|*:failed) printf '0\n';;
 *) return 81;; esac;;
 *) return 82;;
 esac
}
REDIS_PASSWORD=fake-local-test-only
set -- 0
'''.replace('__SIZE__', str(len(blob))).replace('__BLOB__', encoded)
        p = subprocess.run([str(bash), '--noprofile', '--norc'], input=(mock + m.REDIS_SHELL).encode(),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        self.assertEqual(p.returncode, 0, p.stderr.decode(errors='replace'))
        r = m.parse_redis(p.stdout, 'test-app')
        self.assertEqual(len(r['queues']), 11)
        self.assertEqual(r['effective_servers'][0]['concurrency'], 4)
        self.assertNotIn(b'fake-local-test-only', p.stdout)
        for forbidden in ('EVAL', 'DEL ', 'ZREM', 'FLUSH', 'HGETALL', 'CONFIG ', 'KEYS ', 'SCAN '):
            self.assertNotIn(forbidden, m.REDIS_SHELL)


if __name__ == '__main__':
    unittest.main()
