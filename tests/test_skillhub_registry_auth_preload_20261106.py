"""Offline safety/identity regressions; never contacts production/containerd."""

import ast

import gzip

import hashlib

import importlib.util

import io

import json

import os

from pathlib import Path

import tarfile

import tempfile

import time

import types

from unittest.mock import patch

import unittest

import contextlib

import sys

ROOT = Path(__file__).resolve().parents[1]

TOOL = ROOT / 'deploy/enterprise/preload-portal-skillhub-registry-auth-datax01-20261106-r1.py'

GEN = ROOT / 'deploy/enterprise/package-skillhub-registry-auth-preload-20261106.py'

def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value

def make_scope():
    value = module(TOOL, 'skillhub_preload_test')
    value.HOST = 'datax-01'
    return value

def rows(name, digest=None):
    header = b'REF TYPE DIGEST SIZE PLATFORMS LABELS\n'
    return header if digest is None else header + (name+' application/vnd.oci.image.index.v1+json '+digest+' 1.0 MiB linux/arm64 -\n').encode()

def success(raw):
    return raw, b'', {'returncode': 0, 'error_code': None}

def controller(scope):
    I, R, F = scope.load_inputs()
    return scope.controller_class(I, R, F)('ctr', 'crictl', 'kubectl', 'a'*32, None,
        {'bundle_sha256': scope.TRANSPORT_PIN['sha256'], 'append_layer': {}}, time.monotonic())

def node_raw(scope, uid=None, ready='True', pressure='False'):
    return ('\n'.join([uid or scope.NODES['datax-01'], 'datax-01', 'arm64', 'linux', 'Ready='+ready, 'DiskPressure='+pressure])+'\n').encode()

def kubelet(R):
    return json.dumps({'kubeletconfig': {'imageGCHighThresholdPercent': 85,
        'imageGCLowThresholdPercent': 80, 'evictionHard': R.EXPECTED_HARD}}).encode()

def run_main_before_claim(scope, monkeypatch, *, exists=False, error=None, unknown_node=False):
    monkeypatch.setattr(scope.os, 'uname', lambda: types.SimpleNamespace(nodename='datax-01', machine='aarch64'), raising=False)
    monkeypatch.setattr(scope.os, 'geteuid', lambda: 0, raising=False)
    monkeypatch.setattr(scope.os, 'name', 'posix')
    monkeypatch.setattr(scope.sys, 'flags', types.SimpleNamespace(isolated=True, dont_write_bytecode=True))
    monkeypatch.setattr(scope.sys, 'argv', [str(TOOL), '--preload-skillhub-registry-auth-once'])
    monkeypatch.setattr(scope.shutil, 'which', lambda name, **kw: '/usr/bin/'+name)
    monkeypatch.setattr(scope, 'trusted_directory', lambda path: 901)
    monkeypatch.setattr(scope.os.path, 'lexists', lambda path: exists)
    monkeypatch.setattr(scope.os, 'close', lambda fd: None)
    def fresh(*args):
        if unknown_node: raise scope.Refused('fresh_datax01_node_identity')
        return {'node': {'uid':scope.NODES['datax-01']}}
    monkeypatch.setattr(scope, 'fresh_controlplane', fresh)
    def early(*args):
        if error: raise error
        return {'pass': True}
    monkeypatch.setattr(scope, 'early_preload_gate', early)
    with patch.object(scope.os, 'mkdir') as mkdir:
        assert scope.main() == 1
        mkdir.assert_not_called()

def synthetic_archive(scope, monkeypatch, tmp_path, *, bad_hash=False, extra=False):
    layer_stream = io.BytesIO()
    with tarfile.open(fileobj=layer_stream, mode='w', format=tarfile.USTAR_FORMAT) as layer:
        item = tarfile.TarInfo('small'); item.size = 2
        layer.addfile(item, io.BytesIO(b'ok'))
    raw = layer_stream.getvalue(); compressed = gzip.compress(raw, mtime=0)
    diff = 'sha256:'+hashlib.sha256(raw).hexdigest()
    ld = 'sha256:'+hashlib.sha256(compressed).hexdigest()
    cr = json.dumps({'os':'linux','architecture':'arm64','rootfs':{'type':'layers','diff_ids':[diff]}}).encode()
    cd = 'sha256:'+hashlib.sha256(cr).hexdigest()
    manifest = {'schemaVersion':2,'mediaType':'application/vnd.oci.image.manifest.v1+json','config':{'digest':cd,'size':len(cr)},'layers':[{'digest':ld,'size':len(compressed),'mediaType':'application/vnd.oci.image.layer.v1.tar+gzip'}]}
    mr = json.dumps(manifest).encode(); md = 'sha256:'+hashlib.sha256(mr).hexdigest()
    for key, value in dict(MANIFEST=md, CONFIG=cd, FINAL_CHAIN=diff, MANIFEST_BYTES=len(mr), CONFIG_BYTES=len(cr), LAYER_COUNT=1).items():
        monkeypatch.setattr(scope, key, value)
    index = {'schemaVersion':2,'manifests':[{'digest':md,'size':len(mr),'mediaType':manifest['mediaType']}]}
    data = {'index.json':json.dumps(index).encode(), 'oci-layout':b'{"imageLayoutVersion":"1.0.0"}', 'blobs/sha256/'+md[7:]:mr, 'blobs/sha256/'+cd[7:]:cr, 'blobs/sha256/'+ld[7:]: compressed}
    if bad_hash: data['blobs/sha256/'+ld[7:]] = b'x' * len(compressed)
    if extra: data['extra'] = b'not allowed'
    output = tmp_path/'synthetic.tar'
    with tarfile.open(output, 'w', format=tarfile.USTAR_FORMAT) as archive:
        for name, content in data.items():
            item = tarfile.TarInfo(name); item.size = len(content)
            archive.addfile(item, io.BytesIO(content))
    return output


class Patches:
    def __init__(self, stack): self.stack = stack
    def setattr(self, owner, name, value, raising=True):
        self.stack.enter_context(patch.object(owner, name, value, create=not raising))

class Capture:
    def __init__(self, stream): self.stream = stream
    def readouterr(self):
        value = self.stream.getvalue()
        self.stream.seek(0); self.stream.truncate(0)
        return types.SimpleNamespace(out=value)

class SkillhubPreloadTests(unittest.TestCase):
    def setUp(self):
        self.scope = make_scope()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.tmp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.tmp = types.SimpleNamespace(name=self.tmp)
        self.monkeypatch = Patches(self.stack)
        stream = self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.capture = Capture(stream)

    def test_exact_mutation_commands_delegate_without_force(self):
        scope = self.scope
        c = controller(scope)
        c.archive_fd = 41
        base = ['images', 'import', '--local', '--platform', 'linux/arm64', '--snapshotter', 'overlayfs', '--label', 'io.cri-containerd.image=managed']
        for args, fds in [(base+['/proc/self/fd/41'], (41,)),
                          (base+['--no-unpack','/proc/self/fd/41'], (41,)),
                          (['images','tag','--local',c.temp,scope.TAG], ())]:
            with patch.object(c.I.Importer, 'call', return_value=b'accepted') as underlying:
                self.assertEqual(c.call('fixed', args, mutates=True, pass_fds=fds), b'accepted')
                underlying.assert_called_once()
        with self.assertRaisesRegex(scope.Refused, 'mutation_scope'):
            c.call('bad_fd', base+['/proc/self/fd/41'], mutates=True, pass_fds=())

    def test_import_interruption_preserves_uncertain_phase_and_no_retry(self):
        scope = self.scope
        c = controller(scope)
        c.archive_fd = 41
        c.disk = lambda: {'pass':True}
        c.preserved = lambda: None
        receipts = []
        c.checkpoint = lambda name: receipts.append(name)
        def fail(*args, **kwargs): raise scope.Refused('command_timeout')
        c.call = fail
        with patch.object(scope.os, 'lseek'), self.assertRaisesRegex(scope.Refused, 'command_timeout'):
            c.import_phase('phase_a', True)
        self.assertEqual(receipts, ['phase_a-REQUESTED'])
        self.assertTrue(c.report['image_import_requested'])
        self.assertTrue(c.report['image_import_may_be_partial'])
        self.assertEqual(c.report['phase_a'], 'REQUESTED')
        self.assertFalse(c.report['automatic_retry'])

    def test_full_preload_two_phases_preserve_old_index_and_absent_aliases(self):
        scope = self.scope
        c = controller(scope)
        c.p = {'manifest':{}}
        refs = dict(scope.PROTECTED)
        c.target = lambda name: refs.get(name)
        phases = []
        def phase(name, no_unpack):
            phases.append((name,no_unpack))
            refs[c.temp] = scope.MANIFEST
        c.import_phase = phase
        c.closure = lambda *args: None
        def verify(publish=False):
            self.assertTrue(publish)
            refs[scope.TAG] = refs[scope.REPO+'@'+scope.MANIFEST] = scope.MANIFEST
        c.verify = verify
        c.fresh_budget = lambda: {'pass':True}
        result = c.execute_import()
        self.assertEqual(phases, [('phase_a',True),('phase_b',False)])
        self.assertEqual(result['status'], 'SKILLHUB_AUTH_CACHE_PRELOADED_NOT_DEPLOYED')
        self.assertTrue(result['old_references_preserved'])
        self.assertEqual({name:refs[name] for name in scope.PROTECTED}, scope.PROTECTED)
        self.assertTrue(all(name not in refs for name in scope.OPTIONAL_PROTECTED))
        self.assertFalse(result['ready_for_rollout'])

    def test_already_cached_candidate_is_verified_without_import(self):
        scope = self.scope
        c = controller(scope)
        refs = dict(scope.PROTECTED)
        refs.update({scope.TAG:scope.MANIFEST,scope.REPO+'@'+scope.MANIFEST:scope.MANIFEST})
        c.target = lambda name: refs.get(name)
        c.fresh_budget = lambda: {'pass':True}
        with patch.object(c,'import_phase') as phase, patch.object(c,'verify') as verify:
            result = c.execute_import()
        phase.assert_not_called()
        verify.assert_called_once_with()
        self.assertTrue(result['import_skipped_already_complete'])

    def test_source_symlink_is_refused_before_copy(self):
        if os.name != 'posix': self.skipTest('Linux symlink path semantics')
        scope = self.scope
        root = Path(self.tmp.name)
        source = root/'source'
        source.write_bytes(b'not an image')
        alias = root/'alias'
        alias.symlink_to(source)
        self.monkeypatch.setattr(scope, 'INPUT', str(alias))
        with self.assertRaisesRegex(scope.Refused, 'input_parent_symlink'):
            scope.seal_source(None, time.monotonic())

    def test_full_fifteen_layer_controller_stays_within_command_budget(self):
        """Actual controller flow with synthetic runtime responses, not an import."""
        scope = self.scope
        for aliases_present in (False, True):
            with self.subTest(aliases_present=aliases_present):
                c = controller(scope)
                c.archive_fd = 41
                p = dict(scope.EARLY_BUDGET)
                # Runtime readback is mocked. Real archive byte/hash/diff_id
                # checks are separately covered by the generator and tests.
                p['manifest_raw'] = scope.canonical(p['manifest'])
                p['config_raw'] = b'{"synthetic_readback":true}'
                refs = dict(scope.PROTECTED)
                if aliases_present: refs.update(scope.OPTIONAL_PROTECTED)
                state = {'imported': False, 'unpacked': False}
                saved = []
                blobs = set(p['blob_sizes'])
                old_blobs = blobs - {scope.MANIFEST,scope.CONFIG,p['manifest']['layers'][-1]['digest']}
                def runner(argv, **kwargs):
                    if argv[0] == 'kubectl':
                        args = argv[4:]
                        if args[:3] == ['get','node','datax-01']:
                            return success(node_raw(scope))
                        if args == ['get','--raw','/api/v1/nodes/datax-01/proxy/configz']:
                            return success(kubelet(c.R))
                        self.fail('unexpected kubectl read '+repr(args))
                    args = argv[5:]
                    if argv[0] == 'crictl':
                        if args == ['imagefsinfo','--output','json']:
                            return success(json.dumps({'imageFilesystems':[{'fsId':{'mountpoint':scope.EXPECTED_IMAGEFS['snapshot']}}]}).encode())
                        if args[:2] == ['inspecti','--output=json']:
                            self.assertTrue(state['unpacked'])
                            return success(json.dumps({'status':{'id':scope.CONFIG,'repoTags':[scope.TAG], 'repoDigests':[scope.REPO+'@'+scope.MANIFEST]}}).encode())
                        self.fail('unexpected CRI read '+repr(args))
                    self.assertEqual(argv[0], 'ctr')
                    if args == ['version']: return success(b'Server:\n  Version: v2.3.3\n')
                    if args[:2] == ['images','ls']:
                        name = args[-1].removeprefix('name==')
                        return success(rows(name, refs.get(name)))
                    if args[:2] == ['images','import']:
                        self.assertEqual(args[2:5], ['--local','--platform','linux/arm64'])
                        self.assertEqual(kwargs['pass_fds'], (41,))
                        state['imported'] = True
                        if '--no-unpack' not in args: state['unpacked'] = True
                        refs[c.temp] = scope.MANIFEST
                        return success(b'')
                    if args[:3] == ['images','tag','--local']:
                        self.assertEqual(args[3], c.temp)
                        self.assertIn(args[4], [scope.TAG,scope.REPO+'@'+scope.MANIFEST])
                        self.assertNotIn(args[4], refs)
                        refs[args[4]] = scope.MANIFEST
                        return success(b'')
                    if args[:2] == ['images','check']:
                        self.assertTrue(state['unpacked'])
                        return success((args[-1].removeprefix('name==')+'\n').encode())
                    if args[:2] == ['content','get']:
                        self.assertTrue(state['imported'])
                        return success(p['manifest_raw'] if args[2] == scope.MANIFEST else p['config_raw'])
                    if args[:3] == ['content','ls','--quiet']:
                        requested = {v.removeprefix('digest==') for v in args[3:]}
                        return success(('\n'.join(sorted(requested & (blobs if state['imported'] else old_blobs)))+'\n').encode())
                    if args[:2] == ['content','ls']:
                        dg = args[2].removeprefix('digest==')
                        if dg == scope.MANIFEST:
                            labels = {'containerd.io/gc.ref.content.config':scope.CONFIG}
                            labels.update({'containerd.io/gc.ref.content.l.'+str(i):d['digest'] for i,d in enumerate(p['manifest']['layers'])})
                        else:
                            self.assertEqual(dg,scope.CONFIG)
                            self.assertTrue(state['unpacked'])
                            labels = {'containerd.io/gc.ref.snapshot.overlayfs':scope.FINAL_CHAIN}
                        return success(('DIGEST SIZE AGE LABELS\n'+dg+' 1B 1s '+','.join(k+'='+v for k,v in labels.items())+'\n').encode())
                    if args == ['snapshots','--snapshotter','overlayfs','ls']:
                        chains = p['chains'] if state['unpacked'] else p['chains'][:-1]
                        return success(('KEY PARENT KIND\n'+''.join(v['chain_id']+' '+v['parent']+' Committed\n' for v in chains)).encode())
                    self.fail('unexpected containerd command '+repr(args))
                c.runner = runner
                def space(path):
                    return {'path':path,'device':99,'capacity_bytes':316482899968,'available_bytes':120*scope.GIB,
                            'available_inodes':10000000,'total_inodes':20000000,'fragment_size':4096}
                with patch.object(c.I,'write_new',side_effect=lambda directory,name,raw:saved.append((name,len(raw)))), \
                     patch.object(c.R,'observed_directory',side_effect=space), \
                     patch.object(scope.os,'sysconf',return_value=4096,create=True), \
                     patch.object(scope.os,'lseek'):
                    scope.validate_version(c.call('containerd_server_version',['version']))
                    c.fresh_budget()
                    c.p.update(p)
                    c.fresh_budget()
                    result = c.execute_import()
                self.assertEqual(result['status'],'SKILLHUB_AUTH_CACHE_PRELOADED_NOT_DEPLOYED')
                self.assertEqual(len(result['commands']),82)
                self.assertLess(len(result['commands'])+11,140)
                self.assertEqual(sum(bool(row.get('containerd_mutation_requested')) for row in result['commands']),4)
                self.assertEqual({name:refs[name] for name in scope.PROTECTED},scope.PROTECTED)
                self.assertTrue(all((name in refs)==aliases_present for name in scope.OPTIONAL_PROTECTED))
                self.assertLess(sum(size for name,size in saved),1024**2)

    def test_component_pins_and_python37(self):
        scope = self.scope
        ast.parse(TOOL.read_text('utf8'), feature_version=(3, 7))
        assert scope.NODES == {'datax-01': 'ec5698b0-8d82-4eed-bae0-3711c7b7f36c'}
        assert scope.REPO == 'deerflow.local/skillhub-server'
        assert scope.TRANSPORT_PIN['bytes'] == 848886784
        assert scope.LAYER_COUNT == 15
        assert scope.ARCHIVE_MAX == 1024**3
        assert len(scope.PROTECTED) == 1
        assert next(iter(scope.PROTECTED.values())).endswith('71a60be927744fca0c78c922f2aa4083283347cc4b17df60dde50263f20076e2')
        assert not any('frontend' in name for name in scope.PROTECTED)
        assert scope.EARLY_BUDGET['manifest']['config']['digest'] == scope.CONFIG

    def test_helpers_keep_frozen_hashes(self):
        scope = self.scope
        for name, content in scope.SOURCES.items():
            assert scope.SOURCE_PINS[name] == {'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
        assert scope.SOURCE_PINS['importer']['sha256'] == '4b07f6546729c29ed0eebf2db62f531c20b2747b4d6eb80ee0f9ff0c1c7e807f'

    def test_preclaim_old_index_and_absent_optional_aliases(self):
        scope = self.scope
        I, unused, unused2 = scope.load_inputs()
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            name = argv[-1].removeprefix('name==')
            return success(rows(name, scope.PROTECTED.get(name)))
        result = scope.preclaim_references(I, 'ctr', run, time.monotonic())
        assert len(calls) == 6
        assert all(call[5:7] == ['images', 'ls'] for call in calls)
        assert all(result[name] is None for name in scope.OPTIONAL_PROTECTED)
        assert result[scope.TAG] is None

    def test_preclaim_reference_errors_do_not_import_0(self):
        scope = self.scope
        kind = 'missing_old_tag'
        I, unused, unused2 = scope.load_inputs()
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            name = argv[-1].removeprefix('name==')
            value = scope.PROTECTED.get(name)
            if name in scope.PROTECTED and kind == 'missing_old_tag': value = None
            if name in scope.PROTECTED and kind == 'platform_instead_of_index': value = 'sha256:'+'1'*64
            if name == scope.TAG and kind == 'canonical_conflict': value = 'sha256:'+'2'*64
            if name in scope.OPTIONAL_PROTECTED and kind == 'wrong_optional': value = 'sha256:'+'3'*64
            return success(rows(name, value))
        with self.assertRaisesRegex(scope.Refused, 'preclaim_image_reference_changed'):
            scope.preclaim_references(I, 'ctr', run, time.monotonic())
        assert all('import' not in call and 'tag' not in call for call in calls)

    def test_preclaim_reference_errors_do_not_import_1(self):
        scope = self.scope
        kind = 'platform_instead_of_index'
        I, unused, unused2 = scope.load_inputs()
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            name = argv[-1].removeprefix('name==')
            value = scope.PROTECTED.get(name)
            if name in scope.PROTECTED and kind == 'missing_old_tag': value = None
            if name in scope.PROTECTED and kind == 'platform_instead_of_index': value = 'sha256:'+'1'*64
            if name == scope.TAG and kind == 'canonical_conflict': value = 'sha256:'+'2'*64
            if name in scope.OPTIONAL_PROTECTED and kind == 'wrong_optional': value = 'sha256:'+'3'*64
            return success(rows(name, value))
        with self.assertRaisesRegex(scope.Refused, 'preclaim_image_reference_changed'):
            scope.preclaim_references(I, 'ctr', run, time.monotonic())
        assert all('import' not in call and 'tag' not in call for call in calls)

    def test_preclaim_reference_errors_do_not_import_2(self):
        scope = self.scope
        kind = 'canonical_conflict'
        I, unused, unused2 = scope.load_inputs()
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            name = argv[-1].removeprefix('name==')
            value = scope.PROTECTED.get(name)
            if name in scope.PROTECTED and kind == 'missing_old_tag': value = None
            if name in scope.PROTECTED and kind == 'platform_instead_of_index': value = 'sha256:'+'1'*64
            if name == scope.TAG and kind == 'canonical_conflict': value = 'sha256:'+'2'*64
            if name in scope.OPTIONAL_PROTECTED and kind == 'wrong_optional': value = 'sha256:'+'3'*64
            return success(rows(name, value))
        with self.assertRaisesRegex(scope.Refused, 'preclaim_image_reference_changed'):
            scope.preclaim_references(I, 'ctr', run, time.monotonic())
        assert all('import' not in call and 'tag' not in call for call in calls)

    def test_preclaim_reference_errors_do_not_import_3(self):
        scope = self.scope
        kind = 'wrong_optional'
        I, unused, unused2 = scope.load_inputs()
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            name = argv[-1].removeprefix('name==')
            value = scope.PROTECTED.get(name)
            if name in scope.PROTECTED and kind == 'missing_old_tag': value = None
            if name in scope.PROTECTED and kind == 'platform_instead_of_index': value = 'sha256:'+'1'*64
            if name == scope.TAG and kind == 'canonical_conflict': value = 'sha256:'+'2'*64
            if name in scope.OPTIONAL_PROTECTED and kind == 'wrong_optional': value = 'sha256:'+'3'*64
            return success(rows(name, value))
        with self.assertRaisesRegex(scope.Refused, 'preclaim_image_reference_changed'):
            scope.preclaim_references(I, 'ctr', run, time.monotonic())
        assert all('import' not in call and 'tag' not in call for call in calls)

    def test_absent_old_alias_must_remain_absent(self):
        scope = self.scope
        c = controller(scope)
        alias = next(iter(scope.OPTIONAL_PROTECTED))
        c.refs = {alias: None}
        c.target = lambda name: scope.MANIFEST
        with self.assertRaisesRegex(scope.Refused, 'existing_reference_changed'):
            c.preserved()
        c.target = lambda name: None
        c.preserved()

    def test_new_canonical_alias_may_only_appear_at_candidate(self):
        scope = self.scope
        c = controller(scope)
        c.refs = {scope.TAG: None}
        for wanted in (None, scope.MANIFEST):
            c.target = lambda name: wanted
            c.preserved()
        c.target = lambda name: 'sha256:'+'0'*64
        with self.assertRaisesRegex(scope.Refused, 'existing_reference_changed'):
            c.preserved()

    def test_fresh_controlplane_exact_readonly_queries(self):
        scope = self.scope
        unused, R, unused2 = scope.load_inputs()
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            return success(node_raw(scope) if len(calls) == 1 else kubelet(R))
        result = scope.fresh_controlplane(R, 'kubectl', run, time.monotonic())
        assert result['node']['uid'] == scope.NODES['datax-01']
        assert result['mutations'] == 0
        assert len(calls) == 2 and all(call[4] == 'get' for call in calls)
        assert calls[1][-1] == '/api/v1/nodes/datax-01/proxy/configz'

    def test_unknown_or_unhealthy_node_refused_0(self):
        scope = self.scope
        options = {'uid': 'bad-node'}
        unused, R, unused2 = scope.load_inputs()
        with self.assertRaises(R.Refused):
            scope.fresh_controlplane(R, 'kubectl', lambda *args, **kwargs: success(node_raw(scope, **options)), time.monotonic())

    def test_unknown_or_unhealthy_node_refused_1(self):
        scope = self.scope
        options = {'ready': 'False'}
        unused, R, unused2 = scope.load_inputs()
        with self.assertRaises(R.Refused):
            scope.fresh_controlplane(R, 'kubectl', lambda *args, **kwargs: success(node_raw(scope, **options)), time.monotonic())

    def test_unknown_or_unhealthy_node_refused_2(self):
        scope = self.scope
        options = {'pressure': 'True'}
        unused, R, unused2 = scope.load_inputs()
        with self.assertRaises(R.Refused):
            scope.fresh_controlplane(R, 'kubectl', lambda *args, **kwargs: success(node_raw(scope, **options)), time.monotonic())

    def test_changed_kubelet_gc_threshold_is_refused(self):
        scope = self.scope
        unused, R, unused2 = scope.load_inputs()
        value = json.loads(kubelet(R))
        value['kubeletconfig']['imageGCHighThresholdPercent'] = 75
        with self.assertRaisesRegex(R.Refused, 'fixed_effective_kubelet_thresholds_changed'):
            R.thresholds(value)

    def test_only_exact_import_and_candidate_publish_mutations_allowed_0(self):
        scope = self.scope
        args = ['images', 'rm', 'old']
        c = controller(scope)
        with self.assertRaisesRegex(scope.Refused, 'mutation_scope'):
            c.call('bad', args, mutates=True)
        assert c.report['commands'] == []

    def test_only_exact_import_and_candidate_publish_mutations_allowed_1(self):
        scope = self.scope
        args = ['images', 'tag', '--local', 'candidate', 'old']
        c = controller(scope)
        with self.assertRaisesRegex(scope.Refused, 'mutation_scope'):
            c.call('bad', args, mutates=True)
        assert c.report['commands'] == []

    def test_only_exact_import_and_candidate_publish_mutations_allowed_2(self):
        scope = self.scope
        args = ['images', 'tag', '--local', '--force', 'candidate', 'old']
        c = controller(scope)
        with self.assertRaisesRegex(scope.Refused, 'mutation_scope'):
            c.call('bad', args, mutates=True)
        assert c.report['commands'] == []

    def test_only_exact_import_and_candidate_publish_mutations_allowed_3(self):
        scope = self.scope
        args = ['images', 'import', '--local', '/home/cnooc/arbitrary.tar']
        c = controller(scope)
        with self.assertRaisesRegex(scope.Refused, 'mutation_scope'):
            c.call('bad', args, mutates=True)
        assert c.report['commands'] == []

    def test_command_ceiling_applies_to_read_and_api_queries(self):
        scope = self.scope
        c = controller(scope)
        c.report['commands'] = [{}] * 140
        with self.assertRaisesRegex(scope.Refused, 'command_limit'):
            c.call('read', ['version'])
        with self.assertRaisesRegex(scope.Refused, 'command_limit'):
            c.query('read', ['get', 'node', scope.HOST, '-o=jsonpath='+c.R.NODE_PATH])

    def test_one_filesystem_budget_preserves_reserve_and_counts_all_costs(self):
        scope = self.scope
        p = scope.EARLY_BUDGET
        samples = [{'path':path, 'device':1, 'capacity_bytes':316482899968,
                    'available_bytes':71531159552, 'available_inodes':10000000,
                    'total_inodes':15000000, 'fragment_size':4096}
                   for path in [scope.CONTROL]+list(scope.EXPECTED_IMAGEFS.values())]
        cache = {'missing_chains': [p['chains'][-1]['chain_id']], 'missing_content': [scope.MANIFEST, scope.CONFIG, p['manifest']['layers'][-1]['digest']]}
        result = scope.budget(p, samples, 4096, cache=cache)
        device = result['devices'][0]
        assert len(result['devices']) == 1
        assert device['reserve_bytes'] == (316482899968*16+99)//100 + 3*scope.GIB
        assert device['cost_bytes'] >= 3*scope.ARCHIVE_MAX
        assert result['missing_ordered_chain_count'] == 1
        for sample in samples: sample['available_bytes'] = device['required_bytes']-1
        with self.assertRaisesRegex(scope.Refused, 'full_image_resource_budget'):
            scope.budget(p, samples, 4096, cache=cache)

    def test_preclaim_failure_never_creates_marker_0(self):
        scope = self.scope
        monkeypatch = self.monkeypatch
        capsys = self.capture
        kind = 'missing_input'
        errors = {'missing_input': FileNotFoundError(), 'hash_mismatch': scope.Refused('input_changed_or_digest')}
        run_main_before_claim(scope, monkeypatch, exists=kind=='once_exists', error=errors.get(kind), unknown_node=kind=='unknown_node')
        output = json.loads(capsys.readouterr().out)
        assert output['mutation_once_claimed'] == (kind=='once_exists')
        assert output['image_import_requested'] is False
        assert output['application_started'] is False
        assert output['production_workload_mutation'] is False

    def test_preclaim_failure_never_creates_marker_1(self):
        scope = self.scope
        monkeypatch = self.monkeypatch
        capsys = self.capture
        kind = 'hash_mismatch'
        errors = {'missing_input': FileNotFoundError(), 'hash_mismatch': scope.Refused('input_changed_or_digest')}
        run_main_before_claim(scope, monkeypatch, exists=kind=='once_exists', error=errors.get(kind), unknown_node=kind=='unknown_node')
        output = json.loads(capsys.readouterr().out)
        assert output['mutation_once_claimed'] == (kind=='once_exists')
        assert output['image_import_requested'] is False
        assert output['application_started'] is False
        assert output['production_workload_mutation'] is False

    def test_preclaim_failure_never_creates_marker_2(self):
        scope = self.scope
        monkeypatch = self.monkeypatch
        capsys = self.capture
        kind = 'unknown_node'
        errors = {'missing_input': FileNotFoundError(), 'hash_mismatch': scope.Refused('input_changed_or_digest')}
        run_main_before_claim(scope, monkeypatch, exists=kind=='once_exists', error=errors.get(kind), unknown_node=kind=='unknown_node')
        output = json.loads(capsys.readouterr().out)
        assert output['mutation_once_claimed'] == (kind=='once_exists')
        assert output['image_import_requested'] is False
        assert output['application_started'] is False
        assert output['production_workload_mutation'] is False

    def test_preclaim_failure_never_creates_marker_3(self):
        scope = self.scope
        monkeypatch = self.monkeypatch
        capsys = self.capture
        kind = 'once_exists'
        errors = {'missing_input': FileNotFoundError(), 'hash_mismatch': scope.Refused('input_changed_or_digest')}
        run_main_before_claim(scope, monkeypatch, exists=kind=='once_exists', error=errors.get(kind), unknown_node=kind=='unknown_node')
        output = json.loads(capsys.readouterr().out)
        assert output['mutation_once_claimed'] == (kind=='once_exists')
        assert output['image_import_requested'] is False
        assert output['application_started'] is False
        assert output['production_workload_mutation'] is False

    def test_complete_archive_checks_blob_hash_and_uncompressed_diffid(self):
        scope = self.scope
        monkeypatch = self.monkeypatch
        tmp_path = Path(self.tmp.name)
        archive = synthetic_archive(scope, monkeypatch, tmp_path)
        I, unused, unused2 = scope.load_inputs()
        with archive.open('rb') as stream:
            result = scope.inspect_archive(stream.fileno(), I, time.monotonic())
        assert len(result['layer_stats']) == 1
        assert result['chains'][-1]['chain_id'] == scope.FINAL_CHAIN

    def test_archive_mismatch_refused_0(self):
        scope = self.scope
        monkeypatch = self.monkeypatch
        tmp_path = Path(self.tmp.name)
        kwargs,reason = ({'bad_hash': True}, 'blob_digest')
        archive = synthetic_archive(scope, monkeypatch, tmp_path, **kwargs)
        I, unused, unused2 = scope.load_inputs()
        with archive.open('rb') as stream, self.assertRaisesRegex(scope.Refused, reason):
            scope.inspect_archive(stream.fileno(), I, time.monotonic())

    def test_archive_mismatch_refused_1(self):
        scope = self.scope
        monkeypatch = self.monkeypatch
        tmp_path = Path(self.tmp.name)
        kwargs,reason = ({'extra': True}, 'full_blob_closure')
        archive = synthetic_archive(scope, monkeypatch, tmp_path, **kwargs)
        I, unused, unused2 = scope.load_inputs()
        with archive.open('rb') as stream, self.assertRaisesRegex(scope.Refused, reason):
            scope.inspect_archive(stream.fileno(), I, time.monotonic())

    def test_rewritten_archive_only_names_private_candidate(self):
        scope = self.scope
        monkeypatch = self.monkeypatch
        tmp_path = Path(self.tmp.name)
        if os.name != 'posix': self.skipTest('Linux dir_fd and ownership checks')
        archive = synthetic_archive(scope, monkeypatch, tmp_path)
        I, unused, unused2 = scope.load_inputs()
        directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with archive.open('rb') as stream:
                prepared = scope.inspect_archive(stream.fileno(), I, time.monotonic())
                target = scope.rewrite_private(stream.fileno(), directory, prepared, 'private:operation', I, time.monotonic())
            try:
                with os.fdopen(os.dup(target),'rb') as stream, tarfile.open(fileobj=stream,mode='r:') as tar:
                    index = json.load(tar.extractfile('index.json'))
                assert index['manifests'][0]['annotations'] == {'io.containerd.image.name':'private:operation'}
            finally: os.close(target)
        finally: os.close(directory)

    def test_generator_is_pinned_and_readonly_until_final_output(self):
        generator = module(GEN, 'skillhub_package_test')
        assert hashlib.sha256(generator.TEMPLATE.read_bytes()).hexdigest() == generator.TEMPLATE_SHA
        assert 'subprocess' not in GEN.read_text('utf8')
        assert generator.NAME == TOOL.name

if __name__ == "__main__":
    unittest.main()
