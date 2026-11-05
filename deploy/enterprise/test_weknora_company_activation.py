"""Offline rollout safety checks. Kubernetes and enrollment are synthetic."""
import copy
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(sys.argv.pop(1)).resolve()
spec = importlib.util.spec_from_file_location('company_activation', SCRIPT)
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


def deployment():
    spec = {'replicas': 70, 'template': {'spec': {'containers': [{'name': 'gateway', 'image': host.OLD_IMAGE,
        'envFrom': [{'secretRef': {'name': 'deer-flow-env'}}], 'env': [
            {'name': 'OA_POLICY_FLOW_MULTI_RESULT_ENABLED', 'value': 'true'},
            {'name': 'OA_POLICY_FLOW_CALLBACK_ENABLED', 'value': 'false'},
            {'name': 'OTHER_SECRET', 'valueFrom': {'secretKeyRef': {'name': 'existing', 'key': 'key'}}}]}]}},
        'strategy': {'type': 'RollingUpdate', 'rollingUpdate': {'maxSurge': 1, 'maxUnavailable': 0}}}
    return {'metadata': {'uid': 'gateway-test', 'resourceVersion': '100', 'generation': 386}, 'spec': spec,
            'status': {'observedGeneration': 386, 'replicas': 70, 'updatedReplicas': 70, 'readyReplicas': 70, 'availableReplicas': 70}}


def evidence():
    return {
        'image': {'status': 'WEKNORA_COMPANY_IMAGE_ACCEPTED_NOT_DEPLOYED', 'checks': [{'status': 'PASS',
                  'pods': [{'containers': [{'imageID': host.EXPECTED['gateway']['candidate_image']}]}],
                  'observation': {'status': 'INSTALLED_WEKNORA_COMPANY_IMAGE_ACCEPTED', 'checks': list(range(13)), 'real_http_requests': 0}}]},
        'bulk': {'schema': 'portal-weknora-company-bulk-enrollment-host.v1', 'status': host.BULK_COMPLETE, 'once_claimed': True,
                 'production_workload_mutation': False, 'observation': {
                    'status': host.BULK_COMPLETE, 'phase': 'COMPLETE', 'organization_id': host.ORG, 'knowledge_base_id': host.KB,
                    'remaining_workspace_memberships': 0, 'post_responses_unconfirmed': 0, 'uncertain_posts_resolved_by_readback': 0,
                    'database_direct_writes': 0, 'accounts_created': 0, 'account_roles_changed': False}},
    }


class Qualification(unittest.TestCase):
    def plan(self, item):
        expected = copy.deepcopy(host.EXPECTED)
        expected['gateway'].update(host.binding(item))
        with patch.object(host, 'EXPECTED', expected):
            return host.plan(item)

    def test_only_image_and_two_env_entries_change(self):
        item = deployment()
        original = copy.deepcopy(item)
        ops, result = self.plan(item)
        self.assertEqual(item, original)
        c = result['template']['spec']['containers'][0]
        self.assertEqual(c['image'], host.EXPECTED['gateway']['candidate_image'])
        self.assertEqual(c['env'][:3], item['spec']['template']['spec']['containers'][0]['env'])
        self.assertEqual(c['env'][3:], host.OVERRIDES)
        reverse = copy.deepcopy(result)
        reverse['template']['spec']['containers'][0]['image'] = host.OLD_IMAGE
        reverse['template']['spec']['containers'][0]['env'] = c['env'][:3]
        self.assertEqual(reverse, original['spec'])
        self.assertEqual([r['path'] for r in ops[:3]], ['/metadata/uid', '/metadata/resourceVersion', '/spec'])

    def test_existing_policy_env_replaced_without_duplicates(self):
        item = deployment()
        item['spec']['template']['spec']['containers'][0]['env'].extend([
            {'name': 'WEKNORA_COMPANY_KB_ENABLED', 'valueFrom': {'secretKeyRef': {'name': 'prior', 'key': 'enabled'}}},
            {'name': 'WEKNORA_COMPANY_KB_ORGANIZATION_IDS', 'value': '[]'}])
        _, result = self.plan(item)
        env = result['template']['spec']['containers'][0]['env']
        self.assertEqual(env[3:], host.OVERRIDES)
        self.assertEqual(len(env), 5)

    def test_duplicate_env_refused(self):
        item = deployment()
        env = item['spec']['template']['spec']['containers'][0]['env']
        env.append(dict(env[0]))
        with self.assertRaisesRegex(host.Refused, 'DUPLICATE_ENV_KEYS'):
            self.plan(item)

    def test_paused_and_wrong_image_refused(self):
        for kind in ('paused', 'wrong-image'):
            item = deployment()
            if kind == 'paused':
                item['spec']['paused'] = True
            else:
                item['spec']['template']['spec']['containers'][0]['image'] = 'unexpected'
            with self.assertRaises(host.Refused):
                self.plan(item)

    def test_batch_still_running_and_remaining_targets_refused(self):
        for key, value in [('status', 'INCOMPLETE'), ('phase', 'INVITE_AND_READBACK'), ('remaining_workspace_memberships', 1)]:
            item = evidence()
            item['bulk']['observation'][key] = value
            with self.assertRaisesRegex(host.Refused, 'BULK_ENROLLMENT_NOT_COMPLETE'):
                host.validate_evidence(item)

    def test_lost_posts_require_confirmed_readback(self):
        item = evidence()
        item['bulk']['observation']['post_responses_unconfirmed'] = 1
        with self.assertRaisesRegex(host.Refused, 'BULK_ENROLLMENT_NOT_COMPLETE'):
            host.validate_evidence(item)
        item['bulk']['observation']['uncertain_posts_resolved_by_readback'] = 1
        host.validate_evidence(item)

    def test_wrong_space_or_image_not_accepted(self):
        item = evidence()
        item['bulk']['observation']['organization_id'] = 'other'
        with self.assertRaises(host.Refused):
            host.validate_evidence(item)
        item = evidence()
        item['image']['checks'][0]['pods'][0]['containers'][0]['imageID'] = 'other'
        with self.assertRaises(host.Refused):
            host.validate_evidence(item)

    def test_missing_bulk_receipt_cannot_execute(self):
        with patch.object(host, 'RECEIPTS', {'image': host.RECEIPTS['image']}):
            with self.assertRaisesRegex(host.Refused, 'FINAL_ENROLLMENT_RECEIPT_REQUIRED'):
                host.evidence()

    def test_formal_package_binds_both_operator_receipts(self):
        self.assertEqual(set(host.RECEIPTS), {'image', 'bulk'})
        values = {}
        for name, filename in (
            ('image', 'weknora-company-image-check-field-20261024-r1.json'),
            ('bulk', 'weknora-company-enrollment-field-20261024-r1.json'),
        ):
            values[name] = json.loads((Path(__file__).parent / filename).read_text(encoding='utf-8'))['observation']
            self.assertEqual(host.RECEIPTS[name], {k: values[name]['report_' + k] for k in ('path', 'bytes', 'sha256')})
        host.validate_evidence(values)

    def protected_receipts(self, directory):
        pins = {}
        for name, value in evidence().items():
            path = Path(directory) / (name + '.json')
            raw = json.dumps(value).encode()
            path.write_bytes(raw)
            path.chmod(0o600)
            pins[name] = {'path': str(path), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        return pins

    @unittest.skipUnless(hasattr(os, 'geteuid') and os.geteuid() == 0, 'Linux protected report fixture')
    def test_protected_complete_receipts_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            pins = self.protected_receipts(directory)
            with patch.object(host, 'RECEIPTS', pins):
                self.assertEqual(host.evidence(), pins)

    @unittest.skipUnless(hasattr(os, 'geteuid') and os.geteuid() == 0, 'Linux protected report fixture')
    def test_same_size_report_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            pins = self.protected_receipts(directory)
            path = Path(pins['bulk']['path'])
            raw = path.read_bytes().replace(b'COMPLETE', b'COMPLETf')
            self.assertEqual(len(raw), pins['bulk']['bytes'])
            path.write_bytes(raw)
            with patch.object(host, 'RECEIPTS', pins):
                with self.assertRaisesRegex(host.Refused, 'RECEIPT_HASH_CHANGED'):
                    host.evidence()

    @unittest.skipUnless(hasattr(os, 'geteuid') and os.geteuid() == 0, 'Linux protected report fixture')
    def test_symlink_and_writable_report_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            pins = self.protected_receipts(directory)
            path = Path(pins['bulk']['path'])
            link = Path(directory) / 'alias.json'
            link.symlink_to(path)
            changed = copy.deepcopy(pins)
            changed['bulk']['path'] = str(link)
            with patch.object(host, 'RECEIPTS', changed):
                with self.assertRaisesRegex(host.Refused, 'RECEIPT_SYMLINK'):
                    host.evidence()
            path.chmod(0o666)
            with patch.object(host, 'RECEIPTS', pins):
                with self.assertRaisesRegex(host.Refused, 'RECEIPT_METADATA_CHANGED'):
                    host.evidence()

    def test_task_gate_honors_explicit_policy(self):
        observation = dict(status='PASS', database_readonly=True, revisions=['0021_oa_result_sets'],
                           active_runs=2, active_oa_jobs=0, active_callback_leases=0)
        with patch.object(host, 'command', lambda args: observation):
            with self.assertRaisesRegex(host.Refused, 'TASKS_STILL_ACTIVE'):
                host.inspect_tasks({'metadata': {'name': 'sample'}}, True)
            self.assertEqual(host.inspect_tasks({'metadata': {'name': 'sample'}}, False), observation)

    def simulate(self, outcome):
        item = deployment()
        old = [{'metadata': {'name': 'test-pod', 'uid': 'test-pod-uid'}, 'status': {'containerStatuses': [{'containerID': 'old'}]}}]
        expected = copy.deepcopy(host.EXPECTED)
        expected['gateway'].update(host.binding(item))
        before = {'deployments': {'gateway': host.binding(item)}, 'configuration_metadata': {}, 'gateway_service': {}, 'weknora_frontend': {}}
        calls = []

        def updated():
            changed = copy.deepcopy(item)
            _, changed['spec'] = host.plan(item)
            changed['metadata']['generation'] += 1
            changed['status']['observedGeneration'] += 1
            return changed

        def snapshot(target=None):
            if target is None:
                return {'gateway': item}, before
            after = copy.deepcopy(before)
            after['deployments']['gateway'] = target
            return {'gateway': updated()}, after

        def command(args, value=None):
            calls.append(args[0])
            if args[0] == 'patch':
                if outcome in ('timeout', 'timeout-applied'):
                    raise TimeoutError()
                return {}
            if outcome in ('timeout', 'unchanged'):
                return item
            return updated()

        report = {'runtime': [], 'once_claimed': False}
        with tempfile.TemporaryDirectory() as directory, ExitStack() as mocks:
            mocks.enter_context(patch.object(host, 'CONTROL', str(Path(directory) / 'activation')))
            mocks.enter_context(patch.object(host, 'EXPECTED', expected))
            mocks.enter_context(patch.object(host, 'evidence', lambda: {}))
            mocks.enter_context(patch.object(host, 'snapshot', snapshot))
            mocks.enter_context(patch.object(host, 'pods_for', lambda *args: old))
            mocks.enter_context(patch.object(host, 'endpoints', lambda *args: None))
            mocks.enter_context(patch.object(host, 'inspect_tasks', lambda *args: {}))
            mocks.enter_context(patch.object(host, 'runtime', lambda *args: {'status': 'PASS'}))
            mocks.enter_context(patch.object(host, 'save', lambda *args: None))
            mocks.enter_context(patch.object(host, 'command', command))
            mocks.enter_context(patch.object(host.os, 'geteuid', lambda: 0, create=True))
            mocks.enter_context(patch.object(host.socket, 'gethostname', lambda: 'datax-01'))
            if outcome in ('success', 'timeout-applied'):
                host.execute(report, True)
                self.assertEqual(report['status'], host.ACCEPTED)
                self.assertTrue(report['patch_acknowledged_by_readback'])
            else:
                with self.assertRaisesRegex(host.Refused, 'PATCH_NOT_CONFIRMED_DO_NOT_REPLAY'):
                    host.execute(report, True)
        self.assertEqual(calls, ['patch', 'get', 'get'] if outcome in ('success', 'timeout-applied') else ['patch', 'get'])
        self.assertEqual(report['patch_request_count'], 1)
        self.assertTrue(report['patch_may_have_applied'])

    @unittest.skipUnless(hasattr(host.os, 'geteuid') and host.os.geteuid() == 0, 'Linux protected parent fixture')
    def test_timeout_reads_back_but_never_repatches(self):
        self.simulate('timeout')

    @unittest.skipUnless(hasattr(host.os, 'geteuid') and host.os.geteuid() == 0, 'Linux protected parent fixture')
    def test_unconfirmed_patch_never_repatches(self):
        self.simulate('unchanged')

    @unittest.skipUnless(hasattr(host.os, 'geteuid') and host.os.geteuid() == 0, 'Linux protected parent fixture')
    def test_success_finishes_without_second_patch(self):
        self.simulate('success')

    @unittest.skipUnless(hasattr(host.os, 'geteuid') and host.os.geteuid() == 0, 'Linux protected parent fixture')
    def test_lost_patch_reply_accepted_by_readback(self):
        self.simulate('timeout-applied')

    def test_process_command_never_retries_patch(self):
        with patch.object(host.subprocess, 'run', side_effect=host.subprocess.TimeoutExpired('synthetic', 25)) as run:
            with self.assertRaises(host.subprocess.TimeoutExpired):
                host.command(['patch', 'deployment', 'gateway', '--type=json', '--patch-file=/dev/stdin', '-o', 'json'], [])
            self.assertEqual(run.call_count, 1)


if __name__ == '__main__':
    unittest.main()
