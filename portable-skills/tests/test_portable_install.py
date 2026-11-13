"""Mutation boundaries and real local stage/switch behavior; no Kubernetes."""
import ast
import base64
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / 'portable-skills/deployment'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


host = module('portable_install', DEPLOY / 'install-energy-market-daily-portable-20261111-r2.py')
child = module('portable_publish', DEPLOY / 'publish_portable_daily_child.py')


def synthetic_deployment():
    spec = {'replicas': 1, 'strategy': {'type': 'RollingUpdate', 'rollingUpdate': {'maxSurge': 1, 'maxUnavailable': 0}},
            'selector': {'matchLabels': {'app': 'provisioner'}},
            'template': {'metadata': {'labels': {'app': 'provisioner'}}, 'spec': {'containers': [{'name': 'provisioner', 'image': 'unchanged-runtime',
                'resources': {'requests': {'cpu': '100m', 'memory': '128Mi'}},
                'env': [{'name': 'SANDBOX_ENV_SECRET_NAME', 'value': 'deer-flow-env'},
                        {'name': 'SANDBOX_SECRET_ENV_KEYS', 'value': 'SANDBOX_API_KEY,HTTP_PROXY,http_proxy'},
                        {'name': 'SANDBOX_PASSTHROUGH_ENV_KEYS', 'value': 'NO_PROXY,no_proxy'},
                        {'name': 'UNRELATED_SETTING', 'value': 'unchanged'}]}], 'serviceAccountName': 'existing-provisioner'}}}
    return {'metadata': {'name': 'provisioner', 'uid': host.PROVISIONER_UID, 'generation': 86, 'resourceVersion': 'synthetic-rv'}, 'spec': spec}


class InstallTests(unittest.TestCase):
    def test_host_python37_syntax(self):
        ast.parse((DEPLOY / 'install-energy-market-daily-portable-20261111-r2.py').read_text(encoding='utf-8'), feature_version=(3, 7))

    def test_wrong_host_never_reads_or_mutates(self):
        output = io.StringIO()
        with patch.object(host.os, 'name', 'nt'), patch.object(host.Release, 'execute', side_effect=AssertionError('must not run')):
            with contextlib.redirect_stdout(output):
                self.assertEqual(host.main(['--install-portable-daily-once']), 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result['commands'], 0)
        self.assertFalse(result['once_claimed'])

    def test_candidate_changes_only_two_lists_and_program_proxy(self):
        dep = synthetic_deployment()
        original = copy.deepcopy(dep)
        baselines = dict(host.BASELINES, provisioner=(host.PROVISIONER_UID, 86, host.sha(host.canonical(dep['spec']))))
        with patch.object(host, 'BASELINES', baselines):
            candidate = host.candidate_spec(dep)
        self.assertEqual(dep, original)
        env = candidate['template']['spec']['containers'][0]['env']
        self.assertIn({'name': 'ENERGY_DAILY_HTTP_PROXY', 'value': host.PROXY}, env)
        env_by_name = {row['name']: row for row in env}
        self.assertEqual(env_by_name['UNRELATED_SETTING'], {'name': 'UNRELATED_SETTING', 'value': 'unchanged'})
        self.assertEqual(host.key_list(env_by_name['SANDBOX_SECRET_ENV_KEYS']['value']), ['SANDBOX_API_KEY', 'HTTP_PROXY', 'http_proxy', 'JKX_API_URL', 'JKX_API_PASSWORD'])
        candidate['template']['spec']['containers'][0]['env'] = dep['spec']['template']['spec']['containers'][0]['env']
        self.assertEqual(candidate, dep['spec'])

    def test_changed_provisioner_refused(self):
        dep = synthetic_deployment()
        dep['metadata']['generation'] = 87
        with self.assertRaises(host.Refused):
            host.candidate_spec(dep)

    def test_two_secret_additions_preserve_every_existing_value(self):
        secret = {'metadata': {'name': 'deer-flow-env', 'uid': 'secret-id', 'resourceVersion': 'secret-rv'}, 'data': {'OTHER_KEY': 'dW5jaGFuZ2Vk'}}
        credential = {'JKX_API_ENDPOINT': 'http://ip91.jkx.com.cn:6688/jkx_eecc.aspx', 'JKX_API_PASSWORD': 'fixture-private'}
        request, expected = host.secret_patch(secret, credential)
        self.assertEqual(expected['OTHER_KEY'], secret['data']['OTHER_KEY'])
        self.assertEqual(len(request), 4)
        self.assertEqual(request[0], {'op': 'test', 'path': '/metadata/uid', 'value': 'secret-id'})
        self.assertEqual(request[1], {'op': 'test', 'path': '/metadata/resourceVersion', 'value': 'secret-rv'})
        self.assertEqual({row['path'] for row in request[2:]}, {'/data/JKX_API_URL', '/data/JKX_API_PASSWORD'})
        self.assertEqual(base64.b64decode(expected['JKX_API_PASSWORD']).decode(), 'fixture-private')
        self.assertNotIn('JKX_API_PASSWORD', secret['data'])

    def test_conflicting_or_controller_owned_secret_not_overwritten(self):
        credential = {'JKX_API_URL': 'http://ip91.jkx.com.cn:6688/jkx_eecc.aspx', 'JKX_API_PASSWORD': 'fixture-private'}
        for secret in ({'metadata': {'name': 'deer-flow-env', 'uid': 'x', 'resourceVersion': '1'}, 'data': {'JKX_API_PASSWORD': 'b3RoZXI='}},
                       {'metadata': {'name': 'deer-flow-env', 'uid': 'x', 'resourceVersion': '1', 'ownerReferences': [{'uid': 'controller'}]}, 'data': {}},
                       {'metadata': {'name': 'deer-flow-env', 'uid': 'x', 'resourceVersion': '1'}, 'data': {}, 'immutable': True}):
            with self.assertRaises(host.Refused):
                host.secret_patch(secret, credential)

    def test_existing_equal_secret_keys_are_idempotent_without_second_patch(self):
        credential = {'JKX_API_URL': 'http://ip91.jkx.com.cn:6688/jkx_eecc.aspx', 'JKX_API_PASSWORD': 'fixture-private'}
        secret = {'metadata': {'name': 'deer-flow-env', 'uid': 'x', 'resourceVersion': '1'}, 'data': {}}
        _, expected = host.secret_patch(secret, credential)
        secret['data'] = expected
        request, _ = host.secret_patch(secret, credential)
        self.assertEqual(len(request), 2)

    def test_provider_credential_not_sent_to_another_host(self):
        secret = {'metadata': {'name': 'deer-flow-env', 'uid': 'x', 'resourceVersion': '1'}, 'data': {}}
        for url in ('http://other.invalid:6688/jkx_eecc.aspx', 'http://name:private@ip91.jkx.com.cn:6688/jkx_eecc.aspx', 'https://ip91.jkx.com.cn/jkx_eecc.aspx'):
            with self.assertRaises(host.Refused):
                host.secret_patch(secret, {'JKX_API_URL': url, 'JKX_API_PASSWORD': 'fixture-private'})

    def test_unapproved_commands_and_mutations_rejected_before_spawn(self):
        op = host.Release()
        with patch.object(host.subprocess, 'Popen', side_effect=AssertionError('no subprocess')):
            for args in (['delete', 'pod', 'business-pod'], ['get', 'secret', 'unrelated', '-o', 'json'], ['patch', 'deployment', 'gateway'], ['exec', 'business-pod', '--', 'env']):
                with self.assertRaises(host.Refused):
                    op.command(args)
            with self.assertRaises(host.Refused):
                op.mutation(['patch', 'deployment', 'gateway'], {}, 'unknown')

    def test_private_patch_is_stdin_and_ack_is_name_only(self):
        op = host.Release()
        op.secret_request = [{'op': 'add', 'path': '/data/JKX_API_PASSWORD', 'value': 'fixture-base64'}]
        observed = []
        def command(args, body=None):
            observed.append((args, body))
            return 'secret/deer-flow-env'
        with patch.object(op, 'command', command):
            op.mutation(['patch', 'secret', 'deer-flow-env', '--type=json', '--patch-file=/dev/stdin', '-o', 'name'], op.secret_request, 'secret')
        args, body = observed[0]
        self.assertNotIn('fixture-base64', ' '.join(args))
        self.assertIn(b'fixture-base64', body)
        self.assertEqual(args[-2:], ['-o', 'name'])

    def test_publisher_has_only_shared_skill_and_temporary_mounts(self):
        pod = host.pod_manifest('operation', 'publisher')['spec']
        self.assertFalse(pod['automountServiceAccountToken'])
        self.assertEqual(pod['containers'][0]['imagePullPolicy'], 'Never')
        self.assertEqual(set(v['name'] for v in pod['volumes']), {'tmp', 'skills'})
        self.assertEqual(pod['volumes'][1]['persistentVolumeClaim'], {'claimName': host.PVC})
        self.assertNotIn('secret', json.dumps(pod).lower())


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='energy-publication-test-')
        self.parent = Path(self.temporary.name)
        self.live = self.parent / 'public/energy-market-daily'
        self.live.parent.mkdir()
        archive = ROOT / 'release/post44-source-sync-20261113'
        shutil.copytree(archive / 'parent/skills/public/energy-market-daily', self.live)
        for relative in ('SKILL.md', 'scripts/entry.py', 'adapters/nullable_market.py'):
            destination = self.live / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(archive / 'intermediate-energy-20261111' / relative, destination)
        self.parent_patch = patch.object(child, 'PARENT', self.parent)
        self.root_patch = patch.object(child, 'ROOT', self.live)
        self.parent_patch.start()
        self.root_patch.start()
        self.operation = '1234567890abcdef' * 2
        self.encoded = base64.b64encode((ROOT / 'release/energy-daily-portable-20261111/energy-market-daily-2.0.0-public.zip').read_bytes()).decode()

    def tearDown(self):
        self.parent_patch.stop()
        self.root_patch.stop()
        self.temporary.cleanup()

    def test_real_stage_switch_retires_original_outside_skill_discovery(self):
        before = child.identity(self.live)
        staged = child.stage(self.operation, self.encoded)
        self.assertFalse(staged['skill_modified'])
        child.verify_tree(self.live, child.OLD_TREE, 163)
        result = child.publish(self.operation)
        self.assertTrue(result['old_skill_removed_from_discovery'])
        self.assertEqual(child.identity(Path(result['retired_path'])), before)
        self.assertNotIn('public', Path(result['retired_path']).relative_to(self.parent).parts)
        self.assertEqual(child.verify()['files'], 105)
        self.assertFalse((self.live / 'scripts/entry.py').exists())
        self.assertTrue((self.live / 'scripts/daily.py').is_file())
        child.verify_tree(Path(result['backup_path']), child.OLD_TREE, 163)

    def test_live_change_after_backup_refuses_switch(self):
        child.stage(self.operation, self.encoded)
        (self.live / 'SKILL.md').write_text('a concurrent change')
        with self.assertRaisesRegex(ValueError, 'SKILL_TREE_CHANGED'):
            child.publish(self.operation)
        self.assertTrue((self.live / 'scripts/entry.py').is_file())

    def test_candidate_change_refuses_switch(self):
        child.stage(self.operation, self.encoded)
        candidate = child.release_path(self.operation) / 'candidate'
        (candidate / 'scripts/daily.py').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'SKILL_TREE_CHANGED'):
            child.publish(self.operation)
        child.verify_tree(self.live, child.OLD_TREE, 163)

    def test_second_stage_never_overwrites_backup(self):
        child.stage(self.operation, self.encoded)
        with self.assertRaisesRegex(ValueError, 'RELEASE_EXISTS_REVIEW_REQUIRED'):
            child.stage(self.operation, self.encoded)

    def test_second_publication_not_repeated(self):
        child.stage(self.operation, self.encoded)
        child.publish(self.operation)
        with self.assertRaises(ValueError):
            child.publish(self.operation)
        child.verify_tree(self.live, child.NEW_TREE, 105)


if __name__ == '__main__':
    unittest.main()
