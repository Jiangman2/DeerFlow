import copy
import unittest
import yaml

import config_reconcile as cr
import reconcile_runtime as runtime
import test_delivery


class ConfigReconcileTests(unittest.TestCase):
    def config(self):
        return {'models': [{'name': 'existing', 'api_key': '$MODEL_KEY'}], 'loop_detection': {'enabled': True},
                'tools': [{'name': 'datalake_smart_qa', 'retrieve_timeout_seconds': 240},
                          {'name': 'datalake_data_qa', 'use': 'deerflow.community.appforge.tools:datalake_data_qa_tool',
                           'app_id': 'app-v16p2syh', 'api_key': '$APPFORGE_DATA_QA_API_KEY',
                           'api_url': 'http://10.77.124.142:9080', 'conversation_prefix': 'SquareWorkflow_',
                           'timeout_seconds': 180, 'trust_env': False}]}

    def test_real_old_override_shape_migrates_only_four_fields(self):
        before = self.config()
        raw, after = runtime.transform(yaml.safe_dump(before))
        self.assertEqual(yaml.safe_load(raw), after)
        self.assertEqual(after['tools'][1]['app_id'], 'app-jeg6ud59')
        self.assertEqual(after['tools'][1]['api_key'], '$APPFORGE_DATA_QA_API_KEY')
        after['tools'][1] = before['tools'][1]
        self.assertEqual(after, before)

    def test_missing_duplicate_wrong_provider_rejected(self):
        for mutation in ('missing', 'duplicate', 'provider'):
            before = self.config()
            if mutation == 'missing':
                before['tools'].pop()
            elif mutation == 'duplicate':
                before['tools'].append(copy.deepcopy(before['tools'][1]))
            else:
                before['tools'][1]['use'] = 'different:tool'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                runtime.transform(yaml.safe_dump(before))

    def test_shared_yaml_alias_does_not_change_other_sections(self):
        before = self.config()
        before['audit_copy'] = before['tools'][1]
        _, after = runtime.transform(yaml.safe_dump(before))
        self.assertEqual(after['audit_copy']['app_id'], 'app-v16p2syh')
        self.assertEqual(after['tools'][1]['app_id'], 'app-jeg6ud59')

    def deployment(self):
        dep = test_delivery.ReleaseTests().deployment()
        spec = dep['spec']['template']['spec']
        spec['containers'][0]['volumeMounts'].append({'name': 'config', 'mountPath': cr.CONFIG_PATH, 'subPath': 'config.yaml', 'readOnly': True})
        spec['volumes'].append({'name': 'config', 'configMap': {'name': 'old-config', 'defaultMode': 420}})
        return dep

    def test_two_mount_changes_preserve_original_config_object_reference(self):
        dep = self.deployment()
        patch = test_delivery.release.patch_for(dep, 'new-code')
        result = cr.add_config_patch(dep, patch, 'new-config')
        mounts = next(op['value'] for op in result if op['path'].endswith('/volumeMounts'))
        volumes = next(op['value'] for op in result if op['path'].endswith('/volumes'))
        self.assertEqual(next(m for m in mounts if m['mountPath'] == cr.CONFIG_PATH)['name'], 'new-config')
        self.assertEqual(next(v for v in volumes if v['name'] == 'config')['configMap']['name'], 'old-config')
        self.assertEqual(next(v for v in volumes if v['name'] == 'new-config')['configMap']['name'], 'new-config')
        self.assertEqual(cr.binding(dep)[1]['configMap']['name'], 'old-config')

    def test_items_mapping_and_non_configmap_rejected(self):
        dep = self.deployment()
        volume = dep['spec']['template']['spec']['volumes'][-1]
        volume['configMap']['items'] = [{'path': 'config.yaml', 'key': 'settings.yaml'}]
        self.assertEqual(cr.binding(dep)[2], 'settings.yaml')
        del volume['configMap']
        volume['secret'] = {'secretName': 'not-a-configmap'}
        with self.assertRaises(RuntimeError):
            cr.binding(dep)
