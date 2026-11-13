"""Scope, identity and file provenance checks for nonroot browser qualification."""
import ast
import base64
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

DIRECTORY = Path(__file__).resolve().parents[1] / 'deployment/browser-runtime-20261112'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


host = module('nonroot_qualify_host_r4', DIRECTORY / 'qualify-energy-browser-nonroot-20261112-r4.py')
init = module('nonroot_qualify_init_r3', DIRECTORY / 'browser_init_r3.py')
child = module('nonroot_qualify_child_r4', DIRECTORY / 'qualify_browser_child_r4.py')
ASSETS = {'init': b'print("init")', 'child': b'print("child")', 'package': b'zip', 'test_files': b'{}'}


class RuntimeQualificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='energy-qual-unit-')
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_operator_is_python37_syntax(self):
        ast.parse((DIRECTORY / 'qualify-energy-browser-nonroot-20261112-r4.py').read_text(encoding='utf-8'), feature_version=(3, 7))

    def test_actual_browser_size_is_within_repaired_bound(self):
        self.assertTrue(init.file_size_allowed(298683784))
        self.assertTrue(init.file_size_allowed(402653184))
        self.assertFalse(init.file_size_allowed(402653185))
        self.assertFalse(init.file_size_allowed(-1))
        self.assertEqual(init.MAX_BYTES, 512 * 1024 * 1024)
        self.assertEqual(init.MAX_FILES, 256)

    def test_wrong_host_does_not_decode_assets_or_create_resources(self):
        output = io.StringIO()
        with patch.object(host.os, 'name', 'nt'), patch.object(host, 'sealed_assets', side_effect=AssertionError('must not decode')):
            with contextlib.redirect_stdout(output):
                self.assertEqual(host.main(['--qualify-nonroot-browser-once']), 1)
        result = json.loads(output.getvalue())
        self.assertFalse(result['once_claimed'])
        self.assertEqual(result['commands'], 0)

    def test_manifest_keeps_root_reader_and_nonroot_renderer_separate(self):
        spec = host.manifest('operation', 'pod', ASSETS)['spec']
        self.assertFalse(spec['automountServiceAccountToken'])
        self.assertFalse(spec['enableServiceLinks'])
        self.assertEqual(spec['securityContext']['fsGroup'], 1000)
        self.assertTrue(all('emptyDir' in row for row in spec['volumes']))
        self.assertNotIn('hostNetwork', spec)
        initial, main = spec['initContainers'][0], spec['containers'][0]
        self.assertEqual(initial['securityContext']['runAsUser'], 0)
        self.assertEqual(initial['securityContext']['runAsGroup'], 1000)
        self.assertEqual(main['securityContext']['runAsUser'], 1000)
        self.assertEqual(main['securityContext']['runAsGroup'], 1000)
        self.assertTrue(main['securityContext']['runAsNonRoot'])
        for row in (initial, main):
            self.assertEqual(row['imagePullPolicy'], 'Never')
            self.assertEqual(row['securityContext']['capabilities'], {'drop': ['ALL']})
            self.assertTrue(row['securityContext']['readOnlyRootFilesystem'])
            self.assertNotIn('envFrom', row)
        mounted = [row for row in main['volumeMounts'] if row['name'] == 'browser']
        self.assertEqual(len(mounted), 1)
        self.assertTrue(mounted[0]['readOnly'])
        self.assertEqual(main['volumeMounts'][0]['mountPath'], '/tmp')

    def test_mutation_and_secret_commands_rejected_before_subprocess(self):
        op = host.Operation(ASSETS)
        for args in (['patch', 'deployment', 'gateway'], ['get', 'secret', 'deer-flow-env', '-o', 'json'],
                     ['delete', 'pod', 'sandbox-a'], ['exec', '-i', op.name, '-c', 'qualification', '--', 'sh', '-c', 'env']):
            with patch.object(host.subprocess, 'Popen', side_effect=AssertionError('must reject first')):
                with self.assertRaises(host.Refused):
                    op.command(args)

    def test_wrong_delete_uid_is_rejected(self):
        op = host.Operation(ASSETS)
        op.pod_uid = 'expected'
        args = ['delete', '--raw', '/api/v1/namespaces/deer-flow/pods/' + op.name, '-f', '-']
        body = host.canonical({'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': 'wrong'}, 'gracePeriodSeconds': 5})
        with self.assertRaises(host.Refused):
            op.command(args, body)

    def pod(self):
        requested = host.manifest('operation', 'pod', ASSETS)
        pod = copy.deepcopy(requested)
        pod['metadata']['uid'] = 'owned'
        pod['status'] = {'phase': 'Running', 'initContainerStatuses': [{'imageID': host.IMAGE_ID, 'restartCount': 0, 'state': {'terminated': {'exitCode': 0}}}],
                         'containerStatuses': [{'ready': True, 'imageID': host.IMAGE_ID, 'restartCount': 0}]}
        return requested, pod

    def test_both_init_and_renderer_image_identity_required(self):
        requested, pod = self.pod()
        self.assertTrue(host.pod_guard(pod, requested, 'owned'))
        for key in ('initContainerStatuses', 'containerStatuses'):
            bad = copy.deepcopy(pod)
            bad['status'][key][0]['imageID'] = 'sha256:' + '0' * 64
            with self.assertRaises(host.Refused):
                host.pod_guard(bad, requested, 'owned')

    def test_replaced_or_unexpected_privileged_pod_is_rejected(self):
        requested, pod = self.pod()
        for change in ('uid', 'hostNetwork', 'envFrom', 'otherContainer'):
            bad = copy.deepcopy(pod)
            if change == 'uid':
                bad['metadata']['uid'] = 'other'
            elif change == 'hostNetwork':
                bad['spec']['hostNetwork'] = True
            elif change == 'envFrom':
                bad['spec']['containers'][0]['envFrom'] = [{'secretRef': {'name': 'private'}}]
            else:
                bad['spec']['containers'].append(copy.deepcopy(bad['spec']['containers'][0]))
            with self.assertRaises(host.Refused):
                host.pod_guard(bad, requested, 'owned')

    def test_cleanup_identity_guard_accepts_owned_failed_pod(self):
        requested, pod = self.pod()
        pod['status']['phase'] = 'Failed'
        pod['status']['initContainerStatuses'][0]['state']['terminated']['exitCode'] = 1
        with self.assertRaises(host.Refused):
            host.pod_guard(pod, requested, 'owned')
        self.assertFalse(host.pod_guard(pod, requested, 'owned', inspect_status=False))

    def test_init_diagnostics_are_captured_before_cleanup_without_raw_logs(self):
        op = host.Operation(ASSETS)
        op.created = True
        requested, pod = self.pod()
        pod['metadata'].update(name=op.name, labels={host.LABEL: op.operation})
        pod['spec'] = copy.deepcopy(op.requested['spec'])
        pod['status']['phase'] = 'Failed'
        pod['status']['initContainerStatuses'][0].update(name='copy-image-browser', imageID='different-image')
        pod['status']['initContainerStatuses'][0]['state']['terminated']['exitCode'] = 1
        log = 'raw sensitive ignored\n' + json.dumps({'status': 'INCOMPLETE', 'stage': 'temporary_root_group_guard',
                                                   'error_code': 'TEMPORARY_FSGROUP_REQUIRED', 'identity': {'uid': 0, 'gid': 0, 'groups': [0]}})
        with patch.object(op, 'command', side_effect=[pod, log]) as run:
            op.capture_init_diagnostic()
        self.assertEqual(op.out['init_diagnostic'][0]['error_code'], 'TEMPORARY_FSGROUP_REQUIRED')
        self.assertEqual(op.out['init_runtime'][0]['exitcode'], 1)
        self.assertFalse(op.out['init_runtime'][0]['image_id_matches_expected'])
        self.assertNotIn('sensitive', json.dumps(op.out))
        self.assertEqual(run.call_args_list[1].args[0][0], 'logs')

    def test_log_command_still_rejects_other_pod_or_container(self):
        op = host.Operation(ASSETS)
        op.pod_uid = 'owned'
        for args in (['logs', 'other', '-c', 'copy-image-browser', '--tail=8', '--limit-bytes=16384'],
                     ['logs', op.name, '-c', 'qualification', '--tail=8', '--limit-bytes=16384']):
            with self.assertRaises(host.Refused):
                op.command(args)

    def test_diagnostic_capture_precedes_cleanup_on_failure(self):
        output, order = io.StringIO(), []
        with patch.object(host.os, 'name', 'posix'), patch.object(host.os, 'uname', return_value=SimpleNamespace(nodename='datax-01'), create=True), \
             patch.object(host.os, 'geteuid', return_value=0, create=True), patch.object(host, 'sealed_assets', return_value=ASSETS), \
             patch.object(host.Operation, 'execute', side_effect=host.Refused('SYNTHETIC_FAILURE')), \
             patch.object(host.Operation, 'capture_init_diagnostic', side_effect=lambda: order.append('diagnostic')), \
             patch.object(host.Operation, 'cleanup', side_effect=lambda: order.append('cleanup')):
            with contextlib.redirect_stdout(output):
                host.main(['--qualify-nonroot-browser-once'])
        self.assertEqual(order, ['diagnostic', 'cleanup'])

    def test_cleanup_runs_after_execution_failure(self):
        output = io.StringIO()
        with patch.object(host.os, 'name', 'posix'), patch.object(host.os, 'uname', return_value=SimpleNamespace(nodename='datax-01'), create=True), \
             patch.object(host.os, 'geteuid', return_value=0, create=True), patch.object(host, 'sealed_assets', return_value=ASSETS), \
             patch.object(host.Operation, 'execute', side_effect=host.Refused('SYNTHETIC_FAILURE')), patch.object(host.Operation, 'cleanup') as cleanup:
            with contextlib.redirect_stdout(output):
                self.assertEqual(host.main(['--qualify-nonroot-browser-once']), 1)
        cleanup.assert_called_once()
        self.assertEqual(json.loads(output.getvalue())['error_code'], 'SYNTHETIC_FAILURE')

    def test_unsealed_and_modified_assets_are_rejected(self):
        with self.assertRaises(host.Refused):
            host.sealed_assets()
        assets = {name: {'base64': base64.b64encode(raw).decode(), 'sha256': hashlib.sha256(raw).hexdigest()} for name, raw in ASSETS.items()}
        with patch.object(host, 'ASSETS', assets):
            self.assertEqual(host.sealed_assets(), ASSETS)
            assets['child']['sha256'] = '0' * 64
            with self.assertRaises(host.Refused):
                host.sealed_assets()

    def test_zip_traversal_and_private_payloads_rejected(self):
        for name in ('../escape.py', 'energy-market-daily/.private/credentials.json', '/absolute'):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w') as archive:
                archive.writestr(name, b'data')
            raw = buffer.getvalue()
            with self.assertRaises(child.Refused):
                child.extract_package(raw, hashlib.sha256(raw).hexdigest(), self.root)

    @unittest.skipUnless(os.name == 'posix', 'POSIX file copy and executable bits')
    def test_browser_copy_and_verification_preserve_bytes_without_source_changes(self):
        source, destination = self.root / 'source', self.root / 'runtime'
        source.mkdir(); destination.mkdir()
        (source / 'headless_shell').write_bytes(b'fixed binary fixture')
        (source / 'headless_shell').chmod(0o755)
        (source / 'LICENSE').write_bytes(b'license fixture')
        before = (source / 'headless_shell').stat().st_mode
        result = init.copy_browser(source, destination)
        self.assertEqual(result['status'], 'BROWSER_COPIED_FOR_NONROOT')
        inventory, digest = child.verify_runtime(destination)
        self.assertEqual(digest, result['inventory_sha256'])
        self.assertEqual(inventory['licenses'], ['chrome-linux/LICENSE'])
        self.assertEqual(before, (source / 'headless_shell').stat().st_mode)
        self.assertEqual(inventory['files'][1]['executable'], True)

    @unittest.skipUnless(os.name == 'posix', 'POSIX symlink validation')
    def test_browser_source_symlinks_are_not_copied(self):
        source, destination = self.root / 'source', self.root / 'runtime'
        source.mkdir(); destination.mkdir()
        (source / 'headless_shell').symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):
            init.copy_browser(source, destination)

    @unittest.skipUnless(os.name == 'posix', 'POSIX inventory mode validation')
    def test_browser_tamper_and_extra_files_are_rejected(self):
        source, destination = self.root / 'source', self.root / 'runtime'
        source.mkdir(); destination.mkdir()
        (source / 'headless_shell').write_bytes(b'binary')
        (source / 'headless_shell').chmod(0o755)
        init.copy_browser(source, destination)
        (destination / 'extra.txt').write_text('extra')
        with self.assertRaises(child.Refused):
            child.verify_runtime(destination)
        (destination / 'extra.txt').unlink()
        (destination / 'chrome-linux/headless_shell').write_bytes(b'tamper')
        with self.assertRaises(child.Refused):
            child.verify_runtime(destination)


if __name__ == '__main__':
    unittest.main()
