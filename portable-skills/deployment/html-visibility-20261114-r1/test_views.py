"""Bounded multi-node read-only checks, using synthetic Kubernetes responses."""
import copy
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('html_visibility_builder', HERE / 'build.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
RAW, CHILD = builder.build()
scope = types.ModuleType('sealed_html_visibility_test')
exec(compile(RAW, '<sealed-html-visibility>', 'exec'), scope.__dict__)


def pod(name='sandbox-0636d6b8', node='ecs-znws-0004'):
    return {'metadata': {'name': name, 'uid': '01234567-89ab-cdef-0123-456789abcdef',
                         'labels': {'app.kubernetes.io/component': 'sandbox'}},
            'spec': {'nodeName': node, 'containers': [{'name': 'sandbox', 'volumeMounts': [
                {'name': 'skills-public', 'mountPath': '/mnt/skills/public', 'subPath': 'public', 'readOnly': True}]}],
                'volumes': [{'name': 'skills-public', 'persistentVolumeClaim': {'claimName': scope.CLAIM}}]},
            'status': {'phase': 'Running', 'containerStatuses': [{'name': 'sandbox', 'ready': True,
                'state': {'running': {'startedAt': '2026-11-14T00:00:00Z'}}, 'imageID': 'sha256:fb36911f899cc43f1228ee4d0701130761b43b3e9792320bf4e0f16f41f0bec8',
                'containerID': 'fixture', 'restartCount': 0}]}}


class ViewsTests(unittest.TestCase):
    def run_views(self, pods, bad=()):
        by_name = {p['metadata']['name']: p for p in pods}
        calls = []
        def query(args):
            calls.append(args)
            if args[:2] == ['get', 'pods']:
                return {'items': pods}
            if args[:2] == ['get', 'pod']:
                return by_name[args[2]]
            if args[0] == 'exec':
                return {'status': 'INCOMPLETE' if args[1] in bad else 'ORDINARY_SANDBOX_PUBLIC_RUNTIME_VISIBLE',
                        'observed_tree_sha256': 'old' if args[1] in bad else builder.TREE}
            raise AssertionError(args)
        inspector = scope.Inspector(CHILD)
        with patch.object(inspector, 'query', side_effect=query):
            result = inspector.run()
        return result, calls

    def test_expected_inventory_is_exact_accepted_publication(self):
        rows = builder.inventory()
        self.assertEqual(len(rows), 108)
        self.assertEqual(builder.sha(builder.canonical(rows)), builder.TREE)

    def test_failed_node_first_and_at_most_three_distinct_nodes(self):
        pods = [pod('sandbox-other', 'node-a'), pod(), pod('sandbox-same', 'ecs-znws-0004'),
                pod('sandbox-third', 'node-c'), pod('sandbox-fourth', 'node-d')]
        result, calls = self.run_views(pods)
        views = result['sampled_views']
        self.assertEqual(views[0]['sample']['pod'], 'sandbox-0636d6b8')
        self.assertEqual(len(views), 3)
        self.assertEqual(len({r['sample']['node'] for r in views}), 3)
        self.assertEqual(len(calls), 10)
        self.assertTrue(result['all_sampled_views_match'])

    def test_one_stale_view_does_not_get_hidden_by_other_successes(self):
        result, _ = self.run_views([pod(), pod('sandbox-other', 'node-b')], bad=('sandbox-0636d6b8',))
        self.assertFalse(result['all_sampled_views_match'])
        self.assertEqual(result['status'], 'VISIBILITY_PENDING')
        self.assertEqual(result['sampled_views'][0]['observation']['observed_tree_sha256'], 'old')

    def test_pending_pod_without_image_is_ignored_without_exec(self):
        value = pod(); value['status'] = {'phase': 'Pending'}
        result, calls = self.run_views([value])
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['sampled_nodes'], 0)

    def test_pod_replacement_is_not_accepted(self):
        value = pod(); after = copy.deepcopy(value); after['metadata']['uid'] = '11234567-89ab-cdef-0123-456789abcdef'
        inspector = scope.Inspector(CHILD)
        with patch.object(inspector, 'query', side_effect=[{'items': [value]}, value,
                          {'status': 'ORDINARY_SANDBOX_PUBLIC_RUNTIME_VISIBLE'}, after]):
            with self.assertRaisesRegex(scope.Refused, 'POD_CHANGED_DURING_READ'):
                inspector.run()

    def test_mutations_rejected_before_process_launch(self):
        inspector = scope.Inspector(CHILD)
        inspector.selected = scope.ready_candidate(pod())
        with patch.object(scope.subprocess, 'Popen') as launch:
            for args in (['delete', 'pod', 'sandbox-0636d6b8'], ['get', 'secrets', '-o', 'json'],
                         ['exec', 'sandbox-0636d6b8', '--', 'bash']):
                with self.assertRaises(scope.Refused):
                    inspector.query(args)
        launch.assert_not_called()

    def test_child_reports_actual_file_differences_without_file_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = 'PRIVATE_FIXTURE_CONTENT_MUST_NOT_BE_EXPORTED'
            (root / 'zzfixture-extra.txt').write_text(secret)
            actual = [{'path': 'zzfixture-extra.txt', 'bytes': len(secret),
                       'sha256': builder.sha(secret.encode())}]
            constructor = type(root)
            def translate(value):
                return root if str(value) == '/mnt/skills/public/energy-market-daily' else constructor(value)
            with patch.object(pathlib, 'Path', side_effect=translate), \
                 patch.object(scope.os, 'geteuid', return_value=1000, create=True), \
                 patch.object(scope.os, 'getegid', return_value=1000, create=True), \
                 patch.object(sys, 'argv', ['child', builder.TREE]), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                with self.assertRaises(SystemExit) as stopped:
                    exec(compile(CHILD, '<child-file-differences>', 'exec'), {'__name__': 'visibility_child_test'})
            result = json.loads(output.getvalue())
            self.assertEqual(stopped.exception.code, 1)
            self.assertEqual(result['error_code'], 'ORDINARY_SKILL_TREE_MISMATCH', json.dumps(result))
            self.assertEqual(result['observed_tree_sha256'], builder.sha(builder.canonical(actual)))
            self.assertEqual(result['observed_files'], 1)
            self.assertEqual(result['difference_count'], 109)
            self.assertEqual(len(result['differences']), 16)
            self.assertTrue(result['differences_truncated'])
            self.assertNotIn(secret, output.getvalue())


if __name__ == '__main__':
    unittest.main()
