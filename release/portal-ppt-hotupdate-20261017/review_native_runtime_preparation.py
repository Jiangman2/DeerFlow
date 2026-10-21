"""Root review record for local adapters, not a runtime or rollout verdict."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MERGE = ROOT.parent.parent.parent / 'portal-ppt-merge-20261017'


def ref(path):
    raw = path.read_bytes()
    return {'path': str(path), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def read(path):
    return json.loads(path.read_bytes())


def pod_check(pod):
    spec = pod['spec']
    assert 'nodeName' not in spec and spec['schedulerName'] == 'default-scheduler'
    assert spec['preemptionPolicy'] == 'Never' and spec['restartPolicy'] == 'Never'
    assert spec['automountServiceAccountToken'] is False and spec['enableServiceLinks'] is False
    assert not any(spec.get(k, False) for k in ('hostNetwork', 'hostPID', 'hostIPC'))
    assert spec['nodeSelector'] == {'kubernetes.io/hostname': 'datax-02',
                                    'kubernetes.io/arch': 'arm64', 'kubernetes.io/os': 'linux'}
    for container in spec['containers'] + spec.get('initContainers', []):
        assert container['imagePullPolicy'] == 'Never'
        assert container['resources']['requests'] == container['resources']['limits']
        assert container['securityContext']['allowPrivilegeEscalation'] is False
        assert container['securityContext']['capabilities']['drop'] == ['ALL']
        assert not container.get('envFrom')
    for volume in spec['volumes']:
        assert set(volume) - {'name'} in ({'emptyDir'}, {'configMap'})


def main():
    db = MERGE / 'native-db-design-r2'
    hf = MERGE / 'native-host-frontend-r1'
    db_preparation = read(db / 'PREPARATION.json')
    for name, expected in db_preparation['files'].items():
        found = ref(db / name)
        assert all(found[key] == expected[key] for key in ('bytes', 'sha256'))
    hf_manifest = read(hf / 'ARTIFACT-MANIFEST.json')
    for expected in hf_manifest['files']:
        found = ref(hf / expected['path'])
        assert all(found[key] == expected[key] for key in ('bytes', 'sha256'))
    for pod in [read(db / 'POD-TEMPLATE.json')['pod_template'],
                read(hf / 'host-pod.template.json'), read(hf / 'frontend-pod.template.json')]:
        pod_check(pod)
    assert read(hf / 'deny-all.template.json')['spec'] == {
        'podSelector': {}, 'policyTypes': ['Ingress', 'Egress'], 'egress': [], 'ingress': []}
    record = {
        'schema': 'portal-ppt-native-runtime-preparation.parent-review.v1',
        'status': 'LOCAL_ADAPTERS_REVIEWED_FINAL_EXECUTION_BINDINGS_PENDING',
        'reviewed_preparations': [ref(db / 'PREPARATION.json'), ref(hf / 'PREPARATION.json')],
        'fixed_files_recomputed': True,
        'scheduler_and_volume_boundaries_checked': True,
        'native_business_checks_repeated': False,
        'database': {'unchanged_business_probe': ref(db / 'bootstrap_probe.py'),
                     'thin_supervisor': ref(db / 'probe_supervisor.py'),
                     'local_micro_fixture_receipt': ref(db / 'TOOL-QUALIFICATION.json'),
                     'scope': 'Five sequential application bootstrap and schema/data compatibility checks in a disposable minimal DB'},
        'host_frontend': {'receipt': ref(hf / 'LOCAL-VERIFICATION.json'),
                          'scope': 'Host source/dependencies/default browser/Codex-version checks and unchanged 14-group Frontend synthetic HTTP harness'},
        'still_required': [
            'Exact six candidates present and unpacked in datax-02 CRI cache',
            'Safe Host path and PostgreSQL launcher/user binding from fixed image metadata',
            'One reviewed execution controller with unique namespace, owned-object UID cleanup, admission/spec/image checks and bounded failures',
            'Actual owned NetworkPolicy enforcement evidence before application execution',
            'Native test receipts and production-specific backup/integration/release acceptance',
        ],
        'pid_swap_limits': 'Inherited visible cgroup observation only; not claimed as configured Pod API limits',
        'node_capacity_pass': False, 'native_execution_performed': False,
        'ready_for_native_execution': False, 'ready_for_rollout': False,
        'production_service_mutation': False, 'production_database_mutation': False,
    }
    dest = ROOT / 'NATIVE-RUNTIME-PREPARATION-REVIEW.json'
    with dest.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps(ref(dest), ensure_ascii=False))


if __name__ == '__main__':
    main()
