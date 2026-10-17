import ast
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import repair_dwarf_build as r
import package_dwarf_fix as publisher

HERE = Path(__file__).resolve().parent


class DwarfRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old = self.root/'old'
        self.old.mkdir()
        self.patch = self.root/'patch'
        self.patch.mkdir()
        self.script = (HERE/'build_native.sh').read_bytes().replace(b'\r\n', b'\n')
        self.verifier = (HERE/'verify_native_candidate.py').read_bytes().replace(b'\r\n', b'\n')
        self.members = {'build_native.sh':self.script, 'source/app.go':b'original code',
            'source/go.mod':b'original dependencies', 'offline/INPUTS.SHA256':b'original manifest',
            'offline/debian/SQLITE-BUILD-LOCK.json':b'locked SQLite packages',
            'offline/debian/libsqlite3-dev.deb':b'unchanged deb', 'Dockerfile.weknora':b'unchanged runtime',
            'CONTEXT.json':b'{"input_files":42,"sqlite_build_repair":"retained"}'}
        with tarfile.open(self.old/'context.tar', 'w:') as dest:
            for name, raw in self.members.items():
                r.base.add(dest, name, raw=raw)
        (self.patch/'build_native.sh').write_bytes(r.patched_script(self.script))
        (self.patch/'verify_native_candidate.py').write_bytes(r.patched_verifier(self.verifier))
        (self.patch/'LOCAL-VERIFICATION.json').write_bytes(b'{"local_tool_tests":"PASS"}\n')
        (self.old/'verify_native_candidate.py').write_bytes(self.verifier)
        (self.old/'BUILD-PLAN.json').write_bytes(b'{"production_baseline":{"generation":68},"runtime_base":"same","builder":"same","build_repair":"sqlite-preserved"}')
        (self.old/'LOCAL-VERIFICATION.json').write_bytes(b'old')
        (self.old/'builder.oci.tar').write_bytes(b'old builder archive')
        (self.old/'build_weknora.py').write_bytes(b'unchanged build guards')
        (self.old/'PACKAGE.SHA256').write_bytes(''.join(r.base.digest(p)+'  '+p.name+'\n'
                    for p in sorted(self.old.iterdir())).encode())
        self.plan = {'old_context_sha256':r.base.digest(self.old/'context.tar'),
            'old_build_native_sha256':hashlib.sha256(self.script).hexdigest(),
            'old_verifier_sha256':hashlib.sha256(self.verifier).hexdigest(),
            'old_package_manifest_sha256':r.base.digest(self.old/'PACKAGE.SHA256')}

    def delta(self):
        return r.context_delta(self.old/'context.tar', self.root/'new.tar', self.patch, self.plan)

    def test_only_two_context_members_changed(self):
        self.delta()
        with tarfile.open(self.root/'new.tar') as t:
            actual = {m.name:t.extractfile(m).read() for m in t}
        self.assertEqual(set(actual), set(self.members))
        self.assertEqual({k for k,v in self.members.items() if actual[k] != v}, {'build_native.sh','CONTEXT.json'})
        self.assertEqual(json.loads(actual['CONTEXT.json'])['sqlite_build_repair'], 'retained')
        self.assertEqual(json.loads(actual['CONTEXT.json'])['input_files'], 42)

    def test_context_hash_required_before_write(self):
        self.plan['old_context_sha256'] = '0'*64
        with self.assertRaisesRegex(r.base.CheckFailure, 'original_context_changed'):
            self.delta()
        self.assertFalse((self.root/'new.tar').exists())

    def test_script_hash_and_exact_patch_required(self):
        self.plan['old_build_native_sha256'] = '0'*64
        with self.assertRaisesRegex(r.base.CheckFailure, 'original_build_script_changed'):
            self.delta()

    def test_unreviewed_payload_rejected(self):
        (self.patch/'build_native.sh').write_bytes(b'skip tests')
        with self.assertRaisesRegex(r.base.CheckFailure, 'patched_script_binding'):
            self.delta()

    def test_existing_output_preserved(self):
        (self.root/'new.tar').write_bytes(b'receipt')
        with self.assertRaisesRegex(r.base.CheckFailure, 'fresh_context_required'):
            self.delta()
        self.assertEqual((self.root/'new.tar').read_bytes(), b'receipt')

    def test_bad_archive_members_rejected(self):
        for n, case in enumerate(('../x','/x','x\\y','x:y','x\ny','link','duplicate')):
            with self.subTest(case=case):
                with tarfile.open(self.old/'context.tar', 'w:') as t:
                    m = tarfile.TarInfo(case)
                    m.size = 1
                    if case == 'link':
                        m.type, m.linkname = tarfile.SYMTYPE, '/outside'
                    t.addfile(m, io.BytesIO(b'x'))
                    if case == 'duplicate':
                        t.addfile(m, io.BytesIO(b'x'))
                self.plan['old_context_sha256'] = r.base.digest(self.old/'context.tar')
                with self.assertRaisesRegex(r.base.CheckFailure, 'unsafe_context_member'):
                    r.context_delta(self.old/'context.tar', self.root/('bad%d.tar'%n), self.patch, self.plan)

    def test_package_preserves_baseline_and_original_bytes(self):
        before = {p.name:p.read_bytes() for p in self.old.iterdir()}
        with patch.object(r.base, 'private'):
            result = r.prepare(self.old, self.patch, self.root/'fresh', self.plan)
        self.assertEqual(before, {p.name:p.read_bytes() for p in self.old.iterdir()})
        new = self.root/'fresh/package'
        for name in ('builder.oci.tar','build_weknora.py'):
            self.assertEqual((new/name).read_bytes(), before[name])
        plan = json.loads((new/'BUILD-PLAN.json').read_bytes())
        self.assertEqual(plan['production_baseline'], {'generation':68})
        self.assertEqual(plan['runtime_base'], 'same')
        self.assertEqual(plan['builder'], 'same')
        self.assertEqual(plan['build_repair'], 'sqlite-preserved')
        self.assertFalse(result['production_workloads_mutated'])
        self.assertFalse(result['build_started'])

    def test_does_not_accept_test_stripping(self):
        (self.patch/'verify_native_candidate.py').write_bytes(b'def evidence(root): return True\n')
        with patch.object(r.base, 'private'), self.assertRaisesRegex(r.base.CheckFailure, 'verifier_preservation'):
            r.prepare(self.old, self.patch, self.root/'fresh', self.plan)
        self.assertFalse((self.root/'fresh').exists())

    def test_only_adds_to_original_script_and_verifier(self):
        self.assertEqual(r.patched_script(self.script).replace(r.COMPILER_PREFLIGHT.encode(), b''), self.script)
        self.assertEqual(r.patched_verifier(self.verifier).replace(r.VERIFIER_CHECK.encode(), b''), self.verifier)
        new = r.patched_script(self.script).decode()
        self.assertLess(new.index('export GOEXPERIMENT=nodwarf5'), new.index('gettrainedmodels'))
        self.assertLess(new.index('gettrainedmodels'), new.index('for component in rustc'))
        self.assertNotIn('GOEXPERIMENT=none', new)
        self.assertNotIn('-gcflags', new)
        self.assertIn('set(versions)=={4}', new)

    def test_duplicate_patch_rejected(self):
        with self.assertRaises(r.base.CheckFailure):
            r.patched_script(r.patched_script(self.script))
        with self.assertRaises(r.base.CheckFailure):
            r.patched_verifier(r.patched_verifier(self.verifier))

    def test_qualifier_requires_all_new_receipts(self):
        source = r.VERIFIER_CHECK
        namespace = {'json':json, 'require':r.require}
        exec('def check(root):\n'+source, namespace)
        root = self.root/'receipts'
        root.mkdir()
        files = {'compiler-config.json':json.dumps({'GOEXPERIMENT':'nodwarf5','GOOS':'linux','GOARCH':'arm64','GOVERSION':'go1.26.8'}),
            'dwarf-format.json':'{"versions":[4],"compilation_units":1}',
            'compiler-preflight.txt':'compiler_dependency_preflight=PASS GOEXPERIMENT=nodwarf5\n',
            'server-buildinfo.txt':'build\tGOEXPERIMENT=nodwarf5\n'}
        for name, raw in files.items():
            (root/name).write_text(raw)
        namespace['check'](root)
        for name in files:
            with self.subTest(name=name):
                (root/name).unlink()
                with self.assertRaises(FileNotFoundError): namespace['check'](root)
                (root/name).write_text(files[name])
        (root/'dwarf-format.json').write_text('{"versions":[5],"compilation_units":1}')
        with self.assertRaisesRegex(r.base.CheckFailure,'dwarf4_probe_missing'): namespace['check'](root)

    def test_requires_matching_failure(self):
        failure = self.root/'FAILED.json'
        failure.write_text('{"stage":"compile-and-runtime-check","images_imported":false,"production_writes":0}')
        log = self.root/'build.log'
        log.write_bytes(b'panic: runtime error: invalid memory address\nPutLocationListDwarf5\ngithub.com/elastic/go-elasticsearch/v8/typedapi/ml/gettrainedmodels')
        with patch.object(r.base, 'private'):
            r.confirm_failure(failure, log)
            log.write_bytes(b'fatal error: sqlite3.h: No such file or directory')
            with self.assertRaisesRegex(r.base.CheckFailure, 'compiler_dwarf5_failure_not_confirmed'):
                r.confirm_failure(failure, log)

    def test_server_python37_compatible(self):
        ast.parse((HERE/'repair_dwarf_build.py').read_text(), feature_version=(3,7))

    def test_publisher_requires_actual_compiler_and_rehearsal_pass(self):
        proof = self.root/'proof.json'
        probe = self.root/'probe.json'
        proof.write_text('{"local_tool_tests":"PASS","real_context_rehearsal":"PASS"}')
        probe.write_text('{"local_compiler_probe":"FAIL"}')
        with self.assertRaisesRegex(r.base.CheckFailure, 'local_compiler_probe_required'):
            publisher.pack(self.old, self.root/'published', proof, probe)
        self.assertFalse((self.root/'published').exists())

    def test_publisher_keeps_server_pins_and_launcher_safety(self):
        proof = self.root/'proof.json'
        probe = self.root/'probe.json'
        proof.write_text('{"local_tool_tests":"PASS","real_context_rehearsal":"PASS"}')
        probe.write_text('{"local_compiler_probe":"PASS","exact_previously_failing_dependency":"PASS","compiler":{"GOEXPERIMENT":"nodwarf5"}}')
        fake = self.root/'old-sqlite-patch'
        fake.mkdir()
        (fake/'build_native.sh').write_bytes(self.script)
        (fake/'verify_native_candidate.py').write_bytes(self.verifier)
        r.base.write_new(fake/'PACKAGE.SHA256', ''.join(r.base.digest(f)+'  '+f.name+'\n'
                        for f in sorted(fake.iterdir())).encode())
        with patch.object(publisher, 'SQLITE_PATCH_MANIFEST', r.base.digest(fake/'PACKAGE.SHA256')):
            result = publisher.pack(fake, self.root/'published', proof, probe)
        output = self.root/'published'
        plan = json.loads((output/'payload/FIX-PLAN.json').read_bytes())
        self.assertEqual(plan['old_context_sha256'], publisher.OLD_CONTEXT)
        self.assertEqual(plan['old_package_manifest_sha256'], publisher.OLD_MANIFEST)
        text = (output/'START-weknora-glm-dwarf-fix.sh').read_text()
        self.assertIn(result['archive_sha256'], text)
        self.assertNotIn('@PATCH_SHA256@', text)
        self.assertIn('repair_dwarf_build.py --output', text)
        self.assertIn('weknora-glm-dwarf-fix-20261016-r1', text)
        self.assertIn('--check-only', text)
        self.assertIn('test "${RESULT[0]}" -eq 0', text)
        if os.name == 'posix':
            subprocess.run(['bash', '-n'], input=text.encode(), check=True, capture_output=True)
            subprocess.run(['bash', '-n'], input=(output/'payload/build_native.sh').read_bytes(),
                           check=True, capture_output=True)
        with tarfile.open(result['archive']) as archive:
            self.assertEqual({m.name for m in archive}, {f.name for f in (output/'payload').iterdir()})
            self.assertTrue(all(m.isfile() and m.mode == 0o600 for m in archive))

    @unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'root Linux permissions')
    def test_real_permissions_guard(self):
        with tempfile.TemporaryDirectory(dir='/root') as directory:
            root = Path(directory)
            path = root/'evidence.json'
            path.write_bytes(b'{}')
            path.chmod(0o600)
            r.base.private(path)
            path.chmod(0o777)
            with self.assertRaisesRegex(r.base.CheckFailure, 'unsafe_private_path'): r.base.private(path)
            path.chmod(0o600)
            root.chmod(0o777)
            with self.assertRaisesRegex(r.base.CheckFailure, 'unsafe_private_ancestor'): r.base.private(path)
            root.chmod(0o700)


if __name__ == '__main__': unittest.main()
