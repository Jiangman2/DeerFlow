import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import types
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from test_stage_bluegreen16 import load, fake_core, object_for, actual_for, review_for, m, p

r = load('recover_stage_test', 'recover_stage_permissions.py')
HERE = Path(__file__).absolute().parent


def raw(value):
    return (json.dumps(value, sort_keys=True, indent=2)+'\n').encode()


def statrow(mode=0o100777, uid=0, gid=0, links=1, size=5, inode=3, mtime=1, ctime=1):
    return types.SimpleNamespace(st_mode=mode, st_uid=uid, st_gid=gid, st_nlink=links,
        st_size=size, st_dev=1, st_ino=inode, st_mtime_ns=mtime, st_ctime_ns=ctime)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        m.core = p.core = fake_core()
        m.prep = p
        m.core.inv.BASELINES = {}
        self.data = {}
        baseline = {}
        for name in ('deer-flow-mineru-adapter', 'deer-flow-mineru-dispatcher', 'weknora-app', 'weknora-docreader'):
            obj = dict(apiVersion='apps/v1', kind='Deployment', metadata=dict(name=name,
                namespace='deer-flow', uid=name+'-uid', generation=1), spec={'old': name})
            sha = m.core.inv.digest(obj['spec'])
            m.core.inv.BASELINES[name] = (obj['metadata']['uid'], sha)
            baseline[name] = dict(uid=obj['metadata']['uid'], generation=1, spec_sha256=sha)
            self.data[name+'.before.private.json'] = obj
        objects, reviews = [], []
        for name in m.ALLOWED:
            obj = object_for(name)
            answer = actual_for(obj)
            objects.append(obj)
            reviews.append(review_for(obj, answer))
            self.data[name+'.proposed.private.json'] = obj
            self.data[name+'.admitted.private.json'] = answer
        self.data['OBJECTS.proposed.private.json'] = objects
        old = dict(apiVersion='v1', kind='Service', metadata=dict(name=p.WORKER, uid='public-uid',
            resourceVersion='10', namespace='deer-flow'), spec={'type': 'ClusterIP', 'selector': {'app': 'old'}})
        answer = copy.deepcopy(old)
        answer['spec']['selector'] = p.labels('dispatcher')
        self.data['PUBLIC-SERVICE.before.private.json'] = old
        self.data['PUBLIC-SERVICE.forward.admitted.private.json'] = answer
        self.data['PUBLIC-SERVICE.forward.dry-run.private.json'] = p.route_patch(old, p.labels('dispatcher'))
        self.report = dict(mineru_bluegreen16_prepare='PASS', production_workloads_mutated=False,
            ready_for_stage_review=True, initial_plan={'controller': 'ecs-zdz-pro-0012', 'workers': m.NODES, 'total_slots': 7},
            concurrency_plan={'new_initial_slots': 6, 'old_reserved_slots': 10, 'target_slots': 16},
            candidate=m.core.inv.CANDIDATE, dry_runs=reviews, baselines=baseline,
            route_plan=dict(uid='public-uid', original_spec_sha256=m.core.inv.digest(old['spec']),
                proposed_spec_sha256=m.core.inv.digest(answer['spec'])))
        self.data['RESULT.json'] = self.report
        self.pin = patch.object(r, 'PREPARE_SHA', r.sha(raw(self.report)))
        self.pin.start()
        self.addCleanup(self.pin.stop)

    def files(self):
        return {k: raw(v) for k, v in self.data.items()}

    def test_python37_grammar_and_frozen_stage_unchanged(self):
        ast.parse((HERE/'recover_stage_permissions.py').read_bytes(), feature_version=(3, 7))
        self.assertEqual(r.sha((HERE/'stage_bluegreen16.py').read_bytes()), r.STAGE_SHA)
        self.assertEqual(m.PREPARE_SHA, r.sha((HERE/'prepare_bluegreen16.py').read_bytes()))
        self.assertEqual(m.PROBE_SHA, r.sha((HERE/'stage_runtime_probe.py').read_bytes()))

    def test_complete_27_members_pass(self):
        self.assertEqual(len(r.expected_members(m)), 27)
        self.assertEqual(r.validate_snapshots(m, self.files()), self.report)

    def test_report_hash_changed_rejected(self):
        rows = self.files()
        rows['RESULT.json'] += b' '
        with self.assertRaisesRegex(r.RecoveryFailure, 'prepare_report_hash'):
            r.validate_snapshots(m, rows)

    def test_missing_unexpected_member_rejected(self):
        for change in ('add', 'remove'):
            rows = self.files()
            if change == 'add':
                rows['unreviewed.private.json'] = b'{}'
            else:
                del rows['OBJECTS.proposed.private.json']
            with self.subTest(change=change), self.assertRaisesRegex(r.RecoveryFailure, 'unexpected_prepared_members'):
                r.validate_snapshots(m, rows)

    def test_old_baseline_spec_change_rejected(self):
        self.data[p.WORKER+'.before.private.json']['spec']['old'] = 'changed'
        with self.assertRaisesRegex(r.RecoveryFailure, 'baseline_snapshot_changed'):
            r.validate_snapshots(m, self.files())

    def test_old_uid_or_generation_rejected(self):
        obj = self.data[p.WORKER+'.before.private.json']
        for key, value in (('uid', 'changed'), ('generation', 2)):
            prior = obj['metadata'][key]
            obj['metadata'][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(r.RecoveryFailure, 'baseline_snapshot_changed'):
                r.validate_snapshots(m, self.files())
            obj['metadata'][key] = prior

    def test_proposed_spec_change_rejected(self):
        self.data[m.WORKERS[0]+'.proposed.private.json']['spec']['replicas'] = 64
        with self.assertRaisesRegex(r.RecoveryFailure, 'prepared_spec_hash_mismatch'):
            r.validate_snapshots(m, self.files())

    def test_admitted_spec_change_rejected(self):
        self.data[m.WORKERS[0]+'.admitted.private.json']['spec']['replicas'] = 2
        with self.assertRaisesRegex(r.RecoveryFailure, 'prepared_spec_hash_mismatch'):
            r.validate_snapshots(m, self.files())

    def test_wrong_namespace_rejected(self):
        self.data[m.WORKERS[0]+'.proposed.private.json']['metadata']['namespace'] = 'other'
        with self.assertRaisesRegex(r.RecoveryFailure, 'snapshot_object_identity_changed'):
            r.validate_snapshots(m, self.files())

    def test_bundle_difference_rejected(self):
        self.data['OBJECTS.proposed.private.json'] = copy.deepcopy(self.data['OBJECTS.proposed.private.json'])
        self.data['OBJECTS.proposed.private.json'][0]['metadata']['labels']['injected'] = 'yes'
        with self.assertRaisesRegex(r.RecoveryFailure, 'prepared_spec_hash_mismatch'):
            r.validate_snapshots(m, self.files())

    def test_duplicate_bundle_name_rejected(self):
        self.data['OBJECTS.proposed.private.json'][0] = self.data['OBJECTS.proposed.private.json'][1]
        with self.assertRaisesRegex(r.RecoveryFailure, 'object_bundle_names_changed'):
            r.validate_snapshots(m, self.files())

    def test_public_service_spec_uid_or_patch_change_rejected(self):
        original = copy.deepcopy(self.data)
        for change in ('port', 'uid', 'patch'):
            self.data = copy.deepcopy(original)
            if change == 'port':
                self.data['PUBLIC-SERVICE.before.private.json']['spec']['port'] = 123
            elif change == 'uid':
                self.data['PUBLIC-SERVICE.forward.admitted.private.json']['metadata']['uid'] = 'changed'
            else:
                self.data['PUBLIC-SERVICE.forward.dry-run.private.json'].append({'op': 'remove', 'path': '/spec'})
            with self.subTest(change=change), self.assertRaises(r.RecoveryFailure):
                r.validate_snapshots(m, self.files())

    def test_json_duplicate_keys_rejected(self):
        with self.assertRaisesRegex(r.RecoveryFailure, 'duplicate_json_key'):
            r.decode(b'{"a": 1, "a": 2}')

    def test_failed_receipt_requires_all_no_write_evidence(self):
        good = dict(failed_stage='BIND_PREPARE_AND_LIVE_BASELINE', failed_check='unsafe_private_directory',
            cluster_writes_attempted=False, create_requested=[], created=[], last_stage_operation=None,
            existing_workloads_modified=False, public_route_changed=False)
        r.failed_attempt(good)
        for key in good:
            bad = dict(good)
            del bad[key]
            with self.subTest(key=key), self.assertRaises(r.RecoveryFailure):
                r.failed_attempt(bad)

    def test_file_metadata_rejects_symlinks_hardlinks_owners_fifo(self):
        for values in (dict(mode=stat.S_IFLNK|0o777), dict(mode=stat.S_IFIFO|0o600),
                       dict(links=2), dict(uid=1000), dict(gid=1000), dict(size=0),
                       dict(size=r.LIMIT), dict(mode=stat.S_IFREG|0o4600)):
            with self.subTest(values=values), self.assertRaises(r.RecoveryFailure):
                r.file_metadata(statrow(**values))
        r.file_metadata(statrow())
        with self.assertRaises(r.RecoveryFailure):
            r.file_metadata(statrow(), private=True)
        r.file_metadata(statrow(mode=stat.S_IFREG|0o600), private=True)

    def test_read_fd_rejects_content_inode_or_mtime_race(self):
        baseline = statrow(size=5)
        for changed in (statrow(size=5, inode=4), statrow(size=5, mtime=2)):
            with patch.object(r.os, 'fstat', side_effect=[baseline, changed]), \
                 patch.object(r.os, 'stat', return_value=changed), patch.object(r.os, 'lseek'), \
                 patch.object(r.os, 'read', side_effect=[b'12345', b'']), self.assertRaises(r.RecoveryFailure):
                r.read_fd(1, 2, 'x')

    def test_read_fd_checks_path_replacement(self):
        baseline = statrow(size=5)
        with patch.object(r.os, 'fstat', return_value=baseline), \
             patch.object(r.os, 'stat', return_value=statrow(size=5, inode=8)), \
             patch.object(r.os, 'lseek'), patch.object(r.os, 'read', side_effect=[b'12345', b'']), \
             self.assertRaises(r.RecoveryFailure):
            r.read_fd(1, 2, 'x')

    def live_get(self, kind, name=None, selector=None):
        if kind == 'pods':
            return {'items': []}
        if kind == 'deployments' and name in m.core.inv.BASELINES:
            return self.data[name+'.before.private.json']
        if kind == 'services' and name == p.WORKER:
            return self.data['PUBLIC-SERVICE.before.private.json']
        return None

    def test_readonly_reconciliation_calls_only_get(self):
        with patch.object(m, 'get', side_effect=self.live_get) as get:
            r.live_readonly_reconcile(m, self.report)
            self.assertEqual(get.call_count, 15)
            self.assertTrue(all(c.args[0] in ('pods', 'deployments', 'services') for c in get.call_args_list))

    def test_existing_new_name_or_pod_stops_replay(self):
        for target in ('pods', m.WORKERS[0]):
            def getter(kind, name=None, selector=None):
                if target == 'pods' and kind == 'pods':
                    return {'items': [{'metadata': {'uid': 'unreviewed'}}]}
                if name == target:
                    return {'metadata': {'uid': 'unreviewed'}}
                return self.live_get(kind, name, selector)
            with patch.object(m, 'get', side_effect=getter), self.assertRaisesRegex(r.RecoveryFailure, 'already_exist'):
                r.live_readonly_reconcile(m, self.report)

    def test_changed_live_public_route_stops(self):
        def getter(kind, name=None, selector=None):
            obj = copy.deepcopy(self.live_get(kind, name, selector))
            if kind == 'services' and name == p.WORKER:
                obj['spec']['selector'] = {'app': 'changed'}
            return obj
        with patch.object(m, 'get', side_effect=getter), self.assertRaisesRegex(r.RecoveryFailure, 'live_public_route_changed'):
            r.live_readonly_reconcile(m, self.report)

    def test_changed_live_baseline_stops(self):
        def getter(kind, name=None, selector=None):
            obj = copy.deepcopy(self.live_get(kind, name, selector))
            if kind == 'deployments' and name == p.WORKER:
                obj['spec']['replicas'] = 12
            return obj
        with patch.object(m, 'get', side_effect=getter), self.assertRaisesRegex(r.RecoveryFailure, 'live_baseline_changed'):
            r.live_readonly_reconcile(m, self.report)

    def test_stage_once_routes_status_and_restores_argv(self):
        initial = sys.argv
        for flag, expected in ((True, '--status'), (False, '--stage-only')):
            stage = types.SimpleNamespace(main=lambda: (sys.argv[1], sys.argv[0]))
            result = r.stage_once(stage, status=flag)
            self.assertEqual(result, (expected, str(Path(r.__file__).absolute())))
            self.assertIs(sys.argv, initial)

    def test_repeat_operation_rejected(self):
        with patch.object(r, 'safe_dir'), patch.object(Path, 'exists', return_value=True), \
             self.assertRaisesRegex(r.RecoveryFailure, 'operation_exists'):
            r.ensure_new(r.NEW_STAGE)

    def test_dangling_operation_symlink_rejected(self):
        with patch.object(r, 'safe_dir'), patch.object(Path, 'exists', return_value=False), \
             patch.object(Path, 'is_symlink', return_value=True), self.assertRaises(r.RecoveryFailure):
            r.ensure_new(r.NEW_STAGE)

    def test_status_does_not_repair_or_create(self):
        stage = types.SimpleNamespace(core=MagicMock())
        with patch.object(sys, 'argv', ['recover.py', '--status']), \
             patch.object(r.os, 'geteuid', return_value=0, create=True), \
             patch.object(r.os, 'umask'), patch.object(r.socket, 'gethostname', return_value='datax-01'), \
             patch.object(r, 'load_stage', return_value=stage), patch.object(Path, 'exists', return_value=False), \
             patch.object(r, 'stage_once', return_value=0) as invoke, \
             patch.object(r, 'repair_snapshot') as repair, patch.object(Path, 'mkdir') as mkdir, patch.object(r, 'emit'):
            self.assertEqual(r.main(), 0)
            invoke.assert_called_once_with(stage, status=True)
            repair.assert_not_called()
            mkdir.assert_not_called()
            stage.core.save.assert_not_called()

    def repair_environment(self, stack, mutate=False, extra=False):
        raw_files = self.files()
        names = sorted(raw_files)
        descriptors = {name: i+10 for i, name in enumerate(names)}
        by_fd = {fd: name for name, fd in descriptors.items()}
        metadata = {fd: statrow(size=len(raw_files[name]), inode=fd) for name, fd in descriptors.items()}
        directory = statrow(mode=stat.S_IFDIR|0o777, size=4096, inode=99)
        metadata[99] = directory
        counts = {}
        def read(fd, directory_fd, name, private=False):
            self.assertEqual(directory_fd, 99)
            self.assertEqual(by_fd[fd], name)
            counts[name] = counts.get(name, 0)+1
            content = raw_files[name]
            if mutate and name == names[1] and counts[name] == 2:
                content += b' '
            return content, copy.copy(metadata[fd])
        def chmod(fd, mode):
            self.assertIn(fd, metadata)
            self.assertEqual(mode, 0o700 if fd == 99 else 0o600)
            metadata[fd] = copy.copy(metadata[fd])
            metadata[fd].st_mode = (stat.S_IFDIR if fd == 99 else stat.S_IFREG)|mode
            metadata[fd].st_ctime_ns += 1
        def opened(name, flags, **kwargs):
            if str(name) == str(r.PREPARED):
                self.assertFalse(kwargs)
                return 99
            self.assertEqual(kwargs, {'dir_fd': 99})
            return descriptors[name]
        stack.enter_context(patch.object(r, 'safe_dir'))
        stack.enter_context(patch.object(Path, 'lstat', side_effect=lambda: copy.copy(metadata[99])))
        stack.enter_context(patch.object(r.os, 'O_DIRECTORY', 0x10000, create=True))
        stack.enter_context(patch.object(r.os, 'O_NOFOLLOW', 0x20000, create=True))
        stack.enter_context(patch.object(r.os, 'O_NONBLOCK', 0x40000, create=True))
        stack.enter_context(patch.object(r.os, 'open', side_effect=opened))
        stack.enter_context(patch.object(r.os, 'fstat', side_effect=lambda fd: copy.copy(metadata[fd])))
        stack.enter_context(patch.object(r.os, 'fchmod', side_effect=chmod, create=True))
        stack.enter_context(patch.object(r.os, 'fsync'))
        stack.enter_context(patch.object(r.os, 'listdir', return_value=names+(['extra'] if extra else [])))
        stack.enter_context(patch.object(r, 'read_fd', side_effect=read))
        closed = stack.enter_context(patch.object(r.os, 'close'))
        return metadata, closed

    def test_permission_repair_27_fds_bytes_retained_handles_closed(self):
        with ExitStack() as stack:
            metadata, closed = self.repair_environment(stack)
            audit = {}
            self.assertEqual(r.repair_snapshot(m, audit), self.report)
            self.assertEqual(audit['permission_repair'], 'PASS')
            self.assertEqual(audit['full_private_file_historical_integrity'], 'NOT_ASSERTED')
            self.assertEqual(len(audit['files_sha256']), 27)
            self.assertEqual(closed.call_count, 28)
            self.assertEqual(stat.S_IMODE(metadata[99].st_mode), 0o700)
            self.assertTrue(all(stat.S_IMODE(s.st_mode) == 0o600 for fd, s in metadata.items() if fd != 99))

    def test_concurrent_file_change_stops_without_passing(self):
        with ExitStack() as stack:
            metadata, closed = self.repair_environment(stack, mutate=True)
            audit = {}
            with self.assertRaisesRegex(r.RecoveryFailure, 'file_changed_before_permission_repair'):
                r.repair_snapshot(m, audit)
            self.assertNotEqual(audit.get('permission_repair'), 'PASS')
            self.assertEqual(closed.call_count, 28)

    def test_extra_file_stops_before_opening_any_file(self):
        with ExitStack() as stack:
            metadata, closed = self.repair_environment(stack, extra=True)
            with self.assertRaisesRegex(r.RecoveryFailure, 'unexpected_prepared_members'):
                r.repair_snapshot(m, {})
            self.assertEqual(closed.call_count, 1)
            self.assertTrue(all(stat.S_IMODE(s.st_mode) == 0o777 for fd, s in metadata.items() if fd != 99))

    def test_snapshot_content_mismatch_never_chmods_files(self):
        self.data[m.WORKERS[0]+'.admitted.private.json']['spec']['replicas'] = 5
        with ExitStack() as stack:
            metadata, closed = self.repair_environment(stack)
            with self.assertRaisesRegex(r.RecoveryFailure, 'prepared_spec_hash_mismatch'):
                r.repair_snapshot(m, {})
            self.assertEqual(closed.call_count, 28)
            self.assertTrue(all(stat.S_IMODE(s.st_mode) == 0o777 for fd, s in metadata.items() if fd != 99))

    def test_main_repair_failure_cannot_start_stage(self):
        stage = types.SimpleNamespace(core=MagicMock())
        with patch.object(sys, 'argv', ['recover.py', '--recover-and-stage']), \
             patch.object(r.os, 'geteuid', return_value=0, create=True), patch.object(r.os, 'umask'), \
             patch.object(r.socket, 'gethostname', return_value='datax-01'), \
             patch.object(r, 'load_stage', return_value=stage), patch.object(r, 'ensure_new'), \
             patch.object(r, 'locked', return_value=b'{}'), patch.object(r, 'failed_attempt'), \
             patch.object(Path, 'iterdir', return_value=iter([Path('RESULT.json')])), \
             patch.object(Path, 'mkdir'), patch.object(r, 'emit'), \
             patch.object(r, 'repair_snapshot', side_effect=r.RecoveryFailure('prepared_spec_hash_mismatch')), \
             patch.object(r, 'stage_once') as invoke:
            self.assertEqual(r.main(), 1)
            invoke.assert_not_called()
            row = stage.core.save.call_args.args[2]
            self.assertFalse(row['stage_invocation_permitted'])
            self.assertEqual(row['failed_check'], 'prepared_spec_hash_mismatch')


if __name__ == '__main__':
    unittest.main()
