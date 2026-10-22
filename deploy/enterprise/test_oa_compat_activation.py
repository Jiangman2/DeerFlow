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
        self.n['remote'] = lambda *a: {'status': 'PASS', 'revisions': ['0020_ppt_confirmation'], 'database_readonly': True, 'active_runs': 1, 'active_oa_jobs': 0}
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
        with patch('sys.argv', ['activate', '--activate-compatibility-once']), contextlib.redirect_stderr(io.StringIO()):
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

    def flow(self, active=False, patch_fail=False):
        n = self.n
        state = {name: self.deployment(name) for name in n['EXPECTED']}
        front = {'metadata': {'uid': n['FRONT']['uid'], 'generation': n['FRONT']['generation']}, 'spec': {'replicas': 1},
                 'status': {'observedGeneration': n['FRONT']['generation'], 'replicas': 1, 'updatedReplicas': 1, 'readyReplicas': 1, 'availableReplicas': 1}}
        n['FRONT']['spec_sha256'] = n['digest'](front['spec'])
        state['frontend'] = front
        patches = []
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
                return {'items': [{'metadata': {'name': host, 'uid': uid}, 'status': {'conditions': [{'type': 'Ready', 'status': 'True'}, {'type': 'DiskPressure', 'status': 'False'}]}} for p in n['EXPECTED'].values() for host, uid in p['nodes'].items()]}
            if args[:2] in (['get', 'replicasets'], ['get', 'pods'], ['get', 'endpointslices']):
                name = args[3].split('=')[1]
                if args[1] == 'replicasets':
                    return {'items': [{'metadata': {'uid': name + '-rs', 'ownerReferences': [{'uid': state[name]['metadata']['uid'], 'controller': True}]}}]}
                if args[1] == 'pods':
                    return {'items': pods(name)}
                return {'items': [{'endpoints': [{'targetRef': {'uid': p['metadata']['uid']}, 'conditions': {'ready': True}} for p in pods(name)]}]}
            if args[0] == 'exec':
                payload = json.loads(args[-1])
                if payload['action'] == 'database':
                    return {'status': 'PASS', 'revisions': ['0020_ppt_confirmation'], 'database_readonly': True, 'active_runs': int(active), 'active_oa_jobs': 0}
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
        with tempfile.TemporaryDirectory() as directory, patch.object(n['os'], 'uname', return_value=SimpleNamespace(nodename='datax-01')), patch('sys.argv', ['activate', '--activate-compatibility-once', '--tasks-drained']), contextlib.redirect_stdout(io.StringIO()):
            n['CONTROL'] = directory + '/activation'
            code = n['main']()
            report = json.loads(Path(n['CONTROL'], 'REPORT.json').read_text())
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

    def test_uncertain_first_patch_stops_second_deployment(self):
        code, patches, report = self.flow(patch_fail=True)
        self.assertEqual(code, 1)
        self.assertEqual(patches, ['ppt-master-runner'])
        self.assertTrue(report['patches'][0]['patch_may_have_applied'])
        self.assertFalse(report['patches'][0]['acknowledged'])


if __name__ == '__main__':
    unittest.main()
