import ast
import copy
import hashlib
import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = load('transport_validator', ROOT / 'payload/markdown_report.py')
release = load('delivery_release', ROOT / 'update.py')
RULE_VERSION = 'technical-requirements-20261119-v7-md-stream'


async def run_file_io(fn, *args):
    return fn(*args)


def selected(source, names, namespace):
    tree = ast.parse(source)
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    assert len(nodes) == len(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<installed-functions>', 'exec'), namespace)


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.uploads, self.outputs = root / 'uploads', root / 'outputs'
        self.uploads.mkdir()
        self.outputs.mkdir()
        self.raw = '# 自由格式审查报告\n\n服务/供货内容明确性：无法判断。\n没有固定表头。'.encode()
        self.output = self.outputs / '报告.md'
        self.output.write_bytes(self.raw)
        source = self.uploads / 'input.txt'
        source.write_bytes(b'input')
        self.paths = SimpleNamespace(
            sandbox_uploads_dir=lambda *a, **kw: self.uploads,
            sandbox_outputs_dir=lambda *a, **kw: self.outputs,
            resolve_virtual_path=lambda *a, **kw: self.output,
            thread_dir=lambda *a, **kw: root,
        )
        self.namespace = {
            'Path': Path, 'os': os, 'hashlib': hashlib, 'get_paths': lambda: self.paths,
            'run_file_io': run_file_io, 'RULE_VERSION': RULE_VERSION,
            'LEGACY_MARKDOWN_VERSION': 'technical-requirements-20261119-v6-md',
            'LEGACY_RULE_VERSION': 'technical-requirements-20261117-v5',
            'validate_markdown_report': validator.validate_markdown_report,
            'validate_report': lambda *a: (_ for _ in ()).throw(AssertionError('Legacy DOCX must not be used')),
        }
        selected((ROOT / 'fixtures/files.py').read_text(encoding='utf-8'), {'digest', 'snapshot_report'}, self.namespace)
        source = (ROOT / 'fixtures/service.py').read_text(encoding='utf-8')
        selected(source, {'report_format'}, self.namespace)
        tree = ast.parse(source)
        service = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ReviewService')
        service.body = [n for n in service.body if getattr(n, 'name', None) in {'accept', 'accept_run'}]
        exec(compile(ast.Module(body=[service], type_ignores=[]), '<installed-service>', 'exec'), self.namespace)
        self.service = self.namespace['ReviewService']()
        self.job = {'id': 'job', 'request_id': 'request', 'state': 'result_ready', 'payload': {
            'owner_user_id': 'owner', 'assistant_id': 'agent', 'thread_id': 'thread', 'run_id': 'run',
            'rule_version': RULE_VERSION, 'delivery_mode': 'stream', 'deadline': time.time() + 60,
            'result_status': 'succeeded', 'files': [{'stored': 'input.txt', 'name': 'input.txt', 'kind': 'submitted', 'sha256': hashlib.sha256(b'input').hexdigest()}],
        }}

    def test_content_gates_removed_without_rewriting(self):
        for raw in (self.raw, b'```unclosed', b'| partial | table |', '合规评价：通过\n高风险：不符合'.encode()):
            self.output.write_bytes(raw)
            self.service.accept(self.job, '/mnt/user-data/outputs/报告.md')
            snapshot = self.paths.thread_dir() / 'procurement-delivery/report.md'
            self.assertEqual(snapshot.read_bytes(), raw)
            self.assertEqual(self.job['payload']['report_validation'], {'scope': 'MARKDOWN_TRANSPORT_ONLY', 'content_validation': False})
            self.assertEqual(self.job['payload']['report_sha256'], hashlib.sha256(raw).hexdigest())

    def test_encoding_size_and_empty_boundaries(self):
        for raw in (b'', b'\xef\xbb\xbf', b' \r\n', b'\x00', b'\xff', b'x' * (2 * 1024 * 1024 + 1)):
            with self.subTest(raw_size=len(raw)), self.assertRaises(ValueError):
                validator.validate_markdown_report(raw, [])
        self.assertFalse(validator.validate_markdown_report(b'x' * (2 * 1024 * 1024), [])['content_validation'])
        self.assertFalse(validator.validate_markdown_report(b'\xef\xbb\xbf# ok', [])['content_validation'])

    def test_original_input_and_output_boundary_preserved(self):
        (self.uploads / 'input.txt').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'Input changed'):
            self.service.accept(self.job, 'ignored')
        (self.uploads / 'input.txt').write_bytes(b'input')
        self.output = self.uploads / 'input.txt'
        with self.assertRaisesRegex(ValueError, 'Invalid result path'):
            self.service.accept(self.job, 'ignored')

    async def test_original_run_and_checkpoint_required(self):
        run = {'run_id': 'run', 'status': 'success'}
        saved = SimpleNamespace(metadata={'deerflow_timing_run_id': 'run'}, checkpoint={'channel_values': {'artifacts': ['/mnt/user-data/outputs/报告.md']}})
        store = SimpleNamespace(list_by_thread=AsyncMock(return_value=[run]))
        self.service.app = SimpleNamespace(state=SimpleNamespace(run_store=store, checkpointer=SimpleNamespace(aget_tuple=AsyncMock(return_value=saved))))
        await self.service.accept_run(self.job)
        for changed in ('run', 'checkpoint', 'artifact'):
            run['status'] = 'success'
            saved.metadata['deerflow_timing_run_id'] = 'run'
            saved.checkpoint['channel_values']['artifacts'] = ['/mnt/user-data/outputs/报告.md']
            if changed == 'run':
                run['status'] = 'error'
            elif changed == 'checkpoint':
                saved.metadata['deerflow_timing_run_id'] = 'other'
            else:
                saved.checkpoint['channel_values']['artifacts'] = []
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                await self.service.accept_run(self.job)

    async def test_original_sse_roundtrip_and_tampering(self):
        self.service.accept(self.job, 'ignored')
        source = (ROOT / 'fixtures/stream.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        tree.body = [n for n in tree.body if not isinstance(n, ast.ImportFrom) or n.module not in {'deerflow.config.paths', 'deerflow.utils.file_io', 'rules'}]
        namespace = dict(self.namespace)
        exec(compile(tree, '<installed-stream>', 'exec'), namespace)
        service = SimpleNamespace(config=SimpleNamespace(owner_user_id='owner', assistant_id='agent', job_timeout=3600), repo=SimpleNamespace(get=AsyncMock(return_value=self.job)))
        frames = [f async for f in namespace['stream_result'](service, self.job)]
        self.assertEqual(len(frames), 2)
        result = json.loads(frames[-1].decode().split('data: ', 1)[1])
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['report_markdown'].encode(), self.raw)
        self.assertEqual(result['report_bytes'], len(self.raw))
        self.assertEqual(result['report_sha256'], hashlib.sha256(self.raw).hexdigest())
        (self.paths.thread_dir() / 'procurement-delivery/report.md').write_bytes(b'tampered')
        frames = [f async for f in namespace['stream_result'](service, self.job)]
        self.assertIn(b'event: error', frames[-1])
        self.assertNotIn(b'report_markdown', frames[-1])


class ReleaseTests(unittest.TestCase):
    def deployment(self):
        return {'metadata': {'uid': 'uid', 'resourceVersion': '123'}, 'spec': {'template': {'spec': {
            'containers': [{'name': 'gateway', 'image': release.IMAGE, 'env': [{'name': 'DATALAKE_RETRIEVE_TIMEOUT_SECONDS', 'value': '240'}], 'volumeMounts': [
                {'name': 'existing', 'mountPath': release.MODULE_ROOT + 'markdown_report.py', 'subPath': 'markdown_report.py', 'readOnly': True},
                {'name': 'existing', 'mountPath': release.MODULE_ROOT + 'service.py', 'subPath': 'service.py', 'readOnly': True},
                {'name': 'refs', 'mountPath': '/opt/deerflow/procurement/references', 'readOnly': True},
            ]}], 'volumes': [{'name': 'existing', 'configMap': {'name': 'existing'}}, {'name': 'refs', 'configMap': {'name': 'refs'}}],
        }}}}

    def test_only_one_mount_replaced_and_cas_protected(self):
        before = self.deployment()
        after = copy.deepcopy(before)
        ops = release.patch_for(before, 'new')
        self.assertEqual([op['path'] for op in ops[:2]], ['/metadata/uid', '/metadata/resourceVersion'])
        for op in ops[2:]:
            target = after
            parts = op['path'].split('/')[1:]
            for key in parts[:-1]:
                target = target[int(key)] if isinstance(target, list) else target[key]
            target[parts[-1]] = op['value']
        current = after['spec']['template']['spec']
        previous = before['spec']['template']['spec']
        mounts = current['containers'][0]['volumeMounts']
        self.assertEqual([m for m in mounts if m['name'] == 'new'], [{'name': 'new', 'mountPath': release.MODULE_ROOT + 'markdown_report.py', 'subPath': 'markdown_report.py', 'readOnly': True}])
        self.assertEqual(mounts[:-1], previous['containers'][0]['volumeMounts'][1:])
        current['containers'][0]['volumeMounts'] = previous['containers'][0]['volumeMounts']
        current['volumes'].pop()
        self.assertEqual(after, before)

    def test_parent_mount_and_changed_image_rejected(self):
        deployment = self.deployment()
        deployment['spec']['template']['spec']['containers'][0]['volumeMounts'].append({'name': 'parent', 'mountPath': '/app/backend'})
        with self.assertRaises(RuntimeError):
            release.patch_for(deployment, 'new')
        deployment = self.deployment()
        deployment['spec']['template']['spec']['containers'][0]['image'] = 'changed'
        with self.assertRaises(RuntimeError):
            release.patch_for(deployment, 'new')


if __name__ == '__main__':
    unittest.main()
