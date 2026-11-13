"""Offline comparison cases for the two operator-observed admission differences."""
import copy
import hashlib
import json
from pathlib import Path
import runpy


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SOURCE = ROOT / 'portable-skills/deployment/scheduler-budget-20261113-r5/activate-scheduler-budget-20261113-r5.py'
FIXTURES = ROOT / 'portable-skills/deployment/scheduler-budget-20261113-r4/test_scheduler_budget.py'
REVISION = '565b4ff8d6'
SYNTHETIC_VOLUME = 'kube-api-access-z9x8w'


def main():
    host = runpy.run_path(str(SOURCE), run_name='independent_offline_review')
    fixtures = runpy.run_path(str(FIXTURES), run_name='independent_offline_fixtures')
    deployment = fixtures['fixture']()
    requested = deployment['spec']['template']['spec']
    while len(requested['volumes']) < 9:
        requested['volumes'].append({'name': 'synthetic-inert-%d' % len(requested['volumes']), 'emptyDir': {}})
    requested['initContainers'] = [
        {'name': 'synthetic-init-a', 'image': 'synthetic-init:v1', 'command': ['sh', '-c', 'offline-a'],
         'volumeMounts': [{'name': 'config', 'mountPath': '/synthetic/a', 'readOnly': True},
                          {'name': 'ca', 'mountPath': '/synthetic/b', 'readOnly': True}]},
        {'name': 'synthetic-init-b', 'image': 'synthetic-init:v1', 'command': ['sh', '-c', 'offline-b'],
         'volumeMounts': [{'name': 'config', 'mountPath': '/synthetic/c', 'readOnly': True}]},
    ]
    requested['topologySpreadConstraints'] = [{
        'maxSkew': 1, 'topologyKey': 'kubernetes.io/hostname', 'whenUnsatisfiable': 'ScheduleAnyway',
        'labelSelector': {'matchLabels': {'app': 'gateway'}}, 'matchLabelKeys': ['pod-template-hash'],
    }]
    pod, replica_set = fixtures['runtime_fixture'](deployment)
    pod['metadata']['labels'] = {'app': 'gateway', 'pod-template-hash': REVISION}
    replica_set['metadata']['labels'] = {'app': 'gateway', 'pod-template-hash': REVISION}
    replica_set['spec']['template']['metadata']['labels']['pod-template-hash'] = REVISION
    replica_set['spec']['selector'] = {'matchLabels': {'app': 'gateway', 'pod-template-hash': REVISION}}
    # The real mount-name plaintext was not observed. This name is deliberately
    # synthetic; it must be bound dynamically, never mistaken for field evidence.
    pod['spec']['volumes'].append({'name': SYNTHETIC_VOLUME, 'projected': {
        'sources': [{'serviceAccountToken': {'path': 'token'}}]}})
    mount = {'name': SYNTHETIC_VOLUME, 'mountPath': host['SA_PATH'], 'readOnly': True}
    pod['spec']['containers'][0]['volumeMounts'].append(copy.deepcopy(mount))
    for container in pod['spec']['initContainers']:
        container['volumeMounts'].append(copy.deepcopy(mount))
    pod['spec']['topologySpreadConstraints'][0]['labelSelector']['matchExpressions'] = [
        {'key': 'pod-template-hash', 'operator': 'In', 'values': [REVISION]}]

    def actual_expression(p):
        return p['spec']['topologySpreadConstraints'][0]['labelSelector']['matchExpressions'][0]

    def remove_declared_key(d, p, r):
        del d['spec']['template']['spec']['topologySpreadConstraints'][0]['matchLabelKeys']
        del r['spec']['template']['spec']['topologySpreadConstraints'][0]['matchLabelKeys']

    def disable_automount(d, p, r):
        d['spec']['template']['spec']['automountServiceAccountToken'] = False
        r['spec']['template']['spec']['automountServiceAccountToken'] = False
        p['spec']['automountServiceAccountToken'] = False

    cases = [
        ('observed_two_init_mounts_and_self_hash_merge', None),
        ('changed_init_command', lambda d, p, r: p['spec']['initContainers'][0].update(command=['untrusted-command'])),
        ('different_init_volume_name', lambda d, p, r: p['spec']['initContainers'][0]['volumeMounts'][-1].update(name='kube-api-access-other')),
        ('writable_init_mount', lambda d, p, r: p['spec']['initContainers'][1]['volumeMounts'][-1].update(readOnly=False)),
        ('selector_wrong_operator', lambda d, p, r: actual_expression(p).update(operator='NotIn')),
        ('selector_wrong_value', lambda d, p, r: actual_expression(p).update(values=['another-revision'])),
        ('selector_wrong_key', lambda d, p, r: actual_expression(p).update(key='another-key')),
        ('selector_extra_expression', lambda d, p, r: p['spec']['topologySpreadConstraints'][0]['labelSelector']['matchExpressions'].append({'key': 'unrequested', 'operator': 'Exists'})),
        ('changed_topology_key', lambda d, p, r: p['spec']['topologySpreadConstraints'][0].update(topologyKey='topology.kubernetes.io/zone')),
        ('changed_max_skew', lambda d, p, r: p['spec']['topologySpreadConstraints'][0].update(maxSkew=2)),
        ('template_did_not_declare_match_key', remove_declared_key),
        ('replicaset_metadata_hash_disagrees', lambda d, p, r: r['metadata']['labels'].update({'pod-template-hash': 'wrong'})),
        ('replicaset_template_hash_disagrees', lambda d, p, r: r['spec']['template']['metadata']['labels'].update({'pod-template-hash': 'wrong'})),
        ('replicaset_selector_hash_disagrees', lambda d, p, r: r['spec']['selector']['matchLabels'].update({'pod-template-hash': 'wrong'})),
        ('automount_explicitly_disabled', disable_automount),
        ('projected_token_source_absent', lambda d, p, r: p['spec']['volumes'][-1]['projected'].update(sources=[])),
    ]
    results = []
    for name, mutate in cases:
        d, p, r = copy.deepcopy((deployment, pod, replica_set))
        if mutate:
            mutate(d, p, r)
        before = host['canonical']([d, p, r])
        try:
            host['pod_guard'](p, r, d, 'fixed-image-id')
        except host['Refused'] as error:
            assert mutate is not None, str(error)
            result = {'case': name, 'accepted': False, 'error_code': str(error),
                      'error_field': getattr(error, 'field', None), 'error_path': getattr(error, 'path', None)}
        else:
            assert mutate is None, 'UNEXPECTED_ACCEPTANCE:' + name
            result = {'case': name, 'accepted': True}
        assert host['canonical']([d, p, r]) == before, 'COMPARISON_MUTATED_INPUTS:' + name
        results.append(result)
    report = {'schema': 'scheduler-admission-independent-cases.v1',
        'source_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(), 'source_bytes': SOURCE.stat().st_size,
        'basis': 'Exact observed admission field shape; all other fixture content is explicitly synthetic',
        'synthetic_mount_name_is_not_claimed_as_observed': True,
        'observed_init_added_mount_indices': [[0, 2], [1, 1]], 'original_volumes_unchanged': 9,
        'cases': results, 'all_inputs_unchanged': True, 'passed': True,
        'token_or_secret_content_read': False, 'application_imported': False,
        'production_operations': 0, 'author_files_modified': False, 'business_acceptance_proven': False}
    (HERE / 'INDEPENDENT-CASES.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    main()
