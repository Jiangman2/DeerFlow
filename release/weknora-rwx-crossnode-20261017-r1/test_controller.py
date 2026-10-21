import copy
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import controller as m


TOKEN = '1' * 32


def defaulted(obj):
    x = copy.deepcopy(obj)
    x['metadata']['uid'] = 'uid-' + obj['metadata']['name']
    if x['kind'] == 'NetworkPolicy':
        x['spec'].pop('ingress', None)
        x['spec'].pop('egress', None)
    if x['kind'] == 'Pod':
        x['spec'].update(serviceAccountName='default', serviceAccount='default', dnsPolicy='ClusterFirst',
                         schedulerName='default-scheduler', priority=0, preemptionPolicy='PreemptLowerPriority',
                         tolerations=[dict(key='node.kubernetes.io/not-ready', operator='Exists',
                                           effect='NoExecute', tolerationSeconds=300)])
        x['spec']['containers'][0].update(terminationMessagePath='/dev/termination-log', terminationMessagePolicy='File')
    return x


class Shapes(unittest.TestCase):
    def setUp(self):
        self.plan = m.object_plan(TOKEN)

    def test_receipt_hash(self):
        self.assertEqual(hashlib.sha256(m.canonical(m.NFS_RECEIPT) + b'\n').hexdigest(), m.NFS_SHA)

    def test_defaults_semantically_equal(self):
        for x in self.plan:
            m.validate(x, defaulted(x))

    def test_unknown_pod_mutations_rejected(self):
        for key, value in [('hostNetwork', True), ('hostPID', True), ('hostIPC', True),
                           ('automountServiceAccountToken', True), ('initContainers', [{'name': 'bad'}]),
                           ('imagePullSecrets', [{'name': 'production'}]), ('serviceAccountName', 'admin'),
                           ('securityContext', {'fsGroup': 1000}), ('nodeName', 'datax-02')]:
            with self.subTest(key=key):
                actual = defaulted(self.plan[5])
                actual['spec'][key] = value
                with self.assertRaises(m.Failure):
                    m.validate(self.plan[5], actual)

    def test_old_claim_and_extra_mount_rejected(self):
        for change in ('claim', 'volume', 'env', 'sidecar', 'entrypoint'):
            with self.subTest(change=change):
                a = defaulted(self.plan[5])
                s = a['spec']
                if change == 'claim':
                    s['volumes'][0]['persistentVolumeClaim']['claimName'] = 'weknora-data-files'
                if change == 'volume':
                    s['volumes'].append(dict(name='secret', secret=dict(secretName='production')))
                if change == 'env':
                    s['containers'][0]['envFrom'] = [dict(secretRef=dict(name='prod'))]
                if change == 'sidecar':
                    s['containers'].append(dict(name='sidecar'))
                if change == 'entrypoint':
                    s['containers'][0]['command'] = ['/app/WeKnora']
                with self.assertRaises(m.Failure):
                    m.validate(self.plan[5], a)

    def test_new_pv_spec_tampering_rejected(self):
        for field, value in [('persistentVolumeReclaimPolicy', 'Delete'), ('mountOptions', ['soft']),
                             ('nfs', dict(server=m.SERVER, path='/mnt/data/deerflow-rwx/home'))]:
            a = defaulted(self.plan[0])
            a['spec'][field] = value
            with self.assertRaises(m.Failure):
                m.validate(self.plan[0], a)

    def test_expected_claim_ref_api_default(self):
        a = defaulted(self.plan[0])
        a['spec']['claimRef'].update(uid='later-bound', apiVersion='v1', kind='PersistentVolumeClaim', resourceVersion='1')
        m.validate(self.plan[0], a)

    def test_ingress_empty_normalized_but_permission_not_relaxed(self):
        a = defaulted(self.plan[4])
        m.validate(self.plan[4], a)
        a['spec']['egress'] = [{}]
        with self.assertRaises(m.Failure):
            m.validate(self.plan[4], a)

    def test_mutated_labels_selected_by_service(self):
        a = defaulted(self.plan[5])
        a['metadata']['labels']['app'] = 'weknora'
        k = FakeKube()
        k.services = [dict(spec=dict(selector=dict(app='weknora')))]
        with self.assertRaises(m.Failure):
            m.service_safety(k, [a])

    def test_selectorless_service_not_match_all(self):
        k = FakeKube()
        k.services = [dict(spec={})]
        m.service_safety(k, self.plan[5:])


class FakeKube:
    def __init__(self):
        self.objects = {}
        self.calls = []
        self.services = []
        self.fail_create = 0
        self.created = 0
        self.interrupt = False
        self.fail_phase = None
        self.delete_uids = []
        self.io = []

    def get(self, kind, name=None, optional=False):
        self.calls.append(('get', kind, name))
        if kind == 'service':
            return dict(items=self.services)
        if kind == 'storageclass':
            return dict(provisioner='kubernetes.io/no-provisioner', volumeBindingMode='Immediate')
        obj = self.objects.get((kind, name))
        if obj is None and not optional:
            raise m.Failure('fixture_not_found')
        return copy.deepcopy(obj)

    def call(self, args, value=None, timeout=25):
        self.calls.append(('call', args))
        if args[0] == 'create':
            obj = defaulted(value)
            if '--dry-run=server' in args:
                return m.canonical(obj)
            assert (m.STATE / (m.key_for(value) + '-REQUESTED.json')).exists()
            self.created += 1
            kind = m.KINDS[obj['kind']]
            if kind == 'pvc':
                pv = self.objects[('pv', obj['spec']['volumeName'])]
                pv['spec']['claimRef']['uid'] = obj['metadata']['uid']
                pv['status'] = dict(phase='Bound')
                obj['status'] = dict(phase='Bound')
            if kind == 'pods':
                obj['spec']['nodeName'] = obj['spec']['nodeSelector']['kubernetes.io/hostname']
                obj['status'] = dict(phase='Running', containerStatuses=[dict(
                    name='probe', imageID=m.CONFIG_ID, ready=True, restartCount=0, state=dict(running={"startedAt":"now"}))])
            self.objects[(kind, obj['metadata']['name'])] = copy.deepcopy(obj)
            if self.created == self.fail_create:
                if self.interrupt:
                    raise KeyboardInterrupt()
                raise m.Failure('uncertain_create_after_send')
            return m.canonical(obj)
        if 'exec' in args:
            phase, token = args[-2:]
            assert (m.STATE / ('IO-' + phase.upper() + '-REQUESTED.json')).exists()
            self.io.append(phase)
            if phase == self.fail_phase:
                raise m.Failure('uncertain_remote_execution')
            return m.canonical(dict(probe='PASS', phase=phase, token=token, uid=1000, gid=1000,
                volumes=[dict(role=role, result='PASS') for role in ('files', 'docreader')]))
        if args[0] == 'delete':
            path = args[2]
            kind = 'pods' if '/pods/' in path else 'networkpolicy'
            name = path.rsplit('/', 1)[1]
            obj = self.objects[(kind, name)]
            assert value['preconditions']['uid'] == obj['metadata']['uid']
            assert (m.STATE / (m.key_for(obj) + '-DELETE-REQUESTED.json')).exists()
            self.delete_uids.append(value['preconditions']['uid'])
            del self.objects[(kind, name)]
            return b'{}'
        raise AssertionError('unexpected_command')


class Flow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir='/qualify')
        self.root = Path(self.tmp.name)
        self.k = FakeKube()
        self.patches = [patch.object(m, 'STATE', self.root/'state'), patch.object(m, 'PROBE_SOURCE', 'pass'),
                        patch.object(m, 'environment', return_value=self.k),
                        patch.object(m, 'fresh_nodes'), patch.object(m, 'baseline', return_value='stable')]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def run_once(self):
        with contextlib.redirect_stdout(io.StringIO()):
            rc = m.once()
        return rc, m.read_private(m.STATE/'RESULT.json')

    def test_full_success_only_new_objects_remain(self):
        rc, result = self.run_once()
        self.assertEqual(rc, 0)
        self.assertEqual(result['cross_node_io'], 'PASS')
        self.assertFalse(result['ready_to_scale'])
        self.assertFalse(result['ready_to_migrate'])
        self.assertEqual(self.k.created, 7)
        self.assertEqual([x[0] for x in self.k.objects], ['pv', 'pvc', 'pv', 'pvc'])
        self.assertEqual(self.k.io, ['write-a', 'read-a-write-b', 'read-b', 'cleanup'])
        self.assertEqual(len(self.k.delete_uids), 3)

    def test_second_attempt_no_replay(self):
        self.run_once()
        before = len(self.k.calls)
        with self.assertRaises(FileExistsError):
            self.run_once()
        self.assertEqual(len(self.k.calls), before)

    def test_uncertain_create_never_replayed_or_deleted(self):
        self.k.fail_create = 3
        rc, result = self.run_once()
        self.assertEqual(rc, 1)
        self.assertEqual(result['failed_check'], 'uncertain_create_after_send')
        self.assertEqual(self.k.created, 3)
        self.assertFalse(self.k.io)
        self.assertFalse(self.k.delete_uids)

    def test_interruption_durable_status(self):
        self.k.fail_create, self.k.interrupt = 1, True
        rc, result = self.run_once()
        self.assertEqual(rc, 1)
        self.assertEqual(result['error_type'], 'KeyboardInterrupt')
        self.assertTrue(list(m.STATE.glob('*-REQUESTED.json')))

    def test_uncertain_io_not_repeated_or_cleaned(self):
        self.k.fail_phase = 'read-a-write-b'
        rc, result = self.run_once()
        self.assertEqual(rc, 1)
        self.assertEqual(self.k.io, ['write-a', 'read-a-write-b'])
        self.assertFalse(self.k.delete_uids)

    def test_old_baseline_drift_stops_before_create(self):
        with patch.object(m, 'baseline', side_effect=['a', 'b']):
            rc, result = self.run_once()
        self.assertEqual(rc, 1)
        self.assertEqual(self.k.created, 0)
        self.assertEqual(result['failed_check'], 'baseline_changed_before_create')

    def test_status_get_only_and_no_file_changes(self):
        self.run_once()
        files = {p.name: p.read_bytes() for p in m.STATE.iterdir()}
        self.k.calls.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            m.status()
        self.assertTrue(all(x[0] == 'get' for x in self.k.calls))
        self.assertEqual(files, {p.name: p.read_bytes() for p in m.STATE.iterdir()})

    def test_status_failed_attempt_sees_unacknowledged_object(self):
        self.k.fail_create = 1
        self.run_once()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            m.status()
        rows = json.loads(output.getvalue())['objects']
        self.assertTrue(rows[0]['present'])

    def test_uid_replacement_rejected(self):
        self.k.fail_phase = 'write-a'
        self.run_once()
        plan = m.read_private(m.STATE/'STARTED.json')['plan']
        self.k.objects[('pods', plan[5]['metadata']['name'])]['metadata']['uid'] = 'replacement'
        with self.assertRaisesRegex(m.Failure, 'uid_changed'):
            m.check_probe(self.k, plan[5])

    def test_wrong_image_rejected(self):
        self.k.fail_phase = 'write-a'
        self.run_once()
        plan = m.read_private(m.STATE/'STARTED.json')['plan']
        self.k.objects[('pods', plan[5]['metadata']['name'])]['status']['containerStatuses'][0]['imageID'] = 'unknown'
        with self.assertRaisesRegex(m.Failure, 'image_identity'):
            m.check_probe(self.k, plan[5])

    def test_wrong_claim_binding_rejected(self):
        self.k.fail_phase = 'write-a'
        self.run_once()
        plan = m.read_private(m.STATE/'STARTED.json')['plan']
        self.k.objects[('pv', plan[0]['metadata']['name'])]['spec']['claimRef']['uid'] = 'other'
        with self.assertRaisesRegex(m.Failure, 'other_claim'):
            m.check_bindings(self.k, plan)

    def test_private_permission_refuses(self):
        self.root.chmod(0o777)
        with self.assertRaisesRegex(m.Failure, 'unsafe_private'):
            m.safe_dir(self.root)

    def test_symlink_state_refuses(self):
        target = self.root/'actual'
        target.mkdir()
        m.STATE.symlink_to(target)
        with self.assertRaises(m.Failure):
            m.safe_dir(m.STATE)

    def test_hardlinked_receipt_refuses(self):
        m.STATE.mkdir()
        m.record('ONE', dict(value=1))
        os.link(str(m.STATE/'ONE.json'), str(m.STATE/'TWO.json'))
        with self.assertRaises(m.Failure):
            m.read_private(m.STATE/'ONE.json')

    def test_record_cannot_overwrite(self):
        m.STATE.mkdir()
        m.record('ONE', {})
        with self.assertRaises(FileExistsError):
            m.record('ONE', {})

    def test_delete_production_kind_forbidden(self):
        with self.assertRaisesRegex(m.Failure, 'delete_kind_forbidden'):
            m.delete_test_object(self.k, dict(kind='Deployment'))


class CommandTests(unittest.TestCase):
    def test_stdout_bounded(self):
        with self.assertRaises(m.Failure):
            m.command([sys.executable, '-c', 'print("x"*10000)'], limit=100)

    def test_deadline(self):
        with self.assertRaises(m.Failure):
            m.command([sys.executable, '-c', 'import time;time.sleep(5)'], timeout=.1)

    def test_stderr_never_printed(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(m.Failure):
            m.command([sys.executable, '-c', 'import sys;sys.stderr.write("sensitive");sys.exit(1)'])
        self.assertNotIn('sensitive', json.dumps(m.LAST) + out.getvalue())

    def test_remote_classification_preserved(self):
        with self.assertRaises(m.Failure):
            m.command([sys.executable, '-c', 'import sys;print(\'{"probe":"UNCONFIRMED","check":"OS_IO_ERROR"}\');sys.exit(1)'])
        self.assertEqual(m.LAST['remote_probe_check'], 'OS_IO_ERROR')


class BaselineTests(unittest.TestCase):
    def nodes(self):
        objects = {}
        for name, (uid, ip) in m.NODES.items():
            objects[name] = dict(metadata=dict(uid=uid, labels={'kubernetes.io/hostname': name}), spec={},
                status=dict(addresses=[dict(type='InternalIP', address=ip)], conditions=[
                    dict(type=key, status=value) for key, value in
                    [('Ready', 'True'), ('MemoryPressure', 'False'), ('DiskPressure', 'False'), ('PIDPressure', 'False')]]))
        return objects

    def test_reviewed_nodes_pass(self):
        nodes = self.nodes()
        k = type('Getter', (), {'get': lambda self, kind, name: nodes[name]})()
        m.fresh_nodes(k)

    def test_node_drift_and_pressure_fail(self):
        for change in ('uid', 'ip', 'pressure', 'taint', 'cordoned', 'label'):
            with self.subTest(change=change):
                nodes = self.nodes()
                n = nodes['datax-03']
                if change == 'uid': n['metadata']['uid'] = 'other'
                if change == 'ip': n['status']['addresses'][0]['address'] = '1.2.3.4'
                if change == 'pressure': n['status']['conditions'][1]['status'] = 'True'
                if change == 'taint': n['spec']['taints'] = [{'key': 'busy'}]
                if change == 'cordoned': n['spec']['unschedulable'] = True
                if change == 'label': n['metadata']['labels']['kubernetes.io/hostname'] = 'wrong'
                k = type('Getter', (), {'get': lambda self, kind, name: nodes[name]})()
                with self.assertRaises(m.Failure): m.fresh_nodes(k)

    def test_get_errors_not_absence(self):
        k = object.__new__(m.Kube)
        k.base = ['/mock/kubectl']
        with patch.object(m, 'command', side_effect=m.Failure('forbidden')):
            with self.assertRaises(m.Failure):
                k.get('pods', 'probe', optional=True)

    def test_empty_optional_get_only_absence(self):
        k = object.__new__(m.Kube)
        k.base = ['/mock/kubectl']
        with patch.object(m, 'command', return_value=b''):
            self.assertIsNone(k.get('pods', 'probe', optional=True))
            with self.assertRaises(ValueError):
                k.get('pods', 'probe')

    def test_secret_reads_forbidden(self):
        k = object.__new__(m.Kube)
        with self.assertRaises(m.Failure): k.get('secret', 'prod')

    def test_baseline_hash_binds_specs_and_process_without_export(self):
        d = dict(kind='Deployment', metadata=dict(name='weknora-app', uid='44aca5b9-8fc1-44d6-af61-37c6d62723d5', generation=72),
                 spec=dict(replicas=1, private_env='SECRET_SENTINEL'), status=dict(
                     replicas=1, updatedReplicas=1, readyReplicas=1, availableReplicas=1, observedGeneration=72))
        pod = dict(kind='Pod', metadata=dict(name=m.APP_POD, uid=m.APP_UID),
                   spec=dict(nodeName='datax-03', containers=[dict(name='app', image=m.IMAGE)]),
                   status=dict(conditions=[dict(type='Ready', status='True')],
                               containerStatuses=[dict(name='app', containerID='old', restartCount=0, ready=True)]))
        def get(kind, name):
            if kind == 'deployment' and name == 'weknora-app': return d
            if kind == 'pods': return pod
            return dict(kind=kind, metadata=dict(name=name, uid='uid-' + name), spec={}, status=dict(phase='Bound'))
        k = type('Getter', (), {'get': lambda self, kind, name: get(kind, name)})()
        first = m.baseline(k)
        self.assertEqual(len(first), 64)
        self.assertNotIn('SECRET_SENTINEL', first)
        pod['status']['containerStatuses'][0]['containerID'] = 'new'
        self.assertNotEqual(first, m.baseline(k))
        pod['metadata']['uid'] = 'unexpected'
        with self.assertRaises(m.Failure): m.baseline(k)


if __name__ == '__main__':
    unittest.main()
