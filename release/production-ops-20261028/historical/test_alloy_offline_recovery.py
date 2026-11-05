import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import tarfile

spec = importlib.util.spec_from_file_location('alloy', Path(__file__).with_name('restore-portal-alloy-offline-image-20261028-r1.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def digest(b):
    return 'sha256:' + hashlib.sha256(b).hexdigest()


def bundle(bad_diff=False, platform='arm64', unsafe=False):
    layers, diffs, blobs = [], [], {}
    for i in range(5):
        raw = ('layer-' + str(i)).encode()
        data = gzip.compress(raw)
        h = digest(data)
        layers.append({'mediaType': 'application/vnd.docker.image.rootfs.diff.tar.gzip', 'digest': h, 'size': len(data)})
        diffs.append(digest(raw))
        blobs[h] = data
    if bad_diff:
        diffs[0] = 'sha256:' + '0' * 64
    config = json.dumps({'architecture': platform, 'os': 'linux', 'rootfs': {'diff_ids': diffs}}).encode()
    ch = digest(config)
    manifest = json.dumps({'config': {'digest': ch, 'size': len(config)}, 'layers': layers}).encode()
    mh = digest(manifest)
    blobs.update({ch: config, mh: manifest})
    index = json.dumps({'manifests': [{'digest': mh, 'size': len(manifest), 'annotations': {'org.opencontainers.image.ref.name': m.TARGET}}]}).encode()
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w:') as tar:
        contents = [('index.json', index), ('oci-layout', b'{"imageLayoutVersion":"1.0.0"}')]
        contents.extend(('blobs/sha256/' + h[7:], b) for h, b in blobs.items())
        if unsafe:
            contents.append(('../unexpected', b'x'))
        for name, raw in contents:
            entry = tarfile.TarInfo(name)
            entry.size = len(raw)
            tar.addfile(entry, io.BytesIO(raw))
    return out.getvalue(), mh, ch


class Tests(unittest.TestCase):
    @unittest.skipUnless(Path('/package/alloy.arm64.oci.tar').is_file(), 'delivered package optional mount')
    def test_real_delivered_archive_on_linux_python37(self):
        with Path('/package/alloy.arm64.oci.tar').open('rb') as stream:
            result = m.verify_archive(stream)
        self.assertEqual(result['config_digest'], 'sha256:8b089d2834ca96f22974b0ea75528dbd3806c1bf38d67faa4483c72a2e5cab1e')
        self.assertEqual(result['layers'], 5)

    def pins(self, data, mh, ch):
        return patch.multiple(m, ARCHIVE_BYTES=len(data), ARCHIVE_SHA256=hashlib.sha256(data).hexdigest(), MANIFEST=mh, CONFIG=ch)

    def test_real_compressed_and_uncompressed_layer_hashes(self):
        data, mh, ch = bundle()
        with self.pins(data, mh, ch):
            result = m.verify_archive(io.BytesIO(data))
        self.assertEqual(result['layers'], 5)
        self.assertEqual(result['status'], 'PASS')

    def test_transport_corruption_refused(self):
        data, mh, ch = bundle()
        with self.pins(data, mh, ch):
            with self.assertRaisesRegex(m.Refused, 'TRANSPORT_HASH_OR_SIZE'):
                m.verify_archive(io.BytesIO(data[:-1] + b'x'))

    def test_uncompressed_diff_id_mismatch_refused(self):
        data, mh, ch = bundle(bad_diff=True)
        with self.pins(data, mh, ch):
            with self.assertRaisesRegex(m.Refused, 'UNCOMPRESSED_LAYER_HASH'):
                m.verify_archive(io.BytesIO(data))

    def test_other_platform_refused(self):
        data, mh, ch = bundle(platform='amd64')
        with self.pins(data, mh, ch):
            with self.assertRaisesRegex(m.Refused, 'PLATFORM_MISMATCH'):
                m.verify_archive(io.BytesIO(data))

    def test_extra_or_traversal_archive_member_refused(self):
        data, mh, ch = bundle(unsafe=True)
        with self.pins(data, mh, ch):
            with self.assertRaisesRegex(m.Refused, 'ARCHIVE_ENTRY_SCOPE'):
                m.verify_archive(io.BytesIO(data))

    def objects(self):
        node = {'metadata': {'uid': m.NODE_UID}, 'status': {'nodeInfo': {'architecture': 'arm64'}, 'conditions': [{'type': 'Ready', 'status': 'True'}]}}
        ds = {'metadata': {'uid': 'daemonset', 'generation': 1}, 'spec': {'template': {'spec': {'containers': [{'name': 'alloy', 'image': 'grafana/alloy:v1.11.0', 'imagePullPolicy': 'IfNotPresent'}]}}}}
        target = {'metadata': {'uid': m.POD_UID, 'name': 'alloy-bad', 'ownerReferences': [{'controller': True, 'kind': 'DaemonSet', 'uid': 'daemonset'}]}, 'spec': {'nodeName': 'datax-01'}, 'status': {'phase': 'Pending', 'containerStatuses': [{'state': {'waiting': {'reason': 'ImagePullBackOff'}}}]}}
        peer = {'metadata': {'uid': 'peer', 'ownerReferences': [{'controller': True, 'kind': 'DaemonSet', 'uid': 'daemonset'}]}, 'spec': {'nodeName': 'datax-02'}, 'status': {'phase': 'Running', 'conditions': [{'type': 'Ready', 'status': 'True'}], 'containerStatuses': [{'ready': True, 'imageID': m.CONFIG}]}}
        return node, ds, target, peer

    def preflight_objects(self, objs):
        node, ds, target, peer = objs
        def get(kind, *args):
            return {'node': node, 'daemonset': ds, 'pods': {'items': [target, peer]}}[kind]
        with patch.object(m, 'get', side_effect=get):
            return m.preflight()

    def test_matching_peer_and_waiting_pod_accepted(self):
        ds, pod, peers = self.preflight_objects(self.objects())
        self.assertEqual(pod['metadata']['uid'], m.POD_UID)
        self.assertEqual(peers[0]['node'], 'datax-02')

    def test_no_matching_peer_refuses(self):
        objs = self.objects()
        objs[3]['status']['containerStatuses'][0]['imageID'] = 'sha256:' + '0' * 64
        with self.assertRaisesRegex(m.Refused, 'NO_MATCHING_HEALTHY_ALLOY_PEER'):
            self.preflight_objects(objs)

    def test_always_pull_requires_review(self):
        objs = self.objects()
        objs[1]['spec']['template']['spec']['containers'][0]['imagePullPolicy'] = 'Always'
        with self.assertRaisesRegex(m.Refused, 'PULL_POLICY_REQUIRES_REVIEW'):
            self.preflight_objects(objs)

    def test_node_pressure_refuses(self):
        objs = self.objects()
        objs[0]['status']['conditions'].append({'type': 'DiskPressure', 'status': 'True'})
        with self.assertRaisesRegex(m.Refused, 'NODE_PRESSURE'):
            self.preflight_objects(objs)

    def test_replaced_target_refuses(self):
        objs = self.objects()
        objs[2]['metadata']['uid'] = 'changed'
        with self.assertRaisesRegex(m.Refused, 'TARGET_POD_CHANGED'):
            self.preflight_objects(objs)

    def test_wrong_owner_refuses(self):
        objs = self.objects()
        objs[2]['metadata']['ownerReferences'][0]['uid'] = 'other'
        with self.assertRaisesRegex(m.Refused, 'TARGET_POD_CHANGED'):
            self.preflight_objects(objs)

    def test_ready_with_wrong_image_not_accepted(self):
        objs = self.objects()
        peer = objs[3]
        self.assertTrue(m.pod_ready(peer))
        peer['status']['containerStatuses'][0]['imageID'] = 'sha256:' + '0' * 64
        self.assertFalse(m.pod_ready(peer))

    @unittest.skipUnless(os.name == 'posix', 'Linux fd import transport')
    def test_once_import_and_existing_pod_recovery_no_cluster_writes(self):
        data, mh, ch = bundle()
        with tempfile.TemporaryDirectory() as directory, self.pins(data, mh, ch):
            root = Path(directory)
            (root / m.ARCHIVE).write_bytes(data)
            node, ds, target, peer = self.objects()
            ready = json.loads(json.dumps(target))
            ready['status'] = peer['status']
            before = {'old:v1': 'sha256:' + 'f' * 64}
            imported = dict(before, **{m.TARGET: mh})
            calls = []
            def command(args, **kwargs):
                calls.append(args)
                return b''
            def get(kind, *args):
                return ds if kind == 'daemonset' else ready
            with patch.object(m, '__file__', str(root / 'restore.py')), patch.object(m, 'CONTROL', root / 'receipt'), patch.object(m.socket, 'gethostname', return_value='datax-01'), patch.object(m.os, 'geteuid', return_value=0), patch.object(m.os, 'nice'), patch.object(m.shutil, 'which', return_value='/usr/bin/ionice'), patch.object(m, 'command', side_effect=command), patch.object(m, 'preflight', return_value=(ds, target, [{'node': 'datax-02'}])), patch.object(m, 'protect', return_value={'gateway': {'uid': 'preserved'}}), patch.object(m, 'refs', side_effect=[before, before, imported]), patch.object(m, 'get', side_effect=get), patch.object(m.os, 'statvfs', return_value=types.SimpleNamespace(f_bavail=10*1024**3, f_frsize=1)), patch('sys.argv', ['restore.py', '--restore-alloy-image-once']):
                m.main()
            imports = [args for args in calls if 'import' in args]
            self.assertEqual(len(imports), 1)
            self.assertFalse(any(word in ('patch', 'delete', 'pull', 'rollout', 'exec') for args in calls for word in args))
            result = json.loads((root / 'receipt' / 'REPORT.json').read_text())
            self.assertTrue(result['runtime_accepted'])
            self.assertTrue(result['old_image_references_preserved'])


if __name__ == '__main__':
    unittest.main()
