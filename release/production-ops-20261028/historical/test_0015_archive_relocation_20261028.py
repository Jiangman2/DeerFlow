import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('relocation', Path(__file__).with_name('relocate-portal-0015-offline-archives-20261028-r1.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@unittest.skipUnless(sys.platform == 'linux', 'Linux dirfd, xattr and /proc semantics')
class Tests(unittest.TestCase):
    def setUp(self):
        self.src = tempfile.TemporaryDirectory(dir='/tmp')
        self.dst = tempfile.TemporaryDirectory(dir='/dev/shm')
        self.addCleanup(self.src.cleanup)
        self.addCleanup(self.dst.cleanup)
        self.file = Path(self.src.name) / 'old.oci.tar'
        self.file.write_bytes(b'archive bytes to preserve' * 100)
        self.virtual = '/home/cnooc/old.oci.tar'
        self.fd = os.open(self.dst.name, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.fd)
        self.row = m.pin(self.virtual, self.file.stat())
        self.events = []
        real_parent = m.parent_fd
        def parent(path):
            return real_parent(str(self.file)) if path == self.virtual else real_parent(path)
        for key, value in dict(DEST=self.dst.name, SOURCE_DEVICE=self.file.stat().st_dev,
                               DEST_DEVICE=os.fstat(self.fd).st_dev, MIN_AGE=0, MIN_BYTES=0,
                               COPY_RATE=0, READ_RATE=0, DEADLINE=0, PLAN=[self.row]).items():
            p = patch.object(m, key, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(m, 'parent_fd', parent)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(m.os, 'fstatvfs', return_value=types.SimpleNamespace(f_bavail=100*1024**3, f_frsize=1))
        p.start()
        self.addCleanup(p.stop)

    def run_move(self, check=lambda s, p, d: None, journal=None):
        return m.relocate(self.row, self.fd, journal or self.events.append, check)

    def original_intact(self):
        self.assertFalse(self.file.is_symlink())
        self.assertEqual(self.file.read_bytes(), b'archive bytes to preserve' * 100)

    def test_verified_move_preserves_path_bytes_mode_owner_and_xattrs(self):
        self.file.chmod(0o640)
        os.setxattr(str(self.file), 'user.test', b'kept')
        self.row = m.pin(self.virtual, self.file.stat())
        result = self.run_move()
        self.assertTrue(self.file.is_symlink())
        self.assertEqual(self.file.read_bytes(), b'archive bytes to preserve' * 100)
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o640)
        self.assertEqual(os.getxattr(str(self.file), 'user.test'), b'kept')
        self.assertEqual(result['status'], 'RELOCATED_VERIFIED_ORIGINAL_PATH_PRESERVED')
        self.assertEqual(self.events[0]['phase'], 'VERIFIED_COPY_BEFORE_REPLACE')

    def test_metadata_drift_refuses(self):
        self.row['inode'] += 1
        with self.assertRaises(m.Refused): self.run_move()
        self.original_intact()

    def test_hardlink_refuses(self):
        os.link(str(self.file), str(self.file) + '.link')
        with self.assertRaises(m.Refused): self.run_move()
        self.original_intact()

    def test_current_file_refuses(self):
        with patch.object(m, 'MIN_AGE', 86400):
            with self.assertRaises(m.Refused): self.run_move()
        self.original_intact()

    def test_same_device_refuses(self):
        with patch.object(m, 'DEST_DEVICE', m.SOURCE_DEVICE):
            with self.assertRaises(m.Refused): self.run_move()
        self.original_intact()

    def test_source_in_use_refuses(self):
        def busy(s, p, d): raise m.Refused('SOURCE_OPEN_BY_PROCESS')
        with self.assertRaises(m.Refused): self.run_move(check=busy)
        self.original_intact()

    def test_low_disk_preserves_source(self):
        with patch.object(m.os, 'fstatvfs', return_value=types.SimpleNamespace(f_bavail=1, f_frsize=1)):
            with self.assertRaises(m.Refused): self.run_move()
        self.original_intact()

    def test_corrupt_copy_preserves_source(self):
        original = m.transfer
        def transfer(src, out, rate, limit):
            digest, size = original(src, out, rate, limit)
            return ('0'*64, size) if out is None else (digest, size)
        with patch.object(m, 'transfer', transfer):
            with self.assertRaises(m.Refused): self.run_move()
        self.original_intact()

    def test_interruption_preserves_source(self):
        with patch.object(m, 'transfer', side_effect=InterruptedError()):
            with self.assertRaises(InterruptedError): self.run_move()
        self.original_intact()

    def test_journal_failure_preserves_source(self):
        def broken(row): raise OSError('disk full')
        with self.assertRaises(OSError): self.run_move(journal=broken)
        self.original_intact()

    def test_new_open_before_commit_preserves_source(self):
        calls = []
        def check(s, p, d):
            calls.append(p)
            if len(calls) == 2: raise m.Refused('SOURCE_OPEN_BY_PROCESS')
        with self.assertRaises(m.Refused): self.run_move(check=check)
        self.original_intact()

    def test_source_mutation_during_copy_refuses(self):
        original = m.transfer
        def transfer(src, out, rate, limit):
            result = original(src, out, rate, limit)
            if out is not None:
                with self.file.open('ab') as stream: stream.write(b'new')
            return result
        with patch.object(m, 'transfer', transfer):
            with self.assertRaises(m.Refused): self.run_move()
        self.assertFalse(self.file.is_symlink())
        self.assertTrue(self.file.read_bytes().endswith(b'new'))

    def test_existing_destination_never_overwritten(self):
        name = m.hashlib.sha256(self.virtual.encode()).hexdigest()[:24] + '-' + self.file.name
        target = Path(self.dst.name) / name
        target.write_bytes(b'previous evidence')
        with self.assertRaises(FileExistsError): self.run_move()
        self.assertEqual(target.read_bytes(), b'previous evidence')
        self.original_intact()

    def test_repeated_attempt_refuses_symlink(self):
        self.run_move()
        with self.assertRaises(OSError): self.run_move()
        self.assertTrue(self.file.is_symlink())

    def test_source_parent_symlink_refuses(self):
        link = Path(self.src.name) / 'alias'
        link.symlink_to(self.src.name)
        with self.assertRaises(OSError): m.parent_fd(str(link / 'file'))

    def test_disallowed_file_extension(self):
        self.row['path'] = '/home/cnooc/business.docx'
        with self.assertRaises(m.Refused): self.run_move()
        self.original_intact()

    def test_real_process_open_fd_detected(self):
        p = subprocess.Popen([sys.executable, '-c', 'import sys,time; f=open(sys.argv[1],"rb"); print("ready",flush=True); time.sleep(20)', str(self.file)], stdout=subprocess.PIPE)
        try:
            self.assertEqual(p.stdout.readline().strip(), b'ready')
            with self.assertRaisesRegex(m.Refused, 'SOURCE_OPEN_BY_PROCESS'):
                m.require_unused(self.file.stat(), str(self.file))
        finally:
            p.terminate()
            p.wait(timeout=5)
            p.stdout.close()

    def test_real_proc_closed_file_accepted(self):
        m.require_unused(self.file.stat(), str(self.file))

    def mount_fields(self, root='/home', point='/home', options='ro,relatime'):
        device = '{}:{}'.format(os.major(m.SOURCE_DEVICE), os.minor(m.SOURCE_DEVICE))
        return ['1', '0', device, root, point, options, '-', 'xfs', '/dev/root', 'rw']

    def visible_objects(self, path):
        if path in ('/', '/proc/1510/root'):
            return (m.SOURCE_DEVICE, 1, 0o040000)
        if path in (self.virtual, '/proc/1510/root' + self.virtual):
            s = self.file.stat()
            return (s.st_dev, s.st_ino, 0o100000)
        if path in ('/mnt/data', '/proc/1510/root/mnt/data', m.DEST, '/proc/1510/root' + m.DEST):
            return (m.DEST_DEVICE, 2, 0o040000)
        if path in (m.DEST + '/copy', '/proc/1510/root' + m.DEST + '/copy'):
            return (m.DEST_DEVICE, 3, 0o100000)
        return None

    def test_readonly_home_mirror_with_same_source_destination_accepted(self):
        with patch.object(m, 'object_identity', self.visible_objects):
            m.check_mount(self.mount_fields(), self.file.stat(), self.virtual, '1510', m.DEST + '/copy')

    def test_parent_mount_missing_opt_is_rejected(self):
        def visibility(path):
            return None if path == '/proc/1510/root/mnt/data' else self.visible_objects(path)
        with patch.object(m, 'object_identity', visibility):
            with self.assertRaisesRegex(m.Refused, 'DESTINATION_NOT_VISIBLE'):
                m.check_mount(self.mount_fields(), self.file.stat(), self.virtual, '1510')

    def test_parent_mount_different_copy_is_rejected(self):
        def visibility(path):
            return (99, 99, 0o100000) if path == '/proc/1510/root' + m.DEST + '/copy' else self.visible_objects(path)
        with patch.object(m, 'object_identity', visibility):
            with self.assertRaisesRegex(m.Refused, 'COPY_NOT_VISIBLE'):
                m.check_mount(self.mount_fields(), self.file.stat(), self.virtual, '1510', m.DEST + '/copy')

    def test_chroot_with_same_mount_namespace_is_rejected(self):
        def visibility(path):
            return (99, 99, 0o040000) if path == '/proc/1510/root' else self.visible_objects(path)
        with patch.object(m, 'object_identity', visibility):
            with self.assertRaisesRegex(m.Refused, 'PROCESS_ROOT_DIFFERS'):
                m.check_mount(self.mount_fields(), self.file.stat(), self.virtual, '1510')

    def test_writable_or_relocated_parent_mount_still_refused(self):
        for fields in (self.mount_fields(options='rw'), self.mount_fields(point='/host-home'), self.mount_fields(root='/home/cnooc', point='/home/cnooc')):
            with self.assertRaisesRegex(m.Refused, 'SOURCE_BIND_MOUNTED'):
                m.check_mount(fields, self.file.stat(), self.virtual, '1510')

    def test_direct_archive_mount_still_refused(self):
        with self.assertRaisesRegex(m.Refused, 'SOURCE_DIRECT_FILE_MOUNT'):
            m.check_mount(self.mount_fields(root=self.virtual, point='/archive'), self.file.stat(), self.virtual, '1510')

    def test_unrelated_bind_mount_not_blocking(self):
        m.check_mount(self.mount_fields(root='/var/log', point='/var/log'), self.file.stat(), self.virtual, '1510')

    def test_copied_file_checked_at_final_use_scan(self):
        destinations = []
        result = self.run_move(check=lambda s, p, d: destinations.append(d))
        self.assertEqual(destinations, [None, result['destination']])







    def test_unlisted_archive_and_current_preload_rejected(self):
        self.assertFalse(m.allowed_source('/home/cnooc/not-in-plan.oci.tar'))
        self.assertFalse(m.allowed_source('/home/cnooc/frontend.frontend-polish.full.oci.tar'))
        self.assertFalse(m.allowed_source('/var/log/messages-20260916.gz'))

    def test_progress_emitted_for_copy_and_verification(self):
        import contextlib, io, json
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.run_move()
        rows = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual({r['phase'] for r in rows}, {'COPY_ARCHIVE','VERIFY_RETAINED_COPY'})
        self.assertEqual(sum(r.get('complete',False) for r in rows), 2)


if __name__ == '__main__':
    unittest.main()
