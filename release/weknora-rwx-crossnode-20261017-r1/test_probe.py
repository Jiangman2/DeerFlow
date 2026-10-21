import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import probe as p


TOKEN = 'a' * 32


def mounts(paths):
    return ''.join('1 0 0:1 / ' + path + ' rw,relatime - nfs4 ' + p.SERVER + ':' + p.PREFIX
                   + path.rsplit('/', 1)[1] + ' rw,vers=4.1,hard,proto=tcp\n' for path in paths)


class Protocol(unittest.TestCase):
    def test_nfs41_source_exact(self):
        self.assertEqual(p.mount_check('/probe/files', mounts(p.MOUNTS))['protocol'], 'NFSv4.1')

    def test_wrong_mount_denied(self):
        for bad in (mounts(p.MOUNTS).replace('vers=4.1', 'vers=3'),
                    mounts(p.MOUNTS).replace('hard', 'soft'),
                    mounts(p.MOUNTS).replace(p.SERVER, '127.0.0.1'),
                    mounts(p.MOUNTS).replace('/weknora-rwx-r2/', '/deerflow-rwx/home/'),
                    mounts(p.MOUNTS).replace('nfs4', 'overlay')):
            with self.assertRaises(RuntimeError):
                p.mount_check('/probe/files', bad)

    def test_duplicate_mount_denied(self):
        with self.assertRaises(RuntimeError):
            p.mount_check('/probe/files', mounts(p.MOUNTS) * 2)

    def test_payload_bounded_and_distinct(self):
        self.assertEqual(len(p.payload(TOKEN, 'files', 'a')), 65536)
        self.assertNotEqual(p.payload(TOKEN, 'files', 'a'), p.payload(TOKEN, 'docreader', 'a'))

    def test_root_execution_denied(self):
        with self.assertRaisesRegex(RuntimeError, 'not_uid'):
            p.run('write-a', TOKEN)

    def test_invalid_token_and_phase_denied(self):
        for phase, token in [('write-a', '../../old'), ('copy-business', TOKEN)]:
            with self.assertRaises(RuntimeError):
                p.run(phase, token)


class NonrootIO(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='synthetic-rwx-', dir='/tmp')
        self.root = Path(self.tmp.name)
        self.root.chmod(0o755)
        self.paths = [str(self.root / role) for role in ('files', 'docreader')]
        for path in self.paths:
            os.mkdir(path, 0o770)
            os.chown(path, 1000, 1000)
            os.chmod(path, 0o770)

    def tearDown(self):
        self.tmp.cleanup()

    def child(self, phase):
        read, write = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(read)
            try:
                os.setgroups([])
                os.setgid(1000)
                os.setuid(1000)
                with patch.object(p, 'MOUNTS', tuple(self.paths)), \
                     patch.object(p.os, 'fstatvfs', return_value=SimpleNamespace(f_bavail=1048576, f_frsize=4096, f_favail=10000)), \
                     patch('builtins.open', return_value=io.StringIO(mounts(self.paths))):
                    result = p.run(phase, TOKEN)
            except Exception as exc:
                result = dict(error=type(exc).__name__, check=str(exc))
            os.write(write, json.dumps(result).encode())
            os.close(write)
            os._exit(0)
        os.close(write)
        with os.fdopen(read, 'rb') as f:
            data = f.read()
        os.waitpid(pid, 0)
        return json.loads(data)

    def probe_dir(self):
        return Path(self.paths[0]) / ('.weknora-rwx-probe-' + TOKEN)

    def test_full_nonroot_roundtrip_then_cleanup(self):
        for phase in ('write-a', 'read-a-write-b', 'read-b', 'cleanup'):
            r = self.child(phase)
            self.assertEqual(r.get('probe'), 'PASS', r)
            self.assertEqual(r['uid'], 1000)
        for path in self.paths:
            self.assertFalse(list(Path(path).iterdir()))

    def test_write_replay_refused(self):
        self.assertEqual(self.child('write-a')['probe'], 'PASS')
        self.assertIn('error', self.child('write-a'))

    def test_cleanup_refuses_extra_file(self):
        self.child('write-a')
        self.child('read-a-write-b')
        (self.probe_dir()/'unrelated').write_bytes(b'keep')
        result = self.child('cleanup')
        self.assertEqual(result.get('check'), 'unexpected_probe_members')
        self.assertTrue((self.probe_dir()/'a.bin').exists())

    def test_cleanup_refuses_changed_bytes(self):
        self.child('write-a')
        self.child('read-a-write-b')
        (self.probe_dir()/'a.bin').write_bytes(b'x' * 65536)
        self.assertEqual(self.child('cleanup').get('check'), 'synthetic_checksum_mismatch')
        self.assertTrue((self.probe_dir()/'b.bin').exists())

    def test_cleanup_refuses_symlink(self):
        self.child('write-a')
        self.child('read-a-write-b')
        path = self.probe_dir()/'a.bin'
        path.unlink()
        path.symlink_to(self.probe_dir()/'b.bin')
        self.assertIn('error', self.child('cleanup'))
        self.assertTrue((self.probe_dir()/'b.bin').exists())

    def test_nonroot_cannot_write_wrong_mode(self):
        os.chmod(self.paths[0], 0o755)
        self.assertEqual(self.child('write-a').get('check'), 'directory_identity_or_mode')
        self.assertFalse(self.probe_dir().exists())

    def test_cleanup_refuses_hardlink(self):
        self.child('write-a')
        self.child('read-a-write-b')
        os.link(self.probe_dir()/'a.bin', self.root/'other-link')
        self.assertEqual(self.child('cleanup').get('check'), 'synthetic_file_identity')
        self.assertTrue((self.probe_dir()/'a.bin').exists())


if __name__ == '__main__':
    unittest.main()
