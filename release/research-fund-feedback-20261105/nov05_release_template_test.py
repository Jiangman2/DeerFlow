"""Synthetic release-template checks; no cluster, real receipt writes, or user files."""
import ast
import copy
import gzip
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


def module(name):
    spec = importlib.util.spec_from_file_location(name, HERE/(name+'.py'))
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


updater = module('nov05_updater_template')
builder = module('nov05_release_builder')
remote = module('nov05_remote_template')


class TemplateTests(unittest.TestCase):
    def setUp(self):
        updater.AUTH.clear()
        updater.BUNDLE = {'pvc': {'name': 'synthetic-skills'}}
        self.operation = 'a'*32
        self.uid = '11111111-1111-4111-8111-111111111111'
        self.seed = {'node': 'synthetic-node', 'image': 'example.invalid/synthetic:v1', 'image_id': 'sha256:'+'b'*64}
        self.name = 'fund-skill-maint-' + self.operation[:12]
        self.pod = updater.maintenance_pod(self.name, self.operation, self.seed)
        updater.AUTH.update(name=self.name, manifest=copy.deepcopy(self.pod), uid=self.uid)
        self.pod['metadata']['uid'] = self.uid

    def test_unbound_template_stops_before_production(self):
        updater.BUNDLE = None
        with self.assertRaisesRegex(updater.Refused, 'UNBOUND_TEMPLATE'):
            updater.validate_bundle()

    def test_maintenance_has_only_shared_skill_no_business_credentials(self):
        pod = self.pod
        self.assertFalse(pod['spec']['automountServiceAccountToken'])
        self.assertEqual(len(pod['spec']['volumes']), 1)
        self.assertEqual(pod['spec']['containers'][0]['imagePullPolicy'], 'Never')
        updater.verify_maintenance(pod, self.uid, self.operation)

    def test_kubernetes_defaults_and_equivalent_quantities(self):
        pod = self.pod; spec = pod['spec']; c = spec['containers'][0]
        spec.update(dnsPolicy='ClusterFirst', serviceAccountName='default', schedulerName='default-scheduler', securityContext={})
        c.update(terminationMessagePath='/dev/termination-log', terminationMessagePolicy='File')
        c['volumeMounts'][0]['readOnly'] = False
        spec['volumes'][0]['persistentVolumeClaim']['readOnly'] = False
        c['securityContext'].update(privileged=False, runAsNonRoot=False, readOnlyRootFilesystem=False)
        c['resources'] = {'requests': {'cpu': '0.1', 'memory': '134217728'}, 'limits': {'cpu': '1000m', 'memory': '536870912'}}
        updater.verify_maintenance(pod, self.uid, self.operation)

    def test_extra_action_and_permission_changes_rejected(self):
        variants = []
        for key, value in [('lifecycle', {'postStart': {'exec': {'command': ['unapproved']}}}), ('workingDir', '/mnt/skills'), ('env', [{'name': 'TOKEN', 'value': 'synthetic'}]), ('volumeDevices', [{'name': 'skills', 'devicePath': '/dev/unapproved'}])]:
            p = copy.deepcopy(self.pod); p['spec']['containers'][0][key] = value; variants.append(p)
        p = copy.deepcopy(self.pod); p['spec']['securityContext'] = {'fsGroup': 123}; variants.append(p)
        p = copy.deepcopy(self.pod); p['spec']['containers'][0]['resources']['requests']['cpu'] = '10'; variants.append(p)
        for pod in variants:
            with self.assertRaises(updater.Refused): updater.verify_maintenance(pod, self.uid, self.operation)

    def test_create_only_exact_owned_manifest_once(self):
        args = ['create', '-f', '-', '-o', 'json']
        updater.authorize_command(args, updater.canonical(updater.AUTH['manifest']))
        with self.assertRaisesRegex(updater.Refused, 'CREATE_ALREADY'):
            updater.authorize_command(args, updater.canonical(updater.AUTH['manifest']))

    def test_delete_atomic_uid_only_and_only_once(self):
        args = ['delete', '--raw=/api/v1/namespaces/deer-flow/pods/'+self.name, '-f', '-']
        payload = updater.delete_options(self.uid)
        self.assertEqual(json.loads(payload)['preconditions'], {'uid': self.uid})
        with self.assertRaises(updater.Refused): updater.authorize_command(['delete', 'pod', self.name], None)
        with self.assertRaises(updater.Refused): updater.authorize_command(args, b'{}')
        updater.authorize_command(args, payload)
        with self.assertRaises(updater.Refused): updater.authorize_command(args, payload)

    def test_arbitrary_mutations_and_exec_refused(self):
        for args in (['patch', 'deployment', 'gateway'], ['delete', '--raw=/api/v1/namespaces/deer-flow/pods/other', '-f', '-'], ['exec', 'gateway', '--', 'sh']):
            with self.assertRaises(updater.Refused): updater.authorize_command(args, None)

    def test_cleanup_uid_failure_preserves_skill_success(self):
        report = {'operation': self.operation, 'skill_updated': True, 'runtime_node_views_accepted': True}
        changed = copy.deepcopy(self.pod); changed['metadata']['uid'] = '22222222-2222-4222-8222-222222222222'
        with patch.object(updater, 'command', return_value=changed) as command, patch.object(updater, 'persist'):
            try: updater.cleanup_maintenance(report)
            except updater.Refused as exc: updater.classify_failure(report, exc)
        self.assertTrue(report['skill_updated']); self.assertTrue(report['runtime_node_views_accepted'])
        self.assertEqual(report['status'], 'SKILL_UPDATED_RUNTIME_ACCEPTED_CLEANUP_PENDING')
        self.assertEqual(command.call_count, 1)

    def test_cleanup_deletes_exact_uid_then_observes_absence(self):
        report = {'operation': self.operation}
        with patch.object(updater, 'command', side_effect=[self.pod, {'delete_acknowledged': True}, {}]) as command, patch.object(updater, 'persist'):
            updater.cleanup_maintenance(report)
        self.assertTrue(report['maintenance_pod_absent'])
        self.assertEqual(report['maintenance_pod_delete_request_count'], 1)
        self.assertEqual(command.call_args_list[1].args[0], ['delete', '--raw=/api/v1/namespaces/deer-flow/pods/'+self.name, '-f', '-'])
        self.assertEqual(command.call_args_list[1].args[1], updater.delete_options(self.uid))

    def test_already_absent_no_delete(self):
        report = {'operation': self.operation}
        with patch.object(updater, 'command', return_value={}) as command:
            updater.cleanup_maintenance(report)
        self.assertTrue(report['maintenance_pod_absent']); self.assertEqual(command.call_count, 1)

    def test_manifest_and_changed_candidate_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/'SKILL.md').write_bytes(b'synthetic')
            expected = builder.manifest(root)
            self.assertEqual(builder.archive_for(root, expected), builder.archive_for(root, expected))
            (root/'SKILL.md').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'CANDIDATE_CHANGED'): builder.archive_for(root, expected)
            expected['files'][0]['sha256'] = 'f'*64
            with self.assertRaisesRegex(ValueError, 'MANIFEST_INTEGRITY'): builder.validate_manifest(expected)

    def test_manifest_traversal_refused(self):
        rows = [{'path': '../escape', 'bytes': 1, 'sha256': 'a'*64}]
        value = {'files': rows, 'bytes': 1, 'file_count': 1, 'tree_sha256': builder.sha(builder.canonical(rows))}
        with self.assertRaisesRegex(ValueError, 'MANIFEST_PATH'): builder.validate_manifest(value)

    def test_archive_uncompressed_total_bound(self):
        out = io.BytesIO()
        with tarfile.open(fileobj=out, mode='w:gz') as tar:
            for n in range(5):
                data = b'a'*2097152; entry = tarfile.TarInfo('research-fund-settlement/'+str(n)); entry.size = len(data); tar.addfile(entry, io.BytesIO(data))
        with tempfile.TemporaryDirectory() as folder, patch.object(remote, 'ROOT', Path(folder)):
            with self.assertRaisesRegex(remote.Refused, 'ARCHIVE_TOTAL_BOUND'): remote.unpack(out.getvalue(), {}, Path(folder)/'candidate')

    def test_archive_symlink_and_traversal_refused(self):
        for name, kind in [('research-fund-settlement/../escape', tarfile.REGTYPE), ('research-fund-settlement/link', tarfile.SYMTYPE)]:
            out = io.BytesIO()
            with tarfile.open(fileobj=out, mode='w:gz') as tar:
                entry = tarfile.TarInfo(name); entry.type = kind; entry.linkname = '/tmp/unapproved'; tar.addfile(entry)
            with tempfile.TemporaryDirectory() as folder, patch.object(remote, 'ROOT', Path(folder)):
                with self.assertRaises(remote.Refused): remote.unpack(out.getvalue(), {}, Path(folder)/'candidate')

    def test_new_baseline_cannot_reuse_406_or_rewrite_review_required(self):
        baseline = {'schema': 'portal-research-fund-skill-preflight.v3', 'tool_revision': '20261105-r2', 'status': 'READONLY_FINANCE_SKILL_BASELINE_REVIEW_REQUIRED'}
        with self.assertRaisesRegex(ValueError, 'SEPARATE_REVIEW'): builder.validate_baseline(baseline, {})
        baseline.update(status='READONLY_FINANCE_SKILL_BASELINE_ACCEPTED_NO_CHANGE', exitcode=0, stable_readback=True, runtime_loading_review_required=False, review_reasons=[], deployments={'gateway': {'generation': 406}})
        with self.assertRaisesRegex(ValueError, 'OLD_GATEWAY'): builder.validate_baseline(baseline, {})

    def test_remote_refuses_changed_old_and_candidate_hashes(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(remote, 'ROOT', Path(folder)):
            remote.STARTED = time.monotonic(); root = Path(folder)/'public'/remote.NAME; root.mkdir(parents=True); (root/'SKILL.md').write_bytes(b'old')
            before = builder.manifest(root); (root/'SKILL.md').write_bytes(b'changed')
            with self.assertRaisesRegex(remote.Refused, 'MANIFEST_CHANGED'): remote.verify(root, before)

    def test_two_rename_failure_restores_original_name_and_keeps_backup(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(remote, 'ROOT', Path(folder)):
            remote.STARTED = time.monotonic(); root = Path(folder); live = root/'public'/remote.NAME; live.mkdir(parents=True); (live/'SKILL.md').write_bytes(b'old')
            tx = remote.transaction(self.operation); tx.mkdir(); backup = tx/'backup'; backup.mkdir(); (backup/'SKILL.md').write_bytes(b'old')
            candidate = tx/'candidate'; candidate.mkdir(); (candidate/'SKILL.md').write_bytes(b'new')
            before = builder.manifest(live); after = builder.manifest(candidate)
            remote.save_journal(tx, {'status': 'STAGED_SYNTHETIC_ACCEPTED', 'operation': self.operation, 'before_tree_sha256': before['tree_sha256'], 'after_tree_sha256': after['tree_sha256'], 'exchange_supported': False})
            original = remote.os.rename
            def rename(a, b):
                if Path(a) == candidate: raise OSError('synthetic second rename failure')
                return original(a, b)
            with patch.object(remote.os, 'rename', side_effect=rename):
                with self.assertRaisesRegex(remote.Refused, 'LIVE_NAME_RESTORED'): remote.publish({'operation': self.operation, 'before': before, 'after': after})
            self.assertEqual((live/'SKILL.md').read_bytes(), b'old'); self.assertEqual((backup/'SKILL.md').read_bytes(), b'old'); self.assertEqual((candidate/'SKILL.md').read_bytes(), b'new')

    def test_host_and_remote_python37_syntax(self):
        for filename in ('nov05_updater_template.py', 'nov05_remote_template.py'):
            ast.parse((HERE/filename).read_text(encoding='utf-8'), feature_version=(3, 7))


if __name__ == '__main__':
    unittest.main()
