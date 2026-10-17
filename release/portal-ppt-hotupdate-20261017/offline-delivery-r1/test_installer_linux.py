"""Small offline bundles only. The privileged/main entry is never invoked."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent
TEMPLATE = (ROOT / 'installer_template.py').read_text(encoding='utf-8')
BUILD_SOURCE = (ROOT / 'build_offline_delivery.py').read_text(encoding='utf-8')
PAYLOADS = [('file%d.bin' % i, ('offline-fixture-%d\n' % i).encode()) for i in range(8)]
EXPECTED = [dict(name=name, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()) for name, raw in PAYLOADS]
FAKE_SPACE = types.SimpleNamespace(f_bavail=4 * 1024 ** 3, f_frsize=1)


def create_tar(path, variant=None):
    with tarfile.open(str(path), 'w', format=tarfile.USTAR_FORMAT) as archive:
        pairs = list(PAYLOADS)
        if variant == 'extra':
            pairs.append(('extra.bin', b'extra'))
        if variant == 'duplicate':
            pairs[7] = pairs[0]
        if variant == 'traversal':
            pairs[0] = ('../outside.bin', pairs[0][1])
        if variant == 'absolute':
            pairs[0] = ('/tmp/outside.bin', pairs[0][1])
        for i, (name, raw) in enumerate(pairs):
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            if variant in ('symlink', 'hardlink') and i == 0:
                info.type = tarfile.SYMTYPE if variant == 'symlink' else tarfile.LNKTYPE
                info.linkname, info.size = 'file1.bin', 0
                archive.addfile(info)
            else:
                archive.addfile(info, io.BytesIO(raw))
    raw = path.read_bytes()
    return len(raw), hashlib.sha256(raw).hexdigest()


def make_module(size, checksum):
    text = TEMPLATE.replace('__EXPECTED_FILES__', repr(EXPECTED)).replace('__BUNDLE_BYTES__', str(size)).replace('__BUNDLE_SHA256__', checksum)
    module = types.ModuleType('offline_installer_fixture')
    module.__file__ = str(ROOT / 'installer_template.py')
    exec(compile(text, '<offline-installer-fixture>', 'exec'), module.__dict__)
    return module


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / 'bundle.tar'
        self.destination = self.root / 'installed'

    def call(self, variant=None, expected=None, checksum=None):
        size, digest = create_tar(self.archive, variant)
        module = make_module(size, digest)
        with mock.patch.object(module.os, 'statvfs', return_value=FAKE_SPACE), contextlib.redirect_stdout(io.StringIO()):
            result = module.extract_bundle(self.archive, self.destination, expected or EXPECTED, size, checksum or digest)
        return result, module

    def test_success_exact_eight_contents_permissions_and_no_code_execution(self):
        result, _ = self.call()
        self.assertEqual(result['offline_install'], 'VERIFIED')
        self.assertEqual(set(x.name for x in self.destination.iterdir()), {n for n, _ in PAYLOADS})
        self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), 0o700)
        for name, raw in PAYLOADS:
            path = self.destination / name
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.stat().st_nlink, 1)

    def test_outer_hash_mismatch_creates_no_destination(self):
        with self.assertRaisesRegex(Exception, 'bundle_sha256_mismatch'):
            self.call(checksum='0' * 64)
        self.assertFalse(self.destination.exists())
        self.assertTrue(self.archive.exists())

    def test_inner_hash_mismatch_preserves_partial_files(self):
        rows = [dict(row) for row in EXPECTED]
        rows[2]['sha256'] = '0' * 64
        with self.assertRaisesRegex(Exception, 'extracted_file_sha256_mismatch'):
            self.call(expected=rows)
        self.assertEqual(sorted(p.name for p in self.destination.iterdir()), ['file0.bin', 'file1.bin', 'file2.bin'])
        self.assertEqual((self.destination / 'file2.bin').read_bytes(), PAYLOADS[2][1])
        self.assertFalse((self.destination / 'file3.bin').exists())

    def test_extra_member_is_rejected_before_destination_creation(self):
        with self.assertRaisesRegex(Exception, 'extra_tar_member'):
            self.call(variant='extra')
        self.assertFalse(self.destination.exists())

    def test_duplicate_member_is_rejected_before_destination_creation(self):
        with self.assertRaisesRegex(Exception, 'unexpected_or_duplicate_tar_member'):
            self.call(variant='duplicate')
        self.assertFalse(self.destination.exists())

    def test_tar_symlink_and_hardlink_are_rejected(self):
        for variant in ('symlink', 'hardlink'):
            with self.subTest(variant=variant), self.assertRaisesRegex(Exception, 'nonregular_tar_member'):
                self.call(variant=variant)
            self.assertFalse(self.destination.exists())

    def test_traversal_and_absolute_members_are_rejected(self):
        for variant in ('traversal', 'absolute'):
            with self.subTest(variant=variant), self.assertRaisesRegex(Exception, 'unexpected_or_duplicate_tar_member'):
                self.call(variant=variant)
            self.assertFalse(self.destination.exists())
        self.assertFalse((self.root / 'outside.bin').exists())

    def test_existing_destination_is_not_overwritten(self):
        self.destination.mkdir(mode=0o700)
        keep = self.destination / 'keep'
        keep.write_bytes(b'preserve-existing')
        with self.assertRaisesRegex(Exception, 'destination_exists_preserve_for_review'):
            self.call()
        self.assertEqual(keep.read_bytes(), b'preserve-existing')
        self.assertEqual(list(self.destination.iterdir()), [keep])

    def test_source_and_destination_symlinks_are_rejected(self):
        size, digest = create_tar(self.archive)
        module = make_module(size, digest)
        link = self.root / 'bundle-link.tar'
        link.symlink_to(self.archive)
        with mock.patch.object(module.os, 'statvfs', return_value=FAKE_SPACE), self.assertRaises(OSError):
            module.extract_bundle(link, self.destination, EXPECTED, size, digest)
        elsewhere = self.root / 'elsewhere'
        elsewhere.mkdir()
        self.destination.symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaisesRegex(Exception, 'destination_exists_preserve_for_review'):
            module.extract_bundle(self.archive, self.destination, EXPECTED, size, digest)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_insufficient_space_is_checked_before_extract(self):
        size, digest = create_tar(self.archive)
        module = make_module(size, digest)
        with mock.patch.object(module.os, 'statvfs', return_value=types.SimpleNamespace(f_bavail=1024, f_frsize=1)), self.assertRaisesRegex(Exception, 'insufficient_extract_space'):
            module.extract_bundle(self.archive, self.destination, EXPECTED, size, digest)
        self.assertFalse(self.destination.exists())

    def test_shell_set_e_failure_does_not_call_subsequent_sudo(self):
        # Use the actual generated shell header from build_offline_delivery.py,
        # with an in-process tiny fixture call and a fake sudo marker only.
        import ast
        constants = [node.value for node in ast.walk(ast.parse(BUILD_SOURCE))
                     if isinstance(node, ast.Constant) and isinstance(node.value, str)]
        header = next(value for value in constants if value.startswith('#!/usr/bin/env bash\nset -eu\n'))
        # Python AST merges adjacent literal strings, including the generated
        # heredoc opener; keep only the real shell header before that opener.
        header = header.split("python3 -I -B -u - <<", 1)[0]
        size, digest = create_tar(self.archive)
        marker = self.root / 'sudo-was-invoked'
        installer = TEMPLATE.replace('__EXPECTED_FILES__', repr(EXPECTED)).replace('__BUNDLE_BYTES__', str(size)).replace('__BUNDLE_SHA256__', digest)
        for succeed in (False, True):
            destination = self.root / ('success' if succeed else 'failure')
            check_sha = digest if succeed else '0' * 64
            python = '__name__="offline_fixture"\n' + installer + '\n'
            python += 'from unittest import mock\nimport types\nwith mock.patch.object(os,"statvfs",return_value=types.SimpleNamespace(f_bavail=4*1024**3,f_frsize=1)):\n'
            python += ' extract_bundle(' + repr(str(self.archive)) + ',' + repr(str(destination)) + ',EXPECTED,BUNDLE_BYTES,' + repr(check_sha) + ')\n'
            fake_sudo = 'sudo() { printf invoked > ' + shlex.quote(str(marker)) + '; }\n'
            shell = header + fake_sudo + shlex.quote(sys.executable) + " -I -B - <<'FIXTURE_PY'\n" + python + '\nFIXTURE_PY\nsudo fixture-no-real-sudo\n'
            script = self.root / ('pass.sh' if succeed else 'fail.sh')
            script.write_text(shell, encoding='utf-8')
            result = subprocess.run(['/bin/bash', str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            self.assertEqual(result.returncode == 0, succeed, result.stderr.decode('utf-8', errors='replace'))
            self.assertEqual(marker.exists(), succeed)

    def test_actual_generated_shell_exact_binding_syntax_and_tail(self):
        path = ROOT / 'GENERATED-SHELL-COPY.sh'
        raw = path.read_bytes()
        self.assertEqual(len(raw), 7250)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), 'fa9376166ab4e1b700d9703652c866739c1b2bcb22d1232f789da247a636580c')
        delivery = json.loads((ROOT / 'DELIVERY.json').read_bytes())
        expected = TEMPLATE.replace('__EXPECTED_FILES__', repr(delivery['members'])).replace('__BUNDLE_BYTES__', str(delivery['bundle']['bytes'])).replace('__BUNDLE_SHA256__', delivery['bundle']['sha256'])
        header = '#!/usr/bin/env bash\nset -eu\nif [ "$#" -ne 0 ]; then echo "No arguments are supported." >&2; exit 2; fi\n'
        tail = 'sudo python3 -I -B -u /home/cnooc/portal-ppt-native-preload-20261017-r1/preload_native.py --distribute'
        exact = header + "python3 -I -B -u - <<'PORTAL_OFFLINE_PY'\n" + expected + '\nPORTAL_OFFLINE_PY\n' + tail + '\n'
        self.assertEqual(raw.decode('utf-8'), exact)
        result = subprocess.run(['/bin/bash', '-n', str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        self.assertEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
