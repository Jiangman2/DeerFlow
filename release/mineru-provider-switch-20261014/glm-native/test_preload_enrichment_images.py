import ast
import base64
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import socket
import struct
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import preload_enrichment_images as p
import test_export_weknora_base as fixtures


def example():
    item = dict(p.ITEMS[0]); data = b'synthetic private image content'*100
    item.update(archive_bytes=len(data), archive_sha256=hashlib.sha256(data).hexdigest())
    report = dict(item, store_image_export='PASS', uncompressed_diffids='PASS',
                  base_layer_verification='PASS', production_writes=0, runtime_volumes_exported=False,
                  identity=dict(item, source_node=item['node']), archive=p.source_root(item)+'/store.oci.tar')
    raw = json.dumps(report).encode(); item['report_sha256'] = hashlib.sha256(raw).hexdigest()
    return item, data, raw


def wire(report, data): return p.MAGIC+struct.pack('>I', len(report))+report+data


class ProtocolTests(unittest.TestCase):
    def test_fragmented_report_and_stream(self):
        item, data, report = example()
        for size in (1, 7, 65536):
            with self.subTest(size=size):
                output, receipts = [], []
                r = p.Receiver(item, output.append, receipts.append)
                raw = wire(report, data)
                for offset in range(0, len(raw), size): r.feed(raw[offset:offset+size])
                r.finish()
                self.assertEqual(b''.join(output), data); self.assertEqual(receipts, [report])

    def test_corrupt_receipt_writes_no_archive(self):
        item, data, report = example(); output = Mock(); receipt = Mock()
        r = p.Receiver(item, output, receipt)
        with self.assertRaisesRegex(p.verify.CheckFailure, 'export_report_hash'):
            r.feed(wire(b'{}', data))
        output.assert_not_called(); receipt.assert_not_called()

    def test_bad_size_extra_corrupt_and_missing_eof(self):
        item, data, report = example()
        for raw in (b'', wire(report, data[:-1]), wire(report, data+b'x'), wire(report, b'x'+data[1:]),
                    b'banner'+wire(report, data), p.MAGIC+struct.pack('>I', 1048577)):
            with self.subTest(size=len(raw)), self.assertRaises(p.verify.CheckFailure):
                r = p.Receiver(item, lambda b: None, lambda b: None); r.feed(raw); r.finish()

    def test_hash_pinned_receipt_still_requires_correct_identity(self):
        item, _, raw = example()
        for key, val in (('archive_bytes', 1), ('store_image_export', 'FAIL'), ('runtime_volumes_exported', True)):
            obj = json.loads(raw); obj[key] = val; bad = json.dumps(obj).encode()
            with self.subTest(key=key), self.assertRaises(p.verify.CheckFailure):
                p.check_report(dict(item, report_sha256=hashlib.sha256(bad).hexdigest()), bad)

    def test_wrong_source_node_and_root_index_not_confused_with_manifest(self):
        item, _, raw = example(); obj = json.loads(raw)
        for key in ('source_node', 'root_digest'):
            bad_obj = copy.deepcopy(obj); bad_obj['identity'][key] = 'changed'; bad = json.dumps(bad_obj).encode()
            with self.subTest(key=key), self.assertRaises(p.verify.CheckFailure):
                p.check_report(dict(item, report_sha256=hashlib.sha256(bad).hexdigest()), bad)
        self.assertNotEqual(p.ITEMS[1]['root_digest'], p.ITEMS[1]['manifest_digest'])


class TrustTests(unittest.TestCase):
    def test_existing_fingerprints_bound_and_wrong_key_rejected(self):
        home = Path(p.__file__).parents[2]/'portal-home-only-20261014/preload_home.py'
        self.assertIn(p.ITEMS[0]['fingerprint'], home.read_text())
        self.assertEqual(p.ITEMS[1]['fingerprint'], p.transfer.FINGERPRINT)
        key = b'synthetic ecdsa public key bytes'
        fp = 'SHA256:'+base64.b64encode(hashlib.sha256(key).digest()).decode().rstrip('=')
        raw = b'host ecdsa-sha2-nistp256 '+base64.b64encode(key)+b'\n'
        for item in p.ITEMS:
            got = p.known_host(dict(item, fingerprint=fp), raw)
            self.assertTrue(got.startswith(item['node'].encode()+b' '))
            with self.assertRaisesRegex(p.verify.CheckFailure, 'fingerprint'): p.known_host(item, raw)
            with self.assertRaises(p.verify.CheckFailure): p.known_host(item, b'')

    def test_strict_ssh_and_remote_code_only_reads_images(self):
        for item in p.ITEMS:
            cmd = p.ssh_command(item)
            self.assertIn('StrictHostKeyChecking=yes', cmd)
            self.assertIn('GlobalKnownHostsFile=/dev/null', cmd)
            self.assertIn('HostKeyAlias='+item['node'], cmd)
            self.assertEqual(cmd[-2], 'cnooc@'+item['host'])
            parsed = shlex.split(cmd[-1])
            self.assertEqual(parsed[:8], ['sudo', '-S', '-p', '', '--', 'python3', '-I', '-B'])
            self.assertEqual(parsed[-1], p.remote_source(item))
            ast.parse(parsed[-1], feature_version=(3, 7))
            for bad in ('chmod', 'kubectl', 'psql', 'redis-cli', 'subprocess', 'unlink', 'shutil'):
                self.assertNotIn(bad, parsed[-1])

    def test_wrong_host_no_prompt_or_network(self):
        with patch.object(p.socket, 'gethostname', return_value='other'), patch.object(p, 'prepare_once') as start, \
             patch.object(p.getpass, 'getpass') as prompt, patch.object(p, 'emit'):
            self.assertEqual(p.main(['--prepare-once']), 1)
            start.assert_not_called(); prompt.assert_not_called()

    def test_python37_no_destructive_commands(self):
        source = Path(p.__file__).read_text(); ast.parse(source, feature_version=(3, 7))
        for bad in ('shell=True', 'rmtree', 'unlink(', 'kubectl', 'redis-cli', 'psql', 'images rm', 'images pull'):
            self.assertNotIn(bad, source)


class ImportTests(unittest.TestCase):
    def test_exact_image_check_including_platform_and_config(self):
        for item in p.ITEMS:
            with patch.object(p.inventory, 'identity', return_value=dict(item)), \
                 patch.object(p.cli, 'command', return_value=(item['image']+'\n').encode()), \
                 patch.object(p.cli, 'cri', return_value={'status': {'id': item['config_digest'], 'pinned': True}}):
                value = p.image_check(item, pinned=True)
                self.assertEqual(value['content_and_unpack'], 'PASS')
                self.assertTrue(value['pinned'])

    def test_reference_conflict_stops_before_content_check(self):
        item = p.ITEMS[0]
        with patch.object(p.inventory, 'identity', return_value=dict(item, root_digest='changed')), \
             patch.object(p.cli, 'command') as cmd:
            with self.assertRaisesRegex(p.verify.CheckFailure, 'imported_identity'): p.image_check(item)
            cmd.assert_not_called()

    def test_incomplete_image_or_unpinned_refused(self):
        item = p.ITEMS[1]
        with patch.object(p.inventory, 'identity', return_value=dict(item)), \
             patch.object(p.cli, 'command', return_value=b''):
            with self.assertRaisesRegex(p.verify.CheckFailure, 'unpack'): p.image_check(item)
        with patch.object(p.inventory, 'identity', return_value=dict(item)), \
             patch.object(p.cli, 'command', return_value=(item['image']+'\n').encode()), \
             patch.object(p.cli, 'cri', return_value={'status': {'id': item['config_digest'], 'pinned': False}}):
            with self.assertRaisesRegex(p.verify.CheckFailure, 'pin_unconfirmed'): p.image_check(item, pinned=True)

    def test_cold_budget_accounts_for_both_full_images_and_reserve(self):
        cap = 316482899968; reserve = (cap+4)//5
        need = reserve+2*sum(x['compressed_layer_bytes']+x['uncompressed_layer_bytes'] for x in p.ITEMS)+2*p.io.GIB
        s = types.SimpleNamespace(f_blocks=cap, f_frsize=1, f_bavail=need, f_flag=0, f_favail=100001)
        with patch.object(p.io, 'safe_dir'), patch.object(Path, 'stat', return_value=types.SimpleNamespace(st_dev=42)), \
             patch.object(p.os, 'statvfs', return_value=s):
            p.disk_budget([Path('/var/lib/containerd')], p.ITEMS)
            s.f_bavail -= 1
            with self.assertRaisesRegex(p.verify.CheckFailure, 'insufficient_imagefs'): p.disk_budget([Path('/var/lib/containerd')], p.ITEMS)
            p.disk_budget([Path('/var/lib/containerd')], [])
        self.assertLess(need/p.io.GIB, 67.35)

    def test_import_flags_are_offline_local_arm64_no_extra_reference(self):
        args = p.cli.import_args(Path('/opt/private/store.oci.tar'))
        self.assertIn('--local', args); self.assertIn('linux/arm64', args)
        for bad in ('--all-platforms', '--digests', '--index-name', '--no-unpack'): self.assertNotIn(bad, args)

    def test_busy_operations_not_killed(self):
        for line in ('ctr ctr images import /private/file', 'buildctl buildctl build', 'nerdctl nerdctl build'):
            with patch.object(p.cli, 'command', return_value=line.encode()), self.assertRaises(p.verify.CheckFailure):
                p.processes_clear()
        with patch.object(p.cli, 'command', return_value=b'python3 python3 script.py\n'): p.processes_clear()


@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'root Linux filesystem checks')
class PrivateFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir='/root'); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.target = self.root/'operation'
        self.addCleanup(patch.stopall)
        patch.object(p, 'ROOT', self.target).start()
        patch.object(p, 'emit').start()
        patch.object(p, 'IMPORTED', []).start()

    def write(self, path, raw): path.write_bytes(raw); path.chmod(0o600)

    def test_real_remote_private_read_protocol_and_receive_password_wiping(self):
        item, data, report = example(); item['node'] = socket.gethostname().split('.')[0]
        src = self.root/'source'; src.mkdir(mode=0o700)
        obj = json.loads(report); obj['identity']['source_node'] = item['node']; obj['archive'] = str(src/'store.oci.tar')
        report = json.dumps(obj).encode(); item['report_sha256'] = hashlib.sha256(report).hexdigest()
        self.write(src/'RESULT.json', report); self.write(src/'store.oci.tar', data)
        self.target.mkdir(mode=0o700)
        password = bytearray(b'fake-sudo-password\n')
        with patch.object(p, 'source_root', return_value=str(src)):
            argv = [sys.executable, '-I', '-B', '-c', p.remote_source(item)]
            with patch.object(p, 'ssh_command', return_value=argv), open(os.devnull, 'w') as tty:
                path = p.receive(item, self.target, password, tty)
        self.assertEqual(path.read_bytes(), data)
        self.assertEqual(password, bytearray(len(password)))
        self.assertEqual((self.target/'EXPORT.json').read_bytes(), report)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_remote_source_rejects_unsafe_file_before_stream(self):
        item, data, report = example(); src = self.root/'source'; src.mkdir(mode=0o700)
        self.write(src/'RESULT.json', report); self.write(src/'store.oci.tar', data)
        (src/'store.oci.tar').chmod(0o666)
        item['node'] = socket.gethostname().split('.')[0]
        with patch.object(p, 'source_root', return_value=str(src)):
            result = subprocess.run([sys.executable, '-I', '-B', '-c', p.remote_source(item)], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 1); self.assertEqual(result.stdout, b'')
        self.assertNotIn(b'fake-sudo', result.stderr)

    def test_archive_name_requires_pinned_containerd_annotation(self):
        item = p.ITEMS[0]; path = self.root/'index.tar'
        for name in (item['image'], 'wrong/image:latest', None):
            index = dict(manifests=[dict(digest=item['root_digest'], annotations={})])
            if name is not None: index['manifests'][0]['annotations']['io.containerd.image.name'] = name
            raw = json.dumps(index).encode()
            with tarfile.open(path, 'w') as tar:
                info = tarfile.TarInfo('index.json'); info.size = len(raw); tar.addfile(info, io.BytesIO(raw))
            if name == item['image']: p.archive_reference(item, path)
            else:
                with self.assertRaisesRegex(p.verify.CheckFailure, 'import_reference'): p.archive_reference(item, path)

    def prepared(self):
        self.target.mkdir(mode=0o700)
        item = p.ITEMS[0]; directory = self.target/item['role']; directory.mkdir(mode=0o700)
        archive = directory/'store.oci.tar'; self.write(archive, b'locked data')
        return item, archive, p.io.private_meta(archive), directory

    def test_import_has_durable_marker_and_ack_and_pin(self):
        item, archive, meta, directory = self.prepared()
        def command(args, **kwargs):
            self.assertTrue((directory/'IMPORT-REQUESTED.json').exists())
            self.assertFalse((directory/'IMPORT-ACK.json').exists())
            self.assertEqual(args, p.cli.import_args(archive)); return b''
        with patch.object(p, 'reference_present', return_value=False), patch.object(p, 'disk_budget'), \
             patch.object(p, 'processes_clear'), patch.object(p, 'image_check', return_value={'pinned': True}), \
             patch.object(p.cli, 'command', side_effect=command) as run:
            p.preload_one(item, archive, meta, [], p.ITEMS)
        self.assertEqual(run.call_count, 1)
        self.assertTrue((directory/'IMPORT-ACK.json').exists())
        self.assertTrue((directory/'PRELOAD-PASS.json').exists())

    def test_uncertain_import_keeps_marker_no_second_call(self):
        item, archive, meta, directory = self.prepared()
        with patch.object(p, 'reference_present', return_value=False), patch.object(p, 'disk_budget'), \
             patch.object(p, 'processes_clear'), patch.object(p.cli, 'command', side_effect=KeyboardInterrupt()) as run:
            with self.assertRaises(KeyboardInterrupt): p.preload_one(item, archive, meta, [], p.ITEMS)
        self.assertEqual(run.call_count, 1); self.assertEqual(p.IMPORTED, ['postgres'])
        self.assertTrue((directory/'IMPORT-REQUESTED.json').exists())
        self.assertFalse((directory/'IMPORT-ACK.json').exists())

    def test_complete_existing_image_is_not_reimported(self):
        item, archive, meta, directory = self.prepared()
        with patch.object(p, 'reference_present', return_value=True), \
             patch.object(p, 'image_check', return_value={'pinned': True}), patch.object(p.cli, 'command') as cmd:
            p.preload_one(item, archive, meta, [], p.ITEMS)
        cmd.assert_not_called(); self.assertFalse((directory/'IMPORT-REQUESTED.json').exists())

    def test_reference_race_conflict_or_archive_change_blocks_import(self):
        item, archive, meta, directory = self.prepared()
        with patch.object(p, 'reference_present', side_effect=[False, True]), patch.object(p, 'disk_budget'), \
             patch.object(p, 'processes_clear'), patch.object(p.cli, 'command') as cmd:
            with self.assertRaisesRegex(p.verify.CheckFailure, 'reference_appeared'): p.preload_one(item, archive, meta, [], p.ITEMS)
            cmd.assert_not_called()
        self.write(archive, b'changed')
        with patch.object(p.cli, 'command') as cmd:
            with self.assertRaisesRegex(p.verify.CheckFailure, 'sealed_archive_changed'): p.preload_one(item, archive, meta, [], p.ITEMS)
            cmd.assert_not_called()

    def test_private_guard_does_not_chmod_existing_paths(self):
        self.target.mkdir(mode=0o700); self.target.chmod(0o777)
        with self.assertRaises(p.verify.CheckFailure): p.record(self.target/'test.json', {})
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o777)

    def test_status_never_mutates_or_transfers_or_prompts(self):
        self.target.mkdir(mode=0o700); p.record(self.target/'STARTED.json', {'test': 1})
        before = (self.target/'STARTED.json').read_bytes()
        with patch.object(p, 'reference_present', return_value=False), patch.object(p, 'pull') as pull, \
             patch.object(p.getpass, 'getpass') as prompt, patch.object(p.cli, 'command') as cmd:
            self.assertEqual(p.status(), 0)
            pull.assert_not_called(); prompt.assert_not_called(); cmd.assert_not_called()
        self.assertEqual((self.target/'STARTED.json').read_bytes(), before)

    def test_once_guard_precedes_all_network_and_credentials(self):
        self.target.mkdir(mode=0o700)
        with patch.object(p.cli, 'command') as cmd, patch.object(p.getpass, 'getpass') as prompt:
            with self.assertRaisesRegex(p.verify.CheckFailure, 'previous_attempt'): p.prepare_once()
            cmd.assert_not_called(); prompt.assert_not_called()

    def test_no_import_until_both_transfers_verified_and_stop_after_first_uncertainty(self):
        events = []
        def pull(item):
            events.append('pull-'+item['role']); return self.target/(item['role']+'.tar'), (1, 2, 3, 4, 5)
        def load(item, *args):
            events.append('import-'+item['role']); raise p.verify.CheckFailure('synthetic_uncertainty')
        with patch.object(p, 'preflight', return_value=([], [])), patch.object(p, 'known_host', return_value=b'pinned\n'), \
             patch.object(p.cli, 'command', return_value=b'key'), patch.object(p, 'pull', side_effect=pull), \
             patch.object(p, 'preload_one', side_effect=load):
            self.assertEqual(p.prepare_once(), 1)
        self.assertEqual(events, ['pull-postgres', 'pull-redis', 'import-postgres'])
        self.assertTrue((self.target/'FAILED.json').exists())

    def test_full_controller_success_then_repeat_rejected_without_writes(self):
        with patch.object(p, 'preflight', return_value=([], [])), patch.object(p, 'known_host', return_value=b'pinned\n'), \
             patch.object(p.cli, 'command', return_value=b'key'), \
             patch.object(p, 'pull', return_value=(self.target/'archive', (1, 2, 3, 4, 5))), \
             patch.object(p, 'preload_one', return_value={'pinned': True}) as load, \
             patch.object(p, 'image_check', return_value={'pinned': True}), patch.object(p, 'disk_budget'), \
             patch.object(p.io, 'check_space'):
            self.assertEqual(p.prepare_once(), 0)
            self.assertEqual(load.call_count, 2)
        result = json.loads((self.target/'RESULT.json').read_bytes())
        self.assertEqual(result['enrichment_store_preload'], 'PASS')
        self.assertFalse(result['ready_for_rollout']); self.assertFalse(result['model_changed'])
        self.assertEqual(result['production_database_connections'], 0)
        before = {f.name: f.read_bytes() for f in self.target.iterdir()}
        with self.assertRaisesRegex(p.verify.CheckFailure, 'previous_attempt'): p.prepare_once()
        self.assertEqual(before, {f.name: f.read_bytes() for f in self.target.iterdir()})

    def test_pin_label_only_after_durable_marker(self):
        item, archive, meta, directory = self.prepared()
        def label(args, **kwargs):
            self.assertTrue((directory/'PIN-REQUESTED.json').exists())
            self.assertEqual(args, p.io.CTR+['images', 'label', item['image'], p.cli.PIN]); return b''
        with patch.object(p, 'reference_present', return_value=True), \
             patch.object(p, 'image_check', side_effect=[{'pinned': False}, {'pinned': False}, {'pinned': True}]), \
             patch.object(p.cli, 'command', side_effect=label) as cmd:
            p.preload_one(item, archive, meta, [], p.ITEMS)
        self.assertEqual(cmd.call_count, 1)
        self.assertFalse((directory/'IMPORT-REQUESTED.json').exists())


if __name__ == '__main__': unittest.main()
