import ast
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import inventory as m


def obj(name, uid, spec=None, **extra):
    result = dict(metadata=dict(name=name, uid=uid), spec=spec or {})
    result.update(extra)
    return result


def fixtures():
    selector = dict(matchLabels=dict(app='weknora-app'))
    podspec = dict(containers=[dict(name='app', image=m.KNOWN_IMAGE, env=[
        dict(name='WEKNORA_ASYNQ_CORE_CONCURRENCY', value='16'),
        dict(name='DB_PASSWORD', value='DO_NOT_PRINT_PASSWORD'),
        dict(name='STORAGE_TYPE', value='local'),
        dict(name='SYSTEM_AES_KEY', valueFrom=dict(secretKeyRef=dict(
            name='weknora-secret', key='SYSTEM_AES_KEY')))],
        volumeMounts=[dict(name='files', mountPath='/data/files')])],
        volumes=[dict(name='files', persistentVolumeClaim=dict(claimName='weknora-data-files'))])
    deploy = obj('weknora-app', 'dep-1', dict(replicas=1, selector=selector,
        template=dict(metadata=dict(labels=selector['matchLabels']), spec=podspec)),
        status=dict(observedGeneration=4, replicas=1, updatedReplicas=1,
                    readyReplicas=1, availableReplicas=1))
    deploy['metadata']['generation'] = 4
    rs = obj('weknora-app-rs', 'rs-1')
    rs['metadata']['ownerReferences'] = [dict(kind='Deployment', uid='dep-1', controller=True)]
    pod = obj('weknora-app-current', m.KNOWN_POD_UID,
              dict(podspec, nodeName='datax-03'),
              status=dict(phase='Running', conditions=[dict(type='Ready', status='True')],
                          containerStatuses=[dict(name='app', ready=True, restartCount=0,
                                                  imageID='containerd://sha256:' + 'a'*64)]))
    pod['metadata']['ownerReferences'] = [dict(kind='ReplicaSet', uid='rs-1', controller=True)]
    claim = obj('weknora-data-files', 'pvc-1', dict(volumeName='weknora-files-pv',
        accessModes=['ReadWriteOnce'], storageClassName='local'), status=dict(phase='Bound'))
    pv = obj('weknora-files-pv', 'pv-1', dict(local=dict(path='/private/path-not-exported'),
        accessModes=['ReadWriteOnce'], claimRef=dict(namespace='deer-flow',
        name='weknora-data-files', uid='pvc-1'),
        nodeAffinity=dict(required=dict(nodeSelectorTerms=[dict(matchExpressions=[
            dict(key='kubernetes.io/hostname', operator='In', values=['datax-03'])])]))))
    rows = {('deployments', 'weknora-app'): deploy, ('replicasets', None): dict(items=[rs]),
            ('pods', None): dict(items=[pod]), ('persistentvolumeclaims', 'weknora-data-files'): claim,
            ('persistentvolumes', 'weknora-files-pv'): pv,
            ('services', None): dict(items=[obj('weknora-app', 'svc-1', dict(
                selector=selector['matchLabels'], type='ClusterIP'))]),
            ('endpoints', 'weknora-app'): dict(metadata=dict(name='weknora-app', uid='ep-1'),
                subsets=[dict(addresses=[dict(ip='DO_NOT_PRINT_IP', targetRef=dict(
                    kind='Pod', uid=m.KNOWN_POD_UID, name='weknora-app-current'))])]),
            ('horizontalpodautoscalers', None): dict(items=[]),
            ('poddisruptionbudgets', None): dict(items=[]),
            ('networkpolicies', None): dict(items=[])}
    for node in m.NODES:
        rows[('nodes', node)] = obj(node, node + '-uid', status=dict(
            conditions=[dict(type='Ready', status='True')],
            allocatable=dict(cpu='32', memory='64Gi', pods='110')))
    return rows


class FakeKube:
    def __init__(self):
        self.rows = fixtures()
        self.calls = []
        self.second_deployment = None

    def get(self, kind, name=None, selector=None):
        self.calls.append((kind, name, selector))
        if kind == 'deployments' and self.second_deployment and sum(
                x[0] == kind for x in self.calls) > 1:
            return copy.deepcopy(self.second_deployment)
        return copy.deepcopy(self.rows[(kind, name)])


class Tests(unittest.TestCase):
    def test_python37_grammar(self):
        ast.parse(Path(m.__file__).read_text(encoding='utf-8'), feature_version=(3, 7))

    def test_transport_is_get_only(self):
        k = m.Kube('/usr/bin/kubectl')
        with patch.object(m, 'bounded_run', return_value=(0, b'{"items":[]}', b'')) as run:
            k.get('pods', selector='app=weknora-app')
            args = run.call_args[0][0]
            self.assertIn('get', args)
            self.assertNotIn('exec', args)
            self.assertNotIn('--raw', args)
        for kind in ('secrets', 'configmaps', 'exec', 'pods/status', '--raw=/secrets'):
            with self.assertRaises(m.Failure):
                k.get(kind)

    def test_invalid_names_and_selector_fail_before_spawn(self):
        k = m.Kube('/usr/bin/kubectl')
        with patch.object(m, 'bounded_run', side_effect=AssertionError('spawn')):
            for name in ('--all', 'a/b', 'a\nb', ''):
                with self.assertRaises(m.Failure):
                    k.get('pods', name)
            with self.assertRaises(m.Failure):
                k.get('pods', selector='a=x --raw=/')

    def test_stderr_not_in_error(self):
        with patch.object(m, 'bounded_run', return_value=(1, b'PRIVATE', b'SECRET_VALUE')):
            with self.assertRaises(m.Failure) as error:
                m.Kube('/usr/bin/kubectl').get('nodes', 'datax-01')
        self.assertNotIn('SECRET', str(error.exception))
        self.assertNotIn('PRIVATE', str(error.exception))

    def test_output_limit(self):
        with self.assertRaises(m.Failure):
            m.bounded_run([sys.executable, '-c', 'print("x"*100000)'], 5, 100, 100)

    def test_deadline(self):
        with self.assertRaises(m.Failure):
            m.bounded_run([sys.executable, '-c', 'import time; time.sleep(5)'], .1, 100, 100)

    def test_normal_transport(self):
        rc, out, err = m.bounded_run([sys.executable, '-c', 'print("ok")'], 5, 100, 100)
        self.assertEqual((rc, out.strip(), err), (0, b'ok', b''))

    def test_complete_is_never_scaling_approval(self):
        r = m.collect(FakeKube())
        self.assertEqual(r['collection'], 'COMPLETE')
        self.assertTrue(r['baseline_stable'])
        self.assertFalse(r['ready_to_scale'])
        self.assertFalse(r['cross_node_file_access_tested'])
        self.assertFalse(r['production_writes'])
        self.assertEqual(r['deployment']['desired'], 1)

    def test_redaction(self):
        raw = json.dumps(m.collect(FakeKube()))
        for text in ('DO_NOT_PRINT_PASSWORD', 'DO_NOT_PRINT_IP', '/private/path-not-exported'):
            self.assertNotIn(text, raw)

    def test_local_volume_is_barrier(self):
        r = m.collect(FakeKube())
        v = r['persistent_storage'][0]
        self.assertEqual(v['backend'], 'local')
        self.assertEqual(v['node_affinity_hostname_values'], ['datax-03'])
        self.assertIn('NODE_LOCAL_STORAGE_REQUIRES_MIGRATION_OR_ARCHITECTURE_CHANGE', r['findings'])

    def test_rwx_does_not_prove_cross_node_access(self):
        k = FakeKube()
        pv = k.rows[('persistentvolumes', 'weknora-files-pv')]['spec']
        pv.pop('local'); pv.pop('nodeAffinity')
        pv['nfs'] = dict(server='PRIVATE_NFS', path='/private/share')
        pv['accessModes'] = ['ReadWriteMany']
        k.rows[('persistentvolumeclaims', 'weknora-data-files')]['spec']['accessModes'] = ['ReadWriteMany']
        r = m.collect(k)
        self.assertFalse(r['ready_to_scale'])
        self.assertFalse(r['cross_node_file_access_tested'])
        self.assertNotIn('PRIVATE_NFS', json.dumps(r))

    def test_reference_env_does_not_become_runtime_limit(self):
        rows = [dict(name='WEKNORA_MODEL_MAX_CONCURRENCY', valueFrom=dict(
            configMapKeyRef=dict(name='settings', key='limit')))]
        r = m.env_projection(dict(env=rows))
        self.assertEqual(r['WEKNORA_MODEL_MAX_CONCURRENCY']['source'], 'configMapKeyRef')
        self.assertNotIn('value', r['WEKNORA_MODEL_MAX_CONCURRENCY'])

    def test_secret_reference_identity_without_value_or_name(self):
        c = fixtures()[('deployments', 'weknora-app')]['spec']['template']['spec']['containers'][0]
        r = m.env_projection(c)['SYSTEM_AES_KEY']
        self.assertEqual(r['source'], 'secretKeyRef')
        self.assertIn('binding_sha256', r)
        self.assertNotIn('weknora-secret', json.dumps(r))

    def test_invalid_allowlisted_value_redacted(self):
        for name in ('WEKNORA_ASYNQ_CORE_CONCURRENCY', 'STORAGE_TYPE'):
            r = m.env_projection(dict(env=[dict(name=name, value='PRIVATE')]))
            self.assertNotIn('PRIVATE', json.dumps(r))
            self.assertEqual(r[name]['value_class'], 'UNRECOGNIZED_REDACTED')

    def test_deployment_drift(self):
        k = FakeKube(); k.second_deployment = copy.deepcopy(k.rows[('deployments', 'weknora-app')])
        k.second_deployment['spec']['replicas'] = 2
        r = m.collect(k)
        self.assertFalse(r['baseline_stable'])
        self.assertIn('deployments/weknora-app', r['changed_resources'])

    def test_health_changes_affect_fingerprint(self):
        pod = fixtures()[('pods', None)]['items'][0]
        before = m.fingerprint(pod)
        pod['status']['containerStatuses'][0]['restartCount'] = 1
        self.assertNotEqual(m.fingerprint(pod), before)

    def test_resource_version_is_not_config_drift(self):
        pod = fixtures()[('pods', None)]['items'][0]
        before = m.fingerprint(pod)
        pod['metadata']['resourceVersion'] = '1000000'
        self.assertEqual(m.fingerprint(pod), before)

    def test_two_times_104_is_not_runtime_approval(self):
        k = FakeKube()
        c = k.rows[('deployments', 'weknora-app')]['spec']['template']['spec']['containers'][0]
        c['env'] = [dict(name=m.POOL_ENV[key], value=str(value)) for key, value in
                    dict(core=16, postprocess=4, enrichment=64, maintenance=4, shared=4, wiki=12).items()]
        r = m.collect(k)
        self.assertEqual(r['per_replica_template_slots'], 104)
        self.assertEqual(r['hypothetical_two_identical_replicas_slots'], 208)
        self.assertTrue(r['capacity_arithmetic_is_not_a_proposed_change'])
        self.assertFalse(r['ready_to_scale'])

    def test_bad_json_never_prints_raw(self):
        with patch.object(m, 'bounded_run', return_value=(0, b'SECRET_NOT_JSON', b'')):
            with self.assertRaises(m.Failure) as error:
                m.Kube('/usr/bin/kubectl').get('nodes', 'datax-01')
        self.assertEqual(str(error.exception), 'kubectl_invalid_json')

    def test_total_deadline(self):
        with patch.object(m.time, 'monotonic', side_effect=[0, 241]):
            with self.assertRaises(m.Failure) as error:
                m.collect(FakeKube())
        self.assertEqual(str(error.exception), 'inventory_total_deadline')

    def test_foreign_owner_not_counted(self):
        k = FakeKube(); p = copy.deepcopy(k.rows[('pods', None)]['items'][0])
        p['metadata']['uid'] = 'foreign'; p['metadata']['ownerReferences'][0]['uid'] = 'foreign-rs'
        k.rows[('pods', None)]['items'].append(p)
        r = m.collect(k)
        self.assertEqual(len(r['pods']), 1)
        self.assertEqual(r['unowned_selector_pods'], 1)

    def test_wrong_claim_binding_rejected(self):
        k = FakeKube(); k.rows[('persistentvolumes', 'weknora-files-pv')]['spec']['claimRef']['uid'] = 'wrong'
        with self.assertRaises(m.Failure):
            m.collect(k)

    def test_optional_rbac_failure_visible(self):
        k = FakeKube(); original = k.get
        def get(kind, name=None, selector=None):
            if kind == 'horizontalpodautoscalers':
                raise m.Failure('kubectl_get_failed')
            return original(kind, name, selector)
        k.get = get
        r = m.collect(k)
        self.assertEqual(r['collection'], 'INCOMPLETE')
        self.assertIn('horizontalpodautoscalers', r['unavailable_resources'])
        self.assertFalse(r['ready_to_scale'])

    def test_expression_selector_fails_closed(self):
        with self.assertRaises(m.Failure):
            m.label_selector(dict(matchExpressions=[dict(key='app', operator='Exists')]))

    def test_source_binding_explicitly_unverified(self):
        r = m.collect(FakeKube())
        self.assertEqual(r['source_behavior_binding'], 'NOT_VERIFIED_AGAINST_RUNNING_BINARY')


if __name__ == '__main__':
    unittest.main()
