"""Offline Linux/Python 3.7 qualification; no containerd or network actions."""
import base64
import copy
import gzip
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import unittest
from unittest.mock import patch

PACKAGE = Path(os.environ.get('OA_COMPAT_PACKAGE', '/package'))
ROOT = 'app/backend/packages/harness/deerflow/persistence/'
PINS = {
    'gateway': ('gateway-forward', 67, 5, 'b852ea3bd215124e605e8c5a276c1cc99343a67d4165136121d885ea3def9449', '611dff501f59b3b94bccd4b3f9b31309955eb5e38356ed257882ead79d8e1fb1'),
    'runner': ('ppt-master-runner-forward', 384, 10, '990e6381692083a00a856a61c19f072e81f57f4ec41b232db4d8e8ebecf3ab86', '7b704c7f4434556d354f9bbbd326dfdb15f935dbb5b1d8b1fd7908866f02e612'),
}


def load(kind):
    path = PACKAGE / ('preload-portal-' + kind + '-oa-schema-compatibility-20261022-r1.py')
    ns = {'__name__': 'offline_preload'}
    exec(compile(path.read_bytes(), str(path), 'exec'), ns)
    return ns


class Tests(unittest.TestCase):
    def test_validation_does_not_run_commands(self):
        for kind in PINS:
            ns = load(kind)
            with patch.object(subprocess, 'run', side_effect=AssertionError('command')), patch.object(subprocess, 'Popen', side_effect=AssertionError('process')):
                _, _, _, prepared = ns['load_inputs']()
                self.assertEqual(set(prepared['roles']), {PINS[kind][0]})

    def test_exact_field_qualified_two_files_no_migration(self):
        for kind in PINS:
            ns = load(kind)
            _, _, profile, prepared = ns['load_inputs']()
            row = profile['roles'][0]
            packed = prepared['blobs'][row['tail_layers'][0]['blob_digest']]
            with tarfile.open(fileobj=io.BytesIO(gzip.decompress(packed))) as tar:
                self.assertEqual(set(tar.getnames()), {ROOT + 'bootstrap.py', ROOT + 'oa_schema_compatibility.py'})
                self.assertEqual(ns['sha'](tar.extractfile(ROOT + 'bootstrap.py').read()), PINS[kind][4])
                self.assertEqual(ns['sha'](tar.extractfile(ROOT + 'oa_schema_compatibility.py').read()), '2dd04bfafc29856c0f646926043aed7b713d79d69744a9053ff99fdf6af24c8b')

    def test_base_configuration_and_layer_prefix_preserved(self):
        for kind, (role, count, _, _, _) in PINS.items():
            ns = load(kind)
            _, _, profile, prepared = ns['load_inputs']()
            row = prepared['roles'][role]
            self.assertEqual(len(row['manifest']['layers']), count + 1)
            previous = copy.deepcopy(row['config'])
            previous['rootfs']['diff_ids'].pop()
            previous['history'].pop()
            base = profile['bases'][kind]
            self.assertEqual(ns['sha'](ns['canonical'](previous)), base['config_object_sha256'])
            self.assertEqual(ns['sha'](ns['canonical'](row['manifest']['layers'][:-1])), base['descriptor_prefix_sha256'])

    def test_current_reference_protected(self):
        for kind in PINS:
            ns = load(kind)
            _, _, profile, _ = ns['load_inputs']()
            base = profile['bases'][kind]
            self.assertEqual(profile['preserve'][kind][base['tag']], base['manifest_digest'])

    def test_node_role_isolation(self):
        for kind, (role, _, count, _, _) in PINS.items():
            ns = load(kind)
            scope, _, profile, _ = ns['load_inputs']()
            self.assertEqual(len(profile['nodes']), count)
            for host in profile['nodes']:
                self.assertEqual([r['role'] for r in scope.node_roles(host)[1]], [role])
            with self.assertRaises(scope.Failure):
                scope.node_roles('datax-01' if kind == 'gateway' else 'ecs-zdz-pro-0021')

    def test_new_once_namespace_and_flag(self):
        for kind in PINS:
            ns = load(kind)
            self.assertEqual(ns['CONTROL'], '/var/lib/portal-' + kind + '-oa-schema-compatibility-node-20261022-r1')
            self.assertIn('--preload-' + kind + '-oa-schema-compatibility-once', ns['main'].__code__.co_consts)

    def test_embedded_source_mutation_rejected(self):
        for kind in PINS:
            ns = load(kind)
            ns['SOURCES']['preparer'] += b'\n'
            with self.assertRaises(ns['Refused']):
                ns['load_inputs']()

    def test_blob_digest_corruption_rejected(self):
        for kind in PINS:
            ns = load(kind)
            encoded = json.loads(ns['SOURCES']['blobs'])
            encoded[next(iter(encoded))] = base64.b64encode(b'wrong').decode()
            raw = ns['canonical'](encoded)
            ns['SOURCES']['blobs'] = raw
            ns['SOURCE_PINS']['blobs'] = {'bytes': len(raw), 'sha256': ns['sha'](raw)}
            with self.assertRaises(ns['Refused']):
                ns['load_inputs']()

    def test_additional_payload_path_rejected(self):
        for kind in PINS:
            ns = load(kind)
            ns['PAYLOAD_LIMITS'][ROOT + 'migrations/versions/0021_oa_result_sets.py'] = 65536
            with self.assertRaises(ns['Refused']):
                ns['load_inputs']()

    def test_evidence_gate_is_pinned(self):
        ns = load('gateway')
        ns['accepted_nfs_gate']()
        ns['PRODUCT_QUALIFICATION']['checks_passed'] = 0
        with self.assertRaises(ns['Refused']):
            ns['accepted_nfs_gate']()


if __name__ == '__main__':
    unittest.main()
