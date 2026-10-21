import ast
import copy
import hashlib
import json
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

import tune_weknora88 as m
from test_tune_weknora88 import spec, dep, pod, snap


def prior_records():
    older = m.change_env(spec(), {m.review.SETTINGS[k][1]: str(v) for k, v in
        dict(core=4, postprocess=2, enrichment=12, maintenance=4, shared=2, wiki=8, model=32).items()})
    current = m.change_env(older, m.CURRENT_ENV)
    report = dict(configuration_switch='PASS', direction='forward', effective_pools=m.CURRENT,
        effective_worker_slots=56, model_governor_startup_limit=64,
        live_stop_behavior='SIX_POOLS_STOPPED_AND_ZERO_ACTIVE_CONFIRMED',
        live_patch_acknowledged=True, image_changed=False, mineru_slots_changed=False,
        pending_queue_preserved=True)
    plan = dict(original=older, candidate=current, placement={'volume': 'same'})
    return {m.PREVIOUS_RESULT: report, 'PLAN.private.json': plan,
        'ORIGINAL.private.json': dep(older), 'PREPARED.json': dict(prepared='PASS', plan_sha256='1'*64),
        'SUCCESS.json': {k: report[k] for k in ('configuration_switch', 'effective_pools',
                         'effective_worker_slots', 'model_governor_startup_limit')},
        'PATCH-ACK.json': dict(uid=m.process.DEP_UID, target_sha256=m.obs.digest(current))}


class Contracts(unittest.TestCase):
    def prior(self, records, rollback=False):
        def locked(path, expected):
            self.assertEqual(expected, m.PREVIOUS_SHA if Path(path).name == m.PREVIOUS_RESULT else '1'*64)
            return copy.deepcopy(records[Path(path).name])
        with patch.object(m, 'locked_receipt', side_effect=locked), \
             patch.object(m, 'load', side_effect=lambda p: copy.deepcopy(records[Path(p).name])), \
             patch.object(m.process, 'DEP_SPEC', m.obs.digest(records['PLAN.private.json']['original'])), \
             patch.object(Path, 'exists', return_value=rollback):
            return m.previous_release()

    def test_prior_56_success_chain(self):
        r = prior_records(); out = self.prior(r)
        self.assertEqual(out['spec'], r['PLAN.private.json']['candidate'])
        self.assertEqual(out['report_sha256'], m.PREVIOUS_SHA)

    def test_prior_report_failure_rejected(self):
        for key, value in [('configuration_switch', 'UNCONFIRMED'), ('direction', 'rollback'),
                           ('effective_worker_slots', 32), ('model_governor_startup_limit', 128),
                           ('live_patch_acknowledged', False), ('pending_queue_preserved', False),
                           ('image_changed', True), ('mineru_slots_changed', True)]:
            with self.subTest(key=key):
                r = prior_records(); r[m.PREVIOUS_RESULT][key] = value
                with self.assertRaisesRegex(m.obs.CheckFailure, 'previous_release_not_accepted'):
                    self.prior(r)

    def test_prior_rollback_record_blocks(self):
        with self.assertRaisesRegex(m.obs.CheckFailure, 'previous_rollback'):
            self.prior(prior_records(), rollback=True)

    def test_prior_candidate_image_drift_blocks(self):
        r = prior_records(); r['PLAN.private.json']['candidate']['template']['spec']['containers'][0]['image'] = 'changed'
        with self.assertRaisesRegex(m.obs.CheckFailure, 'previous_plan_contract'):
            self.prior(r)

    def test_prior_success_marker_must_match(self):
        r = prior_records(); r['SUCCESS.json']['effective_worker_slots'] = 88
        with self.assertRaisesRegex(m.obs.CheckFailure, 'previous_success_marker'):
            self.prior(r)

    def test_prior_ack_must_bind_candidate(self):
        r = prior_records(); r['PATCH-ACK.json']['target_sha256'] = '0'*64
        with self.assertRaisesRegex(m.obs.CheckFailure, 'previous_patch_ack'):
            self.prior(r)

    def test_only_three_values_actually_change(self):
        s = m.change_env(spec(), m.CURRENT_ENV); out = m.change_env(s, m.TARGET_ENV)
        a = {v['name']: v for v in s['template']['spec']['containers'][0]['env']}
        b = {v['name']: v for v in out['template']['spec']['containers'][0]['env']}
        self.assertEqual(a.keys(), b.keys())
        self.assertEqual({k for k in a if a[k] != b[k]}, {
            'WEKNORA_ASYNQ_CORE_CONCURRENCY', 'WEKNORA_ASYNQ_ENRICHMENT_CONCURRENCY',
            'WEKNORA_MODEL_MAX_CONCURRENCY'})

    def test_read_plan_matches_accepted_source_not_old_32(self):
        prior = self.prior(prior_records())
        plan = dict(original=prior['spec'], candidate=m.change_env(prior['spec'], m.TARGET_ENV),
            previous_report_sha256=m.PREVIOUS_SHA, placement=prior['placement'], original_image_id=m.placement.IMAGE_ID)
        with patch.object(m, 'load', return_value=dict(prepared='PASS', plan_sha256='1'*64)), \
             patch.object(m, 'locked_receipt', return_value=plan), patch.object(m, 'previous_release', return_value=prior):
            self.assertEqual(m.read_plan(), plan)
            plan['original'] = prior_records()['PLAN.private.json']['original']
            with self.assertRaisesRegex(m.obs.CheckFailure, 'plan_contract_changed'):
                m.read_plan()

    def test_forward_process_drift_blocks_signal(self):
        plan = dict(original=spec(), candidate=m.change_env(spec(), m.TARGET_ENV), original_pod=['changed'])
        with tempfile.TemporaryDirectory() as td, patch.object(m, 'deployment', return_value=dep()), \
             patch.object(m, 'app_for', return_value=pod()), patch.object(m.obs, 'call') as call:
            with self.assertRaisesRegex(m.obs.CheckFailure, 'prepared_forward_process_changed'):
                m.mutate(Path(td), plan, 'forward', {})
            call.assert_not_called()

    def test_rollback_accept_target_is_56_64(self):
        with tempfile.TemporaryDirectory() as td:
            op = Path(td); (op/'PATCH-REQUESTED.json').touch()
            original = m.change_env(spec(), m.CURRENT_ENV)
            plan = dict(original=original, candidate=m.change_env(original, m.TARGET_ENV))
            with patch.object(m, 'deployment', return_value=dep(original)), \
                 patch.object(m, 'accept') as accept, patch.object(m, 'patch_call') as call:
                m.mutate(op, plan, 'rollback', {}, continuing=True)
                self.assertEqual(accept.call_args.args[3], m.CURRENT)
                self.assertEqual(accept.call_args.args[4], 64)
                call.assert_not_called()

    def test_startup_captured_and_redacted(self):
        raw = b'secret example hidden\n[ModelLimiter] background model concurrency governed per-model, limit=128 (distributed via redis)\n'
        with tempfile.TemporaryDirectory() as td, patch.object(m.obs, 'call', return_value=raw), \
             patch.object(m, 'write') as write:
            r = m.capture_startup_limit(Path(td), pod(), 128)
            self.assertEqual(r['limit'], 128)
            self.assertFalse(r['raw_logs_saved'])
            self.assertNotIn('secret example', json.dumps(write.call_args.args[1]))

    def test_wrong_startup_limit_blocks(self):
        raw = b'[ModelLimiter] background model concurrency governed per-model, limit=64 (distributed via redis)\n'
        with tempfile.TemporaryDirectory() as td, patch.object(m.obs, 'call', return_value=raw), patch.object(m, 'write') as write:
            with self.assertRaisesRegex(m.obs.CheckFailure, 'model_governor_startup_not_confirmed'):
                m.capture_startup_limit(Path(td), pod(), 128)
            write.assert_not_called()

    def test_resume_reuses_same_process_startup_proof(self):
        proof = dict(pod_identity=m.obs.identity(pod()), limit=128, distributed_via_redis=True, raw_logs_saved=False)
        with tempfile.TemporaryDirectory() as td:
            op = Path(td); (op/'MODEL-STARTUP-test.json').touch()
            with patch.object(m, 'load', return_value=proof), patch.object(m.obs, 'call') as call:
                self.assertEqual(m.capture_startup_limit(op, pod(), 128), proof)
                call.assert_not_called()

    def test_startup_other_process_not_reused(self):
        with tempfile.TemporaryDirectory() as td:
            op = Path(td); (op/'MODEL-STARTUP-test.json').touch()
            with patch.object(m, 'load', return_value={'pod_identity': ['different']}), \
                 patch.object(m.obs, 'call', return_value=b'') as call:
                with self.assertRaisesRegex(m.obs.CheckFailure, 'model_governor_startup_not_confirmed'):
                    m.capture_startup_limit(op, pod(), 128)
                call.assert_called_once()

    def test_accept_88_config_has_no_throughput_claim(self):
        current = m.change_env(spec(), m.CURRENT_ENV); target = m.change_env(current, m.TARGET_ENV)
        p = pod(); plan = dict(original_image_id='same', placement={})
        proof = dict(pod_identity=m.obs.identity(p), limit=128)
        report = {}
        with tempfile.TemporaryDirectory() as td, patch.object(m, 'deployment', return_value=dep(target)), \
             patch.object(m, 'app_for', return_value=p), patch.object(m, 'capture_startup_limit', return_value=proof), \
             patch.object(m, 'snapshot', return_value=snap(m.TARGET, 'active')), patch.object(m, 'redis_binding'), \
             patch.object(m, 'runtime_env'), patch.object(m, 'database'), patch.object(m, 'binary_probe'), \
             patch.object(m, 'resources'), patch.object(m, 'check_dependencies'), \
             patch.object(m, 'scheduling_gate', return_value={}), patch.object(m, 'write'), \
             patch.object(m.time, 'sleep'), patch.object(m.obs, 'emit'):
            m.accept(Path(td), plan, target, m.TARGET, 128, report)
        self.assertEqual(report['configuration_switch'], 'PASS')
        self.assertEqual(report['effective_worker_slots'], 88)
        self.assertEqual(report['max_parse_handlers'], 20)
        self.assertEqual(report['mineru_worker_slots'], 16)
        self.assertEqual(report['throughput_acceptance'], 'PENDING_OBSERVATION_AND_3_10_30_DOCUMENTS')

    def test_dependency_worker_not_healthy_blocks(self):
        ps = {k: [pod()]*(3 if i < 4 else 2) for i, k in enumerate(m.obs.WORKERS)}
        ps[m.obs.DISPATCHER] = [pod()]
        dispatcher = dict(role='dispatcher', counters=dict(live=16, discovered=16, quarantined=0, ready=True))
        def health(p):
            return dispatcher if p is ps[m.obs.DISPATCHER][0] else dict(unavailable={})
        # Each fake Pod is distinct from the dispatcher Pod.
        with patch.object(m.obs, 'baseline', return_value=({}, ps, {}, {})), patch.object(m.obs, 'health', side_effect=health):
            with self.assertRaisesRegex(m.obs.CheckFailure, 'mineru_worker_health'):
                m.check_dependencies()

    def test_prepare_pins_new_pod_before_any_patch(self):
        prior = self.prior(prior_records())
        with patch.object(m, 'previous_release', return_value=prior), patch.object(m.obs, 'read_expansion'), \
             patch.object(m, 'deployment', return_value=dep(prior['spec'])), patch.object(m, 'app_for', return_value=pod()), \
             patch.object(m.obs, 'emit'), patch.object(m, 'patch_call') as call, patch.object(m, 'write') as write:
            with self.assertRaisesRegex(m.obs.CheckFailure, 'accepted_56_app_pod_changed'):
                m.prepare({})
            call.assert_not_called(); write.assert_not_called()

    def test_full_prepare_is_dry_run_only(self):
        prior = self.prior(prior_records()); p = pod(); p['metadata']['uid'] = m.CURRENT_POD_UID
        p['status']['containerStatuses'][0]['imageID'] = m.placement.IMAGE_ID
        target = m.change_env(prior['spec'], m.TARGET_ENV); report = {}; stored = []
        with patch.object(m, 'previous_release', return_value=prior), patch.object(m.obs, 'read_expansion'), \
             patch.object(m, 'deployment', return_value=dep(prior['spec'])), patch.object(m, 'app_for', return_value=p), \
             patch.object(m, 'scheduling_gate', return_value=prior['placement']), patch.object(m, 'check_dependencies'), \
             patch.object(m, 'database', return_value={}), patch.object(m, 'resources', return_value=[]) as resources, \
             patch.object(m, 'runtime_env', return_value=m.CURRENT_ENV), patch.object(m, 'binary_probe', return_value={}), \
             patch.object(m, 'snapshot', return_value=snap(m.CURRENT, 'active')), patch.object(m, 'redis_binding'), \
             patch.object(m, 'patch_call', return_value=dep(target)) as patch_call, \
             patch.object(m, 'read_record', return_value=({}, 'a'*64)), patch.object(m.time, 'sleep'), \
             patch.object(m, 'write', side_effect=lambda p,v: stored.append((p.name,v))), patch.object(m.obs, 'emit'):
            plan = m.prepare(report)
        self.assertEqual(resources.call_count, 3)
        self.assertEqual(plan['original'], prior['spec'])
        self.assertEqual(plan['candidate'], target)
        self.assertTrue(all(c.args[1] is True for c in patch_call.call_args_list))
        self.assertEqual(report['local_per_model_default_target'], 128)
        self.assertNotIn('SIGNAL-REQUESTED.json', [name for name,_ in stored])

    def test_resource_limits_and_db_contention_block(self):
        gib = 1024**3; now = m.obs.utc()
        row = dict(allocatable={'cpu': '32'}, cpu={'time': now, 'usageNanoCores': 1_000_000_000},
            memory={'time': now, 'availableBytes': 49*gib}, filesystem={'time': now, 'availableBytes': 80*gib, 'capacityBytes': 295*gib},
            pods=[{'memory': {'workingSetBytes': gib}}])
        # utc() has +00:00 while kubelet samples have Z.
        for name in ('cpu', 'memory', 'filesystem'): row[name]['time'] = now.replace('+00:00', 'Z')
        db = dict(connections=dict(all=10, max_connections=100, reserved_connections=3, lock_waiters=0, idle_in_transaction=0))
        node = dict(status={'conditions': [{'type': k, 'status': 'True' if k=='Ready' else 'False'}
            for k in ('Ready', 'MemoryPressure', 'DiskPressure', 'PIDPressure')]})
        with patch.object(m.obs, 'resource_snapshot', return_value=[row]), patch.object(m.obs, 'get', return_value=node):
            self.assertEqual(m.resources(pod(), db), [row])
            for key, value in [('all', 80), ('lock_waiters', 1), ('idle_in_transaction', 1)]:
                bad = copy.deepcopy(db); bad['connections'][key] = value
                with self.subTest(key=key), self.assertRaisesRegex(m.obs.CheckFailure, 'database_headroom'):
                    m.resources(pod(), bad)
            row['memory']['availableBytes'] = 23*gib
            with self.assertRaisesRegex(m.obs.CheckFailure, 'memory_headroom'):
                m.resources(pod(), db)

    def test_same_fd_digest_and_record_stability(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'report.json'; raw=b'{"accepted":true}'; p.write_bytes(raw)
            real = p.stat()
            safe = SimpleNamespace(st_mode=stat.S_IFREG|0o600, st_uid=0, st_nlink=1,
                st_size=len(raw), st_dev=real.st_dev, st_ino=real.st_ino,
                st_mtime_ns=real.st_mtime_ns, st_ctime_ns=real.st_ctime_ns)
            with patch.object(m.obs, 'safe_parent'), patch.object(m.os, 'fstat', return_value=safe), \
                 patch.object(Path, 'lstat', return_value=safe), \
                 patch.object(m.os, 'O_NOFOLLOW', getattr(m.os,'O_NOFOLLOW',0), create=True), \
                 patch.object(m.os, 'O_NONBLOCK', getattr(m.os,'O_NONBLOCK',0), create=True):
                self.assertEqual(m.locked_receipt(p, hashlib.sha256(raw).hexdigest()), {'accepted':True})
                with self.assertRaisesRegex(m.obs.CheckFailure, 'review_receipt_changed'):
                    m.locked_receipt(p, '0'*64)
                unsafe = copy.copy(safe); unsafe.st_mode=stat.S_IFREG|0o666
                with patch.object(m.os, 'fstat', return_value=unsafe):
                    with self.assertRaisesRegex(m.obs.CheckFailure, 'unsafe_private_record'):
                        m.load(p)
                changed = copy.copy(safe); changed.st_ino += 1
                with patch.object(Path, 'lstat', return_value=changed):
                    with self.assertRaisesRegex(m.obs.CheckFailure, 'changed_during_read'):
                        m.load(p)


if __name__ == '__main__':
    unittest.main()
