"""Test actual delivered package with synthetic files/cluster; no real kube calls."""
import ast
import contextlib
import copy
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

BUNDLE = Path(os.environ['MINERU_BUILD_BUNDLE'])
sys.path.insert(0, str(BUNDLE))
import build_mineru as b
import guard_mineru as g
import verify_candidate as v
import apply_adapter as a
import source_contract as sc
from isolated_buildkit import build_arguments
from verify_built_image import preserved
from resource_canary import ExecutorCanary
from bounded_runc import amend


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir='/opt')
        self.root = Path(self.tmp.name)
        self.plan = json.loads((BUNDLE/'BUILD-PLAN.json').read_bytes())

    def tearDown(self):
        self.tmp.cleanup()

    def package(self):
        dest = self.root/'package'
        shutil.copytree(str(BUNDLE), str(dest))
        dest.chmod(0o700)
        for file in dest.iterdir():
            file.chmod(0o600)
        return dest

    def test_python37_control_syntax(self):
        for file in BUNDLE.glob('*.py'):
            if file.name != 'payload-adapter.py':
                ast.parse(file.read_text(), feature_version=(3, 7))

    def test_integrity(self):
        self.assertEqual(g.verify_package(self.package()), self.plan)

    def test_changed_payload_rejected(self):
        p = self.package()
        (p/'payload-adapter.py').write_bytes(b'changed')
        with self.assertRaisesRegex(g.CheckFailure, 'package_hash_mismatch'):
            g.verify_package(p)

    def test_unknown_package_member_rejected(self):
        p = self.package()
        (p/'extra').write_bytes(b'changed')
        with self.assertRaisesRegex(g.CheckFailure, 'unexpected_package_contents'):
            g.verify_package(p)

    def test_writable_package_rejected(self):
        p = self.package()
        (p/'payload-adapter.py').chmod(0o666)
        with self.assertRaisesRegex(g.CheckFailure, 'unsafe_file'):
            g.verify_package(p)

    def test_linked_package_rejected(self):
        for hard in (True, False):
            with self.subTest(hard=hard):
                p = self.root/('hard' if hard else 'sym')
                p.write_bytes(b'source')
                q = self.root/('linked'+str(hard))
                if hard: os.link(p, q)
                else: q.symlink_to(p)
                with self.assertRaisesRegex(g.CheckFailure, 'unsafe_file'):
                    g.safe_file(q, 1024)

    def test_frozen_modules_not_rewritten(self):
        lock = json.loads((BUNDLE/'BUILD-INFRA-PROVENANCE.json').read_bytes())
        for name, sha in lock['files'].items():
            self.assertEqual(g.sha((BUNDLE/name).read_bytes()), sha)

    def test_saved_report_and_size_binding(self):
        raw = b'archive'
        archive, report = self.root/'base.tar', self.root/'BASE-EXPORT.json'
        archive.write_bytes(raw)
        base = dict(self.plan['base'], archive=str(archive), report=str(report), bytes=len(raw))
        r = dict(base, base_export='PASS', base_layer_verification='PASS', uncompressed_diffids='PASS')
        report.write_text(json.dumps(r))
        base['report_sha256'] = g.sha(report.read_bytes())
        self.assertEqual(g.archive_input(dict(self.plan, base=base)), archive)
        report.write_bytes(report.read_bytes()+b' ')
        with self.assertRaisesRegex(g.CheckFailure, 'base_report_hash_changed'):
            g.archive_input(dict(self.plan, base=base))

    def test_export_seal_only_new_files(self):
        p = self.root/'mineru-build-metadata.json'
        p.write_bytes(b'{}')
        p.chmod(0o666)
        g.seal_new_export(p, self.root, 1024)
        self.assertEqual(p.stat().st_mode & 0o777, 0o600)
        old = self.root/'old.tar'
        old.write_bytes(b'keep')
        with self.assertRaisesRegex(g.CheckFailure, 'not_owned_new_export'):
            g.seal_new_export(old, self.root, 1024)

    def test_seal_requires_private_output(self):
        p = self.root/'mineru-build-metadata.json'
        p.write_bytes(b'{}')
        self.root.chmod(0o755)
        with self.assertRaisesRegex(g.CheckFailure, 'unsafe_output_directory'):
            g.seal_new_export(p, self.root, 1024)

    def test_canary_uses_correct_venv_and_no_network(self):
        value = b.canary_dockerfile('ppt_runtime_resource_ready_'+'a'*32)
        self.assertIn('/app/.venv/bin/python -I -S', value)
        self.assertIn('--network=none', value)
        with self.assertRaises(g.CheckFailure): b.canary_dockerfile('unsafe')

    def test_canary_requires_real_stdout_and_three_samples(self):
        token = 'ppt_runtime_resource_ready_'+'b'*32
        c = ExecutorCanary(token)
        c.sample(io.BytesIO(('#1 RUN echo '+token+'\n').encode()), lambda: True)
        with self.assertRaises(RuntimeError): c.verify()
        c.sample(io.BytesIO(('#1 0.01 '+token+'\n').encode()), lambda: True)
        with self.assertRaises(RuntimeError): c.verify()
        c.sample(io.BytesIO(), lambda: True)
        c.sample(io.BytesIO(), lambda: True)
        c.verify()

    def test_bounded_runtime_cannot_touch_unowned_cgroups(self):
        settings = {'slice': 'pptrelay'+'a'*16+'.slice', 'device': '253:0'}
        with self.assertRaises(ValueError): amend({'linux': {'cgroupsPath': '/production/foo'}}, settings)
        spec = amend({'linux': {'cgroupsPath': '/'+settings['slice']+'/buildkit/fixture'}}, settings)
        self.assertEqual(spec['linux']['resources']['blockIO']['throttleReadBpsDevice'][0]['rate'], 80*g.GIB//1024//3)

    def test_build_arguments_are_private_arm64_offline(self):
        args = build_arguments('buildctl', 'unix:///opt/private.sock', '/opt/package', 'mineru',
                               '/opt/base', 'sha256:'+'a'*64, '/ownedslice')
        self.assertIn('force-network-mode=none', args)
        self.assertIn('platform=linux/arm64', args)
        self.assertIn('mineru_base=/opt/base', args)
        self.assertNotIn('containerd.sock', ' '.join(args))

    def test_final_stage_one_file_no_runtime_override(self):
        dockerfile = (BUNDLE/'Dockerfile.mineru').read_text()
        final = dockerfile.split(' AS candidate\n')[1].strip()
        self.assertEqual(final, 'COPY --from=checked /app/deerflow_mineru_adapter.py /app/deerflow_mineru_adapter.py')
        for forbidden in ('ENV ', 'CMD ', 'ENTRYPOINT ', 'pip install', 'apt-get', 'curl '):
            self.assertNotIn(forbidden, dockerfile)

    def test_runtime_config_and_prefix_protected(self):
        base = {'config': {'Entrypoint': ['original']}, 'rootfs': {'diff_ids': ['base']}}
        candidate = copy.deepcopy(base)
        candidate['rootfs']['diff_ids'].append('delta')
        preserved(base, candidate)
        candidate['config']['Entrypoint'] = ['other']
        with self.assertRaises(ValueError): preserved(base, candidate)

    def cluster(self):
        deps = {}
        plan = copy.deepcopy(self.plan)
        for name, expected in plan['deployments'].items():
            dep = {'metadata': {'uid': expected['uid']}, 'spec': {'selector': {'matchLabels': {'app': name}},
                'template': {'spec': {'containers': [{'name': expected.get('container', 'app'), 'image': plan['base']['image']}]}}},
                'status': {'readyReplicas': expected['minimum_ready']}}
            deps[name] = dep
            expected['spec_sha256'] = g.fingerprint(dep)['spec_sha256']
        calls = []
        def kube(*args):
            calls.append(args)
            self.assertIn(args[0], ('get', 'exec'))
            self.assertNotIn('secrets', args)
            if args[0] == 'exec':
                return {'source_probe': 'PASS', 'source_sha256': plan['source']['before_sha256'],
                        'metadata': dict(sc.BASE_METADATA)}
            if args[1] == 'node':
                return {'status': {'conditions': [{'type': k, 'status': v} for k, v in
                    [('Ready', 'True'), ('DiskPressure', 'False'), ('MemoryPressure', 'False'), ('PIDPressure', 'False')]]}}
            if args[1] == 'deployment': return deps[args[2]]
            self.assertIn('--selector', args)
            name = args[args.index('--selector')+1].split('=', 1)[1]
            expected = plan['deployments'][name]
            if args[1] == 'replicasets':
                return {'items': [{'metadata': {'uid': name+'-rs', 'ownerReferences':
                    [{'kind': 'Deployment', 'uid': expected['uid'], 'controller': True}]}}]}
            return {'items': [{'metadata': {'name': name+'-'+str(i), 'ownerReferences':
                [{'kind': 'ReplicaSet', 'uid': name+'-rs', 'controller': True}]},
                'spec': deps[name]['spec']['template']['spec'],
                'status': {'phase': 'Running', 'containerStatuses': [{'name': expected['container'],
                    'ready': True, 'imageID': plan['base']['config_digest']}]}}
                for i in range(expected['minimum_ready'])]}
        return plan, deps, calls, kube

    def test_live_source_audit_selected_owned_pods(self):
        plan, deps, calls, kube = self.cluster()
        with patch.object(g, 'kube', side_effect=kube), contextlib.redirect_stdout(io.StringIO()):
            state = g.live_state(plan, source=True)
        self.assertEqual(len(state), 4)
        self.assertEqual(sum(c[0] == 'exec' for c in calls), 7)

    def test_live_deployment_drift_blocks(self):
        plan, deps, calls, kube = self.cluster()
        deps['weknora-app']['spec']['replicas'] = 7
        with patch.object(g, 'kube', side_effect=kube), self.assertRaisesRegex(g.CheckFailure, 'baseline_changed'):
            g.live_state(plan)

    def test_live_readiness_drop_blocks(self):
        plan, deps, calls, kube = self.cluster()
        deps['deer-flow-mineru-adapter']['status']['readyReplicas'] = 5
        with patch.object(g, 'kube', side_effect=kube), self.assertRaisesRegex(g.CheckFailure, 'readiness_below_baseline'):
            g.live_state(plan)

    def test_live_wrong_source_blocks(self):
        plan, deps, calls, kube = self.cluster()
        def changed(*args):
            return {'source_sha256': 'changed'} if args[0] == 'exec' else kube(*args)
        with patch.object(g, 'kube', side_effect=changed), self.assertRaisesRegex(g.CheckFailure, 'live_source_changed'):
            g.live_state(plan, source=True)

    def test_live_legacy_permission_is_exact_not_general_writable_bypass(self):
        for field, value in (('mode', '0777'), ('mode', '0664'), ('uid', 1000), ('gid', 1000),
                             ('bytes', 119417), ('links', 2)):
            with self.subTest(field=field, value=value):
                plan, deps, calls, kube = self.cluster()
                def changed(*args):
                    result = kube(*args)
                    if args[0] == 'exec':
                        result['metadata'][field] = value
                    return result
                with patch.object(g, 'kube', side_effect=changed), self.assertRaisesRegex(g.CheckFailure, 'live_source_changed'):
                    g.live_state(plan, source=True)

    def test_live_source_errors_are_named_and_no_raw_output(self):
        plan, deps, calls, kube = self.cluster()
        def changed(*args):
            return {'source_probe': 'FAIL', 'failed_check': 'source_metadata_changed'} if args[0] == 'exec' else kube(*args)
        with patch.object(g, 'kube', side_effect=changed), self.assertRaisesRegex(g.CheckFailure, 'live_source_probe:source_metadata_changed'):
            g.live_state(plan, source=True)
        with patch.object(g, 'run', side_effect=RuntimeError('private raw detail')):
            with self.assertRaisesRegex(g.CheckFailure, '^kubernetes_source_read_failed:fixture$'):
                g.kube('exec', 'fixture')

    def test_source_contract_requires_exact_plan(self):
        sc.validate_plan(self.plan['source'])
        for field, value in (('path', '/app/other.py'), ('before_sha256', 'b'*64),
                             ('after_sha256', 'c'*64), ('candidate_mode', '0666'), ('before_metadata', {})):
            changed = dict(self.plan['source'], **{field: value})
            with self.subTest(field=field), self.assertRaises(sc.SourceCheck):
                sc.validate_plan(changed)

    def test_explicit_writable_mode_cannot_create_second_exception(self):
        p = self.root/'unrelated.py'
        p.write_bytes(b'x=1\n')
        for mode in (0o664, 0o777):
            p.chmod(mode)
            with self.assertRaisesRegex(sc.SourceCheck, 'unapproved_writable_mode'):
                sc.read_verified(p, g.sha(p.read_bytes()), mode=mode)

    def legacy_fixture(self):
        old = b'# pinned legacy bytes\n' + b' '*(119416-len(b'# pinned legacy bytes\n'))
        new = b'x=2\n'
        p = self.root/'adapter.py'
        p.write_bytes(old)
        p.chmod(0o666)
        plan = dict(self.plan['source'], path=str(p), before_sha256=g.sha(old), after_sha256=g.sha(new))
        return p, old, new, plan

    def test_legacy_exact_source_read_then_atomic_hardening(self):
        p, old, new, plan = self.legacy_fixture()
        with patch.object(sc, 'SOURCE_PATH', p), patch.object(sc, 'BASE_SHA', g.sha(old)), \
             patch.object(sc, 'CANDIDATE_SHA', g.sha(new)):
            before = p.stat()
            self.assertEqual(sc.probe_base()['source_probe'], 'PASS')
            self.assertEqual(p.stat().st_mode, before.st_mode)
            a.apply(p, plan, new)
            self.assertEqual(p.read_bytes(), new)
            self.assertEqual(p.stat().st_mode & 0o7777, 0o644)
            self.assertEqual(sc.probe_base()['source_probe'], 'FAIL')
        self.assertEqual(list(self.root.iterdir()), [p])

    def test_legacy_permission_failure_does_not_replace_original(self):
        p, old, new, plan = self.legacy_fixture()
        with patch.object(sc, 'SOURCE_PATH', p), patch.object(sc, 'BASE_SHA', g.sha(old)), \
             patch.object(sc, 'CANDIDATE_SHA', g.sha(new)), patch.object(a.os, 'fchmod', side_effect=OSError('fixture')):
            with self.assertRaises(OSError):
                a.apply(p, plan, new)
        self.assertEqual(p.read_bytes(), old)
        self.assertEqual(p.stat().st_mode & 0o7777, 0o666)
        self.assertEqual(list(self.root.iterdir()), [p])

    def test_legacy_read_rejects_other_modes_hash_owner_and_links(self):
        p, old, new, plan = self.legacy_fixture()
        with patch.object(sc, 'SOURCE_PATH', p), patch.object(sc, 'BASE_SHA', g.sha(old)):
            for mode in (0o664, 0o660, 0o777, 0o644, 0o1666):
                p.chmod(mode)
                self.assertEqual(sc.probe_base()['source_probe'], 'FAIL')
            p.chmod(0o666)
            os.chown(str(p), 0, 1000)
            self.assertEqual(sc.probe_base()['source_probe'], 'FAIL')
            os.chown(str(p), 0, 0)
            p.write_bytes(b'!' + old[1:])
            self.assertEqual(sc.probe_base()['failed_check'], 'source_hash_mismatch')
            p.write_bytes(old)
            link = self.root/'linked'
            os.link(p, link)
            self.assertEqual(sc.probe_base()['source_probe'], 'FAIL')
            link.unlink()
            p.rename(link)
            p.symlink_to(link)
            self.assertEqual(sc.probe_base()['source_probe'], 'FAIL')

    def test_legacy_rejects_other_path_and_unsafe_parent(self):
        p, old, new, plan = self.legacy_fixture()
        with patch.object(sc, 'BASE_SHA', g.sha(old)):
            with self.assertRaisesRegex(sc.SourceCheck, 'legacy_source_identity_not_pinned'):
                sc.read_verified(p, g.sha(old), mode=0o666, size=119416)
        with patch.object(sc, 'SOURCE_PATH', p), patch.object(sc, 'BASE_SHA', g.sha(old)):
            self.root.chmod(0o777)
            self.assertEqual(sc.probe_base()['failed_check'], 'unsafe_source_parent')

    def test_live_probe_is_stdlib_only_read_receipt(self):
        p, old, new, plan = self.legacy_fixture()
        code = g.SOURCE_PROBE.replace("Path('/app/deerflow_mineru_adapter.py')", 'Path('+repr(str(p))+')')
        code = code.replace(sc.BASE_SHA, g.sha(old))
        before, output = p.stat(), io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(compile(code, '<isolated-probe-fixture>', 'exec'), {})
        receipt = json.loads(output.getvalue())
        self.assertEqual(receipt['source_probe'], 'PASS')
        self.assertEqual(receipt['metadata'], sc.BASE_METADATA)
        self.assertEqual(p.stat().st_mode, before.st_mode)
        self.assertEqual(p.stat().st_mtime_ns, before.st_mtime_ns)

    def test_read_race_blocks_before_trusting_hash(self):
        p, old, new, plan = self.legacy_fixture()
        original_read = sc.os.read
        changed = [False]
        def racing_read(fd, size):
            raw = original_read(fd, size)
            if not changed[0]:
                changed[0] = True
                p.write_bytes(old)
            return raw
        with patch.object(sc, 'SOURCE_PATH', p), patch.object(sc, 'BASE_SHA', g.sha(old)), \
             patch.object(sc.os, 'read', side_effect=racing_read):
            self.assertEqual(sc.probe_base()['failed_check'], 'source_changed_during_read')

    def test_source_mount_rejected(self):
        for mount in ('/', '/app', '/app/deerflow_mineru_adapter.py'):
            spec = {'containers': [{'name': 'adapter', 'image': self.plan['base']['image'], 'volumeMounts': [{'mountPath': mount}]}]}
            with self.subTest(mount=mount), self.assertRaises(g.CheckFailure):
                g.source_container(spec, 'adapter', self.plan)

    def test_space_budget_and_root_reserve(self):
        free = {'/': 68, '/opt': 900}
        def statfs(path):
            key = '/' if str(path) == '/' else '/opt'
            return types.SimpleNamespace(f_bavail=free[key], f_frsize=g.GIB, f_blocks=295 if key=='/' else 3000,
                                         f_favail=100001, f_flag=0)
        with patch.object(g.os, 'stat', side_effect=lambda p: types.SimpleNamespace(st_dev=1 if str(p)=='/' else 2)), \
             patch.object(g.os, 'statvfs', side_effect=statfs):
            guard = g.SpaceGuard(Path('/opt/new'))
            self.assertEqual(guard.check()['peak_build_budget_GiB'], 45)
            free['/opt'] -= 44
            guard.check()
            free['/opt'] -= 2
            with self.assertRaisesRegex(g.CheckFailure, 'budget_breached'): guard.check()
            free['/opt'], free['/'] = 900, 60
            with self.assertRaisesRegex(g.CheckFailure, 'root_disk_reserve'): g.SpaceGuard(Path('/opt/new')).check()
            free['/'], free['/opt'] = 68, 644
            with self.assertRaisesRegex(g.CheckFailure, 'initial_build_space'): g.SpaceGuard(Path('/opt/new')).check()

    def test_five_phase_build_orchestration_no_deploy(self):
        builder = object.__new__(b.Builder)
        builder.package, builder.output, builder.plan = BUNDLE, self.root, self.plan
        builder.binary, builder.address, builder.cgroup = {'buildctl': 'buildctl'}, 'unix:///private', '/slice'
        builder.slice, builder.production_state = 'own.slice', {'unchanged': True}
        builder.space, builder.guard = MagicMock(), MagicMock()
        builder.space.check.return_value = {'disk_check': 'PASS'}
        phases = []
        def phase(label, args, canary=False):
            phases.append(label)
            if label == 'stage-mineru':
                result = {k: self.plan['base'][k] for k in ('manifest_digest', 'config_digest', 'compressed_layer_bytes')}
                return json.dumps(dict(result, audited_oci_base='PASS', layers_verified=26)).encode()
            if label == 'resource-canary': self.assertTrue(canary.startswith('ppt_runtime_resource_ready_'))
            if label == 'candidate-mineru':
                (self.root/'mineru.candidate.oci.tar.partial').write_bytes(b'candidate')
                (self.root/'mineru-build-metadata.json').write_bytes(b'{}')
            if label == 'verify-mineru':
                return json.dumps({'audited_oci_base': 'PASS', 'archive_sha256': 'a'*64}).encode()
            return b''
        builder.phase = phase
        with patch.object(b, 'archive_input', return_value=Path('/opt/base')), contextlib.redirect_stdout(io.StringIO()):
            result = builder.build()
        self.assertEqual(phases, ['stage-mineru', 'resource-canary', 'source-check-mineru', 'candidate-mineru', 'verify-mineru'])
        self.assertEqual(set(result['components']), {'mineru-adapter'})
        for flag in ('images_imported', 'ready_for_rollout', 'concurrency_changed', 'credentials_saved', 'production_workloads_mutated'):
            self.assertIs(result[flag], False)

    def test_delta_only_expected_regular_source(self):
        for case in ('good', 'extra', 'symlink', 'wrong-hash', 'writable', 'traversal', 'wrong-group', 'executable', 'private-mode'):
            with self.subTest(case=case):
                raw = b'synthetic_source'
                buf = io.BytesIO()
                with tarfile.open(fileobj=buf, mode='w') as tar:
                    names = ['app/deerflow_mineru_adapter.py'] + (['etc/other'] if case=='extra' else [])
                    for name in names:
                        m = tarfile.TarInfo('../escape' if case=='traversal' else name)
                        m.size, m.mode = len(raw), (0o666 if case=='writable' else 0o644)
                        if case == 'wrong-group': m.gid = 1000
                        if case == 'executable': m.mode = 0o755
                        if case == 'private-mode': m.mode = 0o600
                        if case=='symlink':
                            m.type, m.linkname, m.size = tarfile.SYMTYPE, '/etc/shadow', 0
                            tar.addfile(m)
                        else: tar.addfile(m, io.BytesIO(raw))
                compressed = gzip.compress(buf.getvalue())
                path = self.root/('layer-'+case)
                path.write_bytes(compressed)
                layer = {'size': len(compressed), 'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip'}
                expected = g.sha(raw) if case != 'wrong-hash' else 'f'*64
                if case == 'good': v.delta_members(path, layer, expected)
                else:
                    with self.assertRaises(g.CheckFailure): v.delta_members(path, layer, expected)

    def test_atomic_apply_preserves_permissions_and_only_target(self):
        p = self.root/'adapter.py'
        old, new = b'x=1\n', b'x=2\n'
        p.write_bytes(old)
        p.chmod(0o640)
        plan = {'before_sha256': g.sha(old), 'after_sha256': g.sha(new)}
        a.apply(p, plan, new)
        self.assertEqual(p.read_bytes(), new)
        self.assertEqual(p.stat().st_mode & 0o777, 0o640)
        self.assertEqual(list(self.root.iterdir()), [p])

    def test_bad_payload_or_permission_failure_leaves_old_source(self):
        for issue in ('payload', 'permissions'):
            p = self.root/('source-'+issue)
            old, new = b'x=1\n', b'x=2\n'
            p.write_bytes(old)
            plan = {'before_sha256': g.sha(old), 'after_sha256': g.sha(new)}
            if issue == 'payload':
                with self.assertRaises(RuntimeError): a.apply(p, plan, b'invalid')
            else:
                with patch.object(a.os, 'fchmod', side_effect=OSError('fixture')):
                    with self.assertRaises(OSError): a.apply(p, plan, new)
            self.assertEqual(p.read_bytes(), old)
        self.assertFalse(any(p.name.startswith('.mineru-candidate-') for p in self.root.iterdir()))

    def test_full_oci_verifier_cli(self):
        def serialize(value): return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
        def digest(raw): return 'sha256:'+g.sha(raw)
        def layer_file(name, raw):
            out = io.BytesIO()
            with tarfile.open(fileobj=out, mode='w') as t:
                m = tarfile.TarInfo(name)
                m.size, m.mode = len(raw), 0o644
                t.addfile(m, io.BytesIO(raw))
            return out.getvalue()
        for case in ('good', 'runtime', 'diffid', 'extra-file', 'prefix'):
            with self.subTest(case=case):
                root = self.root/case
                root.mkdir(mode=0o700)
                source = b'x=2\n'
                oldraw = layer_file('app/deerflow_mineru_adapter.py', b'x=1\n')
                newraw = layer_file('etc/unapproved' if case=='extra-file' else 'app/deerflow_mineru_adapter.py', source)
                oldblob, newblob = gzip.compress(oldraw), gzip.compress(newraw)
                layer = lambda blob: {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip',
                                      'digest': digest(blob), 'size': len(blob)}
                oldcfg = {'os': 'linux', 'architecture': 'arm64', 'config': {'Entrypoint': ['original']},
                          'rootfs': {'type': 'layers', 'diff_ids': [digest(oldraw)]}}
                newcfg = copy.deepcopy(oldcfg)
                newcfg['rootfs']['diff_ids'].append('sha256:'+'f'*64 if case=='diffid' else digest(newraw))
                if case=='runtime': newcfg['config']['Entrypoint'] = ['changed']
                def metadata(cfg, layers):
                    raw = serialize(cfg)
                    manifest = serialize({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json',
                        'config': {'mediaType': 'application/vnd.oci.image.config.v1+json', 'digest': digest(raw), 'size': len(raw)},
                        'layers': layers})
                    return raw, manifest
                oldc, oldm = metadata(oldcfg, [layer(oldblob)])
                first = layer(oldblob)
                if case=='prefix': first['annotations'] = {'unexpected': 'change'}
                newc, newm = metadata(newcfg, [first, layer(newblob)])
                base = root/'base'
                (base/'blobs/sha256').mkdir(parents=True)
                for raw in (oldc, oldm, oldblob): (base/'blobs/sha256'/digest(raw)[7:]).write_bytes(raw)
                image = 'deerflow.local/test:candidate'
                index = {'schemaVersion': 2, 'manifests': [{'mediaType': 'application/vnd.oci.image.manifest.v1+json',
                    'digest': digest(newm), 'size': len(newm), 'annotations': {'io.containerd.image.name': image}}]}
                files = {'oci-layout': serialize({'imageLayoutVersion': '1.0.0'}), 'index.json': serialize(index)}
                for raw in (newc, newm, oldblob, newblob): files['blobs/sha256/'+digest(raw)[7:]] = raw
                archive = root/'candidate.tar'
                with tarfile.open(str(archive), 'w') as t:
                    for name, raw in files.items():
                        m = tarfile.TarInfo(name)
                        m.size = len(raw)
                        t.addfile(m, io.BytesIO(raw))
                args = ['verify_candidate.py', '--archive', str(archive), '--output', str(root/'checked'), '--base', str(base), '--image', image]
                for name, data in [('metadata', {'containerimage.digest': digest(newm)}),
                    ('base-receipt', {'manifest_digest': digest(oldm)}),
                    ('plan', {'base': {'layers': 1, 'uncompressed_layer_bytes': len(oldraw)}, 'source': {'after_sha256': g.sha(source)}})]:
                    p = root/(name+'.json')
                    p.write_bytes(serialize(data))
                    args += ['--'+name, str(p)]
                output = io.StringIO()
                with patch.object(sys, 'argv', args), contextlib.redirect_stdout(output):
                    if case=='good':
                        v.main()
                        self.assertEqual(json.loads(output.getvalue().splitlines()[-1])['source_delta'], 'ONE_FILE_ONLY')
                    else:
                        with self.assertRaises((ValueError, RuntimeError)): v.main()


if __name__ == '__main__':
    unittest.main(verbosity=2)
