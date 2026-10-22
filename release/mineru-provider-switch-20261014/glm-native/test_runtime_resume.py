import ast
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import resume_runtime_finish_once as r


@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'Linux root-private fixture required')
class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir='/root')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.old = self.root/'old'; self.old.mkdir(mode=0o700)
        self.state = self.root/'old-state'; self.state.mkdir(mode=0o700)
        self.newstate = self.root/'new-state'
        for key, value in (('OLD_ROOT', self.old), ('OLD_STATE', self.state), ('STATE', self.newstate)):
            p = patch.object(r, key, value); p.start(); self.addCleanup(p.stop)
        r.write(self.state/'STARTED.json', {'automatic_retry': False, 'build_root': str(self.old/'rebuild')})
        self.delivered = self.old/'patch'; self.delivered.mkdir(mode=0o700)
        source = Path('/delivered')
        for path in source.iterdir():
            if path.is_file():
                shutil.copyfile(path, self.delivered/path.name)
                (self.delivered/path.name).chmod(0o600)

    def test_exact_interruption_and_patch(self):
        self.assertEqual(r.verify_patch(), self.delivered)
        self.assertEqual(r.original_boundary(), hashlib.sha256((self.state/'STARTED.json').read_bytes()).hexdigest())

    def test_reject_started_binding_change(self):
        (self.state/'STARTED.json').write_text('{"automatic_retry":true}')
        with self.assertRaisesRegex(r.RecoveryFailure, 'binding_changed'): r.original_boundary()

    def test_reject_progress_or_failure_receipts(self):
        for name in ('PREPARED.json', 'FAILED.json', 'EXTRA.json'):
            r.write(self.state/name, {'present': True})
            with self.assertRaisesRegex(r.RecoveryFailure, 'not_started_only'): r.original_boundary()
            (self.state/name).unlink()  # Only our isolated synthetic fixture.

    def test_reject_existing_or_dangling_output(self):
        target = self.old/'rebuild'
        target.symlink_to(self.root/'absent')
        with self.assertRaisesRegex(r.RecoveryFailure, 'previous_output_exists'): r.original_boundary()

    def test_reject_build_log(self):
        (self.old/'build.log').touch()
        with self.assertRaisesRegex(r.RecoveryFailure, 'build_log_exists'): r.original_boundary()

    def test_reject_unsafe_private_permissions(self):
        self.state.chmod(0o777)
        with self.assertRaisesRegex(r.RecoveryFailure, 'unsafe_private_directory'): r.original_boundary()

    def test_reject_patch_hash_drift(self):
        (self.delivered/'runtime_check.py').write_text('not the delivered code')
        with self.assertRaisesRegex(r.RecoveryFailure, 'patch_hash_changed'): r.verify_patch()

    def test_reject_patch_extra_member(self):
        (self.delivered/'extra').touch()
        with self.assertRaisesRegex(r.RecoveryFailure, 'members_changed'): r.verify_patch()

    def test_verified_module_import(self):
        # Do not reuse another test module's helper loaded from the repository.
        previous = sys.modules.pop('repair_sqlite_build', None)
        old_path = list(sys.path)
        try:
            module = r.load_verified(r.verify_patch())
            self.assertEqual(Path(module.safe.__file__).resolve(), self.delivered/'repair_sqlite_build.py')
        finally:
            sys.modules.pop('repair_sqlite_build', None)
            if previous is not None: sys.modules['repair_sqlite_build'] = previous
            sys.path[:] = old_path

    def test_hash_progress_keeps_digest(self):
        file = self.root/'synthetic.tar'
        # Sparse local test data only; no large download or build.
        with file.open('wb') as f: f.truncate(64*1024**2)
        out = io.StringIO()
        with redirect_stdout(out): digest = r.progress_digest(file)
        self.assertEqual(digest, hashlib.sha256(file.read_bytes()).hexdigest())
        events = [json.loads(x) for x in out.getvalue().splitlines()]
        self.assertEqual(events[0]['runtime_resume'], 'HASH_START')
        self.assertEqual(events[-1]['runtime_resume'], 'HASH_READ_COMPLETE')

    def test_original_module_globals_restored_after_interrupt(self):
        original = object()
        module = types.SimpleNamespace(STATE='old', safe=types.SimpleNamespace(digest=original))
        def interrupted():
            self.assertEqual(module.STATE, self.newstate/'preparation')
            self.assertIs(module.safe.digest, r.progress_digest)
            raise KeyboardInterrupt()
        module.main = interrupted
        argv = sys.argv
        with self.assertRaises(KeyboardInterrupt): r.prepare_fresh(module, self.root/'rebuild')
        self.assertIs(sys.argv, argv)
        self.assertIs(module.safe.digest, original)
        self.assertEqual(module.STATE, 'old')

    def test_resume_orchestration_once_and_old_evidence_unchanged(self):
        before = {p.name: p.read_bytes() for p in self.state.iterdir()}
        newroot = self.root/'fresh-output'; newroot.mkdir(mode=0o700)
        stages = []
        def fake_prepare(module, output):
            output.mkdir(mode=0o700); (output/'package').mkdir(mode=0o700)
        def fake_child(command, cwd):
            stages.append(command[-1])
            if command[-1] == 'YES':
                b = cwd.parent/'build1'; b.mkdir(mode=0o700)
                r.write(b/'BUILD-RESULT.json', {'weknora_glm_candidate_build': 'PASS', 'compile_reused': True,
                        'images_imported': False, 'ready_for_rollout': False})
        with patch.object(r, 'processes_clear'), patch.object(r, 'load_verified'), \
             patch.object(r, 'prepare_fresh', side_effect=fake_prepare), \
             patch.object(r, 'child', side_effect=fake_child), \
             patch.object(r.tempfile, 'mkdtemp', return_value=str(newroot)), redirect_stdout(io.StringIO()):
            r.resume()
            with self.assertRaisesRegex(r.RecoveryFailure, 'already_attempted'): r.resume()
        self.assertEqual(stages, ['--check-only', 'YES'])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.state.iterdir()})
        self.assertEqual(json.loads((self.newstate/'RESULT.json').read_bytes())['runtime_resume'], 'PASS')

    def test_interrupt_records_failure_without_touching_old(self):
        newroot = self.root/'new'; newroot.mkdir(mode=0o700)
        original = (self.state/'STARTED.json').read_bytes()
        with patch.object(r, 'processes_clear'), patch.object(r, 'load_verified'), \
             patch.object(r, 'prepare_fresh', side_effect=KeyboardInterrupt), \
             patch.object(r.tempfile, 'mkdtemp', return_value=str(newroot)), redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt): r.resume()
        self.assertEqual((self.state/'STARTED.json').read_bytes(), original)
        value = json.loads((self.newstate/'RESULT.json').read_bytes())
        self.assertEqual(value['runtime_resume'], 'UNCONFIRMED')
        self.assertEqual(value['error_type'], 'KeyboardInterrupt')

    def test_status_absent_is_read_only(self):
        out = io.StringIO()
        with redirect_stdout(out): r.status()
        self.assertFalse(self.newstate.exists())
        self.assertFalse(json.loads(out.getvalue())['state_exists'])

    def test_child_exit_propagates(self):
        with self.assertRaisesRegex(r.RecoveryFailure, 'child_failed'):
            r.child([sys.executable, '-c', 'raise SystemExit(9)'], self.root)


class PortableTests(unittest.TestCase):
    def test_process_inventory_does_not_export_args(self):
        raw = b'100 1 buildctl buildctl --addr private\n101 1 ctr ctr -n k8s.io images import secret.tar\n'
        raw += b'102 1 python3 python3 prepare_runtime_finish.py --output private\n'
        raw += b'103 1 ctr ctr -n k8s.io images ls\n104 1 python3 python3 resume_runtime_finish_once.py\n'
        hits = r.process_hits(raw, 104)
        self.assertEqual([x['pid'] for x in hits], [100, 101, 102])
        self.assertNotIn('private', json.dumps(hits)); self.assertNotIn('secret', json.dumps(hits))

    def test_python37_and_no_erase_or_compile_path(self):
        text = Path(r.__file__).read_text()
        ast.parse(text, feature_version=(3, 7))
        for forbidden in ('shutil.rmtree', '.unlink(', 'go build', 'cargo build', 'kubectl patch', 'images import'):
            self.assertNotIn(forbidden, text)
        self.assertIn("module.main()", text)
        self.assertIn("['--build-only-ack', 'YES']", text)


if __name__ == '__main__':
    unittest.main()
