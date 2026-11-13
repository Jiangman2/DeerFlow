"""Offline boundary tests; no cluster, supplier or application imports."""
import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


PATH = Path(__file__).resolve().parents[1] / 'inspect-energy-daily-result-link-source-20261111-r2.py'
SPEC = importlib.util.spec_from_file_location('conversation_link_inspector', PATH)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)
POD_UID = '11111111-1111-4111-8111-111111111111'
RS_UID = '22222222-2222-4222-8222-222222222222'


@pytest.fixture
def objects(monkeypatch):
    template = {'metadata': {'labels': {'app': 'gateway'}}, 'spec': {'containers': [{'name': 'gateway', 'image': MOD.IMAGE}], 'volumes': []}}
    dep = {'metadata': {'name': 'gateway', 'uid': MOD.UID, 'generation': MOD.GENERATION},
           'spec': {'replicas': 70, 'selector': {'matchLabels': {'app': 'gateway'}}, 'template': template},
           'status': dict(observedGeneration=413, replicas=70, updatedReplicas=70, readyReplicas=70, availableReplicas=70)}
    rs = {'metadata': {'name': 'gateway-abcd', 'uid': RS_UID, 'generation': 1,
                       'ownerReferences': [{'kind': 'Deployment', 'name': 'gateway', 'uid': MOD.UID, 'controller': True}]},
          'spec': {'replicas': 70, 'template': copy.deepcopy(template)}}
    rs['spec']['template']['metadata']['labels']['pod-template-hash'] = 'abcd'
    pod = {'metadata': {'name': 'gateway-abcd-0001', 'uid': POD_UID,
                        'ownerReferences': [{'kind': 'ReplicaSet', 'name': 'gateway-abcd', 'uid': RS_UID, 'controller': True}]},
           'spec': dict(copy.deepcopy(template['spec']), nodeName='ecs-zdz-pro-0018'),
           'status': {'phase': 'Running', 'conditions': [{'type': 'Ready', 'status': 'True'}],
                      'containerStatuses': [{'name': 'gateway', 'ready': True, 'imageID': MOD.IMAGE, 'containerID': 'containerd://' + '1' * 64,
                                             'restartCount': 0, 'state': {'running': {'startedAt': '2026-11-11T12:00:00Z'}}}]}}
    monkeypatch.setattr(MOD, 'SPEC_SHA', MOD.sha(MOD.canonical(dep['spec'])))
    return dep, rs, pod


def child_namespace():
    ns = {'__name__': 'readonly_test_child'}
    exec(compile(MOD.CHILD, '<fixed-inspection-child>', 'exec'), ns)
    return ns


def child_result():
    return dict(status='FIXED_SOURCE_HASHES_OBSERVED', stable_readback=True, code_root_present=True,
                files={name: {'present': True, 'bytes': 10, 'sha256': refs['candidate']} for name, refs in MOD.REFERENCES.items()},
                isolated_python_sys_path_count=4, isolated_python_sys_path_sha256='f' * 64,
                application_imported=False, source_text_exported=False, environment_read=False, files_modified=False, provider_requests=0)


class FakeInspector(MOD.Inspector):
    def __init__(self, objects, mutate=None):
        super().__init__()
        self.dep, self.rs, self.pod = copy.deepcopy(objects)
        self.calls = []
        self.mutate = mutate

    def command(self, args, child=False):
        self.commands += 1
        self.calls.append((args, child))
        if child:
            if self.mutate:
                self.mutate(self)
            return child_result()
        if args == MOD.LIST_ARGS:
            return 'gateway-old-terminal ' + '3' * 8 + '-3333-4333-8333-' + '3' * 12 + ' Failed False <none>\n' + self.pod['metadata']['name'] + ' ' + POD_UID + ' Running True <none>\n'
        if args[1] == 'deployment':
            return copy.deepcopy(self.dep)
        if args[1] == 'replicaset':
            return copy.deepcopy(self.rs)
        if args[1] == 'pod':
            return copy.deepcopy(self.pod)
        raise AssertionError(args)


def test_complete_readonly_flow_selects_owned_ready_pod(objects):
    inspector = FakeInspector(objects)
    result = inspector.run()
    assert result['status'] == 'GATEWAY_LINK_SOURCE_OBSERVED_NO_CHANGE'
    assert inspector.commands == 8
    exec_args = [args for args, child in inspector.calls if child][0]
    assert exec_args[:10] == ['exec', 'gateway-abcd-0001', '-c', 'gateway', '--', 'python3', '-I', '-S', '-B', '-c']
    assert all(row['matches_candidate'] for row in result['runtime']['files'].values())


@pytest.mark.parametrize('change', [
    lambda d: d['metadata'].update(generation=414),
    lambda d: d['spec'].update(replicas=69),
    lambda d: d['status'].update(readyReplicas=69),
    lambda d: d['status'].update(terminatingReplicas=1),
])
def test_changed_baseline_or_readiness_never_executes(objects, change):
    inspector = FakeInspector(objects)
    change(inspector.dep)
    with pytest.raises(MOD.Refused):
        inspector.run()
    assert not any(child for _, child in inspector.calls)


@pytest.mark.parametrize('change', [
    lambda i: i.pod['status']['containerStatuses'][0].update(restartCount=1),
    lambda i: i.pod['status']['containerStatuses'][0].update(containerID='containerd://' + '9' * 64),
    lambda i: i.pod['metadata'].update(uid='44444444-4444-4444-8444-444444444444'),
    lambda i: i.rs['metadata'].update(generation=2),
])
def test_mid_read_runtime_or_identity_change_refused(objects, change):
    with pytest.raises(MOD.Refused, match='GATEWAY_CHANGED_DURING_READ'):
        FakeInspector(objects, change).run()


def test_pod_list_ignores_failed_completed_and_deleting():
    uid = POD_UID
    raw = '\n'.join(['a ' + uid + ' Failed False <none>', 'b ' + uid + ' Succeeded False <none>',
                     'c ' + uid + ' Running True 2026-11-11T12:00:00Z', 'd ' + uid + ' Running True <none>'])
    assert MOD.parse_candidates(raw) == [('d', uid)]


@pytest.mark.parametrize('raw', ['../../escape ' + POD_UID + ' Running True <none>',
                                'a ' + POD_UID + ' Running True <none>\na ' + POD_UID + ' Running True <none>',
                                'a ' + POD_UID + ' Failed False <none>', 'not structured', 'x' * 131073], ids=['escape', 'duplicate', 'terminal', 'shape', 'oversize'])
def test_malformed_or_absent_ready_pod_list_refused(raw):
    with pytest.raises(MOD.Refused):
        MOD.parse_candidates(raw)


@pytest.mark.parametrize('mutation', [
    lambda r, p: p['metadata']['ownerReferences'].append(copy.deepcopy(p['metadata']['ownerReferences'][0])),
    lambda r, p: r['metadata']['ownerReferences'][0].update(uid='33333333-3333-4333-8333-333333333333'),
    lambda r, p: r['spec']['template']['spec']['containers'][0].update(image='other-image'),
    lambda r, p: p['status'].update(phase='Failed'),
])
def test_owner_chain_and_current_template_are_required(objects, mutation):
    dep, rs, pod = objects
    mutation(rs, pod)
    with pytest.raises(MOD.Refused):
        MOD.pod_guard(pod, rs, dep)


def configmap_mount(pod, *, subpath='manager.py', mount=None):
    pod['spec']['volumes'] = [{'name': 'source-overlay', 'configMap': {'name': 'gateway-source-overlay', 'items': [{'key': 'manager', 'path': 'manager.py'}]}}]
    value = {'name': 'source-overlay', 'mountPath': mount or MOD.ROOT + '/' + MOD.FILES[0], 'readOnly': True}
    if subpath:
        value['subPath'] = subpath
    pod['spec']['containers'][0]['volumeMounts'] = [value]


def test_only_relevant_configmap_mapping_is_exported(objects):
    pod = objects[2]
    configmap_mount(pod)
    pod['spec']['volumes'].append({'name': 'credentials', 'secret': {'secretName': 'do-not-read'}})
    pod['spec']['containers'][0]['volumeMounts'].append({'name': 'credentials', 'mountPath': '/run/credentials', 'readOnly': True})
    mounts, projections = MOD.mount_projection(pod)
    row = mounts[MOD.FILES[0]]
    assert row['configmap_name'] == 'gateway-source-overlay' and row['mapped_keys'] == ['manager']
    assert row['configmap_api_values_read'] is False and projections[MOD.FILES[0]] is None
    assert 'do-not-read' not in json.dumps(mounts)


@pytest.mark.parametrize('source', [{'secret': {'secretName': 'private'}}, {'persistentVolumeClaim': {'claimName': 'business-data'}},
                                   {'projected': {'sources': [{'secret': {'name': 'private'}}]}}, {'hostPath': {'path': '/host'}}, {'emptyDir': {}}])
def test_non_configmap_code_mount_refused_before_exec(objects, source):
    inspector = FakeInspector(objects)
    configmap_mount(inspector.pod)
    inspector.pod['spec']['volumes'][0] = dict(source, name='source-overlay')
    with pytest.raises(MOD.Refused, match='CODE_MOUNT_NOT_CONFIGMAP'):
        inspector.run()
    assert not any(child for _, child in inspector.calls)


def test_projected_directory_mapping_and_subpath_expr(objects):
    pod = objects[2]
    configmap_mount(pod, subpath=None, mount=MOD.ROOT + '/app/channels')
    mounts, projections = MOD.mount_projection(pod)
    assert projections[MOD.FILES[0]] == MOD.ROOT + '/app/channels'
    assert mounts[MOD.FILES[0]]['mapped_keys'] == ['manager']
    pod['spec']['containers'][0]['volumeMounts'][0]['subPathExpr'] = '$(PRIVATE)'
    with pytest.raises(MOD.Refused, match='CODE_MOUNT_SHAPE'):
        MOD.mount_projection(pod)


@pytest.mark.skipif(os.name != 'posix', reason='Linux fstat/stat ctime identity check; host is datax-01 only')
def test_child_hashes_fixed_files_without_importing_code(tmp_path):
    ns = child_namespace()
    root = tmp_path / 'backend'
    for name in MOD.FILES[:3]:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'raise RuntimeError("must never import application")\n')
    result = ns['inspect'](root, dict.fromkeys(MOD.FILES))
    assert result['files'][MOD.FILES[3]] == {'present': False}
    assert result['files'][MOD.FILES[0]]['sha256'] == MOD.sha((root / MOD.FILES[0]).read_bytes())
    assert result['application_imported'] is False and result['environment_read'] is False


def test_child_rejects_oversize_file(tmp_path):
    ns = child_namespace()
    path = tmp_path / MOD.FILES[0]
    path.parent.mkdir(parents=True)
    path.write_bytes(b'x' * 1048577)
    with pytest.raises(ValueError, match='FILE_BOUND'):
        ns['source'](tmp_path, MOD.FILES[0], None)


@pytest.mark.skipif(os.name != 'posix', reason='Linux projected ConfigMap symlinks')
def test_child_accepts_only_confined_configmap_projection(tmp_path):
    ns = child_namespace()
    root = tmp_path / 'backend'
    base = root / 'app/channels'
    real = base / '..2026'
    real.mkdir(parents=True)
    (real / 'manager.py').write_bytes(b'fixed application source\n')
    (base / '..data').symlink_to('..2026', target_is_directory=True)
    (base / 'manager.py').symlink_to('..data/manager.py')
    result = ns['source'](root, MOD.FILES[0], str(base))
    assert result['present'] is True
    with pytest.raises(ValueError, match='CODE_PATH_LINK'):
        ns['source'](root, MOD.FILES[0], None)
    (base / 'manager.py').unlink()
    (base / 'manager.py').symlink_to('/etc/passwd')
    with pytest.raises(ValueError, match='CODE_PROJECTION_ESCAPE'):
        ns['source'](root, MOD.FILES[0], str(base))


def test_child_unknown_fields_never_escape_projection():
    raw = child_result()
    raw['sensitive'] = 'never-export'
    raw['files'][MOD.FILES[0]]['source'] = 'never-export'
    assert 'never-export' not in json.dumps(MOD.runtime_projection(raw))
    raw['files'].pop(MOD.FILES[0])
    with pytest.raises(MOD.Refused, match='CHILD_FILE_SET'):
        MOD.runtime_projection(raw)


def test_missing_new_helper_matches_absent_git_parent():
    raw = child_result()
    raw['files'][MOD.FILES[3]] = {'present': False}
    row = MOD.runtime_projection(raw)['files'][MOD.FILES[3]]
    assert row['matches_git_parent'] is True and row['matches_candidate'] is False


@pytest.mark.parametrize('args', [['get', 'secret', 'anything', '-o', 'json'], ['get', 'configmap', 'anything', '-o', 'json'],
                                  ['patch', 'deployment', 'gateway'], ['exec', 'unknown', '--', 'env']])
def test_command_allowlist_blocks_other_reads_or_mutations(args, monkeypatch):
    monkeypatch.setattr(MOD.subprocess, 'Popen', lambda *a, **k: pytest.fail('must not start process'))
    with pytest.raises(MOD.Refused, match='READ_SCOPE'):
        MOD.Inspector().command(args)


def test_command_output_bound_kills_without_export(monkeypatch):
    class Oversize:
        returncode = 0

        def __init__(self, args, **kwargs):
            kwargs['stdout'].write(b'x' * 131073)

        def poll(self):
            return 0

    monkeypatch.setattr(MOD.subprocess, 'Popen', Oversize)
    with pytest.raises(MOD.Refused, match='OUTPUT_BOUND'):
        MOD.Inspector().command(MOD.LIST_ARGS)


def test_host_guard_blocks_all_commands(monkeypatch, capsys):
    if os.name == 'posix':
        monkeypatch.setattr(MOD.os, 'uname', lambda: SimpleNamespace(nodename='offline-test-host'))
    monkeypatch.setattr(MOD.subprocess, 'Popen', lambda *a, **k: pytest.fail('must not start process'))
    assert MOD.main(['--inspect-readonly']) == 1
    result = json.loads(capsys.readouterr().out)
    assert result['error_code'] == 'DATAX01_ROOT_REQUIRED' and result['queries'] == 0
    assert result['all_gateway_pods_verified'] is False and result['ready_for_deployment'] is False


def test_python37_syntax_and_isolated_entry_host_guard():
    import ast
    ast.parse(PATH.read_text(encoding='utf-8'), feature_version=(3, 7))
    ast.parse(MOD.CHILD, feature_version=(3, 7))
    completed = subprocess.run([sys.executable, '-I', '-B', str(PATH), '--inspect-readonly'], capture_output=True, timeout=10)
    assert completed.returncode == 1
    result = json.loads(completed.stdout)
    assert result['error_code'] == 'DATAX01_ROOT_REQUIRED' and result['queries'] == 0
