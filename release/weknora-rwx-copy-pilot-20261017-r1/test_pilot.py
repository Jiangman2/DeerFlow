import contextlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import time
import types
import unittest
from unittest.mock import patch
import pilot as m


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir='/qualify')
        self.base = Path(self.tmp.name)
        self.src = self.base/'src'; self.dst = self.base/'dst'
        for path in (self.src, self.dst):
            path.mkdir(mode=0o770); path.chmod(0o770); os.chown(path, 1000, 1000)
        self.sfd = os.open(str(self.src), os.O_RDONLY | os.O_DIRECTORY)
        self.dfd = os.open(str(self.dst), os.O_RDONLY | os.O_DIRECTORY)

    def tearDown(self):
        os.close(self.sfd); os.close(self.dfd); self.tmp.cleanup()

    def put(self, rel='a', data=b'synthetic-only', mode=0o644):
        path = self.src/rel
        path.parent.mkdir(parents=True, exist_ok=True)
        for p in (path.parent,) + tuple(path.parent.parents):
            if p == self.base: break
            os.chown(p, 1000, 1000); p.chmod(0o770)
        path.write_bytes(data); os.chown(path, 1000, 1000); path.chmod(mode)
        return (tuple(rel.split('/')), m.identity(path.stat()), m.band(len(data)))

    def invoke(self, item, pacer=None):
        os.setegid(1000); os.seteuid(1000)
        try:
            return m.copy_one(self.sfd, self.dfd, item, pacer or m.Pacer(rate=10**12), time.monotonic()+5)
        finally:
            os.seteuid(0); os.setegid(0)

    def test_copy_real_content_and_private_destination(self):
        item = self.put('nested/a', b'abcd'*200000)
        self.assertEqual(self.invoke(item), 800000)
        self.assertEqual((self.dst/'nested/a').read_bytes(), b'abcd'*200000)
        self.assertEqual(stat.S_IMODE((self.dst/'nested/a').stat().st_mode), 0o600)
        self.assertEqual((self.src/'nested/a').stat().st_mtime_ns, item[1][3])

    def test_empty_file(self):
        self.assertEqual(self.invoke(self.put(data=b'')), 0)

    def test_file_symlink_rejected(self):
        item = self.put()
        (self.src/'a').rename(self.src/'other')
        (self.src/'a').symlink_to('other')
        with self.assertRaises(OSError): self.invoke(item)
        self.assertEqual(list(self.dst.iterdir()), [])

    def test_ancestor_symlink_rejected(self):
        item = self.put('nest/a')
        (self.src/'nest').rename(self.src/'real')
        (self.src/'nest').symlink_to('real')
        with self.assertRaises(OSError): self.invoke(item)

    def test_hardlink_rejected(self):
        item = self.put()
        os.link(str(self.src/'a'), str(self.src/'another'))
        with self.assertRaisesRegex(m.Failure, 'source_changed'): self.invoke(item)

    def test_wrong_owner_rejected(self):
        item = self.put(); os.chown(self.src/'a', 0, 0)
        with self.assertRaises((m.Failure, PermissionError)): self.invoke(item)

    def test_modified_source_before_copy(self):
        item = self.put(); (self.src/'a').write_bytes(b'changed')
        with self.assertRaisesRegex(m.Failure, 'before_copy'): self.invoke(item)

    def test_mutation_during_copy_fails_without_deletion(self):
        item = self.put(data=b'A'*500000)
        class Mutator:
            def consume(_, count):
                fd = os.open('a', os.O_WRONLY | os.O_APPEND, dir_fd=self.sfd)
                try: os.write(fd, b'X')
                finally: os.close(fd)
        with self.assertRaisesRegex(m.Failure, 'during_copy'): self.invoke(item, Mutator())
        self.assertTrue((self.dst/'a').exists())
        self.assertLessEqual((self.dst/'a').stat().st_size, item[1][2])

    def test_destination_exists_never_overwritten(self):
        item = self.put(); (self.dst/'a').write_bytes(b'keep')
        with self.assertRaises(FileExistsError): self.invoke(item)
        self.assertEqual((self.dst/'a').read_bytes(), b'keep')

    def test_destination_symlink_never_followed(self):
        item = self.put(); outside = self.base/'outside'; outside.write_bytes(b'keep')
        (self.dst/'a').symlink_to(outside)
        with self.assertRaises(FileExistsError): self.invoke(item)
        self.assertEqual(outside.read_bytes(), b'keep')

    def test_destination_parent_symlink_rejected(self):
        item = self.put('nested/a'); (self.dst/'nested').symlink_to(self.src)
        with self.assertRaises(OSError): self.invoke(item)

    def test_parent_traversal_rejected(self):
        _, ident, idx = self.put()
        with self.assertRaises(m.Failure): self.invoke((('..', 'a'), ident, idx))

    def test_expired_deadline(self):
        item = self.put()
        with self.assertRaisesRegex(m.Failure, 'deadline'):
            m.copy_one(self.sfd, self.dfd, item, m.Pacer(), time.monotonic()-1)

    def test_source_readback_verification_detects_corruption(self):
        item = self.put(data=b'A'*100)
        calls = []
        class Corruptor:
            def consume(_, count):
                calls.append(count)
                if len(calls) == 2:
                    fd = os.open('a', os.O_WRONLY | os.O_TRUNC, dir_fd=self.dfd)
                    try: os.write(fd, b'B'*100)
                    finally: os.close(fd)
        with self.assertRaises(m.Failure): self.invoke(item, Corruptor())

    def test_descendant_mount_guard(self):
        item = self.put('nested/a')
        with patch.object(m, 'fd_mount_id', side_effect=[1, 2]):
            with self.assertRaisesRegex(m.Failure, 'mount'): self.invoke(item)

    def test_sample_caps_and_no_content_reads(self):
        for n in range(30): self.put('n'+str(n), b'A'*(n+1))
        (self.src/'link').symlink_to('/etc/shadow')
        os.mkfifo(str(self.src/'fifo'))
        with patch.object(m, 'MIN_AGE', -1), patch.object(m, 'QUOTAS', (1, 4, 4, 4, 4)), \
             patch.object(m, 'BUDGETS', (0, 80, 0, 0, 0)), patch.object(m.os, 'read', side_effect=AssertionError('content read')):
            chosen, summary = m.sample(self.sfd, lambda x: None)
        self.assertLessEqual(len(chosen), 4)
        self.assertLessEqual(sum(x[1][2] for x in chosen), 80)
        self.assertGreaterEqual(summary['skipped_ineligible_or_vanished'], 2)

    def test_sample_too_recent_is_not_success(self):
        self.put()
        with self.assertRaisesRegex(m.Failure, 'empty'): m.sample(self.sfd, lambda x: None)

    def test_scan_entry_deadline_fails_closed(self):
        self.put()
        with patch.object(m, 'SCAN_ENTRIES', 0):
            with self.assertRaisesRegex(m.Failure, 'scan_limit'): m.sample(self.sfd, lambda x: None)

    def test_sample_scan_time_deadline(self):
        self.put()
        with patch.object(m, 'SCAN_SECONDS', 0):
            with self.assertRaisesRegex(m.Failure, 'scan_limit'): m.sample(self.sfd, lambda x: None)

    def test_source_permission_change_is_rejected(self):
        item = self.put(); (self.src/'a').chmod(0o666)
        with self.assertRaises(m.Failure): self.invoke(item)

    def test_private_receipt_rejects_unsafe_permissions(self):
        p = self.base/'RESULT'; p.write_text('{}'); p.chmod(0o666)
        with self.assertRaises(m.Failure): m.private_read(p)

    def test_private_receipt_rejects_symlink(self):
        p = self.base/'RESULT'; p.symlink_to('/etc/passwd')
        with self.assertRaises(OSError): m.private_read(p)

    def test_save_once_and_progress_replace(self):
        p = self.base/'RESULT'
        m.save(p, {'a': 1})
        with self.assertRaises(FileExistsError): m.save(p, {'a': 2})
        m.save(self.base/'PROGRESS', {'b': 1}, replace=True)
        m.save(self.base/'PROGRESS', {'b': 2}, replace=True)
        self.assertEqual(json.loads(m.private_read(self.base/'PROGRESS')), {'b': 2})

    def test_start_never_replays_existing_state(self):
        with patch.object(m, 'STATE', self.base), patch.object(m, 'command') as cmd:
            with self.assertRaisesRegex(m.Failure, 'previous_attempt'): m.start()
            cmd.assert_not_called()

    def test_start_once_pins_script_and_background_arguments(self):
        state = self.base/'new-state'
        with patch.object(m, 'STATE', state), patch.object(m, 'verify_audit', return_value={'bound': True}), \
             patch.object(m, 'resources', return_value={}), patch.object(m, 'executable', side_effect=lambda n: '/usr/bin/'+n), \
             patch.object(m, 'private_read', return_value=b'# synthetic only\n'), \
             patch.object(m, 'command', return_value=b'') as cmd, contextlib.redirect_stdout(io.StringIO()):
            m.start()
        args = cmd.call_args[0][0]
        self.assertIn('--property=Restart=no', args)
        self.assertIn('--propagation', args); self.assertIn('private', args)
        self.assertNotIn('--wait', args)
        self.assertEqual((state/'pilot.py').read_bytes(), b'# synthetic only\n')
        self.assertTrue((state/'START-ACK.json').exists())

    def test_uncertain_service_start_not_retried(self):
        state = self.base/'new-state'
        with patch.object(m, 'STATE', state), patch.object(m, 'verify_audit', return_value={}), \
             patch.object(m, 'resources', return_value={}), patch.object(m, 'executable', side_effect=lambda n: '/bin/'+n), \
             patch.object(m, 'private_read', return_value=b'# test\n'), \
             patch.object(m, 'command', side_effect=m.Failure('uncertain')) as cmd:
            with self.assertRaises(m.Failure): m.start()
            with self.assertRaisesRegex(m.Failure, 'previous_attempt'): m.start()
            self.assertEqual(cmd.call_count, 1)
        self.assertTrue((state/'START-REQUESTED.json').exists())
        self.assertFalse((state/'START-ACK.json').exists())

    def test_audit_hash_mismatch(self):
        with patch.object(m, 'private_read', return_value=b'{}'):
            with self.assertRaisesRegex(m.Failure, 'hash'): m.verify_audit()

    def test_sample_caps_enforced_during_copy(self):
        self.put('a'); self.put('b')
        vfs = types.SimpleNamespace(f_flag=os.ST_RDONLY, f_bavail=200*1024**3, f_frsize=1, f_favail=200000)
        original_sample = m.sample
        def select_sample(fd, report):
            with patch.object(m, 'MAX_FILES', 5000): return original_sample(fd, report)
        with patch.object(m, 'STATE', self.base), patch.object(m, 'MIN_AGE', -1), \
             patch.object(m, 'MAX_FILES', 1), patch.object(m, 'sample', side_effect=select_sample), \
             patch.object(m.os, 'fstatvfs', return_value=vfs), contextlib.redirect_stdout(io.StringIO()):
            result = m.run_worker(self.sfd, self.dfd, 'c'*32)
        self.assertEqual(result['failed_check'], 'hard_copy_budget')
        self.assertEqual(len(list((self.dst/('.weknora-copy-pilot-'+'c'*32)).iterdir())), 1)

    def test_copy_requires_readonly_source(self):
        self.put()
        vfs = types.SimpleNamespace(f_flag=0, f_bavail=200*1024**3, f_frsize=1, f_favail=200000)
        with patch.object(m, 'STATE', self.base), patch.object(m.os, 'fstatvfs', return_value=vfs), \
             contextlib.redirect_stdout(io.StringIO()):
            result = m.run_worker(self.sfd, self.dfd, 'd'*32)
        self.assertEqual(result['failed_check'], 'business_source_not_readonly')
        self.assertEqual(list(self.dst.iterdir()), [])

    def test_low_nfs_capacity_stops_before_data_write(self):
        self.put()
        vfs = types.SimpleNamespace(f_flag=os.ST_RDONLY, f_bavail=1, f_frsize=1, f_favail=200000)
        with patch.object(m, 'STATE', self.base), patch.object(m.os, 'fstatvfs', return_value=vfs), \
             contextlib.redirect_stdout(io.StringIO()):
            result = m.run_worker(self.sfd, self.dfd, 'e'*32)
        self.assertEqual(result['failed_check'], 'nfs_headroom_low')
        self.assertEqual(list(self.dst.iterdir()), [])

    def test_status_only_reads_receipts_and_service(self):
        m.save(self.base/'RESULT.json', {'pilot': 'PASS'})
        with patch.object(m, 'STATE', self.base), patch.object(m, 'executable', return_value='/bin/systemctl'), \
             patch.object(m, 'command', return_value=b'ActiveState=inactive\nResult=success\n') as cmd, \
             patch.object(m, 'save', side_effect=AssertionError('write')), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            m.status()
        self.assertIn('READ_ONLY_STORED_EVIDENCE', out.getvalue())
        self.assertEqual(cmd.call_args[0][0][1], 'show')

    def test_real_uid1000_worker_and_opath_destination(self):
        # Linux fork/drop is real; fixture is synthetic. Mount RO and large NFS
        # statvfs are mocked because offline container has no CAP_SYS_ADMIN/NFS.
        item = self.put('n/a', b'A'*200000)
        dest = os.open(str(self.dst), os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
        vfs = os.fstatvfs
        def stats(fd):
            s = vfs(fd)
            return types.SimpleNamespace(f_flag=s.f_flag | os.ST_RDONLY, f_bavail=200*1024**3,
                                         f_frsize=1, f_favail=200000)
        try:
            with patch.object(m, 'STATE', self.base), patch.object(m, 'MIN_AGE', -1), \
                 patch.object(m.os, 'fstatvfs', side_effect=stats), contextlib.redirect_stdout(io.StringIO()):
                result = m.run_worker(self.sfd, dest, 'a'*32)
        finally: os.close(dest)
        self.assertEqual(result['pilot'], 'PASS', result)
        target = self.dst/('.weknora-copy-pilot-'+'a'*32)/'n/a'
        self.assertEqual(target.read_bytes(), b'A'*200000)
        self.assertEqual(target.stat().st_uid, 1000)
        self.assertEqual(target.stat().st_gid, 1000)
        self.assertEqual(result['verified_files'], 1)
        self.assertEqual(result['payload_io_bytes'], 400000)

    def test_real_uid_worker_rejects_nonempty_target(self):
        self.put(); (self.dst/'existing').write_bytes(b'keep')
        vfs = types.SimpleNamespace(f_flag=os.ST_RDONLY, f_bavail=200*1024**3, f_frsize=1, f_favail=200000)
        with patch.object(m, 'STATE', self.base), patch.object(m.os, 'fstatvfs', return_value=vfs), \
             contextlib.redirect_stdout(io.StringIO()):
            result = m.run_worker(self.sfd, self.dfd, 'b'*32)
        self.assertEqual(result['pilot'], 'UNCONFIRMED')
        self.assertEqual(result['failed_check'], 'target_export_not_empty_review_required')
        self.assertEqual((self.dst/'existing').read_bytes(), b'keep')


class Logic(unittest.TestCase):
    def test_budget_constants(self):
        self.assertEqual(sum(m.QUOTAS), 5000)
        self.assertEqual(sum(m.BUDGETS), 1024**3)

    def test_band_edges(self):
        self.assertEqual([m.band(n) for n in (0, 65535, 65536, 1048576, 16*m.MIB, 64*m.MIB, 64*m.MIB+1)],
                         [0, 1, 2, 3, 4, 4, None])

    def test_pacer_has_no_idle_credit(self):
        now, sleeps = [0.0], []
        def sleep(n): sleeps.append(n); now[0] += n
        p = m.Pacer(100, lambda: now[0], sleep)
        p.consume(100); now[0] += 100; p.consume(100)
        self.assertEqual(sleeps, [1, 1]); self.assertEqual(p.total, 200)

    def test_safe_error_never_prints_filename(self):
        e = OSError(13, 'private_doc_name API_KEY test')
        self.assertNotIn('private_doc', json.dumps(m.failure(e)))
        self.assertEqual(m.failure(e)['errno'], 13)

    def rows(self):
        return [dict(point='/src', fstype='xfs', source='/dev/test', options={'ro'}),
                dict(point='/dst', fstype='nfs4', source=m.EXPORT,
                     options={'rw','vers=4.1','hard','nodev','nosuid','noexec'})]

    def test_exact_mounts(self):
        m.check_mounts(self.rows(), Path('/src'), Path('/dst'))

    def test_wrong_export_rejected(self):
        r = self.rows(); r[1]['source'] += 'different'
        with self.assertRaises(m.Failure): m.check_mounts(r, Path('/src'), Path('/dst'))

    def test_soft_mount_rejected(self):
        r = self.rows(); r[1]['options'].add('soft')
        with self.assertRaises(m.Failure): m.check_mounts(r, Path('/src'), Path('/dst'))

    def test_writable_source_rejected(self):
        r = self.rows(); r[0]['options'] = {'rw'}
        with self.assertRaises(m.Failure): m.check_mounts(r, Path('/src'), Path('/dst'))

    def test_submount_rejected(self):
        r = self.rows(); r.append(dict(point='/src/sub'))
        with self.assertRaises(m.Failure): m.check_mounts(r, Path('/src'), Path('/dst'))

    def test_mountinfo_parser(self):
        r = m.mount_rows('43 25 0:11 / /dst rw,nodev,nosuid,noexec - nfs4 '+m.EXPORT+' rw,vers=4.1,hard\n')
        self.assertEqual(r[0]['source'], m.EXPORT)

    def test_same_mount_namespace_rejected(self):
        with patch.object(m.os, 'readlink', return_value='mnt:[1]'):
            with self.assertRaises(m.Failure): m.isolated_namespace()

    def test_shared_mount_propagation_rejected(self):
        with patch.object(m.os, 'readlink', side_effect=['mnt:[2]', 'mnt:[1]']), \
             patch.object(m, 'mount_rows', return_value=[dict(propagation=['shared:1'])]):
            with self.assertRaises(m.Failure): m.isolated_namespace()


if __name__ == '__main__':
    unittest.main()
