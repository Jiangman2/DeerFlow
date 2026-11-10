"""Offline two-file release boundary tests. No production, credential or HTTP use."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


BASE = Path(__file__).resolve().parents[1]
SOURCE = BASE / 'deploy/enterprise/energy_chatfix_update_child.py'
SPEC = importlib.util.spec_from_file_location('energy_chatfix_update_child_tested', SOURCE)
child = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(child)
OLD_SKILL = BASE / 'release/energy-market-released-20261110/baseline/skill'
NEW_SKILL = BASE / 'skills/public/energy-market-daily'
OPERATION = '1234567890abcdef1234567890abcdef'


def payload():
    files = {name: (NEW_SKILL / name).read_bytes() for name in child.ORDER}
    return dict(files={name: base64.b64encode(raw).decode() for name, raw in files.items()},
                sha256={name: child.sha(raw) for name, raw in files.items()})


class UpdateChildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='energy-chatfix-child-test-')
        self.addCleanup(temporary.cleanup)
        self.top = Path(temporary.name)
        self.live = self.top / 'skills/public/energy-market-daily'
        # The upstream/archive are unchanged; restore only the two pinned old
        # entry files, without relying on a developer's sibling worktree.
        shutil.copytree(NEW_SKILL, self.live)
        for name, expected in child.OLD.items():
            raw = (OLD_SKILL / name).read_bytes()
            self.assertEqual(child.sha(raw), expected)
            (self.live / name).write_bytes(raw)
        self.temp = self.top / 'tmp'
        self.temp.mkdir()
        self.root_patch = patch.object(child, 'ROOT', self.live)
        self.temp_patch = patch.object(child, 'TEMP', self.temp)
        self.root_patch.start()
        self.temp_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.addCleanup(self.temp_patch.stop)
        self.inputs = payload()
        self.original = child.inventory(self.live)

    def stage(self):
        # The real browser is qualified separately on the approved ARM64 runtime.
        with patch.object(child, 'smoke', return_value=dict(test_count=5, test_fixture=True)):
            result = child.stage(OPERATION, self.inputs)
        return result, dict(stage_manifest_sha256=result['stage_manifest_sha256'],
                            sha256=self.inputs['sha256'])

    def test_exact_old_original_seal_is_real(self):
        self.assertEqual(len(child.verify_original(self.live)), 158)
        self.assertEqual({name: self.original[name] for name in child.ORDER}, child.OLD)

    def test_stage_changes_only_temporary_two_files_and_seals_manifest(self):
        before = child.identity(self.live)
        result, binding = self.stage()
        workspace, release = child.paths(OPERATION)
        self.assertEqual(result['status'], 'STAGED_CHATFIX_ACCEPTED')
        self.assertEqual(child.identity(self.live), before)
        self.assertEqual(child.inventory(self.live), self.original)
        self.assertFalse(release.exists())
        self.assertEqual(child.sha((workspace / 'MANIFEST.json').read_bytes()), binding['stage_manifest_sha256'])
        changed = [name for name, digest in child.inventory(workspace / 'skill').items()
                   if digest != self.original[name]]
        self.assertEqual(set(changed), set(child.ORDER))

    def test_stage_refuses_extra_file_and_no_directory_is_created(self):
        self.inputs['files']['upstream/app/fix.py'] = 'eA=='
        with self.assertRaisesRegex(ValueError, 'TWO_FILES_REQUIRED'):
            child.stage(OPERATION, self.inputs)
        self.assertFalse(child.paths(OPERATION)[0].exists())

    def test_stage_refuses_digest_mismatch_before_any_write(self):
        self.inputs['sha256']['SKILL.md'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'PAYLOAD_HASH'):
            child.stage(OPERATION, self.inputs)
        self.assertFalse(child.paths(OPERATION)[0].exists())

    def test_stage_refuses_oversize_candidate(self):
        self.inputs['files']['SKILL.md'] = 'x' * (child.LIMITS['SKILL.md'] * 2 + 1)
        with self.assertRaisesRegex(ValueError, 'PAYLOAD_SIZE'):
            child.stage(OPERATION, self.inputs)

    def test_stage_refuses_changed_original(self):
        target = self.live / 'upstream/.gitignore'
        target.write_bytes(target.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'ORIGINAL_TREE_CHANGED'):
            child.stage(OPERATION, self.inputs)
        self.assertFalse(child.paths(OPERATION)[0].exists())

    def test_stage_refuses_changed_old_wrapper(self):
        target = self.live / 'SKILL.md'
        target.write_bytes(target.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'OLD_WRAPPER_CHANGED'):
            child.stage(OPERATION, self.inputs)

    def test_failed_synthetic_stage_has_no_publication_manifest(self):
        with patch.object(child, 'smoke', side_effect=ValueError('SYNTHETIC_FAILURE')):
            with self.assertRaisesRegex(ValueError, 'SYNTHETIC_FAILURE'):
                child.stage(OPERATION, self.inputs)
        workspace, release = child.paths(OPERATION)
        self.assertFalse((workspace / 'MANIFEST.json').exists())
        self.assertFalse(release.exists())
        self.assertEqual(child.inventory(self.live), self.original)

    def test_real_offline_smoke_reaches_browser_after_four_real_guards(self):
        # Real entry original verification, init, status, sample guard and mocked
        # collector failure run before this deliberately unavailable browser.
        with patch.object(child, 'BROWSER', str(self.top / 'absent-browser')):
            with self.assertRaisesRegex(ValueError, 'APPROVED_HEADLESS_MISSING'):
                child.stage(OPERATION, self.inputs)
        workspace, _ = child.paths(OPERATION)
        meta = json.loads((workspace / 'projects/fixed-reproduction/PROJECT.json').read_bytes())
        self.assertEqual(meta['mode'], 'reproduce')
        self.assertEqual(meta['publication_date'], '2026-11-07')
        self.assertEqual(child.inventory(self.live), self.original)

    def test_publish_preserves_live_directory_other_files_modes_and_backups(self):
        before_id = child.identity(self.live)
        modes = {name: stat.S_IMODE((self.live / name).stat().st_mode) for name in child.ORDER}
        _, binding = self.stage()
        result = child.publish(OPERATION, binding)
        workspace, release = child.paths(OPERATION)
        self.assertEqual(result['status'], 'TWO_FILE_UPDATE_ACCEPTED')
        self.assertEqual(result['applied'], list(child.ORDER))
        self.assertEqual(child.identity(self.live), before_id)
        self.assertEqual(child.inventory(self.live), dict(self.original, **self.inputs['sha256']))
        for name in child.ORDER:
            self.assertEqual(child.sha((release / 'backup' / name).read_bytes()), child.OLD[name])
            self.assertEqual(stat.S_IMODE((self.live / name).stat().st_mode), modes[name])
        self.assertEqual((release / 'MANIFEST.json').read_bytes(), (workspace / 'MANIFEST.json').read_bytes())
        journal = json.loads((release / child.JOURNAL_NAME).read_bytes())
        self.assertEqual(journal['status'], 'TWO_FILE_UPDATE_ACCEPTED')
        self.assertFalse(journal['replacement_may_have_applied'])
        self.assertFalse(journal['automatic_rollback'])

    def test_verify_is_readonly_after_publish(self):
        _, binding = self.stage()
        child.publish(OPERATION, binding)
        files = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in self.top.rglob('*') if p.is_file()}
        result = child.verify(OPERATION, binding)
        self.assertEqual(result['status'], 'CHATFIX_LIVE_FILES_VERIFIED')
        self.assertEqual(files, {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in files})

    def test_publish_refuses_staged_tamper(self):
        _, binding = self.stage()
        candidate = child.paths(OPERATION)[0] / 'skill/SKILL.md'
        candidate.write_bytes(candidate.read_bytes() + b'tamper')
        with self.assertRaisesRegex(ValueError, 'STAGED_FILES_CHANGED'):
            child.publish(OPERATION, binding)
        self.assertEqual(child.inventory(self.live), self.original)

    def test_publish_refuses_live_changes_after_stage(self):
        _, binding = self.stage()
        (self.live / 'unexpected.txt').write_text('later operation', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'LIVE_BASELINE_CHANGED'):
            child.publish(OPERATION, binding)
        self.assertFalse(child.paths(OPERATION)[1].exists())

    def test_publish_owner_mismatch_stops_before_either_live_replacement(self):
        _, binding = self.stage()
        actual_attributes = child.file_attributes
        def different_created_owner(path):
            value = actual_attributes(path)
            if Path(path).name == 'candidate-1':
                value['uid'] += 1
            return value
        with patch.object(child, 'file_attributes', side_effect=different_created_owner):
            with self.assertRaisesRegex(child.PublicationFailure, 'NEW_FILE_OWNER_MISMATCH') as caught:
                child.publish(OPERATION, binding)
        self.assertEqual(caught.exception.publication['applied'], [])
        self.assertFalse(caught.exception.publication['replacement_may_have_applied'])
        self.assertEqual(child.inventory(self.live), self.original)

    def test_publish_detects_attribute_change_after_stage(self):
        _, binding = self.stage()
        actual_attributes = child.file_attributes
        def changed_mode(path):
            value = actual_attributes(path)
            if Path(path) == self.live / 'SKILL.md':
                value['mode'] ^= stat.S_IWUSR
            return value
        with patch.object(child, 'file_attributes', side_effect=changed_mode):
            with self.assertRaisesRegex(child.PublicationFailure, 'OLD_FILE_ATTRIBUTES_CHANGED'):
                child.publish(OPERATION, binding)
        self.assertEqual(child.inventory(self.live), self.original)

    def test_host_manifest_binding_is_required(self):
        _, binding = self.stage()
        binding['stage_manifest_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'STAGE_MANIFEST_CHANGED'):
            child.publish(OPERATION, binding)

    def test_failed_second_replacement_preserves_first_and_precise_journal(self):
        _, binding = self.stage()
        actual_replace = os.replace
        def fail_second(source, destination):
            if Path(destination) == self.live / 'SKILL.md':
                raise OSError('TEST_PRIVATE_SENTINEL')
            return actual_replace(source, destination)
        with patch.object(child.os, 'replace', side_effect=fail_second):
            with self.assertRaises(child.PublicationFailure) as caught:
                child.publish(OPERATION, binding)
        self.assertNotIn('TEST_PRIVATE_SENTINEL', str(caught.exception))
        self.assertEqual(caught.exception.publication['applied'], ['scripts/entry.py'])
        self.assertTrue(caught.exception.publication['replacement_may_have_applied'])
        tree = child.inventory(self.live)
        self.assertEqual(tree['scripts/entry.py'], self.inputs['sha256']['scripts/entry.py'])
        self.assertEqual(tree['SKILL.md'], child.OLD['SKILL.md'])
        release = child.paths(OPERATION)[1]
        journal = json.loads((release / child.JOURNAL_NAME).read_bytes())
        self.assertEqual(journal['status'], 'PUBLICATION_INCOMPLETE')
        self.assertEqual(journal['next_file'], 'SKILL.md')
        with self.assertRaises(ValueError):
            child.publish(OPERATION, binding)
        for name in child.ORDER:
            self.assertEqual(child.sha((release / 'backup' / name).read_bytes()), child.OLD[name])

    def test_existing_operation_cannot_be_reentered(self):
        _, binding = self.stage()
        with self.assertRaisesRegex(ValueError, 'OPERATION_ALREADY_EXISTS'):
            child.stage(OPERATION, self.inputs)
        child.publish(OPERATION, binding)
        with self.assertRaises(ValueError):
            child.publish(OPERATION, binding)

    def test_symlink_original_refused(self):
        target = self.live / 'upstream/.gitignore'
        other = self.top / 'outside.txt'
        other.write_bytes(target.read_bytes())
        target.unlink()
        try:
            target.symlink_to(other)
        except OSError:
            self.skipTest('symlink creation is unavailable on this platform')
        with self.assertRaisesRegex(ValueError, 'SYMLINK_REFUSED'):
            child.stage(OPERATION, self.inputs)

    @unittest.skipUnless(sys.platform.startswith('linux') and os.path.isfile(child.BROWSER),
                         'requires the approved Linux ARM64 browser runtime')
    def test_real_headless_fixed_original_render_then_publish(self):
        result = child.stage(OPERATION, self.inputs)
        self.assertEqual(result['checks']['test_count'], 5)
        self.assertEqual(result['checks']['png_dimensions'][0], 780)
        binding = dict(stage_manifest_sha256=result['stage_manifest_sha256'], sha256=self.inputs['sha256'])
        self.assertEqual(child.publish(OPERATION, binding)['status'], 'TWO_FILE_UPDATE_ACCEPTED')


class ChildCommandTests(unittest.TestCase):
    def test_invalid_action_returns_bounded_json_failure(self):
        completed = subprocess.run([sys.executable, '-I', '-B', str(SOURCE), 'unexpected', OPERATION],
                                   input=b'{}', stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stderr, b'')
        report = json.loads(completed.stdout)
        self.assertEqual(report['error_code'], 'ACTION_ARGUMENTS')
        self.assertEqual(report['status'], 'INCOMPLETE')
        self.assertFalse(report['credentials_read'])

    def test_untrusted_exception_text_is_not_exported(self):
        completed = subprocess.run([sys.executable, '-I', '-B', str(SOURCE), 'stage', OPERATION],
                                   input=b'{"private":"TEST_SECRET_SENTINEL",}',
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        self.assertEqual(completed.returncode, 1)
        self.assertNotIn(b'TEST_SECRET_SENTINEL', completed.stdout + completed.stderr)
        self.assertEqual(json.loads(completed.stdout)['error_code'], 'LOCAL_FAILURE_REDACTED')


if __name__ == '__main__':
    unittest.main()
