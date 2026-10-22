import ast
import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('preload_native', ROOT / 'preload_native.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
PROFILE = json.loads((ROOT / 'PROFILE.json').read_bytes())


def blob(value):
    raw = p.encoded(value)
    return 'sha256:' + p.sha(raw), raw


class Tests(unittest.TestCase):
    def setUp(self):
        p.DEADLINE = None

    def test_profile_exact_six_paths_and_no_host_upload(self):
        self.assertEqual(p.sha((ROOT / 'PROFILE.json').read_bytes()), p.PROFILE_SHA)
        rows = PROFILE['images']
        self.assertEqual(len(rows), 6)
        self.assertEqual(len({r['tag'] for r in rows}), 6)
        self.assertEqual(sum(r['archive_bytes'] for r in rows), 9155877888)
        self.assertEqual(sum(r['expanded_bytes_upper_bound'] for r in rows), 24710817280)
        self.assertEqual([r['basename'] for r in rows[:5]], ['frontend.oci.tar', 'gateway.candidate.oci.tar',
                         'gateway-rollback-compatible.candidate.oci.tar', 'runner.candidate.oci.tar',
                         'runner-rollback-compatible.candidate.oci.tar'])
        self.assertFalse(rows[5]['windows_upload'])
        for row in rows:
            self.assertEqual(row['digest_reference'], row['repository'] + '@' + row['manifest_digest'])

    def test_budget_same_vs_distinct_devices_counts_all_copies_and_expansion(self):
        def fs(dev):
            return dict(device=dev, capacity_bytes=100 * p.GIB, available_bytes=99 * p.GIB, available_inodes=100001)
        same = p.budget_plan(dict(upload=fs(1), sealed=fs(1), image=fs(1)), 100, 1000, 90, False, False)
        self.assertEqual(len(same), 1)
        self.assertEqual(same[0]['required_bytes'], 22 * p.GIB + 300 + 2000 + 90)
        split = p.budget_plan(dict(upload=fs(2), sealed=fs(1), image=fs(3)), 100, 1000, 90, True, True)
        self.assertEqual(sum(x['required_bytes'] for x in split), 66 * p.GIB + 100 + 2000 + 90)

    def test_imagefs_includes_container_snapshot_device_and_rejects_ambiguous(self):
        value = dict(imageFilesystems=[dict(fsId=dict(mountpoint='/var/lib/containerd'))],
                     containerFilesystems=[dict(fsId=dict(mountpoint='/mnt/snapshots'))])
        self.assertEqual(p.image_fs_paths(value), ['/mnt/snapshots', '/var/lib/containerd'])
        with self.assertRaises(p.Failure):
            p.image_fs_paths(dict(value, status=value))

    def test_ref_collision_rejects_tag_and_digest_alias(self):
        row = PROFILE['images'][0]
        with mock.patch.object(p, 'reference', side_effect=[None, 'sha256:' + '0' * 64]):
            with self.assertRaisesRegex(p.Failure, 'existing_candidate_reference_conflict'):
                p.collision_check(dict(images=[row]))
        with mock.patch.object(p, 'reference', return_value=row['manifest_digest']):
            p.collision_check(dict(images=[row]))
        self.assertEqual(p.listing(b'REF TYPE DIGEST SIZE\n', row['tag']), None)
        with self.assertRaises(p.Failure):
            p.listing(b'other.invalid/x:tag media sha256:' + b'0' * 64, row['tag'])

    def test_host_safe_paths_and_codex_selector_exclude_secret_values(self):
        config = dict(config=dict(Env=['PATH=/usr/local/bin:/usr/bin:/bin', 'HOME=/root',
                      'XDG_CACHE_HOME=/opt/cache', 'PLAYWRIGHT_BROWSERS_PATH=0',
                      'PPT_MASTER_AGENT_COMMAND=codex --api-key secret-value', 'API_KEY=do-not-export']))
        value = p.host_projection(config, PROFILE['images'][5]['config_digest'])
        self.assertEqual(value['codex_selector'], 'UNSUPPORTED_CONFIGURED_LAUNCHER')
        raw = p.encoded(value)
        self.assertNotIn(b'secret-value', raw)
        self.assertNotIn(b'do-not-export', raw)
        self.assertEqual(value['runtime_paths']['PLAYWRIGHT_BROWSERS_PATH'], '0')
        for value in ('/usr/bin::/bin', '/usr/bin:relative', '/root/../bin', '/bin?token=secret'):
            with self.assertRaises(p.Failure):
                p.host_projection(dict(config=dict(Env=['PATH=' + value])), 'sha256:' + '0' * 64)
        clean = p.host_projection(dict(config=dict(Env=['PATH=/bin', 'PPT_MASTER_AGENT_COMMAND="/usr/local/bin/codex"'])), 'sha256:' + '0' * 64)
        self.assertNotIn('HOME', clean['runtime_paths'])
        self.assertEqual(clean['codex_selector'], '/usr/local/bin/codex')

    def test_pg_contract_only_known_shape_and_bounded_user_major(self):
        actual = dict(config=dict(Entrypoint=['docker-entrypoint.sh'], Cmd=['postgres'], User='', Env=['PG_MAJOR=13', 'PGPASSWORD=hidden']))
        out = p.postgres_contract(actual)
        self.assertEqual(out['launcher'], 'DOCKER_ENTRYPOINT_POSTGRES')
        self.assertEqual(out['pg_major'], '13')
        self.assertEqual(out['user'], '')
        self.assertNotIn('hidden', json.dumps(out))
        actual['config'].update(Cmd=['sh', '-c', 'secret'], User='secret-user', Env=['PG_MAJOR=secret'])
        out = p.postgres_contract(actual)
        self.assertEqual(out['launcher'], 'UNSUPPORTED_LAUNCHER')
        self.assertEqual(out['user'], 'UNSUPPORTED_USER')
        self.assertEqual(out['pg_major'], 'UNSUPPORTED')
        self.assertNotIn('secret', json.dumps(out))

    def test_pg_index_is_resolved_without_confusing_config_digest(self):
        config = dict(os='linux', architecture='arm64', config=dict(Entrypoint=['docker-entrypoint.sh'], Cmd=['postgres'], Env=['PG_MAJOR=13']))
        cd, cb = blob(config)
        md, mb = blob(dict(schemaVersion=2, config=dict(digest=cd, size=len(cb)), layers=[dict(digest='sha256:' + 'f' * 64)]))
        idx = dict(schemaVersion=2, manifests=[dict(digest=md, size=len(mb), platform=dict(os='linux', architecture='arm64'))])
        ide, ib = blob(idx)
        store = {ide: ib, md: mb, cd: cb}
        ref = 'test.invalid/postgres@' + ide
        with mock.patch.object(p, 'reference', return_value=ide), mock.patch.object(p, 'capture', side_effect=lambda argv, *_args, **_kw: store[argv[-1]]):
            result = p.postgres_cache(dict(postgres_reference=ref))
        self.assertEqual(result['target_digest'], ide)
        self.assertEqual(result['manifest_digest'], md)
        self.assertEqual(result['config_digest'], cd)
        self.assertFalse(result['imported'])
        with mock.patch.object(p, 'reference', return_value=cd), mock.patch.object(p, 'capture', return_value=cb):
            with self.assertRaises(p.Failure):
                p.postgres_cache(dict(postgres_reference='test.invalid/postgres@' + cd))

    def test_node_guard_fixed_admin_config_and_pressure_checks(self):
        node = dict(metadata=dict(name='datax-02', uid=PROFILE['target']['uid']), spec={}, status=dict(
            nodeInfo=dict(architecture='arm64', operatingSystem='linux'), addresses=[dict(type='InternalIP', address=PROFILE['target']['ip'])],
            conditions=[dict(type=k, status=v) for k, v in [('Ready', 'True'), ('MemoryPressure', 'False'), ('DiskPressure', 'False'), ('PIDPressure', 'False')]]))
        with mock.patch.object(p, 'capture', return_value=p.encoded(node)) as call:
            result = p.node_guard(PROFILE)
        self.assertIn('--kubeconfig=/etc/kubernetes/admin.conf', call.call_args[0][0])
        self.assertFalse(result['capacity_pass'])
        node['spec']['taints'] = [dict(key='new')]
        with mock.patch.object(p, 'capture', return_value=p.encoded(node)), self.assertRaises(p.Failure):
            p.node_guard(PROFILE)

    def test_bootstrap_only_small_bound_code_no_archive_tty(self):
        command = p.bootstrap_command(b'print("fixture")\n', (ROOT / 'PROFILE.json').read_bytes(), 'a' * 32)
        argv = __import__('shlex').split(command)
        code = argv[argv.index('-c') + 1]
        compile(code, '<bootstrap>', 'exec')
        self.assertIn('os.O_EXCL', code)
        self.assertIn('os.O_NOFOLLOW', code)
        self.assertIn('datax-02', code)
        self.assertNotIn('tarfile', code)
        with self.assertRaises(p.Failure):
            p.bootstrap_command(b'x', b'{}', 'a' * 32)
        with self.assertRaises(p.Failure):
            p.paths('../escape')

    def test_remote_receipt_failure_is_preserved_and_secret_noise_not_parsed(self):
        value = p.base_report('a' * 32)
        value.update(status='INCOMPLETE', error_code='fixture_failure', image_rows=[dict(role='frontend', content_and_unpack='PASS')])
        raw = b'not-a-report-secret\r\n' + p.MARKER.encode() + base64.b64encode(p.encoded(value)) + b'\r\n'
        self.assertEqual(p.remote_result(raw), value)
        self.assertNotIn('secret', json.dumps(p.remote_result(raw)))
        with self.assertRaises(p.Failure):
            p.remote_result(raw + raw)
        self.assertFalse(value['ready_for_rollout'])

    def fixture_archive(self, path, annotation_kind='short', bad_extra=False):
        config = dict(os='linux', architecture='arm64', config=dict(Env=['SECRET=no-log']))
        cd, cb = blob(config)
        md, mb = blob(dict(schemaVersion=2, config=dict(digest=cd, size=len(cb)), layers=[]))
        tag = 'test.invalid/native:fixture'
        annotations = {'io.containerd.image.name': tag, 'org.opencontainers.image.ref.name': 'fixture' if annotation_kind == 'short' else tag}
        if bad_extra:
            annotations['org.opencontainers.image.ref.name'] = 'different'
        idx = dict(schemaVersion=2, manifests=[dict(digest=md, annotations=annotations)])
        with tarfile.open(str(path), 'w') as t:
            for name, raw in [('index.json', p.encoded(idx)), ('blobs/sha256/' + md[7:], mb), ('blobs/sha256/' + cd[7:], cb)]:
                member = tarfile.TarInfo(name)
                member.size = len(raw)
                t.addfile(member, io.BytesIO(raw))
        return dict(tag=tag, manifest_digest=md, config_digest=cd,
                    role='ppt-master-agent-host-forward' if annotation_kind == 'full' else 'frontend')

    def test_archive_actual_buildkit_short_and_host_full_ref_name(self):
        with tempfile.TemporaryDirectory() as temp:
            for kind in ('short', 'full'):
                path = Path(temp) / (kind + '.tar')
                row = self.fixture_archive(path, kind)
                config = p.archive_metadata(path, row)
                self.assertEqual(config['architecture'], 'arm64')
            path = Path(temp) / 'bad.tar'
            row = self.fixture_archive(path, bad_extra=True)
            with self.assertRaises(p.Failure):
                p.archive_metadata(path, row)

    def test_complete_requires_both_references_and_cri_and_unpack(self):
        row = PROFILE['images'][0]
        seen = []
        def cap(argv, *a, **kw):
            seen.append(argv)
            return p.encoded(dict(status=dict(id=row['manifest_digest']))) if 'inspecti' in argv else (argv[-1][6:] + '\n').encode()
        with mock.patch.object(p, 'reference', return_value=row['manifest_digest']), mock.patch.object(p, 'capture', side_effect=cap), mock.patch.object(p, 'manifest_config', return_value=(row['config_digest'], {})):
            result, _ = p.complete_row(row)
        self.assertEqual(result['cri_visibility'], 'PASS')
        self.assertEqual([a[-1] for a in seen if 'inspecti' in a], list(p.refs(row)))

    def test_import_six_exact_scoped_commands_and_keep_partial_receipts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            upload = root / 'upload'
            upload.mkdir()
            s = upload.stat()
            prepared = dict(operation='a' * 32, status='PREPARED_NO_IMPORT', upload_device=s.st_dev, upload_inode=s.st_ino, upload_uid=s.st_uid)
            calls, records = [], []
            def complete(row):
                return dict(role=row['role'], content_and_unpack='PASS'), dict(config=dict(Env=['PATH=/bin']))
            patches = [mock.patch.object(p, 'paths', return_value=dict(state=root, upload=upload)),
                       mock.patch.object(p, 'safe_dir'), mock.patch.object(p, 'read_file', return_value=p.encoded(prepared)),
                       mock.patch.object(p, 'write_new', side_effect=lambda path, raw: records.append((path.name, p.parse(raw)))),
                       mock.patch.object(p, 'target_budget', return_value={}), mock.patch.object(p, 'collision_check'),
                       mock.patch.object(p, 'seal'), mock.patch.object(p, 'archive_metadata'),
                       mock.patch.object(p, 'capture', return_value=p.encoded(dict(imageFilesystems=[dict(fsId=dict(mountpoint='/var/lib/containerd'))]))),
                       mock.patch.object(p, 'run', side_effect=lambda argv, *a, **kw: calls.append(argv)),
                       mock.patch.object(p, 'complete_row', side_effect=complete),
                       mock.patch.object(p, 'postgres_cache', side_effect=p.Failure('not_cached'))]
            from contextlib import ExitStack
            with ExitStack() as stack:
                for patch in patches:
                    stack.enter_context(patch)
                result = p.import_target(PROFILE, 'a' * 32)
            self.assertEqual(result['status'], 'COMPLETE_PRELOAD')
            self.assertEqual(len(calls), 6)
            self.assertEqual(len(result['image_rows']), 6)
            self.assertEqual(result['postgres_cache']['state'], 'NOT_COLLECTED')
            for call, row in zip(calls, PROFILE['images']):
                self.assertIn('--digests', call)
                self.assertEqual(call[call.index('--base-name') + 1], row['repository'])
                self.assertIn('io.cri-containerd.image=managed', call)
                for forbidden in ('--force', 'pull', 'push', 'delete', 'pinned'):
                    self.assertNotIn(forbidden, call)
            self.assertEqual(len([name for name, _ in records if name.endswith('.PASS.json')]), 6)

    def test_python37_parse_and_no_unapproved_network_commands(self):
        code = (ROOT / 'preload_native.py').read_text(encoding='utf-8')
        ast.parse(code, feature_version=(3, 7))
        self.assertNotIn('shell=True', code)
        self.assertNotIn('images", "pull', code)
        self.assertNotIn('io.cri-containerd.pinned=', code)
        self.assertNotIn('kubectl", "exec', code)

    def test_pinned_ssh_key_and_options_reject_unknown_host_key(self):
        key = b'test-key-bytes'
        fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(key).digest()).decode().rstrip('=')
        raw = b'192.0.2.1 ecdsa-sha2-nistp256 ' + base64.b64encode(key) + b'\n'
        value = p.trusted_key(raw, dict(name='datax-02', ssh_fingerprint=fingerprint))
        self.assertTrue(value.startswith(b'datax-02 '))
        with self.assertRaises(p.Failure):
            p.trusted_key(raw, PROFILE['target'])
        options = p.ssh_options('/var/lib/fixed/known_hosts')
        self.assertIn('StrictHostKeyChecking=yes', options)
        self.assertIn('ForwardAgent=no', options)

    @unittest.skipUnless(os.name == 'posix', 'POSIX file-descriptor fixture')
    def test_seal_nofollow_intermediate_symlink_and_digest_failure_kept(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'real').mkdir()
            (root / 'real' / 'input').write_bytes(b'fixture')
            (root / 'alias').symlink_to(root / 'real', target_is_directory=True)
            row = dict(archive_bytes=7, archive_sha256=p.sha(b'fixture'), role='fixture')
            with mock.patch.object(p, 'safe_dir'):
                p.seal(root / 'real' / 'input', root / 'sealed', row)
                with self.assertRaises(OSError):
                    p.seal(root / 'alias' / 'input', root / 'rejected', row)
                row['archive_sha256'] = '0' * 64
                with self.assertRaises(p.Failure):
                    p.seal(root / 'real' / 'input', root / 'retained-partial', row)
            self.assertTrue((root / 'retained-partial').exists())

    @unittest.skipUnless(os.name == 'posix', 'POSIX bounded child fixture')
    def test_real_pipe_limit_and_timeout_never_reemit_stderr(self):
        with self.assertRaises(p.Failure):
            p.run([sys.executable, '-c', 'import os;os.write(1,b"x"*100000);os.write(2,b"secret")'], 'FIXTURE_CAP', limit=1024)
        with self.assertRaises(p.Failure):
            p.run([sys.executable, '-c', 'import time;time.sleep(5)'], 'FIXTURE_TIMEOUT', timeout=0.15)

    @unittest.skipUnless(os.name == 'posix', 'POSIX owned-group fixture')
    def test_exited_leader_does_not_leave_term_ignoring_descendant(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'pids'
            code = 'import os,signal,time\np=os.fork()\nif p==0:\n signal.signal(signal.SIGTERM,signal.SIG_IGN)\n time.sleep(20)\nelse:\n open(' + repr(str(path)) + ',"w").write(str(os.getpid())+" "+str(p))\n time.sleep(.1)\n os._exit(0)\n'
            with self.assertRaises(p.Failure):
                p.run([sys.executable, '-c', code], 'FIXTURE_EXITED_LEADER', timeout=10)
            leader, child = map(int, path.read_text().split())
            proc_stat = Path('/proc') / str(child) / 'stat'
            # SIGKILL delivery is asynchronous; wait for the kernel's bounded
            # transition to exited/zombie, not an immediate post-kill sample.
            import time
            until = time.monotonic() + 1
            while proc_stat.exists() and proc_stat.read_text().split()[2] != 'Z' and time.monotonic() < until:
                time.sleep(.01)
            if proc_stat.exists():
                self.assertEqual(proc_stat.read_text().split()[2], 'Z')

    @unittest.skipUnless(os.name == 'posix', 'POSIX interactive PTY fixture')
    def test_interactive_child_owns_group_restores_foreground_and_cleans_timeout(self):
        import pty
        import select
        import signal
        import time
        pid, master = pty.fork()
        if pid == 0:
            try:
                import termios
                before = os.tcgetpgrp(0)
                attributes = termios.tcgetattr(0)
                _, raw = p.run([sys.executable, '-c', 'import os;print("OK",flush=True)'], 'FIXTURE_TTY', interactive=True)
                assert raw.strip() == b'OK'
                assert os.tcgetpgrp(0) == before
                try:
                    code = 'import os,termios,time\nf=os.open("/dev/tty",os.O_RDWR)\na=termios.tcgetattr(f)\na[3]&=~(termios.ECHO|termios.ICANON)\ntermios.tcsetattr(f,termios.TCSANOW,a)\ntime.sleep(10)'
                    p.run([sys.executable, '-c', code], 'FIXTURE_TTY_TIMEOUT', timeout=.15, interactive=True)
                    os._exit(4)
                except p.Failure:
                    assert os.tcgetpgrp(0) == before
                    assert termios.tcgetattr(0) == attributes
                os._exit(0)
            except BaseException:
                os._exit(3)
        status = None
        try:
            until = time.monotonic() + 10
            while time.monotonic() < until:
                found, result = os.waitpid(pid, os.WNOHANG)
                if found:
                    status = result
                    break
                if select.select([master], [], [], .1)[0]:
                    try:
                        os.read(master, 65536)
                    except OSError:
                        pass
            self.assertIsNotNone(status)
            self.assertEqual(os.waitstatus_to_exitcode(status), 0)
        finally:
            if status is None:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            os.close(master)


if __name__ == '__main__':
    unittest.main()
