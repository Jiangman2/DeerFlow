import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('prepare', ROOT / 'prepare-wave1.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def deployment():
    return {'metadata': {'name': 'frontend', 'uid': 'd1', 'generation': 3},
            'spec': {'replicas': 1, 'selector': {'matchLabels': {'app': 'frontend'}},
                     'template': {'metadata': {'labels': {'app': 'frontend'}}, 'spec': {
                         'containers': [{'name': 'frontend', 'image': m.IMAGE, 'imagePullPolicy': 'Never',
                                         'env': [{'name': 'PASSWORD', 'value': 'do-not-emit'}],
                                         'resources': {'limits': {'cpu': '1', 'memory': '1Gi'}}}],
                         'volumes': [{'name': 'demo', 'projected': {'sources': [
                             {'configMap': {'name': 'demo-config'}}]}}]}}},
            'status': {'observedGeneration': 3, 'replicas': 1, 'updatedReplicas': 1,
                       'readyReplicas': 1, 'availableReplicas': 1}}


def node():
    return {'metadata': {'name': m.TARGET, 'uid': 'n1', 'labels': {
        'kubernetes.io/arch': 'arm64', 'kubernetes.io/os': 'linux',
        'kubernetes.io/hostname': m.TARGET}}, 'spec': {'unschedulable': True,
        'taints': [{'key': 'node.kubernetes.io/unschedulable', 'effect': 'NoSchedule'}]},
        'status': {'conditions': [{'type': k, 'status': v} for k, v in {
            'Ready': 'True', 'MemoryPressure': 'False', 'DiskPressure': 'False',
            'PIDPressure': 'False', 'NetworkUnavailable': 'False'}.items()],
            'allocatable': {'cpu': '32', 'memory': '62Gi', 'pods': '110'},
            'addresses': [{'type': 'InternalIP', 'address': '192.168.111.171'}]}}


class SafetyTests(unittest.TestCase):
    def test_baseline_redacts_env(self):
        got = m.frontend_projection(deployment())
        self.assertNotIn('do-not-emit', json.dumps(got))
        self.assertEqual(got['image'], m.IMAGE)
        self.assertEqual(got['configmaps'], ['demo-config'])

    def test_other_frontend_image_needs_review(self):
        d = deployment(); d['spec']['template']['spec']['containers'][0]['image'] += '-new'
        with self.assertRaises(m.Blocked): m.frontend_projection(d)

    def test_old_generation_blocked(self):
        d = deployment(); d['status']['observedGeneration'] = 2
        with self.assertRaises(m.Blocked): m.frontend_projection(d)

    def test_replica_change_blocked(self):
        d = deployment(); d['spec']['replicas'] = 2
        with self.assertRaises(m.Blocked): m.frontend_projection(d)

    def test_terminating_extra_replica_blocked(self):
        d = deployment(); d['status']['replicas'] = 2
        with self.assertRaises(m.Blocked): m.frontend_projection(d)

    def test_init_container_blocked(self):
        d = deployment(); d['spec']['template']['spec']['initContainers'] = [{'name': 'writer'}]
        with self.assertRaises(m.Blocked): m.frontend_projection(d)

    def test_sidecar_blocked(self):
        d = deployment(); d['spec']['template']['spec']['containers'].append({'name': 'sidecar'})
        with self.assertRaises(m.Blocked): m.frontend_projection(d)

    def test_pvc_hostpath_blocked(self):
        for k in ('persistentVolumeClaim', 'hostPath'):
            d = deployment(); d['spec']['template']['spec']['volumes'] = [{'name': 'x', k: {}}]
            with self.assertRaises(m.Blocked): m.frontend_projection(d)

    def test_privileged_or_host_features_blocked(self):
        for key in ('hostNetwork', 'hostPID', 'hostIPC'):
            d = deployment(); d['spec']['template']['spec'][key] = True
            with self.assertRaises(m.Blocked): m.frontend_projection(d)

    def test_cordoned_node_is_observation_not_uncordon(self):
        n = node(); before = copy.deepcopy(n)
        self.assertTrue(m.node_projection(n)['cordoned'])
        self.assertEqual(n, before)

    def test_wrong_arch_and_pressure_blocked(self):
        n = node(); n['metadata']['labels']['kubernetes.io/arch'] = 'amd64'
        with self.assertRaises(m.Blocked): m.node_projection(n)
        n = node(); n['status']['conditions'][2]['status'] = 'True'
        with self.assertRaises(m.Blocked): m.node_projection(n)

    def test_unknown_taints_blocked(self):
        n = node(); n['spec']['taints'].append({'key': 'dedicated', 'value': 'database', 'effect': 'NoSchedule'})
        with self.assertRaises(m.Blocked): m.node_projection(n)

    def test_digest_handles_runtime_and_repo_forms(self):
        h = 'a' * 64
        for v in ('sha256:' + h, 'containerd://sha256:' + h, 'registry/path@sha256:' + h):
            self.assertEqual(m.digest(v), 'sha256:' + h)
        with self.assertRaises(m.Blocked): m.digest('no-digest')

    def test_image_identity_requires_matching_tag_and_digest(self):
        h = 'sha256:' + 'a' * 64
        info = {'status': {'id': h, 'repoTags': [m.IMAGE], 'repoDigests': []}}
        self.assertEqual(m.check_image(info, h), [h])
        with self.assertRaises(m.Blocked): m.check_image(info, 'sha256:' + 'b' * 64)
        info['status']['repoTags'] = ['another:tag']
        with self.assertRaises(m.Blocked): m.check_image(info, h)

    def test_top_level_root_not_plugin_root(self):
        self.assertEqual(m.parse_root('root = "/mnt/data/containerd"\n[plugins]\nroot = "/wrong"'), '/mnt/data/containerd')
        self.assertEqual(m.parse_root("root = '/mnt/data/containerd'\n[plugins]"), '/mnt/data/containerd')
        with self.assertRaises(m.Blocked): m.parse_root('[plugins]\nroot = "/wrong"')

    def test_disk_thresholds_fail_closed(self):
        m.disk_gate({'free_gib': 100, 'used_percent': 60}, 40, 75)
        for d in ({'free_gib': 39, 'used_percent': 60}, {'free_gib': 100, 'used_percent': 76}):
            with self.assertRaises(m.Blocked): m.disk_gate(d, 40, 75)

    def test_plan_expired_or_future_blocked(self):
        p = {'version': m.VERSION, 'phase': 'cluster', 'target': m.TARGET,
             'collected_epoch': 1000, 'frontend': {'image': m.IMAGE, 'image_id': 'sha256:' + 'a'*64}}
        with patch.object(m.time, 'time', return_value=1005): m.validate_plan(p)
        for now in (500, 1000 + 21601):
            with patch.object(m.time, 'time', return_value=now):
                with self.assertRaises(m.Blocked): m.validate_plan(p)

    def test_report_write_never_overwrites_or_follows_symlink(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'report.json'; m.write_new(p, {'a': 1})
            with self.assertRaises(FileExistsError): m.write_new(p, {'a': 2})
            self.assertEqual(json.loads(p.read_text()), {'a': 1})

    def test_no_kubernetes_mutation_verbs(self):
        for verb in ('apply', 'patch', 'delete', 'scale', 'uncordon', 'cordon', 'create', 'replace', 'rollout'):
            with self.assertRaises(m.Blocked): m.kube(verb, 'node', m.TARGET)

    def test_cmd_failure_does_not_echo_credentials(self):
        import subprocess
        with patch.object(m.subprocess, 'Popen') as popen:
            popen.return_value.communicate.return_value = ('secret-value', 'password=secret')
            popen.return_value.returncode = 3
            with self.assertRaises(m.Blocked) as e: m.run(['fake', 'arg'])
            self.assertNotIn('secret', str(e.exception))

    def test_exec_only_exact_readonly_probe(self):
        with patch.object(m, 'run', return_value='{}') as r:
            m.kube('exec', 'frontend-pod', '-c', 'frontend', '--', 'node', '-e', m.PROBE)
            self.assertEqual(r.call_count, 1)
        with self.assertRaises(m.Blocked): m.kube('exec', 'frontend-pod', '--', 'sh', '-c', 'anything')

    def test_secret_get_is_forbidden(self):
        with self.assertRaises(m.Blocked): m.kube('get', 'secrets', '-o', 'json')

    def test_supply_ack_before_runtime_mutation(self):
        with patch.object(m, 'validate_plan'), patch.object(m, 'run') as r:
            with self.assertRaises(m.Blocked): m.supply('export', {}, None)
            r.assert_not_called()

    def test_supply_wrong_node_before_runtime_mutation(self):
        with patch.object(m, 'validate_plan'), patch.object(m.socket, 'gethostname', return_value='unknown'), patch.object(m, 'run') as r:
            with self.assertRaises(m.Blocked):
                m.supply('export', {'frontend': {'source_node': 'datax-02'}}, 'FRONTEND_IMAGE_ONLY')
            r.assert_not_called()

    def test_source_digest_failure_before_export(self):
        plan = {'frontend': {'source_node': 'datax-02', 'image_id': 'sha256:' + 'a'*64}}
        with patch.object(m, 'validate_plan'), patch.object(m.socket, 'gethostname', return_value='datax-02'), \
             patch.object(m, 'active_operations', return_value=[]), patch.object(m, 'inspect_image', return_value={}), \
             patch.object(m, 'run') as r:
            with self.assertRaises(m.Blocked): m.supply('export', plan, 'FRONTEND_IMAGE_ONLY')
            r.assert_not_called()

    def test_template_hash_changes_with_hidden_env_change(self):
        a = deployment(); b = copy.deepcopy(a)
        b['spec']['template']['spec']['containers'][0]['env'][0]['value'] = 'other-secret'
        self.assertNotEqual(m.frontend_projection(a)['spec_sha256'], m.frontend_projection(b)['spec_sha256'])

    def test_report_has_lf_no_bom(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'r.json'; m.write_new(p, {'a': 1})
            self.assertFalse(p.read_bytes().startswith(b'\xef\xbb\xbf'))
            self.assertNotIn(b'\r', p.read_bytes())

    def test_python36_grammar(self):
        import ast
        ast.parse((ROOT / 'prepare-wave1.py').read_text(encoding='utf-8'), feature_version=(3, 6))


class ClusterFlowTests(unittest.TestCase):
    def setUp(self):
        self.front = deployment()
        self.n = node()
        self.pod = {'metadata': {'name': 'frontend-p1', 'uid': 'p1', 'ownerReferences': [
            {'kind': 'ReplicaSet', 'name': 'rs1', 'uid': 'r1', 'controller': True}]},
            'spec': {'nodeName': 'datax-02', 'containers': [{'name': 'frontend', 'image': m.IMAGE}]},
            'status': {'conditions': [{'type': 'Ready', 'status': 'True'}],
                       'containerStatuses': [{'name': 'frontend', 'ready': True, 'imageID': 'sha256:' + 'a'*64}]}}
        self.rs = {'metadata': {'uid': 'r1', 'ownerReferences': [{'controller': True, 'uid': 'd1'}]}}
        self.hpas = []
        self.endpoints = [{'targetRef': {'uid': 'p1'}, 'conditions': {'ready': True}}]
        self.front_reads = 0
        self.change = False

    def getter(self, kind, *args):
        if kind == 'deployment':
            if args == ('frontend',):
                self.front_reads += 1
                obj = copy.deepcopy(self.front)
                if self.change and self.front_reads > 1: obj['spec']['replicas'] = 2
                return obj
            return {'metadata': {'uid': args[0]}, 'spec': {'replicas': 1, 'template': {'spec': {
                'containers': [{'name': args[0], 'image': 'private:stable', 'env': [{'value': 'secret'}]}]}}},
                'status': {'readyReplicas': 1}}
        if kind == 'pods': return {'items': [self.pod] if '-A' not in args else []}
        if kind == 'replicaset': return self.rs
        if kind == 'node': return self.n
        if kind == 'service': return {'spec': {'selector': {'app': 'frontend'}, 'type': 'ClusterIP'}}
        if kind == 'endpointslices': return {'items': [{'endpoints': self.endpoints}]}
        if kind == 'hpa': return {'items': self.hpas}
        if kind == 'namespace': return {'metadata': {'uid': 'cluster1'}}
        raise AssertionError((kind, args))

    def call(self):
        with patch.object(m.socket, 'gethostname', return_value='datax-01'), \
             patch.object(m.Path, 'is_file', return_value=True), patch.object(m, 'get', side_effect=self.getter), \
             patch.object(m, 'kube', return_value='{"build_id_sha256":"abc"}'), patch.dict(m.os.environ, {}):
            return m.cluster()

    def test_full_cluster_projection_no_sensitive_values(self):
        r = self.call()
        self.assertEqual(r['frontend']['source_node'], 'datax-02')
        self.assertEqual(r['target_node']['cordoned'], True)
        self.assertNotIn('do-not-emit', json.dumps(r))
        self.assertNotIn('"value": "secret"', json.dumps(r))

    def test_hpa_blocks(self):
        self.hpas = [{'spec': {'scaleTargetRef': {'name': 'frontend'}}}]
        with self.assertRaises(m.Blocked): self.call()

    def test_foreign_pod_owner_blocks(self):
        self.rs['metadata']['ownerReferences'][0]['uid'] = 'another-deployment'
        with self.assertRaises(m.Blocked): self.call()

    def test_wrong_endpoint_blocks(self):
        self.endpoints[0]['targetRef']['uid'] = 'another-pod'
        with self.assertRaises(m.Blocked): self.call()

    def test_terminating_endpoint_blocks(self):
        self.endpoints[0]['conditions']['terminating'] = True
        with self.assertRaises(m.Blocked): self.call()

    def test_changed_during_read_blocks(self):
        self.change = True
        with self.assertRaises(m.Blocked): self.call()


def make_archive(path, architecture='arm64', extra=False, corrupt=False):
    import io
    import tarfile
    config = json.dumps({'architecture': architecture, 'os': 'linux'}).encode()
    cid = 'sha256:' + m.hashlib.sha256(config).hexdigest()
    desc = {'digest': cid, 'size': len(config)}
    manifest = json.dumps({'config': desc, 'layers': []}).encode()
    mid = 'sha256:' + m.hashlib.sha256(manifest).hexdigest()
    index = {'manifests': [{'digest': mid, 'size': len(manifest), 'annotations': {'io.containerd.image.name': m.IMAGE}}]}
    if extra: index['manifests'].append({'digest': 'sha256:' + 'f'*64, 'size': 1})
    with tarfile.open(str(path), 'w') as t:
        for name, data in [('index.json', json.dumps(index).encode()), ('blobs/sha256/' + cid.split(':')[1], config),
                           ('blobs/sha256/' + mid.split(':')[1], b'bad' if corrupt else manifest)]:
            ti = tarfile.TarInfo(name); ti.size = len(data); t.addfile(ti, io.BytesIO(data))
    return mid


class ArchiveTests(unittest.TestCase):
    def test_archive_exact_arm64_identity(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'a.tar'; mid = make_archive(p)
            self.assertEqual(m.verify_archive(p, mid)['platform'], 'linux/arm64')

    def test_archive_wrong_platform_other_tag_and_corruption(self):
        for opts in ({'architecture': 'amd64'}, {'extra': True}, {'corrupt': True}):
            with tempfile.TemporaryDirectory() as td:
                p = Path(td) / 'a.tar'; mid = make_archive(p, **opts)
                with self.assertRaises(m.Blocked): m.verify_archive(p, mid)

    def test_archive_wrong_live_digest(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'a.tar'; make_archive(p)
            with self.assertRaises(m.Blocked): m.verify_archive(p, 'sha256:' + 'a'*64)

    def test_preload_missing_only_imports_checked_archive(self):
        with tempfile.TemporaryDirectory() as td:
            stage = Path(td); mid = make_archive(stage / m.ARCHIVE)
            plan = {'frontend': {'image_id': mid}}
            m.write_new(stage / 'image-receipt.json', {'version': m.VERSION, 'image': m.IMAGE, 'image_id': mid,
                'archive': m.ARCHIVE, 'sha256': m.hash_file(stage / m.ARCHIVE),
                'size': (stage / m.ARCHIVE).stat().st_size, 'plan_sha256': m.sha(plan)})
            with patch.object(m, 'validate_plan'), patch.object(m.socket, 'gethostname', return_value=m.TARGET), \
                 patch.object(m, 'active_operations', return_value=[]), patch.object(m, 'node_check', return_value={'containerd_root': '/mnt/data/containerd'}), \
                 patch.object(m, 'safe_stage', return_value=stage), patch.object(m, 'run', return_value='') as run, \
                 patch.object(m, 'inspect_image', return_value={'status': {'id': mid, 'repoTags': [m.IMAGE]}}):
                got = m.supply('preload', plan, 'FRONTEND_IMAGE_ONLY')
                self.assertEqual(got['unpack_and_pod_start'], 'NOT_TESTED')
                commands = [c.args[0] for c in run.call_args_list]
                imports = [c for c in commands if 'import' in c]
                self.assertEqual(len(imports), 1)
                self.assertIn('--no-unpack', imports[0])
                self.assertNotIn('kubectl', [c[0] for c in commands])

    def test_preload_bad_receipt_never_calls_import(self):
        with tempfile.TemporaryDirectory() as td:
            stage = Path(td); mid = make_archive(stage / m.ARCHIVE)
            m.write_new(stage / 'image-receipt.json', {'version': 'wrong'})
            with patch.object(m, 'validate_plan'), patch.object(m.socket, 'gethostname', return_value=m.TARGET), \
                 patch.object(m, 'active_operations', return_value=[]), patch.object(m, 'node_check', return_value={}), \
                 patch.object(m, 'safe_stage', return_value=stage), patch.object(m, 'run') as run:
                with self.assertRaises(m.Blocked):
                    m.supply('preload', {'frontend': {'image_id': mid}}, 'FRONTEND_IMAGE_ONLY')
                run.assert_not_called()

    def test_export_emits_receipt_no_image_import(self):
        with tempfile.TemporaryDirectory() as td:
            stage = Path(td); fixture = stage / 'fixture.tar'; mid = make_archive(fixture)
            plan = {'frontend': {'source_node': 'datax-02', 'image_id': mid}}
            def command(argv, timeout=60):
                self.assertIn('export', argv)
                (stage / (m.ARCHIVE + '.partial')).write_bytes(fixture.read_bytes())
                return ''
            with patch.object(m, 'validate_plan'), patch.object(m.socket, 'gethostname', return_value='datax-02'), \
                 patch.object(m, 'active_operations', return_value=[]), patch.object(m, 'safe_stage', return_value=stage), \
                 patch.object(m, 'disk', return_value={'free_gib': 100, 'used_percent': 60}), \
                 patch.object(m, 'inspect_image', return_value={'status': {'id': mid, 'repoTags': [m.IMAGE]}}), \
                 patch.object(m, 'run', side_effect=command):
                self.assertEqual(m.supply('export', plan, 'FRONTEND_IMAGE_ONLY')['image_export'], 'PASS')
            self.assertTrue((stage / m.ARCHIVE).is_file())
            self.assertFalse((stage / (m.ARCHIVE + '.partial')).exists())
            self.assertEqual(json.loads((stage / 'image-receipt.json').read_text())['image_id'], mid)


if __name__ == '__main__':
    unittest.main()
