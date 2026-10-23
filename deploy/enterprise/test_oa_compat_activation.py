import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import patch


class Tests(unittest.TestCase):
    def setUp(self):
        self.n = {'__name__': 'activation_test'}
        exec(compile(Path('/candidate.py').read_bytes(), '/candidate.py', 'exec'), self.n)

    def deployment(self, name):
        pin = self.n['EXPECTED'][name]
        d = {'metadata': {'uid': pin['uid'], 'generation': pin['generation'], 'resourceVersion': '123'},
             'spec': {'replicas': 2 if name == 'gateway' else 1, 'selector': {'matchLabels': {'app': name}},
                      'template': {'spec': {'containers': [{'name': name, 'image': pin['old_image'], 'env': [{'name': 'KEEP', 'value': 'unchanged'}]}]}}}}
        if name == 'gateway':
            d['spec']['template']['spec'].update(
                nodeSelector={'workload': 'gateway'},
                affinity={'nodeAffinity': {'requiredDuringSchedulingIgnoredDuringExecution': {
                    'nodeSelectorTerms': [{'matchExpressions': [{'key': 'kubernetes.io/hostname',
                        'operator': 'In', 'values': sorted(pin['nodes'])}]}]}}},
                tolerations=[{'key': 'dedicated', 'value': 'gateway', 'operator': 'Equal', 'effect': 'NoSchedule'}])
        d['status'] = {k: d['spec']['replicas'] for k in ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas')}
        d['status']['observedGeneration'] = pin['generation']
        pin['spec_sha256'] = self.n['digest'](d['spec'])
        return d

    def test_image_only_cas_patch(self):
        d = self.deployment('gateway')
        before = copy.deepcopy(d)
        ops, expected = self.n['plan'](d, self.n['EXPECTED']['gateway'])
        self.assertEqual(d, before)
        self.assertEqual([x['op'] for x in ops], ['test', 'test', 'test', 'replace'])
        self.assertEqual(ops[-1]['path'], '/spec/template/spec/containers/0/image')
        expected['template']['spec']['containers'][0]['image'] = d['spec']['template']['spec']['containers'][0]['image']
        self.assertEqual(expected, d['spec'])

    def test_drift_refused(self):
        d = self.deployment('gateway')
        d['spec']['replicas'] += 1
        with self.assertRaises(self.n['Refused']):
            self.n['plan'](d, self.n['EXPECTED']['gateway'])

    def test_active_task_gate(self):
        self.n['remote'] = lambda *a: {'status': 'PASS', 'revisions': ['0020_ppt_confirmation'], 'database_readonly': True, 'active_runs': 1, 'active_oa_jobs': 0, 'active_callback_leases': 0}
        with self.assertRaisesRegex(self.n['Refused'], 'TASKS_STILL_ACTIVE'):
            self.n['database_gate']({}, True)

    def test_wrong_schema_refused(self):
        self.n['remote'] = lambda *a: {'status': 'PASS', 'revisions': ['0021_oa_result_sets'], 'database_readonly': True}
        with self.assertRaisesRegex(self.n['Refused'], 'DATABASE_BASELINE_CHANGED'):
            self.n['database_gate']({}, False)

    def test_uncertain_patch_not_retried(self):
        with patch.object(self.n['subprocess'], 'run', side_effect=self.n['subprocess'].TimeoutExpired('kubectl', 45)) as run:
            with self.assertRaises(self.n['subprocess'].TimeoutExpired):
                self.n['command'](['patch', 'deployment', 'gateway'], [])
        self.assertEqual(run.call_count, 1)

    def test_config_references_cover_mounts_and_environment(self):
        d = self.deployment('gateway')
        s = d['spec']['template']['spec']
        s['volumes'] = [{'configMap': {'name': 'cfg'}}, {'secret': {'secretName': 'mounted'}}]
        s['containers'][0]['envFrom'] = [{'secretRef': {'name': 'env-all'}}]
        s['containers'][0]['env'].append({'name': 'KEY', 'valueFrom': {'secretKeyRef': {'name': 'env-key', 'key': 'private'}}})
        self.assertEqual(self.n['configuration_refs']({'gateway': d}), [('configmap', 'cfg'), ('secret', 'env-all'), ('secret', 'env-key'), ('secret', 'mounted')])

    def test_evidence_and_preload_binding(self):
        n = self.n
        value = {'status': 'COMPAT_IMAGES_ACCEPTED_NOT_DEPLOYED', 'production_database_access': False, 'schema_migrated': False,
                 'checks': [{'deployment': name, 'status': 'PASS', 'observation': {'status': 'INSTALLED_COMPAT_IMAGE_ACCEPTED'},
                             'pods': [{'containers': [{'imageID': pin['candidate_image']}]}]} for name, pin in n['EXPECTED'].items()]}
        raw = json.dumps(value).encode()
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'REPORT.json'
            p.write_bytes(raw)
            n['ACCEPTANCE'] = {'path': str(p), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
            n['evidence']()
            n['PRELOADS'][0]['node_preload_passed'] = False
            with self.assertRaisesRegex(n['Refused'], 'PRELOAD_NOT_ACCEPTED'):
                n['evidence']()

    def test_missing_tasks_drained_flag_exits_before_action(self):
        with patch('sys.argv', ['activate', '--resume-compatibility-once']), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.n['main']()

    def test_database_child_uses_only_readonly_selects(self):
        events = []
        class Transaction:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
        class Connection:
            def transaction(self, **kwargs):
                events.append(('transaction', kwargs))
                return Transaction()
            async def fetch(self, sql):
                events.append(('sql', sql))
                return [('0020_ppt_confirmation',)]
            async def fetchval(self, sql):
                events.append(('sql', sql))
                return 0
            async def close(self):
                events.append(('closed', True))
        async def connect(*args, **kwargs):
            return Connection()
        pg = ModuleType('asyncpg'); pg.connect = connect
        cfg = ModuleType('deerflow.config')
        cfg.get_app_config = lambda: SimpleNamespace(database=SimpleNamespace(backend='postgres', postgres_url='postgresql://synthetic'))
        output = io.StringIO()
        with patch.dict('sys.modules', {'asyncpg': pg, 'deerflow.config': cfg}), patch('sys.argv', ['check', '{"action":"database"}']), contextlib.redirect_stdout(output):
            exec(self.n['CHILD'], {'__name__': 'child_test'})
        self.assertEqual(json.loads(output.getvalue())['status'], 'PASS')
        self.assertIn(('transaction', {'readonly': True}), events)
        self.assertTrue(all(s.startswith('SELECT ') for k, s in events if k == 'sql'))
        self.assertEqual(events[-1], ('closed', True))

    def flow(self, active=False, patch_fail=False, removed_taint=False, taint_removed_after_patch=False,
             inspect=False, retry_when_drained=False, callback_active=False, race_before_first_patch=False,
             allow_interruption=False):
        n = self.n
        state = {name: self.deployment(name) for name in n['EXPECTED']}
        front = {'metadata': {'uid': n['FRONT']['uid'], 'generation': n['FRONT']['generation']}, 'spec': {'replicas': 1},
                 'status': {'observedGeneration': n['FRONT']['generation'], 'replicas': 1, 'updatedReplicas': 1, 'readyReplicas': 1, 'availableReplicas': 1}}
        n['FRONT']['spec_sha256'] = n['digest'](front['spec'])
        state['frontend'] = front
        patches = []
        database_calls = []
        def pods(name):
            d = state[name]
            pin = n['EXPECTED'][name]
            return [{'metadata': {'name': name + '-' + str(i), 'uid': name + '-uid-' + str(i), 'ownerReferences': [{'uid': name + '-rs', 'controller': True}]},
                     'spec': {'nodeName': next(iter(pin['nodes'])), 'containers': copy.deepcopy(d['spec']['template']['spec']['containers'])},
                     'status': {'containerStatuses': [{'ready': True, 'imageID': d['spec']['template']['spec']['containers'][0]['image'], 'containerID': 'container-' + name + str(i), 'restartCount': 0}]}}
                    for i in range(d['spec']['replicas'])]
        def command(args, value=None, raw=False):
            self.assertNotIn(args[0], ('apply', 'delete', 'create'))
            if args[:2] == ['get', 'deployment']:
                return copy.deepcopy(state[args[2]])
            if args[:2] == ['get', 'service']:
                return {'spec': {'selector': {'app': args[2]}}}
            if args[:2] == ['get', 'nodes']:
                nodes = []
                for name, pin in n['EXPECTED'].items():
                    for host, uid in pin['nodes'].items():
                        node = {'metadata': {'name': host, 'uid': uid, 'labels': {'kubernetes.io/hostname': host}},
                                'spec': {}, 'status': {'conditions': [{'type': 'Ready', 'status': 'True'}, {'type': 'DiskPressure', 'status': 'False'}]}}
                        if name == 'gateway':
                            node['metadata']['labels']['workload'] = 'gateway'
                            node['spec']['taints'] = [{'key': 'dedicated', 'value': 'gateway', 'effect': 'NoSchedule'}]
                        nodes.append(node)
                for host, uid in n['REVIEWED_EXTRA_NODES'].items():
                    taints = [{'key': 'dedicated', 'value': 'gateway-redis', 'effect': 'NoSchedule'}] if host.endswith('0008') else [
                        {'key': 'node.kubernetes.io/unschedulable', 'effect': 'NoSchedule'}]
                    if (removed_taint or (taint_removed_after_patch and patches)) and host.endswith('0008'):
                        taints = []
                    nodes.append({'metadata': {'name': host, 'uid': uid}, 'spec': {'taints': taints}})
                return {'items': nodes}
            if args[:2] in (['get', 'replicasets'], ['get', 'pods'], ['get', 'endpointslices']):
                name = args[3].split('=')[1]
                if args[1] == 'replicasets':
                    return {'items': [{'metadata': {'uid': name + '-rs', 'ownerReferences': [{'uid': state[name]['metadata']['uid'], 'controller': True}]}}]}
                if args[1] == 'pods':
                    return {'items': pods(name)}
                return {'items': [{'endpoints': [{'targetRef': {'uid': p['metadata']['uid']}, 'conditions': {'ready': True}} for p in pods(name)]}]}
            if args[0] == 'exec':
                payload = json.loads(args[-1])
                if payload['action'] in ('database', 'tasks'):
                    database_calls.append(payload['action'])
                    racing = race_before_first_patch and len(database_calls) > 1
                    return {'status': 'PASS', 'revisions': ['0020_ppt_confirmation'], 'database_readonly': True,
                            'active_runs': int(active or racing), 'active_oa_jobs': 0, 'active_callback_leases': int(callback_active)}
                return {'status': 'PASS', 'sources': payload['pin']['sources']}
            if args[0] == 'patch':
                name = args[2]
                patches.append(name)
                if patch_fail:
                    raise n['subprocess'].TimeoutExpired('kubectl', 45)
                self.assertEqual(value[0]['value'], state[name]['metadata']['uid'])
                self.assertEqual(value[2]['value'], state[name]['spec'])
                state[name]['spec']['template']['spec']['containers'][0]['image'] = value[3]['value']
                state[name]['metadata']['generation'] += 1
                state[name]['status']['observedGeneration'] += 1
                return copy.deepcopy(state[name])
            raise AssertionError(args)
        n['command'] = command
        n['evidence'] = lambda: None
        n['prior_failure_evidence'] = lambda: {'status': 'SYNTHETIC_NOOP_PRIOR'}
        argv = ['activate', '--inspect-tasks-readonly'] if inspect else ['activate', '--resume-compatibility-once', '--tasks-drained']
        if allow_interruption:
            argv[-1] = '--allow-interrupt-active-tasks'
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, patch.object(n['os'], 'uname', return_value=SimpleNamespace(nodename='datax-01')), patch('sys.argv', argv), contextlib.redirect_stdout(output):
            n['CONTROL'] = directory + '/activation'
            code = n['main']()
            report = json.loads(output.getvalue().splitlines()[-1])
            self.assertEqual(Path(n['CONTROL']).exists(), report['once_claimed'])
            if retry_when_drained:
                self.assertFalse(report['once_claimed'])
                self.assertEqual(patches, [])
                active = False
                output.truncate(0)
                output.seek(0)
                code = n['main']()
                report = json.loads(output.getvalue().splitlines()[-1])
            if report['once_claimed']:
                report['runtime'] = json.loads(Path(n['CONTROL'], 'REPORT.json').read_text())['runtime']
        return code, patches, report

    def test_complete_rollout_sequence_and_receipts(self):
        code, patches, report = self.flow()
        self.assertEqual(code, 0)
        self.assertEqual(patches, ['ppt-master-runner', 'gateway'])
        self.assertEqual(len(report['runtime']['gateway']), 2)
        self.assertEqual(len(report['runtime']['ppt-master-runner']), 1)
        self.assertFalse(report['schema_migration_requested'])
        self.assertTrue(report['frontend_preserved'])

    def test_active_tasks_stop_before_patch(self):
        code, patches, report = self.flow(active=True)
        self.assertEqual(code, 1)
        self.assertEqual(patches, [])
        self.assertEqual(report['error_code'], 'TASKS_STILL_ACTIVE')
        self.assertEqual(report['task_observation']['active_runs'], 1)
        self.assertFalse(report['once_claimed'])
        self.assertIsNone(report['report_path'])

    def test_same_r3_can_be_used_after_readonly_task_gate_clears(self):
        code, patches, report = self.flow(active=True, retry_when_drained=True)
        self.assertEqual(code, 0)
        self.assertEqual(patches, ['ppt-master-runner', 'gateway'])
        self.assertTrue(report['once_claimed'])

    def test_new_task_at_final_prepatch_check_does_not_consume_once(self):
        code, patches, report = self.flow(race_before_first_patch=True)
        self.assertEqual(code, 1)
        self.assertEqual(patches, [])
        self.assertFalse(report['once_claimed'])
        self.assertEqual(report['task_observation']['active_runs'], 1)

    def test_live_callback_lease_blocks_rollout(self):
        code, patches, report = self.flow(callback_active=True)
        self.assertEqual(code, 1)
        self.assertEqual(patches, [])
        self.assertEqual(report['task_observation']['active_callback_leases'], 1)

    def test_inspection_with_active_tasks_does_not_claim_or_patch(self):
        code, patches, report = self.flow(active=True, inspect=True)
        self.assertEqual(code, 0)
        self.assertEqual(patches, [])
        self.assertEqual(report['status'], 'TASKS_ACTIVE_NOT_DRAINED')
        self.assertFalse(report['once_claimed'])
        self.assertFalse(report['files_modified'])

    def test_inspection_without_tasks_is_only_point_in_time(self):
        code, patches, report = self.flow(inspect=True)
        self.assertEqual(code, 0)
        self.assertEqual(patches, [])
        self.assertEqual(report['status'], 'TASKS_DRAINED_AT_SAMPLE_ONLY')
        self.assertFalse(report['ready_for_oa_migration'])
        self.assertFalse(report['once_claimed'])

    def test_explicit_interruption_authority_records_tasks_and_updates_both_roles(self):
        code, patches, report = self.flow(active=True, callback_active=True, allow_interruption=True)
        self.assertEqual(code, 0)
        self.assertEqual(patches, ['ppt-master-runner', 'gateway'])
        self.assertFalse(report['tasks_drained_operator_attested'])
        self.assertTrue(report['active_task_interruption_operator_authorized'])
        self.assertEqual(report['task_policy'], 'ALLOW_ACTIVE_TASK_INTERRUPTION')
        self.assertEqual(report['database_before']['active_runs'], 1)
        self.assertEqual(report['database_after']['active_callback_leases'], 1)
        self.assertTrue(all(p['active_task_observation']['active_runs'] == 1 for p in report['patches']))
        self.assertFalse(report['schema_migration_requested'])

    def test_interruption_authority_does_not_skip_node_coverage(self):
        code, patches, report = self.flow(active=True, allow_interruption=True, removed_taint=True)
        self.assertEqual(code, 1)
        self.assertEqual(patches, [])
        self.assertEqual(report['error_code'], 'SCHEDULABLE_NODE_NOT_PRELOADED')

    def test_interruption_authority_does_not_retry_uncertain_patch(self):
        code, patches, report = self.flow(active=True, allow_interruption=True, patch_fail=True)
        self.assertEqual(code, 1)
        self.assertEqual(patches, ['ppt-master-runner'])
        self.assertTrue(report['patches'][0]['patch_may_have_applied'])
        self.assertFalse(report['patches'][0]['acknowledged'])

    def test_uncertain_first_patch_stops_second_deployment(self):
        code, patches, report = self.flow(patch_fail=True)
        self.assertEqual(code, 1)
        self.assertEqual(patches, ['ppt-master-runner'])
        self.assertTrue(report['patches'][0]['patch_may_have_applied'])
        self.assertFalse(report['patches'][0]['acknowledged'])

    def test_newly_schedulable_redis_node_refuses_before_any_patch(self):
        code, patches, report = self.flow(removed_taint=True)
        self.assertEqual(code, 1)
        self.assertEqual(patches, [])
        self.assertEqual(report['error_code'], 'SCHEDULABLE_NODE_NOT_PRELOADED')
        self.assertEqual(report['node_gate_failure']['role_matrix']['ppt-master-runner']['uncovered_nodes'],
                         ['ecs-zdz-pro-0008'])

    def test_removed_taint_during_runner_rollout_stops_gateway_patch(self):
        code, patches, report = self.flow(taint_removed_after_patch=True)
        self.assertEqual(code, 1)
        self.assertEqual(patches, ['ppt-master-runner'])
        self.assertTrue(report['production_workload_mutation'])
        self.assertEqual(report['error_code'], 'SCHEDULABLE_NODE_NOT_PRELOADED')

    def test_existing_r4_control_is_not_reused_or_overwritten(self):
        n = self.n
        n['prior_failure_evidence'] = lambda: {'status': 'SYNTHETIC_NOOP_PRIOR'}
        n['evidence'] = lambda: None
        n['command'] = lambda *a, **k: self.fail('no cluster command expected')
        with tempfile.TemporaryDirectory() as directory, patch.object(n['os'], 'uname', return_value=SimpleNamespace(nodename='datax-01')), patch('sys.argv', ['activate', '--resume-compatibility-once', '--tasks-drained']):
            n['CONTROL'] = directory + '/activation'
            Path(n['CONTROL']).mkdir()
            report = Path(n['CONTROL']) / 'REPORT.json'
            report.write_bytes(b'previous-r4-receipt')
            with self.assertRaisesRegex(n['Refused'], 'RESUME_ALREADY_CLAIMED'):
                n['main']()
            self.assertEqual(report.read_bytes(), b'previous-r4-receipt')

    def prior_report(self):
        return json.loads(Path(__file__).with_name('oa-compat-activation-field-20261022-r2.json').read_text())

    def test_exact_noop_prior_report_is_preserved(self):
        raw = (json.dumps(self.prior_report(), sort_keys=True) + '\n').encode()
        self.assertEqual(len(raw), self.n['PRIOR_FAILURE']['bytes'])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), self.n['PRIOR_FAILURE']['sha256'])
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'REPORT.json'
            p.write_bytes(raw)
            self.n['PRIOR_FAILURE']['path'] = str(p)
            before = p.stat()
            result = self.n['prior_failure_evidence']()
            self.assertFalse(result['production_workload_mutation'])
            self.assertEqual(p.read_bytes(), raw)
            self.assertEqual(p.stat().st_mtime_ns, before.st_mtime_ns)

    def test_changed_or_uncertain_prior_report_refused(self):
        for mode in ('hash', 'write'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                v = self.prior_report()
                v['patches'] = [{'patch_may_have_applied': True}]
                raw = (json.dumps(v, sort_keys=True) + '\n').encode()
                p = Path(directory) / 'REPORT.json'
                p.write_bytes(raw)
                self.n['PRIOR_FAILURE']['path'] = str(p)
                self.n['PRIOR_FAILURE']['bytes'] = len(raw)
                if mode == 'write':
                    self.n['PRIOR_FAILURE']['sha256'] = hashlib.sha256(raw).hexdigest()
                with self.assertRaisesRegex(self.n['Refused'], 'PRIOR_REPORT_HASH|PRIOR_REPORT_NOT_NOOP'):
                    self.n['prior_failure_evidence']()


if __name__ == '__main__':
    unittest.main()
