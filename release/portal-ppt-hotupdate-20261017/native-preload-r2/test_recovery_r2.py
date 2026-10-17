"""Narrow source recovery tests; no images, SSH or existing r1 file mutations."""
import ast
import copy
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent
PARENT = ROOT.parent
R1 = PARENT / 'native-preload-r1' / 'preload_native.py'
R2 = ROOT / 'preload_native_r2.py'
spec = importlib.util.spec_from_file_location('preload_recovery', R2)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
PROFILE = json.loads((PARENT / 'native-preload-r1' / 'PROFILE.json').read_bytes())
FAIL_RAW = (PARENT / 'native-preload-attempt-r1' / 'REPORT.json').read_bytes()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        p.REPORT, p.STATE, p.TARGET_IMPORT_REQUESTED = {}, Path('/not-used-fixture'), False

    def observations(self):
        return [dict(path='/opt', device=64785, capacity_bytes=3219650514944,
                     available_bytes=750326616064, available_inodes=284844672),
                dict(path='/var/lib', device=64512, capacity_bytes=316482899968,
                     available_bytes=54902648832, available_inodes=107232652)]

    def check_space(self, rows):
        with mock.patch.object(p, 'disk', side_effect=rows), mock.patch.object(p, 'safe_dir') as safe, mock.patch.object(p, 'write_new') as saved:
            result = p.source_storage_check(PROFILE)
        safe.assert_called_once_with('/opt')
        self.assertEqual(saved.call_args[0][0].name, 'SOURCE-STORAGE.json')
        self.assertEqual(p.parse(saved.call_args[0][1]), result)
        return result

    def test_observed_separate_opt_passes_original_full_reserve_and_root_small_budget(self):
        result = self.check_space(self.observations())
        self.assertEqual(result['sealed']['required_bytes'], 655233464525)
        self.assertEqual(result['sealed']['available_bytes'] - result['sealed']['required_bytes'], 95093151539)
        self.assertEqual(result['control']['required_bytes'], 4 * p.GIB + 16 * 1024 ** 2)
        self.assertLess(result['control']['required_bytes'], result['control']['available_bytes'])
        old_root_need = PROFILE['archive_total_bytes'] + p.reserve(result['control']) + 2 * p.GIB
        self.assertEqual(old_root_need, 74599941530)
        self.assertGreater(old_root_need, result['control']['available_bytes'])

    def test_actual_storage_observation_survives_byte_inode_or_same_device_failure(self):
        for failure in ('bytes', 'inode', 'device', 'control'):
            with self.subTest(failure=failure):
                rows = self.observations()
                if failure == 'bytes':
                    rows[0]['available_bytes'] = 1
                elif failure == 'inode':
                    rows[0]['available_inodes'] = 1
                elif failure == 'device':
                    rows[0]['device'] = rows[1]['device']
                else:
                    rows[1]['available_bytes'] = 1
                p.REPORT = {}
                with mock.patch.object(p, 'disk', side_effect=rows), mock.patch.object(p, 'safe_dir'), mock.patch.object(p, 'write_new') as saved:
                    with self.assertRaises(p.Failure):
                        p.source_storage_check(PROFILE)
                self.assertIn('source_storage_before', p.REPORT)
                saved.assert_called_once()
                self.assertEqual(p.parse(saved.call_args[0][1]), p.REPORT['source_storage_before'])

    def test_target_paths_ssh_import_and_budget_math_unchanged_ast(self):
        before = {n.name: n for n in ast.parse(R1.read_text(encoding='utf-8')).body if isinstance(n, ast.FunctionDef)}
        after = {n.name: n for n in ast.parse(R2.read_text(encoding='utf-8')).body if isinstance(n, ast.FunctionDef)}
        changed = {'target_budget', 'import_target', 'base_report', 'distribute', 'main'}
        for name in before:
            if name not in changed:
                self.assertEqual(ast.dump(before[name], include_attributes=False), ast.dump(after[name], include_attributes=False), name)
        # Import loop differs only in the explicit requested flag.
        imported = copy.deepcopy(after['import_target'])
        imported.body[0].names.remove('TARGET_IMPORT_REQUESTED')
        for statement in imported.body:
            if isinstance(statement, ast.For):
                statement.body = [n for n in statement.body if not (
                    isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'TARGET_IMPORT_REQUESTED' or
                    isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and t.value.id == 'REPORT' and
                    isinstance(t.slice, ast.Constant) and t.slice.value == 'target_import_requested' for t in n.targets))]
        self.assertEqual(ast.dump(before['import_target'], include_attributes=False), ast.dump(imported, include_attributes=False))
        self.assertEqual(p.PROFILE_SHA, 'fdeef64d8c1ced80251f733e0e83cad589032712c1b8a476affc1797105002d2')

    def test_source_stage_and_monitor_use_opt_while_lock_and_target_paths_stay_r1(self):
        module = ast.parse(R2.read_text(encoding='utf-8'))
        distribute = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == 'distribute')
        text = ast.get_source_segment(R2.read_text(encoding='utf-8'), distribute)
        self.assertIn("fresh(Path('/opt/portal-ppt-native-sealed-r2-' + token))", text)
        self.assertIn("monitor_paths(['/opt'])", text)
        self.assertNotIn("monitor_paths(['/var/lib'])", text)
        source = R2.read_text(encoding='utf-8')
        self.assertIn("'/var/lib/portal-ppt-native-preload-r1.lock'", source)
        self.assertIn("profile_path = OFFLINE_ROOT / 'PROFILE.json' if args.distribute else", source)
        self.assertEqual(p.paths('a' * 32)['state'], Path('/var/lib/portal-ppt-native-preload-target-' + 'a' * 32))

    @unittest.skipUnless(os.name == 'posix', 'POSIX actual nofollow source validation')
    def test_exact_known_failure_and_eight_offline_inputs_reject_unknown_evidence_or_tools(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            failure_dir, offline = root / 'old-state', root / 'offline'
            failure_dir.mkdir()
            offline.mkdir()
            fail = failure_dir / 'FAIL.json'
            fail.write_bytes(FAIL_RAW)
            (failure_dir / 'STARTED.json').write_bytes(b'{}')
            tools = []
            for name in ('preload_native.py', 'PROFILE.json', 'RUNBOOK.txt'):
                raw = ('tiny-' + name).encode()
                (offline / name).write_bytes(raw)
                tools.append((name, len(raw), p.sha(raw)))
            profile = dict(images=[])
            for n in range(5):
                name = 'archive%d.tar' % n
                (offline / name).write_bytes(b'fixture')
                profile['images'].append(dict(windows_upload=True, source_path=str(offline / name), basename=name,
                                             archive_bytes=7, archive_sha256=p.sha(b'fixture')))
            profile['images'].append(dict(windows_upload=False))
            with mock.patch.object(p, 'PRIOR_FAIL', fail), mock.patch.object(p, 'OFFLINE_ROOT', offline), mock.patch.object(p, 'OFFLINE_TOOLS', tools), mock.patch.object(p, 'safe_dir'):
                result = p.validate_recovery_inputs(profile)
                self.assertEqual(len(result['offline_inputs']), 8)
                self.assertEqual(result['prior_failure_sha256'], p.PRIOR_FAIL_SHA)
                (failure_dir / 'TARGET-PREPARE.json').write_bytes(b'{}')
                with self.assertRaisesRegex(p.Failure, 'prior_attempt_has_unreviewed_evidence'):
                    p.validate_recovery_inputs(profile)
                (failure_dir / 'TARGET-PREPARE.json').unlink()
                (offline / 'RUNBOOK.txt').write_bytes(b'changed')
                with self.assertRaises(p.Failure):
                    p.validate_recovery_inputs(profile)
                fail.write_bytes(b'{}')
                with self.assertRaisesRegex(p.Failure, 'prior_failure_binding_mismatch'):
                    p.validate_recovery_inputs(profile)

    def test_target_budget_failure_still_retains_actual_numbers(self):
        row = dict(path='/var/lib', device=1, capacity_bytes=100 * p.GIB, available_bytes=1, available_inodes=1)
        with mock.patch.object(p, 'capture', return_value=p.encoded(dict(imageFilesystems=[dict(fsId=dict(mountpoint='/var/lib'))]))), mock.patch.object(p, 'disk', return_value=row):
            with self.assertRaises(p.Failure):
                p.target_budget(PROFILE)
        actual = p.REPORT['target_storage_observations'][0]['filesystems'][0]
        self.assertEqual(actual['available_bytes'], 1)
        self.assertGreater(actual['required_bytes'], actual['available_bytes'])

    def test_source_failure_flag_false_and_unknown_remote_import_true(self):
        source = R2.read_text(encoding='utf-8')
        self.assertFalse(p.base_report('a' * 32)['target_import_requested'])
        self.assertIn('image_import_may_be_partial=TARGET_IMPORT_REQUESTED', source)
        self.assertTrue(p.import_request_may_have_started(dict(operation='other', target_import_requested=False), 'known'))
        self.assertTrue(p.import_request_may_have_started(dict(operation='known'), 'known'))
        self.assertTrue(p.import_request_may_have_started(dict(operation='known', target_import_requested=0), 'known'))
        self.assertFalse(p.import_request_may_have_started(dict(operation='known', target_import_requested=False), 'known'))
        # The source flag appears immediately before, never before prepare/scp.
        fn = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'distribute')
        assign = next(i for i, n in enumerate(fn.body) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'TARGET_IMPORT_REQUESTED' for t in n.targets))
        self.assertIn("'REMOTE_IMPORT'", ast.get_source_segment(source, fn.body[assign + 2]))

    def test_python37_parse_and_single_file_profile_source(self):
        ast.parse(R2.read_text(encoding='utf-8'), feature_version=(3, 7))
        self.assertEqual(p.OFFLINE_ROOT, Path('/home/cnooc/portal-ppt-native-preload-20261017-r1'))
        self.assertEqual(len(FAIL_RAW), 1140)
        self.assertEqual(p.sha(FAIL_RAW), p.PRIOR_FAIL_SHA)

    @unittest.skipUnless(os.name == 'posix', 'POSIX exclusive marker fixture')
    def test_one_shot_marker_real_exclusive_nofollow_preserves_first(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / 'attempt.json'
            with mock.patch.object(p, 'safe_dir'):
                p.write_new(marker, b'first-attempt')
                with self.assertRaises(FileExistsError):
                    p.write_new(marker, b'not-an-automatic-retry')
                link = Path(temp) / 'link.json'
                link.symlink_to(marker)
                with self.assertRaises(FileExistsError):
                    p.write_new(link, b'no-link-write')
            self.assertEqual(marker.read_bytes(), b'first-attempt')
            self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)

    def test_existing_recovery_marker_gives_precise_error_without_overwrite(self):
        with mock.patch.object(p, 'CODE_SNAPSHOT', b'fixture-script'), mock.patch.object(p, 'write_new', side_effect=FileExistsError) as write:
            with self.assertRaisesRegex(p.Failure, 'recovery_attempt_already_started'):
                p.claim_recovery_marker(dict(prior_operation=p.PRIOR_OPERATION))
        write.assert_called_once()


if __name__ == '__main__':
    unittest.main()
