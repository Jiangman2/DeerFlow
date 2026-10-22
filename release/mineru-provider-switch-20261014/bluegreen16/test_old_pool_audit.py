import ast
import copy
import json
from pathlib import Path
import types
import unittest
from unittest.mock import MagicMock, patch

import audit_old_pool as audit
import old_pool_probe as probe

HERE = Path(__file__).absolute().parent
HEADER = 'sl local_address rem_address st tx_queue:rx_queue tr:tm->when retrnsmt uid timeout inode\n'


def tcp_line(local=50052, remote=30000, state='01', tx=0, rx=0):
    return '0: 0100007F:%04X 0100007F:%04X %s %08X:%08X 00:00000000 00000000 0 0 123\n' % (
        local, remote, state, tx, rx)


def receipt(role='worker', active=0, busy=0, waiting=0, state=None):
    tcp = probe.summarize_tcp([] if state is None else
        probe.parse_tcp(HEADER+tcp_line(state=state)))
    return dict(old_pool_probe='PASS', role=role, source_sha256=probe.SOURCE_SHA,
        provider_requests=0, documents_submitted=0,
        counters=dict(active=active, busy=busy, waiting=waiting),
        tcp_before=tcp, tcp_after=copy.deepcopy(tcp))


def pod(name='old-worker', container='adapter', mode='running'):
    cs = dict(name=container, ready=True, restartCount=0, imageID='sha256:old',
        containerID='containerd://locked', state={'running': {'startedAt': '2026-10-15'}})
    phase = 'Running'
    if mode == 'missing':
        cs.update(ready=False, containerID='', state={'waiting': {'reason': 'ErrImageNeverPull'}})
        phase = 'Pending'
    return dict(metadata=dict(name=name, uid='uid-'+name), spec=dict(nodeName='node1'),
                status=dict(phase=phase, containerStatuses=[cs]))


class ProbeTests(unittest.TestCase):
    def test_python37_syntax(self):
        for name in ('old_pool_probe.py', 'audit_old_pool.py'):
            ast.parse((HERE/name).read_bytes(), feature_version=(3, 7))

    def test_empty_socket_table(self):
        self.assertEqual(probe.parse_tcp(HEADER), [])

    def test_upload_socket_visible_without_parse_counter(self):
        row = probe.summarize_tcp(probe.parse_tcp(HEADER+tcp_line(rx=2048)))
        self.assertEqual(row['inbound_nonterminal'], 1)
        self.assertEqual(row['inbound_pending_receive_bytes'], 2048)

    def test_time_wait_not_counted_as_running(self):
        row = probe.summarize_tcp(probe.parse_tcp(HEADER+tcp_line(state='06')))
        self.assertEqual(row['inbound_nonterminal'], 0)
        self.assertEqual(row['inbound_states'], {'TIME_WAIT': 1})

    def test_closing_connections_conservatively_counted(self):
        for state in ('04', '05', '08', '09', '0B', '0C'):
            with self.subTest(state=state):
                self.assertEqual(probe.summarize_tcp(probe.parse_tcp(HEADER+tcp_line(state=state)))['inbound_nonterminal'], 1)

    def test_listener_backlog_recorded(self):
        row = probe.summarize_tcp(probe.parse_tcp(HEADER+tcp_line(state='0A', remote=0, rx=2)))
        self.assertEqual(row['listener_count'], 1)
        self.assertEqual(row['listener_receive_queue'], 2)

    def test_outbound_worker_probe_not_inbound(self):
        row = probe.summarize_tcp(probe.parse_tcp(HEADER+tcp_line(local=35000, remote=50052)))
        self.assertEqual(row['inbound_nonterminal'], 0)
        self.assertEqual(row['outbound_states'], {'ESTABLISHED': 1})

    def test_unrelated_ports_excluded(self):
        self.assertEqual(probe.parse_tcp(HEADER+tcp_line(local=40000, remote=443)), [])

    def test_ipv6_ports_parsed(self):
        line = tcp_line().replace('0100007F', '00000000000000000000000001000000')
        self.assertEqual(len(probe.parse_tcp(HEADER+line)), 1)

    def test_invalid_table_fails_not_zero(self):
        for value in ('', 'invalid\n', HEADER+'short\n', HEADER+tcp_line(state='FF')):
            with self.subTest(value=value), self.assertRaises(probe.ProbeFailure):
                probe.parse_tcp(value)

    def test_output_excludes_ips(self):
        text = json.dumps(probe.summarize_tcp(probe.parse_tcp(HEADER+tcp_line())))
        self.assertNotIn('0100007F', text)
        self.assertNotIn('remote', text)

    def test_health_worker_accepts_busy_observation(self):
        self.assertEqual(probe.safe_health(dict(role='worker', status='ok', parser={'active': 1, 'waiting': 0}), 'worker')['active'], 1)

    def test_health_rejects_invalid_counters(self):
        for v in (None, -1, True, '0'):
            with self.subTest(v=v), self.assertRaises(probe.ProbeFailure):
                probe.safe_health(dict(role='worker', status='ok', parser={'active': v, 'waiting': 0}), 'worker')

    def test_health_output_allowlist(self):
        result = probe.safe_health(dict(role='worker', status='ok', parser={'active': 0, 'waiting': 0, 'secret': 'never-print'}), 'worker')
        self.assertEqual(result, {'active': 0, 'waiting': 0})


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.original_sw = audit.sw
        self.s = MagicMock()
        audit.sw = types.SimpleNamespace(s=self.s)
        self.s.prep.WORKER = 'old-workers'
        self.s.prep.DISPATCHER = 'old-dispatch'
        self.s.core.inv.BASE_IDS = {'sha256:old'}

    def tearDown(self):
        audit.sw = self.original_sw

    def test_idle_does_not_authorize_retirement(self):
        rows = [{'summary': {'idle_observed': True}}]*3
        result = audit.conclusions(rows)
        self.assertEqual(result['observation'], 'QUIET_SAMPLES_ONLY')
        self.assertFalse(result['ready_to_retire'])
        self.assertFalse(result['ready_to_expand_16'])
        self.assertEqual(result['admission_closed'], 'NOT_PROVEN')

    def test_partial_samples_not_quiet(self):
        self.assertNotEqual(audit.conclusions([{'summary': {'idle_observed': True}}])['observation'], 'QUIET_SAMPLES_ONLY')

    def test_socket_without_active_work_not_idle(self):
        result = audit.aggregate([{'receipt': receipt(state='01')}])
        self.assertEqual(result['worker_active'], 0)
        self.assertFalse(result['idle_observed'])

    def test_queued_dispatcher_not_idle(self):
        result = audit.aggregate([{'receipt': receipt(role='dispatcher', waiting=1)}])
        self.assertEqual(result['dispatcher_waiting'], 1)
        self.assertFalse(result['idle_observed'])

    def test_listener_backlog_blocks_quiet(self):
        r = receipt()
        r['tcp_before']['listener_receive_queue'] = 1
        self.assertFalse(audit.aggregate([{'receipt': r}])['idle_observed'])

    def test_busy_worker_not_idle(self):
        self.assertFalse(audit.aggregate([{'receipt': receipt(active=1)}])['idle_observed'])

    def test_pod_replacement_detected(self):
        p = pod()
        before = audit.pod_identity(p, 'adapter')
        p['metadata']['uid'] = 'new'
        self.s.get.return_value = p
        with self.assertRaises(audit.AuditFailure):
            audit.observe(before)
        self.s.call.assert_not_called()

    def test_container_restart_after_probe_detected(self):
        p = pod()
        changed = copy.deepcopy(p)
        changed['status']['containerStatuses'][0]['containerID'] = 'new'
        self.s.get.side_effect = [p, changed]
        self.s.call.return_value = json.dumps(receipt()).encode()
        with self.assertRaises(audit.AuditFailure):
            audit.observe(audit.pod_identity(p, 'adapter'))

    def test_observe_only_exec_no_write(self):
        p = pod()
        self.s.get.return_value = p
        self.s.call.return_value = json.dumps(receipt()).encode()
        result = audit.observe(audit.pod_identity(p, 'adapter'))
        args = self.s.call.call_args[0][0]
        self.assertEqual(args[0], 'exec')
        self.assertEqual(args[-2:], ['--role', 'worker'])
        self.assertEqual(result['receipt']['provider_requests'], 0)

    def test_failed_probe_never_counted_as_zero(self):
        p = pod()
        self.s.get.return_value = p
        self.s.call.return_value = b'{"old_pool_probe":"INCOMPLETE"}'
        with self.assertRaises(audit.AuditFailure):
            audit.observe(audit.pod_identity(p, 'adapter'))

    def test_running_unready_is_not_silently_skipped(self):
        p = pod()
        p['status']['containerStatuses'][0]['ready'] = False
        self.assertEqual(audit.pod_identity(p, 'adapter')['name'], p['metadata']['name'])

    def inventory_setup(self):
        deps = {name: dict(spec={'template': {'metadata': {'labels': {'app': name}}}}) for name in ('old-workers', 'old-dispatch')}
        workers = [pod('worker-'+str(i)) for i in range(6)] + [pod('missing-'+str(i), mode='missing') for i in range(3)]
        dispatcher = [pod('dispatch', 'dispatcher')]
        self.s.core.inv.owned_pods.side_effect = [workers, dispatcher]
        self.s.get.return_value = {'items': [dict(metadata=dict(name='old-headless', uid='svc-old'),
            spec=dict(selector={'app': 'old-workers'}, clusterIP='None', type='ClusterIP'))]}
        return deps, workers

    def test_inventory_counts_missing_images_separately(self):
        deps, _ = self.inventory_setup()
        result = audit.old_inventory(deps)
        self.assertEqual(len(result['targets']), 7)
        self.assertEqual(len(result['pending']), 3)
        self.assertEqual(result['services'][0]['name'], 'old-headless')

    def test_unknown_container_state_blocks_review(self):
        deps, workers = self.inventory_setup()
        workers[-1]['status']['containerStatuses'][0]['restartCount'] = 1
        with self.assertRaises(audit.AuditFailure):
            audit.old_inventory(deps)

    def test_source_hash_mismatch_not_adopted(self):
        p = pod()
        self.s.get.return_value = p
        r = receipt()
        r['source_sha256'] = 'different'
        self.s.call.return_value = json.dumps(r).encode()
        with self.assertRaises(audit.AuditFailure):
            audit.observe(audit.pod_identity(p, 'adapter'))

    def test_run_full_orchestration_is_observation_only(self):
        fake = MagicMock()
        fake.s = self.s
        candidates = [dict(container='adapter', name=str(i)) for i in range(6)]+[dict(container='dispatcher')]
        fake.candidate_ready.return_value = candidates
        inv = dict(targets=[dict(name='old')], pending=[], terminal=[], services=[])
        with patch.object(audit, 'sw', fake), patch.object(audit, 'bootstrap', return_value='probe'), \
                patch.object(audit, 'old_inventory', return_value=inv), \
                patch.object(audit, 'observe', return_value={'receipt': receipt()}), \
                patch.object(audit.time, 'sleep'), patch.object(audit, 'emit'):
            report = dict(samples=[])
            audit.run(Path('/var/lib/private-test'), report)
        self.assertEqual(len(report['samples']), 3)
        self.assertEqual(report['old_pool_audit'], 'COMPLETE')
        self.assertFalse(report['ready_to_retire'])
        fake.patch_once.assert_not_called()
        fake.public_health.assert_called()
        self.assertEqual(fake.invariants.call_count, 2)

    def test_pod_termination_blocks_exec(self):
        p = pod()
        p['metadata']['deletionTimestamp'] = 'now'
        with self.assertRaises(audit.AuditFailure):
            audit.pod_identity(p, 'adapter')

    def test_grammar_probe_contains_only_get_health(self):
        tree = ast.parse((HERE/'old_pool_probe.py').read_bytes())
        methods = [node.args[0].value for node in ast.walk(tree) if isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Attribute) and node.func.attr == 'request']
        self.assertEqual(methods, ['GET'])

    def test_exec_failure_diagnostic_is_safe_and_actionable(self):
        self.s.LAST_OPERATION = dict(operation='exec', exit_code=1, stderr_bytes=69,
            raw_stderr='must not print', probe_failure=dict(stage_runtime='FAIL',
                failed_check='tcp_header_invalid', error_type='ProbeFailure', raw='must not print'))
        result = audit.safe_last_operation()
        self.assertEqual(result['probe_failure']['failed_check'], 'tcp_header_invalid')
        self.assertNotIn('must not print', json.dumps(result))


class FailedPodRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.uid = '11111111-1111-1111-1111-111111111111'
        self.name = 'deer-flow-mineru-adapter-7f59bc8879-2vvwx'
        self.cid = audit.HISTORICAL[self.name][1]
        self.p = pod(self.name)
        self.p['metadata']['uid'] = self.uid
        self.p['spec'].update(nodeName='datax-01', containers=[{'name': 'adapter'}])
        self.p['status']['phase'] = 'Failed'
        self.p['status']['containerStatuses'][0].update(ready=False, containerID='containerd://'+self.cid,
            state={'terminated': {'reason': 'Error', 'exitCode': 1, 'finishedAt': '2026-10-01'}})
        self.record = audit.terminal_record(self.p, 'adapter')
        self.cgroups = dict(matches={self.uid: 0}, unreadable=0)
        self.sandbox = dict(id='a'*64, metadata=dict(uid=self.uid, name=self.name, namespace='deer-flow'))
        self.container = dict(id=self.cid, podSandboxId='a'*64, state='CONTAINER_EXITED')

    def test_exact_failed_record_is_preserved_not_declared_dead(self):
        r = audit.terminal_record(self.p, 'adapter')
        self.assertTrue(r['node_runtime_check_required'])
        self.assertEqual(r['uid'], self.uid)
        self.assertNotIn('no_activity_observed', r)

    def test_real_pool_shape_keeps_seven_live_and_three_terminal_records(self):
        fake = MagicMock()
        fake.s.prep.WORKER = 'old-workers'
        fake.s.prep.DISPATCHER = 'old-dispatch'
        fake.s.core.inv.BASE_IDS = {'sha256:old'}
        workers = [pod('worker-'+str(i)) for i in range(6)]
        workers += [pod('missing-'+str(i), mode='missing') for i in range(3)]
        for i, (name, (reason, cid)) in enumerate(audit.HISTORICAL.items()):
            p = copy.deepcopy(self.p)
            p['metadata'].update(name=name, uid='11111111-1111-1111-1111-%012d' % i)
            p['status']['containerStatuses'][0].update(
                containerID='containerd://'+cid if cid else '', restartCount=0 if cid else 1,
                state={'terminated': {'reason': reason, 'exitCode': 1 if cid else 137}})
            workers.append(p)
        fake.s.core.inv.owned_pods.side_effect = [workers, [pod('dispatcher', 'dispatcher')]]
        fake.s.get.return_value = {'items': []}
        deps = {n: {'spec': {'template': {'metadata': {'labels': {'app': n}}}}}
                for n in ('old-workers', 'old-dispatch')}
        with patch.object(audit, 'sw', fake), patch.object(audit, 'emit'):
            result = audit.old_inventory(deps)
        self.assertEqual(len(result['targets']), 7)
        self.assertEqual(len(result['pending']), 3)
        self.assertEqual(len(result['terminal']), 3)
        self.assertTrue(all(x['node_runtime_check_required'] for x in result['terminal']))
        fake.s.call.assert_not_called()

    def test_other_failed_pod_not_skipped(self):
        self.p['metadata']['name'] = 'unreviewed-pod'
        with self.assertRaises(audit.AuditFailure):
            audit.terminal_record(self.p, 'adapter')

    def test_different_node_not_checked_as_local(self):
        self.p['spec']['nodeName'] = 'datax-02'
        with self.assertRaises(audit.AuditFailure):
            audit.terminal_record(self.p, 'adapter')

    def test_known_container_replacement_rejected(self):
        self.p['status']['containerStatuses'][0]['containerID'] = 'containerd://'+'f'*64
        with self.assertRaises(audit.AuditFailure):
            audit.terminal_record(self.p, 'adapter')

    def test_unknown_status_record_requires_runtime_check(self):
        self.p['metadata']['name'] = 'deer-flow-mineru-adapter-7f59bc8879-mdr5k'
        self.p['status']['containerStatuses'][0].update(containerID='', restartCount=1,
            state={'terminated': {'reason': 'ContainerStatusUnknown', 'exitCode': 137}})
        row = audit.terminal_record(self.p, 'adapter')
        self.assertEqual(row['known_container_ids'], [])
        self.assertTrue(row['node_runtime_check_required'])

    def test_exited_metadata_no_tasks_no_processes_observed(self):
        r = audit.runtime_row(self.record, [self.sandbox], [self.container], {}, self.cgroups)
        self.assertTrue(r['no_activity_observed'])
        self.assertTrue(r['snapshot_only'])

    def test_garbage_collected_metadata_requires_other_clear_checks(self):
        r = audit.runtime_row(self.record, [], [], {}, self.cgroups)
        self.assertTrue(r['no_activity_observed'])
        self.assertEqual(r['cri_container_states'], [])

    def test_unknown_cri_state_kept_as_unresolved(self):
        self.container['state'] = 'CONTAINER_UNKNOWN'
        self.assertFalse(audit.runtime_row(self.record, [self.sandbox], [self.container], {}, self.cgroups)['no_activity_observed'])

    def test_running_cri_container_blocks_clear(self):
        self.container['state'] = 'CONTAINER_RUNNING'
        self.assertFalse(audit.runtime_row(self.record, [self.sandbox], [self.container], {}, self.cgroups)['no_activity_observed'])

    def test_containerd_task_without_cri_metadata_detected_by_known_id(self):
        r = audit.runtime_row(self.record, [], [], {self.cid: 'RUNNING'}, self.cgroups)
        self.assertFalse(r['no_activity_observed'])

    def test_even_stopped_task_retained_for_review(self):
        self.assertFalse(audit.runtime_row(self.record, [], [], {self.cid: 'STOPPED'}, self.cgroups)['no_activity_observed'])

    def test_orphan_pod_cgroup_process_blocks_clear(self):
        self.cgroups['matches'][self.uid] = 1
        self.assertFalse(audit.runtime_row(self.record, [], [], {}, self.cgroups)['no_activity_observed'])

    def test_unreadable_cgroup_not_treated_as_absent(self):
        self.cgroups['unreadable'] = 1
        self.assertFalse(audit.runtime_row(self.record, [], [], {}, self.cgroups)['no_activity_observed'])

    def test_cgroup_both_drivers_and_container_id(self):
        for raw in ('8:memory:/kubepods/burstable/pod'+self.uid+'/abc',
                    '0::/kubepods.slice/kubepods-burstable-pod'+self.uid.replace('-', '_')+'.slice',
                    '0::/runtime/'+self.cid):
            self.assertTrue(audit.cgroup_matches(raw.encode(), self.record))
        self.assertFalse(audit.cgroup_matches(b'0::/unrelated', self.record))

    def test_runtime_uid_name_conflict_rejected(self):
        self.sandbox['metadata']['name'] = 'different'
        with self.assertRaises(audit.AuditFailure):
            audit.runtime_row(self.record, [self.sandbox], [], {}, self.cgroups)

    def test_numeric_container_state(self):
        self.container['state'] = 2
        self.assertTrue(audit.runtime_row(self.record, [self.sandbox], [self.container], {}, self.cgroups)['no_activity_observed'])

    def test_missing_or_boolean_state_not_zero(self):
        for value in (None, True, False, 'OTHER'):
            self.container['state'] = value
            with self.subTest(value=value), self.assertRaises(audit.AuditFailure):
                audit.runtime_row(self.record, [self.sandbox], [self.container], {}, self.cgroups)

    def test_task_table_parser(self):
        self.assertEqual(audit.parse_tasks(('TASK PID STATUS\n'+self.cid+' 123 RUNNING\n').encode()), {self.cid: 'RUNNING'})
        self.assertEqual(audit.parse_tasks(b'TASK PID STATUS\n'), {})
        self.assertEqual(audit.parse_tasks(b'TASK PID STATUS\nmanual-test 23 STOPPED\n'), {'manual-test': 'STOPPED'})

    def test_malformed_task_table_never_empty(self):
        for raw in (b'', b'ERROR\n', b'TASK PID STATUS\nshort\n', b'TASK PID STATUS\nx 0 NEWSTATE\n'):
            with self.subTest(raw=raw), self.assertRaises(audit.AuditFailure):
                audit.parse_tasks(raw)

    def test_runtime_command_failure_is_retained_not_suppressed(self):
        with patch.object(audit.socket, 'gethostname', return_value='wrong-node'), patch.object(audit, 'emit'):
            result = audit.terminal_runtime([self.record])
        self.assertEqual(result['check'], 'INCOMPLETE')
        self.assertFalse(result['no_activity_observed'])

    def test_terminal_inventory_does_not_abort_live_observations(self):
        fake = MagicMock()
        candidates = [dict(container='adapter', name=str(i)) for i in range(6)]+[dict(container='dispatcher')]
        fake.candidate_ready.return_value = candidates
        inv = dict(targets=[dict(name='old')], pending=[], terminal=[self.record], services=[])
        with patch.object(audit, 'sw', fake), patch.object(audit, 'bootstrap', return_value='probe'), \
                patch.object(audit, 'old_inventory', return_value=inv), \
                patch.object(audit, 'terminal_runtime', return_value={'no_activity_observed': False, 'check': 'INCOMPLETE'}), \
                patch.object(audit, 'observe', return_value={'receipt': receipt()}), \
                patch.object(audit.time, 'sleep'), patch.object(audit, 'emit'):
            report = dict(samples=[])
            audit.run(Path('/var/lib/private-test'), report)
        self.assertEqual(len(report['samples']), 3)
        self.assertEqual(report['old_pool_audit'], 'OBSERVATIONS_COLLECTED_WITH_RUNTIME_GAP')
        self.assertFalse(report['ready_to_retire'])
        self.assertFalse(report['terminal_runtime_clear'])
        fake.patch_once.assert_not_called()


if __name__ == '__main__':
    unittest.main()
