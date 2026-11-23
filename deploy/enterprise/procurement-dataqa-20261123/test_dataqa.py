import copy
import json
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import dataqa_config as dq
import test_delivery


class DataQAConfigTests(unittest.TestCase):
    def test_patch_contains_only_target_values_and_secret_reference(self):
        dep = test_delivery.ReleaseTests().deployment()
        container = dep['spec']['template']['spec']['containers'][0]
        container['env'] += [{'name': 'UNRELATED_PRIVATE', 'value': 'do-not-copy-to-patch'},
                             {'name': dq.KEY, 'valueFrom': {'secretKeyRef': {'name': 'old', 'key': 'old'}}}]
        ops = dq.add_dataqa_patch(dep, [], 'new-secret')
        self.assertEqual(len(ops), 4)
        self.assertNotIn('do-not-copy-to-patch', json.dumps(ops))
        updated = copy.deepcopy(container)
        for op in ops:
            pos = op['path'].rsplit('/', 1)[1]
            if pos == '-':
                updated['env'].append(op['value'])
            else:
                updated['env'][int(pos)] = op['value']
        dq.validate_planned(container, updated, 'new-secret')
        self.assertEqual(updated['env'][0], container['env'][0])
        self.assertEqual(updated['env'][1], container['env'][1])
        self.assertEqual([e for e in updated['env'] if e['name'] == dq.KEY][0]['valueFrom']['secretKeyRef']['name'], 'new-secret')

    def test_unrelated_settings_change_rejected(self):
        before = test_delivery.ReleaseTests().deployment()['spec']['template']['spec']['containers'][0]
        after = copy.deepcopy(before)
        after['env'] = dq.updated_env(before, 'new')
        after['image'] = 'wrong'
        with self.assertRaises(RuntimeError):
            dq.validate_planned(before, after, 'new')

    def test_secret_stdin_only_and_creation_failure(self):
        with patch('dataqa_config.subprocess.run', return_value=SimpleNamespace(returncode=0)) as run:
            dq.create_key_secret(['kubectl'], 'new', 'synthetic-test-key')
            args, kwargs = run.call_args
            self.assertNotIn('synthetic-test-key', json.dumps(args))
            self.assertEqual(json.loads(kwargs['input'])['stringData']['api-key'], 'synthetic-test-key')
        with patch('dataqa_config.subprocess.run', return_value=SimpleNamespace(returncode=1)):
            with self.assertRaises(RuntimeError):
                dq.create_key_secret(['kubectl'], 'new', 'synthetic-test-key')

    def test_preserves_timeouts_and_inputs_in_runtime_expectations(self):
        before = {'retrieve_timeout': 240, 'dataqa_timeout': 180, 'trust_env': False,
                  'inputs': {'hytoken': '', 'hyaccount': ''}, 'app_id': 'old'}
        expected = dq.target_proof(before, 'hash')
        self.assertEqual(expected['app_id'], 'app-jeg6ud59')
        self.assertEqual(expected['conversation_prefix'], 'SquareWorkflow_')
        self.assertEqual(expected['retrieve_timeout'], 240)
        self.assertEqual(expected['dataqa_timeout'], 180)
        self.assertEqual(before['app_id'], 'old')
