import gzip
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('cleanup', Path(__file__).with_name('cleanup-datax01-observed-files.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def pin(p):
    s = p.stat()
    return dict(path=str(p), device=s.st_dev, inode=s.st_ino, bytes=s.st_size,
                mtime_ns=s.st_mtime_ns, uid=s.st_uid)


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def archive(self, name='file', data=b'content'):
        p = self.root / 'old.tar'
        with tarfile.open(str(p), 'w') as tar:
            member = tarfile.TarInfo(name)
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
        return p

    def test_archive_only_removed_after_complete_copy(self):
        (self.root / 'file').write_bytes(b'content')
        p = self.archive()
        result = m.remove_redundant_archive(pin(p), str(self.root), lambda x: None)
        self.assertFalse(p.exists())
        self.assertEqual(result['verified_files'], 1)
        self.assertEqual((self.root / 'file').read_bytes(), b'content')

    def test_mismatch_preserves_archive(self):
        (self.root / 'file').write_bytes(b'changed')
        p = self.archive()
        with self.assertRaises(m.Refused):
            m.remove_redundant_archive(pin(p), str(self.root), lambda x: None)
        self.assertTrue(p.exists())

    def test_missing_copy_preserves_archive(self):
        p = self.archive()
        with self.assertRaises(FileNotFoundError):
            m.remove_redundant_archive(pin(p), str(self.root), lambda x: None)
        self.assertTrue(p.exists())

    def test_unsafe_archive_path_refused(self):
        p = self.archive('../outside')
        with self.assertRaises(m.Refused):
            m.remove_redundant_archive(pin(p), str(self.root), lambda x: None)
        self.assertTrue(p.exists())

    def test_symlink_copy_refused(self):
        (self.root / 'other').write_bytes(b'content')
        (self.root / 'file').symlink_to(self.root / 'other')
        p = self.archive()
        with self.assertRaises(OSError):
            m.remove_redundant_archive(pin(p), str(self.root), lambda x: None)
        self.assertTrue(p.exists())

    def test_compression_is_lossless_preserves_metadata_and_other_files(self):
        p = self.root / 'old.log.1'
        content = b'abcd' * 600000
        p.write_bytes(content)
        p.chmod(0o640)
        row = pin(p)
        untouched = self.root / 'current.log'
        untouched.write_bytes(b'active')
        m.compress_log(row, lambda x: None)
        dest = Path(str(p) + '.gz')
        self.assertFalse(p.exists())
        with gzip.open(str(dest), 'rb') as f:
            self.assertEqual(f.read(), content)
        self.assertEqual(dest.stat().st_mtime_ns, row['mtime_ns'])
        self.assertEqual(dest.stat().st_mode & 0o777, 0o640)
        self.assertEqual(untouched.read_bytes(), b'active')

    def test_changed_pin_refused(self):
        p = self.root / 'file'
        p.write_bytes(b'old')
        row = pin(p)
        p.write_bytes(b'new content')
        with self.assertRaises(m.Refused):
            m.compress_log(row, lambda x: None)
        self.assertTrue(p.exists())

    def test_existing_compressed_copy_never_overwritten(self):
        p = self.root / 'file'
        p.write_bytes(b'x' * 2000000)
        dst = Path(str(p) + '.gz')
        dst.write_bytes(b'keep')
        with self.assertRaises(m.Refused):
            m.compress_log(pin(p), lambda x: None)
        self.assertEqual(dst.read_bytes(), b'keep')
        self.assertTrue(p.exists())

    def test_active_other_process_refused(self):
        p = self.root / 'file'
        p.write_bytes(b'content')
        proc = subprocess.Popen([sys.executable, '-c', 'import sys,time; f=open(sys.argv[1]); print("ready",flush=True); time.sleep(30)', str(p)], stdout=subprocess.PIPE)
        try:
            self.assertEqual(proc.stdout.readline().strip(), b'ready')
            with self.assertRaisesRegex(m.Refused, 'FILE_IN_USE'):
                m.require_closed(p.stat())
        finally:
            proc.terminate()
            proc.wait()
            proc.stdout.close()

    def test_digest_limit(self):
        with self.assertRaisesRegex(m.Refused, 'CONTENT_LIMIT'):
            m.digest_stream(io.BytesIO(b'1234'), 3)

    def test_incompressible_kept(self):
        p = self.root / 'file'
        p.write_bytes(os.urandom(2000000))
        with self.assertRaisesRegex(m.Refused, 'INSUFFICIENT_COMPRESSION_SAVING'):
            m.compress_log(pin(p), lambda x: None)
        self.assertTrue(p.exists())
        self.assertFalse(Path(str(p) + '.gz').exists())

    def test_journal_failure_preserves_source(self):
        p = self.root / 'file'
        p.write_bytes(b'x' * 2000000)
        def fail(row):
            raise OSError('journal unavailable')
        with self.assertRaises(OSError):
            m.compress_log(pin(p), fail)
        self.assertTrue(p.exists())


if __name__ == '__main__':
    unittest.main()
