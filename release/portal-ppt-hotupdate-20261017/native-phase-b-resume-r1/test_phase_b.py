"""Small Linux fixtures for the successor boundary; no daemon or network calls."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import tarfile
import tempfile
import types
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
PREP = HERE.parent


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


G = load(HERE / 'resume_phase_b.py', 'guard')
F = load(PREP / 'native-preload-attempt-r3/derived_target_code.py', 'frozen_target')
B = load(PREP / 'native-preload-r2/preload_native_r2.py', 'frozen_helpers')
F.B = B


@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'Linux FD fixtures')
class PhaseBFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.target = self.root / 'target'; self.target.mkdir(mode=0o700)
        self.control = self.root / 'control'; self.control.mkdir(mode=0o700)

    def tearDown(self):
        self.temp.cleanup()

    def put(self, path, raw):
        path.write_bytes(raw); path.chmod(0o600)

    def checkpoint(self):
        profile = json.loads((PREP / 'native-preload-r1/PROFILE.json').read_bytes())
        fail = (PREP / 'native-preload-attempt-r3-local-resume/TARGET-FAIL.json').read_bytes()
        receipts = {'STARTED.json': {}, 'PREPARED.json': {}, 'RUN-REQUESTED.json': {},
                    'FAIL.json': json.loads(fail), 'PHASE-A-REQUESTED.json': dict(mode='FULL_OCI_CONTENT_ONLY', automatic_retry=False),
                    'PHASE-A-PASS.json': dict(all_fixed_outer_blobs_present=True, all_refs_and_config_bound=True)}
        for row in profile['images']:
            receipts[row['role'] + '.SEALED.json'] = dict(role=row['role'], archive_sha256=row['archive_sha256'], archive_bytes=row['archive_bytes'], metadata_only_bytes=10240)
            receipts[row['role'] + '.PHASE-A-REQUESTED.json'] = dict(role=row['role'], phase='PHASE-A', automatic_retry=False)
            receipts[row['role'] + '.PHASE-A-PASS.json'] = dict(returncode=0, stdout_bytes=10, stderr_bytes=0, seconds=0.5)
        pins = {}
        for name, data in receipts.items():
            raw = G.encoded(data)
            self.put(self.target / name, raw)
            pins[name] = dict(sha256=G.sha(raw), bytes=len(raw))
        wire = b'{"fixture":true}\n'; self.put(self.control / 'PLAN-WIRE.json', wire)
        module = types.SimpleNamespace(B=B, unwire_plan=mock.Mock(return_value={'fixture': True}), validate_plan=mock.Mock())
        return profile, pins, wire, module

    def test_frozen_code_failure_and_unreleased_default_pins(self):
        raw = (PREP / 'native-preload-attempt-r3/derived_target_code.py').read_bytes()
        self.assertEqual((len(raw), G.sha(raw)), (G.TARGET_CODE_BYTES, G.TARGET_CODE_SHA))
        raw = (PREP / 'native-preload-attempt-r3-local-resume/TARGET-FAIL.json').read_bytes()
        self.assertEqual((len(raw), G.sha(raw)), (G.FAIL_BYTES, G.FAIL_SHA))
        self.assertEqual(G.RELEASE_STATE, 'PREPARATION_NOT_DELIVERED')
        self.assertEqual(G.CHECKPOINT_SHA256, {})

    def test_exact_checkpoint_rejects_changed_extra_and_phase_b_receipts(self):
        profile, pins, wire, module = self.checkpoint()
        with mock.patch.multiple(G, TARGET=self.target, CONTROL=self.control, CHECKPOINT_SHA256=pins, WIRE_SHA=G.sha(wire), WIRE_BYTES=len(wire)):
            plan, receipts = G.inspect_checkpoint(module, profile)
            self.assertTrue(plan['fixture']); self.assertEqual(len(receipts), 24)
            old = (self.target / 'PHASE-A-PASS.json').read_bytes()
            self.put(self.target / 'PHASE-A-PASS.json', old + b' ')
            with self.assertRaisesRegex(ValueError, 'checkpoint_receipt_changed'):
                G.inspect_checkpoint(module, profile)
            self.put(self.target / 'PHASE-A-PASS.json', old)
            extra = self.target / 'PHASE-B-REQUESTED.json'; extra.symlink_to(self.root / 'missing')
            with self.assertRaisesRegex(ValueError, 'original_checkpoint_advanced_or_changed'):
                G.inspect_checkpoint(module, profile)

    def test_sealed_inode_fd_survives_user_parent_rename_and_symlink_substitution(self):
        opt = self.root / 'opt'; opt.mkdir(mode=0o755)
        name = F.state_paths(G.OP)['sealed_name']
        sealed = opt / name; sealed.mkdir(mode=0o700)
        value = sealed.stat(); expected = (value.st_dev, value.st_ino, value.st_uid, 0o700)
        with mock.patch.object(G, 'SEALED_IDENTITY', expected):
            fd = G.open_sealed(F, str(self.root))
            moved = opt / 'moved'; sealed.rename(moved); sealed.symlink_to(moved, target_is_directory=True)
            try:
                self.assertEqual(os.fstat(fd).st_ino, value.st_ino)
                with self.assertRaises(OSError):
                    G.open_sealed(F, str(self.root))
            finally:
                os.close(fd)

    def metadata(self, extra=None, corrupt=False):
        diff = 'sha256:' + 'd' * 64; blob = 'sha256:' + 'b' * 64
        cfg = G.encoded(dict(architecture='arm64', os='linux', rootfs=dict(diff_ids=[diff])))
        cfgdig = 'sha256:' + G.sha(cfg)
        manifest = G.encoded(dict(config=dict(digest=cfgdig), layers=[dict(digest=blob, size=123)]))
        mdig = 'sha256:' + G.sha(manifest)
        row = dict(role='frontend', basename='fixture.oci.tar', tag='deerflow.local/fixture:test', manifest_digest=mdig, config_digest=cfgdig)
        index = G.encoded(dict(schemaVersion=2, manifests=[dict(digest=mdig, annotations={
            'io.containerd.image.name': row['tag'], 'org.opencontainers.image.ref.name': 'test'})]))
        parts = {'oci-layout': G.encoded({'imageLayoutVersion': '1.0.0'}), 'index.json': index,
                 'blobs/sha256/' + mdig[7:]: manifest, 'blobs/sha256/' + cfgdig[7:]: cfg + (b' ' if corrupt else b'')}
        if extra:
            parts[extra] = b'{}'
        path = self.root / (row['basename'] + '.metadata.tar')
        with tarfile.open(path, 'w', format=tarfile.USTAR_FORMAT) as archive:
            for name, raw in parts.items():
                member = tarfile.TarInfo(name); member.size = len(raw); member.mode = 0o600
                archive.addfile(member, io.BytesIO(raw))
        path.chmod(0o600)
        image = dict(outer_blobs={mdig: len(manifest), cfgdig: len(cfg), blob: 123},
                     layers=[dict(blob_digest=blob, compressed_bytes=123, diff_id=diff)])
        return path, row, image

    def test_exact_four_metadata_members_verified_without_full_archive_or_layers(self):
        path, row, image = self.metadata()
        directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fd = G.metadata_fd(F, directory, row, image, {'metadata_only_bytes': path.stat().st_size}, 65536)
            try:
                import fcntl
                self.assertEqual(fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE, os.O_RDONLY)
                self.assertEqual(os.lseek(fd, 0, os.SEEK_CUR), 0)
            finally:
                os.close(fd)
        finally:
            os.close(directory)

    def test_extra_metadata_member_and_mutated_config_are_rejected(self):
        directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for kwargs, error in (({'extra': 'manifest.json'}, 'metadata_exact_four_members'), ({'corrupt': True}, 'sealed_plan_header_binding')):
                path, row, image = self.metadata(**kwargs)
                with self.assertRaisesRegex(ValueError, error):
                    G.metadata_fd(F, directory, row, image, {'metadata_only_bytes': path.stat().st_size}, 65536)
        finally:
            os.close(directory)

    def test_metadata_symlink_and_hardlink_are_rejected(self):
        path, row, image = self.metadata(); size = path.stat().st_size
        other = self.root / 'other'; path.rename(other)
        directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            path.symlink_to(other)
            with self.assertRaises(OSError):
                G.metadata_fd(F, directory, row, image, {'metadata_only_bytes': size}, 65536)
            path.unlink(); os.link(other, path)
            with self.assertRaisesRegex(ValueError, 'fd_regular_required'):
                G.metadata_fd(F, directory, row, image, {'metadata_only_bytes': size}, 65536)
        finally:
            os.close(directory)

    def phase_b_module(self):
        profile = json.loads((PREP / 'native-preload-r1/PROFILE.json').read_bytes())
        helper = types.SimpleNamespace(collision_check=mock.Mock(), complete_row=mock.Mock(return_value=({'complete': True}, {})),
                                      host_projection=mock.Mock(return_value={'state': 'COMPLETE'}), postgres_cache=mock.Mock(return_value={'state': 'COMPLETE'}), Failure=B.Failure)
        module = types.SimpleNamespace(B=helper, REPORT=dict(image_rows=[], phase_a='PASS', phase_b='NOT_REQUESTED'),
                    all_content_present=mock.Mock(), verify_references=mock.Mock(), storage_check=mock.Mock(), write_receipt=mock.Mock(), request_import=mock.Mock())
        return module, profile

    def test_space_failure_before_phase_b_never_imports_or_writes_requested(self):
        module, profile = self.phase_b_module(); module.storage_check.side_effect = ValueError('r3_storage_budget_insufficient')
        with self.assertRaisesRegex(ValueError, 'r3_storage_budget_insufficient'):
            G.continue_phase_b(module, profile, {}, 7, {})
        module.request_import.assert_not_called(); module.write_receipt.assert_not_called()
        self.assertEqual(module.REPORT['phase_b'], 'NOT_REQUESTED')

    def test_only_phase_b_each_unpack_has_fresh_checks_and_final_six_completion(self):
        module, profile = self.phase_b_module(); metadata = {row['role']: i + 10 for i, row in enumerate(profile['images'])}
        G.continue_phase_b(module, profile, {'fixed': True}, 7, metadata)
        self.assertEqual(module.request_import.call_count, 6)
        self.assertTrue(all(call.args[0] == 'PHASE-B' for call in module.request_import.call_args_list))
        self.assertEqual(module.all_content_present.call_count, 7); self.assertEqual(module.verify_references.call_count, 7)
        self.assertEqual(module.storage_check.call_count, 7); self.assertEqual(module.B.collision_check.call_count, 7)
        self.assertEqual(module.B.complete_row.call_count, 12)
        self.assertEqual(module.REPORT['status'], 'COMPLETE_PRELOAD'); self.assertEqual(module.REPORT['phase_b'], 'PASS')
        self.assertEqual(len(module.REPORT['image_rows']), 6)

    def test_failure_in_unpack_stops_without_replaying_or_next_role(self):
        module, profile = self.phase_b_module(); module.request_import.side_effect = ValueError('import_nonzero')
        metadata = {row['role']: 10 for row in profile['images']}
        with self.assertRaisesRegex(ValueError, 'import_nonzero'):
            G.continue_phase_b(module, profile, {}, 7, metadata)
        self.assertEqual(module.request_import.call_count, 1); module.B.complete_row.assert_not_called()
        self.assertEqual(module.REPORT['phase_b'], 'REQUESTED')

    def test_main_holds_real_common_lock_preserves_old_receipts_and_claims_once(self):
        import fcntl
        profile = json.loads((PREP / 'native-preload-r1/PROFILE.json').read_bytes())
        old = self.target / 'FAIL.json'; self.put(old, b'original failure preserved\n')
        lockpath = self.root / 'common.lock'; once = self.root / 'new-once.json'
        def common_lock():
            fd = os.open(lockpath, os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        module = types.SimpleNamespace(B=B, common_lock=common_lock, IMPORT_REQUESTED=False, REPORT=None)
        module.base_report = lambda op: dict(phase_a='NOT_REQUESTED', phase_b='NOT_REQUESTED', image_rows=[], ready_for_rollout=False, native_runtime_passed=False)
        module.write_receipt = lambda name, value: B.write_new(module.STATE / name, G.encoded(value))
        results = []
        module.handoff_report = lambda raw: results.append(json.loads(raw)) or '/fixture/REPORT.json'
        def continue_checked(*args):
            fd = os.open(lockpath, os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fd)
            self.assertNotEqual(module.STATE, self.target)
            module.IMPORT_REQUESTED = True
            module.REPORT.update(phase_b='PASS', status='COMPLETE_PRELOAD', image_rows=[{}] * 6)
        plan = dict(images=profile['images'], allocation={'maximum_metadata_import_bytes': 10240})
        receipts = {row['role'] + '.SEALED.json': {} for row in profile['images']}
        handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGHUP)}
        try:
            with mock.patch.multiple(G, RELEASE_STATE='REVIEWED_FOR_MANUAL_EXECUTION', ROOT=self.root, ONCE=once), \
                    mock.patch.object(G, 'host_identity', return_value={}), \
                    mock.patch.object(G, 'load_target', return_value=(module, profile)), \
                    mock.patch.object(G, 'inspect_checkpoint', return_value=(plan, receipts)), \
                    mock.patch.object(G, 'open_sealed', side_effect=lambda m: os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)), \
                    mock.patch.object(G, 'metadata_fd', side_effect=lambda *a: os.open(old, os.O_RDONLY)), \
                    mock.patch.object(G, 'continue_phase_b', side_effect=continue_checked) as continuation, \
                    mock.patch.object(B, 'progress'), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(G.run(), 0)
                continuation.assert_called_once()
                self.assertEqual(old.read_bytes(), b'original failure preserved\n')
                self.assertEqual(G.run(), 1)
                self.assertEqual(continuation.call_count, 1)
            self.assertFalse(results[0]['ready_for_rollout'])
            self.assertFalse(results[0]['native_runtime_passed'])
            self.assertFalse(results[0]['phase_a_repeated'])
            self.assertEqual(results[1]['error_code'], 'phase_b_continuation_already_started')
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)


if __name__ == '__main__':
    unittest.main(verbosity=2)
