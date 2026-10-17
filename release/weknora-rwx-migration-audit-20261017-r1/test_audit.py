import contextlib
import copy
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

import audit as m


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir='/qualify')
        self.root = Path(self.tmp.name)/'source'
        self.root.mkdir(mode=0o770)
        self.root.chmod(0o770)
        os.chown(self.root, 1000, 1000)

    def tearDown(self):
        self.tmp.cleanup()

    def file(self, name='PRIVATE_NAME', data=b'synthetic', mode=0o600, uid=1000):
        path = self.root/name
        path.write_bytes(data)
        path.chmod(mode)
        os.chown(path, uid, uid)
        return path

    def test_regular_counts_no_name_or_contents(self):
        self.file(data=b'NEVER_PRINT_THIS')
        result = m.scan(self.root, 'files')
        self.assertEqual(result['traversal'], 'COMPLETE')
        self.assertEqual(result['counters']['files'], 1)
        self.assertEqual(result['counters']['apparent_bytes'], 16)
        self.assertNotIn('PRIVATE_NAME', json.dumps(result))
        self.assertNotIn('NEVER_PRINT_THIS', json.dumps(result))
        self.assertFalse(result['point_in_time_snapshot'])

    def test_empty_directory_not_size_inference(self):
        r = m.scan(self.root, 'docreader')
        self.assertEqual(r['counters']['files'], 0)
        self.assertEqual(r['traversal'], 'COMPLETE')
        self.assertFalse(r['point_in_time_snapshot'])

    def test_symlinks_not_followed(self):
        (self.root/'SECRET_LINK').symlink_to('/etc')
        r = m.scan(self.root, 'files')
        self.assertEqual(r['counters']['symlinks'], 1)
        self.assertEqual(r['counters']['files'], 0)
        self.assertNotIn('SECRET_LINK', json.dumps(r))

    def test_fifo_never_opened(self):
        os.mkfifo(self.root/'fifo')
        r = m.scan(self.root, 'files')
        self.assertEqual(r['counters']['special'], 1)

    def test_hardlinks_and_sparse_reported(self):
        f = self.file()
        os.link(f, self.root/'second')
        s = self.file('sparse', b'')
        with s.open('wb') as out:
            out.seek(1048576)
            out.write(b'x')
        r = m.scan(self.root, 'files')['counters']
        self.assertEqual(r['hardlinked_entries'], 2)
        self.assertEqual(r['sparse_entries'], 1)

    def test_mode_estimate_not_effective_permission_proof(self):
        self.file(mode=0o600, uid=0)
        r = m.scan(self.root, 'files')
        self.assertEqual(r['counters']['non_1000_owner'], 1)
        self.assertEqual(r['counters']['posix_unreadable_for_uid1000'], 1)
        self.assertEqual(r['effective_uid1000_readability'], 'NOT_TESTED_POSIX_MODE_ESTIMATE_ONLY')

    def test_entry_limit_not_complete(self):
        self.file('one')
        self.file('two')
        with patch.object(m, 'SCAN_ENTRIES', 1):
            r = m.scan(self.root, 'files')
        self.assertEqual(r['traversal'], 'INCOMPLETE')
        self.assertEqual(r['stop_reason'], 'TIME_OR_ENTRY_LIMIT')

    def test_deadline_not_complete(self):
        with patch.object(m, 'SCAN_SECONDS', 0):
            r = m.scan(self.root, 'files')
        self.assertEqual(r['traversal'], 'INCOMPLETE')

    def test_nested_directory_fd_traversal(self):
        (self.root/'nested').mkdir()
        (self.root/'nested/data').write_bytes(b'x')
        r = m.scan(self.root, 'files')
        self.assertEqual(r['counters']['files'], 1)
        self.assertEqual(r['counters']['directories'], 2)

    def test_same_device_bind_mount_excluded(self):
        (self.root/'mounted').mkdir()
        (self.root/'mounted/secret').write_bytes(b'do not scan')
        with patch.object(m, 'descendant_mounts', return_value={('mounted',)}):
            r = m.scan(self.root, 'files')
        self.assertEqual(r['counters']['files'], 0)
        self.assertEqual(r['traversal'], 'INCOMPLETE')

    def test_mount_table_drift_marks_incomplete(self):
        with patch.object(m, 'descendant_mounts', side_effect=[set(), {('new',)}]):
            r = m.scan(self.root, 'files')
        self.assertEqual(r['stop_reason'], 'MOUNT_TABLE_CHANGED')
        self.assertEqual(r['traversal'], 'INCOMPLETE')

    def test_late_bind_mount_detected_by_fd_mount_id(self):
        (self.root/'late').mkdir()
        (self.root/'late/unrelated').write_bytes(b'not scanned')
        with patch.object(m, 'fd_mount_id', side_effect=[1, 2]):
            r = m.scan(self.root, 'files')
        self.assertEqual(r['counters']['files'], 0)
        self.assertEqual(r['counters']['crossed_device_or_mount'], 1)
        self.assertEqual(r['traversal'], 'INCOMPLETE')

    def test_symlink_root_rejected(self):
        p = self.root.parent/'link'
        p.symlink_to(self.root)
        with self.assertRaises(m.Failure): m.scan(p, 'files')

    def test_wrong_root_owner_rejected(self):
        os.chown(self.root, 0, 0)
        with self.assertRaisesRegex(m.Failure, 'root_identity'): m.scan(self.root, 'files')

    def test_read_errors_cannot_be_zero_success(self):
        self.file()
        old = os.stat
        def wrapped(path, *args, **kwargs):
            if 'dir_fd' in kwargs:
                raise PermissionError('private content')
            return old(path, *args, **kwargs)
        with patch.object(m.os, 'stat', side_effect=wrapped):
            r = m.scan(self.root, 'files')
        self.assertEqual(r['traversal'], 'INCOMPLETE')
        self.assertEqual(r['counters']['errors'], 1)
        self.assertNotIn('private content', json.dumps(r))


def pv(name='source', claim='files', ns=m.NS, path=None):
    return dict(kind='PersistentVolume', metadata=dict(name=name, uid='pv-' + name),
                spec=dict(local=dict(path=path or str(m.ROOTS['files'])),
                          claimRef=dict(namespace=ns, name=claim, uid='pvc-' + claim)))


def pod(name='worker', claim='files', ro=False, namespace=m.NS):
    return dict(kind='Pod', metadata=dict(name=name, namespace=namespace, uid='pod-' + name),
        spec=dict(nodeName='datax-03', volumes=[dict(name='data', persistentVolumeClaim=dict(claimName=claim))],
            containers=[dict(name='app', env=[dict(name='KEY', value='NEVER_EXPORT_THIS')],
                             volumeMounts=[dict(name='data', mountPath='/data', readOnly=ro)])]), status=dict(phase='Running'))


class Consumers(unittest.TestCase):
    def test_pod_writer_projection_redacts_env(self):
        rows = m.consumers([pod()], [pv()])
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]['mounts'][0]['read_only_declared'])
        self.assertNotIn('NEVER_EXPORT_THIS', json.dumps(rows))

    def test_readonly_at_either_layer(self):
        p = pod(ro=True)
        self.assertTrue(m.consumers([p], [pv()])[0]['mounts'][0]['read_only_declared'])
        p = pod()
        p['spec']['volumes'][0]['persistentVolumeClaim']['readOnly'] = True
        self.assertTrue(m.consumers([p], [pv()])[0]['mounts'][0]['read_only_declared'])

    def test_other_namespace_claim_not_same_volume(self):
        self.assertFalse(m.consumers([pod(namespace='other')], [pv()]))

    def test_alias_pv_and_cross_namespace_writer_found(self):
        rows = m.consumers([pod(namespace='other')], [pv(ns='other')])
        self.assertEqual(len(rows), 1)

    def test_parent_hostpath_conservative_match(self):
        p = pod(namespace='other')
        p['spec']['volumes'] = [dict(name='data', hostPath=dict(path='/opt'))]
        rows = m.consumers([p], [])
        self.assertEqual(rows[0]['mounts'][0]['roles'], ['source_docreader_hostpath', 'source_files_hostpath'])

    def test_direct_nfs_detected(self):
        p = pod()
        p['spec']['volumes'] = [dict(name='data', nfs=dict(server='192.168.111.189', path='/mnt/data/weknora-rwx-r2/files'))]
        self.assertEqual(m.consumers([p], [])[0]['mounts'][0]['roles'], ['target_files_direct_nfs'])

    def test_scaled_zero_controller_template_not_ignored(self):
        p = pod()
        deployment = dict(kind='Deployment', metadata=p['metadata'], spec=dict(replicas=0, template=dict(spec=p['spec'])))
        rows = m.consumers([deployment], [pv()])
        self.assertEqual(rows[0]['replicas'], 0)

    def test_cron_init_and_ephemeral_consumers_found(self):
        p = pod()
        p['spec']['initContainers'] = p['spec'].pop('containers')
        cron = dict(kind='CronJob', metadata=p['metadata'], spec=dict(suspend=True,
                    jobTemplate=dict(spec=dict(template=dict(spec=p['spec'])))))
        rows = m.consumers([cron], [pv()])
        self.assertTrue(rows[0]['suspended'])
        self.assertEqual(rows[0]['mounts'][0]['container_type'], 'initContainers')
        p['spec']['ephemeralContainers'] = p['spec'].pop('initContainers')
        self.assertEqual(m.consumers([p], [pv()])[0]['mounts'][0]['container_type'], 'ephemeralContainers')

    def test_path_prefix_not_sibling_match(self):
        self.assertFalse(m.overlap(str(m.ROOTS['files']) + '-other', str(m.ROOTS['files'])))
        self.assertTrue(m.overlap('/', str(m.ROOTS['files'])))
        self.assertFalse(m.overlap('/opt/../etc', str(m.ROOTS['files'])))


class Safety(unittest.TestCase):
    def test_kube_only_get_allowlist(self):
        k = object.__new__(m.Kube)
        k.exe = '/mock/kubectl'
        with self.assertRaises(m.Failure): k.get('secrets')
        with self.assertRaises(m.Failure): k.get('exec')
        with patch.object(m, 'run', return_value=b'{"items":[]}') as call, contextlib.redirect_stdout(io.StringIO()):
            k.get('pods')
            self.assertIn('get', call.call_args[0][0])
            self.assertIn('-A', call.call_args[0][0])

    def test_command_timeout(self):
        with self.assertRaises(m.Failure):
            m.run([sys.executable, '-c', 'import time;time.sleep(5)'], timeout=.1)

    def test_command_output_limit(self):
        with self.assertRaises(m.Failure):
            m.run([sys.executable, '-c', 'print("x"*10000)'], limit=100)

    def test_command_errors_redacted(self):
        with self.assertRaises(m.Failure):
            m.run([sys.executable, '-c', 'import sys;sys.stderr.write("SECRET");sys.exit(1)'])
        self.assertNotIn('SECRET', json.dumps(m.LAST))

    def test_private_receipts_no_overwrite_or_links(self):
        with tempfile.TemporaryDirectory(dir='/qualify') as td:
            path = Path(td)/'RESULT.json'
            m.save(path, {'safe': True})
            self.assertTrue(json.loads(m.read_private(path))['safe'])
            with self.assertRaises(FileExistsError): m.save(path, {})
            os.link(path, Path(td)/'alias')
            with self.assertRaises(m.Failure): m.read_private(path)

    def test_wrong_receipt_hash_stops_before_kube(self):
        with patch.object(m, 'read_private', return_value=b'{}'), patch.object(m, 'Kube') as kube:
            with self.assertRaisesRegex(m.Failure, 'hash_changed'): m.cluster_audit()
            kube.assert_not_called()

    def test_status_reads_only_saved_receipt(self):
        with tempfile.TemporaryDirectory(dir='/qualify') as td:
            base = Path(td)
            folder = base/(m.PREFIX + 'fixture')
            folder.mkdir()
            m.save(folder/'RESULT.json', dict(collection='COMPLETE'))
            with patch.object(m, 'BASE', base), patch.object(m.socket, 'gethostname', return_value='datax-01'), \
                 patch.object(sys, 'argv', ['audit.py', '--status', str(folder)]), patch.object(m, 'run') as run, \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(m.main(), 0)
            run.assert_not_called()
            self.assertFalse(json.loads(output.getvalue())['live_cluster_read'])


class Cluster:
    def __init__(self):
        self.pvs, self.pvcs, self.created = [], {}, {}
        for group, pairs in [('source', m.OLD), ('target', m.NEW)]:
            for role, (pv_name, pvc_name) in pairs.items():
                obj = pv(pv_name, pvc_name, path=str(m.ROOTS[role]))
                obj['status'] = dict(phase='Bound')
                pvc = dict(kind='PersistentVolumeClaim', metadata=dict(name=pvc_name, namespace=m.NS, uid='pvc-' + pvc_name),
                           spec=dict(volumeName=pv_name), status=dict(phase='Bound'))
                if group == 'target':
                    obj['spec'].pop('local')
                    obj['spec'].update(nfs=dict(server='192.168.111.189', path='/mnt/data/weknora-rwx-r2/' + role),
                                       accessModes=['ReadWriteMany'], persistentVolumeReclaimPolicy='Retain',
                                       mountOptions=['nfsvers=4.1', 'hard'])
                    self.created['PersistentVolume/' + pv_name] = obj['metadata']['uid']
                    self.created['PersistentVolumeClaim/' + pvc_name] = pvc['metadata']['uid']
                self.pvs.append(obj)
                self.pvcs[pvc_name] = pvc
        self.app = pod(name='weknora-app-c467d64fc-2zbls', claim='weknora-data-files')
        self.app['metadata']['uid'] = '2fb12206-2d91-4b12-806b-471e1ca19c61'

    def get(self, kind, name=None):
        if kind == 'pv': return copy.deepcopy(dict(items=self.pvs))
        if kind == 'pvc': return copy.deepcopy(self.pvcs[name])
        if kind == 'pods': return copy.deepcopy(dict(items=[self.app]))
        return dict(items=[])


class ClusterTests(unittest.TestCase):
    def test_snapshot_success_aggregated_only(self):
        k = Cluster()
        result = m.cluster_snapshot(k, k.created)
        self.assertEqual(len(result['volumes']), 4)
        self.assertEqual(len(result['declared_consumers']), 1)
        self.assertNotIn('NEVER_EXPORT_THIS', json.dumps(result))

    def test_pvc_uid_binding_mismatch_rejected(self):
        k = Cluster()
        k.pvs[0]['spec']['claimRef']['uid'] = 'other'
        with self.assertRaisesRegex(m.Failure, 'binding_changed'): m.cluster_snapshot(k, k.created)

    def test_target_uid_change_rejected(self):
        k = Cluster()
        k.pvs[2]['metadata']['uid'] = 'other'
        with self.assertRaisesRegex(m.Failure, 'target_uid_changed'): m.cluster_snapshot(k, k.created)

    def test_changed_nfs_server_rejected(self):
        k = Cluster()
        k.pvs[2]['spec']['nfs']['server'] = '192.168.111.1'
        with self.assertRaisesRegex(m.Failure, 'target_nfs_changed'): m.cluster_snapshot(k, k.created)

    def test_changed_app_process_rejected(self):
        k = Cluster()
        k.app['metadata']['uid'] = 'new'
        with self.assertRaisesRegex(m.Failure, 'app_identity_changed'): m.cluster_snapshot(k, k.created)

    def test_changed_source_path_rejected(self):
        k = Cluster()
        k.pvs[0]['spec']['local']['path'] = '/other'
        with self.assertRaisesRegex(m.Failure, 'source_path_changed'): m.cluster_snapshot(k, k.created)

    def test_cluster_receipt_and_two_sample_drift(self):
        result = dict(cross_node_io='PASS', production_baseline='UNCHANGED')
        raw = m.canonical(result)
        reads = [raw, m.canonical(dict(plan=[]))]
        with patch.object(m, 'PIN', m.sha(raw)), patch.object(m, 'read_private', side_effect=reads), \
             patch.object(m, 'Kube'), patch.object(m, 'cluster_snapshot', side_effect=[{'sample': 1}, {'sample': 2}]):
            r = m.cluster_audit()
        self.assertFalse(r['baseline_stable'])
        self.assertEqual(r['collection'], 'BASELINE_CHANGED_REVIEW_REQUIRED')
        self.assertEqual(r['write_quiescence'], 'NOT_PROVEN')


if __name__ == '__main__':
    unittest.main()
