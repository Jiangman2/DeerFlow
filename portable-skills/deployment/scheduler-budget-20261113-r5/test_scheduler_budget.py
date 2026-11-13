"""No Kubernetes/network tests: semantic scope, fail-closed and readonly mode."""
import base64
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, str(HERE / filename))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


host = module('scheduler_budget_host', 'activate-scheduler-budget-20261113-r5.py')
child = module('scheduler_budget_child', 'qualify_budget_child.py')


def fixture():
    volumes = [{'name': 'config', 'configMap': {'name': host.OLD_CM}}]
    mounts = [{'name': 'config', 'mountPath': '/app/backend/config.yaml', 'subPath': 'config.yaml', 'readOnly': True}]
    for index, (name, paths) in enumerate(host.OVERLAYS.items()):
        volume = 'source-%s' % index
        volumes.append({'name': volume, 'configMap': {'name': name}})
        if name == host.OPENSSL_CM:
            mounts.append({'name': volume, 'mountPath': '/etc/deerflow-openssl', 'readOnly': True})
        else:
            mounts.extend({'name': volume, 'mountPath': path, 'subPath': key, 'readOnly': True} for key, path in paths.items())
    # The unrelated references and every strategy/environment field must survive.
    volumes += [{'name': 'ca', 'secret': {'secretName': 'never-read-secret'}},
                {'name': 'userdata', 'persistentVolumeClaim': {'claimName': 'fixed-userdata'}}]
    mounts += [{'name': 'ca', 'mountPath': '/etc/deerflow-ca', 'readOnly': True},
               {'name': 'userdata', 'mountPath': '/app/backend/.deer-flow', 'subPath': 'deer-flow'}]
    return {'metadata': {'name': 'gateway', 'namespace': host.NS, 'uid': host.DEPLOYMENT_UID,
            'generation': 415, 'resourceVersion': '42'}, 'spec': {'replicas': 70, 'paused': False,
            'selector': {'matchLabels': {'app': 'gateway'}},
            'strategy': {'type': 'RollingUpdate', 'rollingUpdate': {'maxSurge': 1, 'maxUnavailable': 0}},
            'template': {'metadata': {'labels': {'app': 'gateway'}, 'annotations': {'retain': 'exact'}},
            'spec': {'containers': [{'name': 'gateway', 'image': host.IMAGE, 'command': ['python', 'fixed.py'],
                'env': [{'name': 'KEEP', 'valueFrom': {'secretKeyRef': {'name': 'private', 'key': 'unused'}}}],
                'resources': {'requests': {'cpu': '1'}}, 'volumeMounts': mounts}], 'volumes': volumes}}},
            'status': {'observedGeneration': 415, 'replicas': 70, 'updatedReplicas': 70,
                       'readyReplicas': 70, 'availableReplicas': 70}}


def runtime_fixture(deployment, name='gateway-test', number=0):
    rs = {'metadata': {'name': 'gateway-current-rs', 'uid': 'rs-uid', 'labels': {'pod-template-hash': 'hash'},
        'ownerReferences': [{'name': 'gateway', 'uid': host.DEPLOYMENT_UID, 'kind': 'Deployment', 'controller': True}]},
        'spec': {'template': copy.deepcopy(deployment['spec']['template'])}}
    rs['spec']['template']['metadata']['labels']['pod-template-hash'] = 'hash'
    pod = {'metadata': {'name': name, 'uid': '00000000-0000-4000-8000-%012x' % number, 'labels': {'pod-template-hash': 'hash'},
        'ownerReferences': [{'name': rs['metadata']['name'], 'uid': rs['metadata']['uid'], 'kind': 'ReplicaSet', 'controller': True}]},
        'spec': copy.deepcopy(deployment['spec']['template']['spec']),
        'status': {'phase': 'Running', 'podIP': '10.1.0.%d' % (number + 1), 'podIPs': [{'ip': '10.1.0.%d' % (number + 1)}],
        'conditions': [{'type': 'Ready', 'status': 'True'}],
        'containerStatuses': [{'name': 'gateway', 'ready': True, 'restartCount': 0,
            'containerID': 'container-%d' % number, 'imageID': 'fixed-image-id',
            'state': {'running': {'startedAt': '2026-11-12T00:00:00Z'}}}]}}
    pod['spec']['nodeName'] = 'fixed-node'
    return pod, rs


def endpoint_fixture(dual=False):
    deployment = fixture()
    pods = [runtime_fixture(deployment, 'gateway-p%02d' % i, i)[0] for i in range(70)]
    service = {'metadata': {'uid': 'service-uid'}, 'spec': {'ipFamilies': ['IPv4'],
        'ports': [{'name': 'http', 'port': 80, 'targetPort': 8001, 'protocol': 'TCP'}]}}
    slices = {'items': []}
    families = ['IPv4', 'IPv6'] if dual else ['IPv4']
    service['spec']['ipFamilies'] = families
    if dual:
        for i, pod in enumerate(pods):
            pod['status']['podIPs'].append({'ip': 'fd00::%x' % (i + 1)})
    for family in families:
        slices['items'].append({'metadata': {'name': 'gateway-' + family.lower(), 'namespace': host.NS,
            'labels': {'kubernetes.io/service-name': 'gateway'},
            'ownerReferences': [{'kind': 'Service', 'name': 'gateway', 'uid': 'service-uid'}]},
            'addressType': family, 'ports': [{'name': 'http', 'port': 8001, 'protocol': 'TCP'}],
            'endpoints': [{'conditions': {'ready': True}, 'addresses': [
                'fd00::%x' % (i + 1) if family == 'IPv6' else '10.1.0.%d' % (i + 1)],
                'targetRef': {'kind': 'Pod', 'name': pod['metadata']['name'], 'uid': pod['metadata']['uid'],
                              'namespace': host.NS}} for i, pod in enumerate(pods)]})
    return pods, service, slices


def synthetic_source_baseline(maps):
    result = {path: {'bytes': size, 'sha256': digest} for path, (size, digest) in host.CORE_SOURCES.items()}
    for name, paths in host.OVERLAYS.items():
        for key, path in paths.items():
            raw = maps[name]['data'][key].encode('utf-8')
            result[path] = {'bytes': len(raw), 'sha256': host.sha(raw)}
    return result


class BudgetReplacementTests(unittest.TestCase):
    def run_replace(self, raw):
        with mock.patch.object(host, 'OLD_CONFIG_BYTES', len(raw)), mock.patch.object(host, 'OLD_CONFIG_SHA', host.sha(raw)):
            return host.replace_budget(raw)

    def test_only_exact_ascii_scalar_changed_and_comments_crlf_preserved(self):
        raw = b'# budget 300\r\nmax_recursion_limit: 10000\r\nscheduler:\r\n  enabled: true\r\n  recursion_limit: 300 # keep\r\n  other: 300\r\nrest: yes\r\n'
        self.assertEqual(self.run_replace(raw), raw.replace(b'recursion_limit: 300 #', b'recursion_limit: 10000 #'))

    def test_requires_fixed_original_hash(self):
        with self.assertRaisesRegex(host.Refused, 'ORIGINAL_CONFIG_HASH_REQUIRED'):
            host.replace_budget(b'scheduler:\n  recursion_limit: 300\n')

    def test_10000_candidate_adds_two_bytes_and_preserves_existing_global_10000(self):
        raw = b'max_recursion_limit: 10000\nscheduler:\n  recursion_limit: 300\nunchanged: 300\n'
        candidate = self.run_replace(raw)
        self.assertEqual(len(candidate), len(raw) + 2)
        self.assertEqual(candidate, b'max_recursion_limit: 10000\nscheduler:\n  recursion_limit: 10000\nunchanged: 300\n')
        self.assertEqual(candidate.count(b'10000'), 2)

    def test_duplicate_scheduler_and_duplicate_budget_refused(self):
        for raw in (b'scheduler:\n  recursion_limit: 300\nscheduler:\n  recursion_limit: 300\n',
                    b'scheduler:\n  recursion_limit: 300\n  recursion_limit: 300\n'):
            with self.assertRaises(host.Refused):
                self.run_replace(raw)

    def test_budget_with_bool_string_other_integer_and_indent_refused(self):
        for token in (b'true', b'"300"', b'301', b'0300'):
            with self.assertRaises(host.Refused):
                self.run_replace(b'scheduler:\n  recursion_limit: ' + token + b'\n')
        with self.assertRaises(host.Refused):
            self.run_replace(b'scheduler:\n    recursion_limit: 300\n')

    def test_wrong_scheduler_block_does_not_select_nested_budget(self):
        with self.assertRaises(host.Refused):
            self.run_replace(b'scheduler:\n  enabled: true\nother:\n  recursion_limit: 300\n')


class SpecTests(unittest.TestCase):
    def build(self, original):
        with mock.patch.object(host, 'BEFORE_SPEC_SHA', host.sha(host.canonical(original['spec']))):
            return host.build_spec(original, 'new-fixed-config')

    def test_full_spec_unchanged_except_one_configmap_name(self):
        original = fixture()
        before = copy.deepcopy(original)
        candidate = self.build(original)
        self.assertEqual(original, before)
        candidate['template']['spec']['volumes'][0]['configMap']['name'] = host.OLD_CM
        self.assertEqual(candidate, original['spec'])

    def test_preserves_strategy_images_secrets_sources_and_ca(self):
        original = fixture()
        candidate = self.build(original)
        self.assertEqual(candidate['strategy'], original['spec']['strategy'])
        self.assertEqual(candidate['template']['spec']['containers'], original['spec']['template']['spec']['containers'])
        self.assertEqual(candidate['template']['spec']['volumes'][1:], original['spec']['template']['spec']['volumes'][1:])

    def test_full_compare_rejects_drift_and_wrong_generation(self):
        original = fixture()
        with mock.patch.object(host, 'BEFORE_SPEC_SHA', host.sha(host.canonical(original['spec']))):
            for key, value in (('generation', 416), ('uid', 'wrong')):
                changed = copy.deepcopy(original)
                changed['metadata'][key] = value
                with self.assertRaisesRegex(host.Refused, 'BASELINE_CHANGED'):
                    host.build_spec(changed, 'candidate')
            changed = copy.deepcopy(original)
            changed['spec']['strategy']['rollingUpdate']['maxUnavailable'] = 1
            with self.assertRaisesRegex(host.Refused, 'BASELINE_CHANGED'):
                host.build_spec(changed, 'candidate')

    def test_fresh_image_is_pinned_independently_of_fixture_hash(self):
        original = fixture()
        original['spec']['template']['spec']['containers'][0]['image'] = 'unrelated@sha256:bad'
        with self.assertRaisesRegex(host.Refused, 'FRESH_BASELINE_IMAGE_CHANGED'):
            self.build(original)

    def test_config_requires_readonly_unique_unshadowed_subpath(self):
        for mutate in (
            lambda pod: pod['containers'][0]['volumeMounts'][0].update(readOnly=False),
            lambda pod: pod['containers'][0]['volumeMounts'][0].update(subPath='wrong'),
            lambda pod: pod['containers'][0]['volumeMounts'].append({'name': 'x', 'mountPath': '/app/backend'}),
        ):
            original = fixture()
            mutate(original['spec']['template']['spec'])
            with self.assertRaises(host.Refused):
                self.build(original)

    def test_patch_is_four_tests_one_replace_no_whole_spec_replace(self):
        original = fixture()
        candidate = self.build(original)
        patch = host.cas_patch(original, candidate)
        self.assertEqual([row['op'] for row in patch], ['test', 'test', 'test', 'test', 'replace'])
        self.assertEqual(patch[3]['value'], original['spec'])
        self.assertEqual(patch[-1], {'op': 'replace', 'path': '/spec/template/spec/volumes/0/configMap/name', 'value': 'new-fixed-config'})

    def test_old_counts_before_observed_new_generation_are_not_success(self):
        original = fixture()
        original['metadata']['generation'] = 416
        self.assertFalse(host.is_converged(original, 416))
        original['status']['observedGeneration'] = 416
        self.assertTrue(host.is_converged(original, 416))
        original['status']['terminatingReplicas'] = 1
        self.assertFalse(host.is_converged(original, 416))

    def test_cm_clone_preserves_extra_data_binary_source_not_mutated(self):
        original = {'data': {'config.yaml': 'original', 'extra': 'keep'}, 'binaryData': {'fixed': 'YQ=='}}
        before = copy.deepcopy(original)
        result = host.new_configmap(original, 'new', '0' * 32, b'candidate')
        self.assertEqual(original, before)
        self.assertEqual(result['data'], {'config.yaml': 'candidate', 'extra': 'keep'})
        self.assertEqual(result['binaryData'], original['binaryData'])
        self.assertIs(result['immutable'], True)
        self.assertNotIn('ownerReferences', result['metadata'])


class StrictYamlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import yaml
        except ImportError:
            raise unittest.SkipTest('PyYAML qualification belongs to Gateway interpreter')

    def test_int_budgets_and_semantic_other_fields_equal(self):
        before = b'max_recursion_limit: 10000\nscheduler:\n  recursion_limit: 300\nunchanged: [yes, 42]\n'
        after = before.replace(b'limit: 300', b'limit: 10000')
        parsed = child.check_config(before, {'bytes': len(before), 'sha256': child.digest(before), 'scheduler_recursion_limit': 300})
        next_parsed = child.check_config(after, {'bytes': len(after), 'sha256': child.digest(after), 'scheduler_recursion_limit': 10000})
        self.assertEqual(next_parsed['scheduler']['recursion_limit'], next_parsed['max_recursion_limit'])
        self.assertEqual(next_parsed['max_recursion_limit'], 10000)
        self.assertEqual(next_parsed['scheduler']['recursion_limit'], next_parsed['max_recursion_limit'])
        self.assertEqual(next_parsed['max_recursion_limit'], 10000)
        next_parsed['scheduler']['recursion_limit'] = 300
        self.assertEqual(parsed, next_parsed)

    def test_duplicate_root_nested_key_alias_merge_and_unsafe_tag_refused(self):
        for raw in (b'a: 1\na: 2\n', b'a:\n  b: 1\n  b: 2\n', b'a: &value [1]\nb: *value\n',
                    b'a: {<<: {x: 1}}\n', b'a: !!python/object/apply:os.system [echo]\n', b'a: 1\n---\na: 2\n'):
            with self.assertRaises(Exception):
                child.parse_yaml(raw)

    def test_bool_string_float_budget_and_max_change_refused(self):
        for value in ('true', '"300"', '300.0'):
            raw = ('max_recursion_limit: 10000\nscheduler:\n  recursion_limit: %s\n' % value).encode()
            with self.assertRaises(child.Refused):
                child.check_config(raw, {'bytes': len(raw), 'sha256': child.digest(raw), 'scheduler_recursion_limit': 300})
        raw = b'max_recursion_limit: 10001\nscheduler:\n  recursion_limit: 300\n'
        with self.assertRaises(child.Refused):
            child.check_config(raw, {'bytes': len(raw), 'sha256': child.digest(raw), 'scheduler_recursion_limit': 300})


class BoundaryTests(unittest.TestCase):
    def test_child_asset_hash_and_not_sealed_guard(self):
        with self.assertRaisesRegex(host.Refused, 'NOT_SEALED'):
            host.load_child()
        with mock.patch.object(host, 'CHILD_ASSET', {'base64': base64.b64encode(b'pass').decode(), 'sha256': '0' * 64}):
            with self.assertRaisesRegex(host.Refused, 'HASH'):
                host.load_child()

    def test_command_whitelist_refuses_secret_delete_arbitrary_exec(self):
        operation = host.Operator('fixed-child', readonly=True)
        operation.known_pods.add('fixed-pod')
        with mock.patch.object(host.subprocess, 'Popen') as launch:
            for args in (['get', 'secret', 'unused', '-o', 'json'], ['delete', 'pod', 'anything'],
                         ['exec', '-i', 'fixed-pod', '-c', 'gateway', '--', 'sh', '-c', 'dangerous']):
                with self.assertRaisesRegex(host.Refused, 'COMMAND_SCOPE'):
                    operation.command(args)
            launch.assert_not_called()

    def test_readonly_mode_cannot_create_patch_claim_or_write_journal(self):
        operation = host.Operator('fixed-child', readonly=True)
        operation.report['once_claimed'] = True
        with mock.patch.object(host, 'private_write') as write, mock.patch.object(host.subprocess, 'Popen') as launch:
            operation.journal('read-only')
            for args in (['create', '-f', '-', '-o', 'json'], ['patch', 'deployment', 'gateway']):
                with self.assertRaises(host.Refused):
                    operation.command(args)
            write.assert_not_called()
            launch.assert_not_called()

    def test_readonly_in_progress_returns_without_patch_wait_or_disk_write(self):
        operation = host.Operator('fixed-child', readonly=True)
        original = fixture()
        operation.expected = original['spec']
        operation.before = copy.deepcopy(original)
        operation.plan = {'before': host.identity(original)}
        original['metadata']['generation'] = 416
        with mock.patch.object(operation, 'load_plan'), mock.patch.object(operation, 'get', return_value=original), \
             mock.patch.object(operation, 'verify') as verify, mock.patch.object(operation, 'rollout') as rollout, \
             mock.patch.object(operation, 'cutover') as patch, mock.patch.object(host, 'private_write') as write:
            operation.run()
            self.assertEqual(operation.report['status'], 'GATEWAY_416_ROLLOUT_IN_PROGRESS_NO_CHANGE')
            verify.assert_not_called()
            rollout.assert_not_called()
            patch.assert_not_called()
            write.assert_not_called()

    def test_readonly_old_baseline_does_not_resubmit_recorded_patch(self):
        operation = host.Operator('fixed-child', readonly=True)
        operation.before = fixture()
        operation.plan = {'before': host.identity(operation.before)}
        with mock.patch.object(operation, 'load_plan'), mock.patch.object(operation, 'get', return_value=operation.before), \
             mock.patch.object(operation, 'cutover') as patch, mock.patch.object(host, 'private_write') as write:
            operation.run()
            self.assertEqual(operation.report['status'], 'SCHEDULER_BUDGET_PATCH_NOT_OBSERVED_REVIEW_REQUIRED')
            self.assertEqual(operation.report['error_code'], 'RECORDED_PATCH_NOT_OBSERVED_NO_RETRY')
            patch.assert_not_called()
            write.assert_not_called()

    def test_private_write_exclusive_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'once.json'
            host.private_write(path, b'first')
            with self.assertRaises(FileExistsError):
                host.private_write(path, b'second')
            self.assertEqual(path.read_bytes(), b'first')
            if os.name == 'posix':
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_child_audit_blocks_network_writes_process(self):
        for event, arguments in (('socket.__new__', ()), ('subprocess.Popen', ()),
                                  ('open', ('fixed', 'w', os.O_WRONLY)), ('os.rename', ())):
            with self.assertRaises(child.Refused):
                child.audit(event, arguments)
        child.audit('open', ('fixed', 'r', os.O_RDONLY))

    def test_wrong_host_guard_before_assets_commands_or_files(self):
        with mock.patch.object(host.os, 'name', 'nt'), mock.patch.object(host, 'load_child') as load, \
             mock.patch.object(host.Operator, 'run') as run, mock.patch('builtins.print') as output:
            self.assertEqual(host.main(['--activate-scheduler-budget-once']), 1)
            load.assert_not_called()
            run.assert_not_called()
            result = json.loads(output.call_args[0][0])
            self.assertEqual(result['error_code'], 'DATAX01_REQUIRED')
            self.assertEqual(result['commands'], 0)
            self.assertFalse(result['once_claimed'])


class FleetTests(unittest.TestCase):
    def test_pod_owner_template_and_runtime_guard_accept_exact_projection(self):
        deployment = fixture()
        pod, rs = runtime_fixture(deployment)
        stamp = host.pod_guard(pod, rs, deployment, 'fixed-image-id')
        self.assertEqual(stamp['uid'], '00000000-0000-4000-8000-000000000000')
        self.assertNotIn('fixed-image-id', json.dumps(stamp))

    def test_serviceaccount_projection_is_only_allowed_extra(self):
        deployment = fixture()
        pod, rs = runtime_fixture(deployment)
        pod['spec']['volumes'].append({'name': 'kube-api-access-fake', 'projected': {'sources': [{'serviceAccountToken': {'path': 'token', 'expirationSeconds': 3607}}]}})
        pod['spec']['containers'][0]['volumeMounts'].append({'name': 'kube-api-access-fake', 'mountPath': '/var/run/secrets/kubernetes.io/serviceaccount', 'readOnly': True})
        host.pod_guard(pod, rs, deployment)
        pod['spec']['containers'][0]['volumeMounts'][-1]['mountPath'] = '/app/backend'
        with self.assertRaisesRegex(host.Refused, 'EXTRA_MOUNT'):
            host.pod_guard(pod, rs, deployment)

    def test_drift_restart_injected_environment_wrong_image_or_owner_refused(self):
        deployment = fixture()
        for mutate in (
            lambda pod, rs: pod['metadata']['ownerReferences'][0].update(uid='wrong'),
            lambda pod, rs: pod['status']['containerStatuses'][0].update(restartCount=1),
            lambda pod, rs: pod['status']['containerStatuses'][0].update(imageID='different'),
            lambda pod, rs: pod['spec']['containers'][0]['env'].append({'name': 'injected', 'value': 'x'}),
            lambda pod, rs: rs['spec']['template']['spec']['containers'][0].update(command=['changed']),
        ):
            pod, rs = runtime_fixture(deployment)
            mutate(pod, rs)
            with self.assertRaises(host.Refused):
                host.pod_guard(pod, rs, deployment, 'fixed-image-id')

    def test_unexpected_host_flags_init_containers_runtime_or_dns_refused(self):
        deployment = fixture()
        for key, value in (('hostNetwork', True), ('hostPID', True), ('hostIPC', True),
                ('shareProcessNamespace', True), ('initContainers', [{'name': 'injected', 'image': 'untrusted'}]),
                ('hostAliases', [{'ip': '203.0.113.200', 'hostnames': ['private']}]), ('runtimeClassName', 'untrusted'),
                ('dnsConfig', {'nameservers': ['203.0.113.200']})):
            pod, rs = runtime_fixture(deployment)
            pod['spec'][key] = value
            with self.assertRaisesRegex(host.Refused, 'POD_UNEXPECTED_SPEC_FIELD'):
                host.pod_guard(pod, rs, deployment)

    def test_only_known_api_defaults_and_two_admission_tolerations_accepted(self):
        deployment = fixture()
        pod, rs = runtime_fixture(deployment)
        pod['spec'].update(priority=0, serviceAccount='default', serviceAccountName='default',
            enableServiceLinks=True, preemptionPolicy='PreemptLowerPriority', dnsPolicy='ClusterFirst',
            securityContext={}, tolerations=copy.deepcopy(list(host.DEFAULT_TOLERATIONS)))
        host.pod_guard(pod, rs, deployment)
        pod['spec']['tolerations'].append({'operator': 'Exists'})
        with self.assertRaisesRegex(host.Refused, 'POD_EXTRA_TOLERATIONS'):
            host.pod_guard(pod, rs, deployment)

    def test_deprecated_serviceaccount_alias_matches_requested_nondefault_only(self):
        deployment = fixture()
        deployment['spec']['template']['spec']['serviceAccountName'] = 'fixed-gateway-account'
        pod, rs = runtime_fixture(deployment)
        pod['spec']['serviceAccount'] = 'fixed-gateway-account'
        host.pod_guard(pod, rs, deployment)
        pod['spec']['serviceAccount'] = 'wrong-account'
        with self.assertRaisesRegex(host.Refused, 'POD_UNEXPECTED_SPEC_FIELD'):
            host.pod_guard(pod, rs, deployment)

    def test_endpoints_exact_owner_ready_uid_count(self):
        pods, service, slices = endpoint_fixture()
        self.assertEqual(len(host.endpoints(slices, service, pods)), 70)
        wrong = copy.deepcopy(slices)
        wrong['items'][0]['endpoints'][0]['conditions']['terminating'] = True
        with self.assertRaisesRegex(host.Refused, 'ENDPOINT_NOT_READY'):
            host.endpoints(wrong, service, pods)
        wrong_service = copy.deepcopy(service)
        wrong_service['metadata']['uid'] = 'wrong-owner'
        with self.assertRaisesRegex(host.Refused, 'SERVICE_OWNER'):
            host.endpoints(slices, wrong_service, pods)

    def test_fake_correct_targetrefs_wrong_ip_or_port_and_duplicate_refused(self):
        pods, service, slices = endpoint_fixture()
        wrong = copy.deepcopy(slices)
        for item in wrong['items'][0]['endpoints']:
            item['addresses'] = ['203.0.113.200']
        with self.assertRaisesRegex(host.Refused, 'ADDRESS_MISMATCH'):
            host.endpoints(wrong, service, pods)
        wrong = copy.deepcopy(slices)
        wrong['items'][0]['ports'] = [{'name': 'http', 'port': 5432, 'protocol': 'UDP'}]
        with self.assertRaisesRegex(host.Refused, 'PORT_SET_CHANGED'):
            host.endpoints(wrong, service, pods)
        wrong = copy.deepcopy(slices)
        wrong['items'][0]['endpoints'].append(copy.deepcopy(wrong['items'][0]['endpoints'][0]))
        with self.assertRaisesRegex(host.Refused, 'DUPLICATE'):
            host.endpoints(wrong, service, pods)
        wrong = copy.deepcopy(slices)
        wrong['items'][0]['endpoints'].pop()
        with self.assertRaisesRegex(host.Refused, 'CLOSURE_MISMATCH'):
            host.endpoints(wrong, service, pods)

    def test_named_port_and_dual_stack_closure(self):
        pods, service, slices = endpoint_fixture(dual=True)
        service['spec']['ports'][0]['targetPort'] = 'gateway-http'
        for pod in pods:
            pod['spec']['containers'][0]['ports'] = [{'name': 'gateway-http', 'containerPort': 8001, 'protocol': 'TCP'}]
        self.assertEqual(len(host.endpoints(slices, service, pods)), 70)
        pods[0]['spec']['containers'][0]['ports'][0]['containerPort'] = 8002
        with self.assertRaisesRegex(host.Refused, 'TARGET_PORT_VARIANCE'):
            host.endpoints(slices, service, pods)

    def test_unnamed_service_port_and_controller_empty_port_name_are_equivalent(self):
        pods, service, slices = endpoint_fixture()
        service['spec']['ports'][0].pop('name')
        slices['items'][0]['ports'][0]['name'] = ''
        self.assertEqual(len(host.endpoints(slices, service, pods)), 70)
        slices['items'][0]['ports'][0]['name'] = 'unexpected'
        with self.assertRaisesRegex(host.Refused, 'PORT_SET_CHANGED'):
            host.endpoints(slices, service, pods)

    def test_source_closure_requires_all_eight_ro_projections_and_openssl(self):
        deployment = fixture()
        maps = {name: {'kind': 'ConfigMap', 'metadata': {'name': name, 'namespace': host.NS, 'uid': name, 'resourceVersion': '1'},
            'data': {key: 'x' * 189 if name == host.OPENSSL_CM else 'pass\n' for key in paths}} for name, paths in host.OVERLAYS.items()}
        with mock.patch.object(host, 'OPENSSL_SHA', host.sha(b'x' * 189)), \
             mock.patch.object(host, 'EXPECTED_SOURCES', synthetic_source_baseline(maps)):
            sources = host.source_binding(deployment['spec'], maps)
            self.assertEqual(len(sources), 13)
            deployment['spec']['template']['spec']['containers'][0]['volumeMounts'][1]['readOnly'] = False
            with self.assertRaisesRegex(host.Refused, 'SOURCE_CONFIGMAP_MOUNT'):
                host.source_binding(deployment['spec'], maps)

    def test_unchanged_configmap_name_cannot_hide_source_content_drift(self):
        deployment = fixture()
        maps = {name: {'kind': 'ConfigMap', 'metadata': {'name': name, 'namespace': host.NS, 'uid': name, 'resourceVersion': '1'},
            'data': {key: 'x' * 189 if name == host.OPENSSL_CM else 'pass\n' for key in paths}} for name, paths in host.OVERLAYS.items()}
        expected = synthetic_source_baseline(maps)
        with mock.patch.object(host, 'OPENSSL_SHA', host.sha(b'x' * 189)), mock.patch.object(host, 'EXPECTED_SOURCES', expected):
            self.assertEqual(host.source_binding(deployment['spec'], maps), expected)
            maps[host.LINK_CM]['data']['manager.py'] = 'pass\n# changed with identical CM name\n'
            with self.assertRaisesRegex(host.Refused, 'FRESH_BASELINE_SOURCE_HASH_CHANGED'):
                host.source_binding(deployment['spec'], maps)


class RecoveryTests(unittest.TestCase):
    def test_full_fake_cutover_and_separate_readonly_journal_recovery(self):
        # Root ownership is a production host requirement. Tests run as UID1000
        # keep the exclusive-write implementation and inject only its read shim.
        original = fixture()
        config_raw = b'max_recursion_limit: 10000\nscheduler:\n  recursion_limit: 300\nkeep: true\n'
        maps = {name: {'apiVersion': 'v1', 'kind': 'ConfigMap',
            'metadata': {'name': name, 'namespace': host.NS, 'uid': name + '-uid', 'resourceVersion': '1'},
            'data': {key: 'x' * 189 if name == host.OPENSSL_CM else 'pass\n' for key in paths}} for name, paths in host.OVERLAYS.items()}
        maps[host.OLD_CM] = {'apiVersion': 'v1', 'kind': 'ConfigMap',
            'metadata': {'name': host.OLD_CM, 'namespace': host.NS, 'uid': 'old-config-uid', 'resourceVersion': '1'},
            'data': {'config.yaml': config_raw.decode(), 'preserved': 'fixed'}, 'binaryData': {'unchanged': 'YQ=='}}
        service = {'metadata': {'name': 'gateway', 'namespace': host.NS, 'uid': 'service-uid'},
            'spec': {'selector': {'app': 'gateway'}, 'ports': [{'name': 'http', 'port': 8001, 'targetPort': 8001, 'protocol': 'TCP'}], 'ipFamilies': ['IPv4']}}
        calls = []
        state = {'deployment': copy.deepcopy(original), 'new_cm': None}

        class Fake(host.Operator):
            def command(self, args, body=None, **kwargs):
                calls.append(args)
                if args == ['get', 'deployment', 'gateway', '-o', 'json']:
                    return copy.deepcopy(state['deployment'])
                if args == ['get', 'service', 'gateway', '-o', 'json']:
                    return copy.deepcopy(service)
                if args[:2] == ['get', 'configmap']:
                    return copy.deepcopy(maps[args[2]] if args[2] in maps else state['new_cm'])
                if args == host.POD_LIST_ARGS:
                    return ''.join('gateway-p%02d 00000000-0000-4000-8000-%012x Running <none>\n' % (i, i) for i in range(70))
                if args[:2] == ['get', 'pod']:
                    i = int(args[2][-2:])
                    return runtime_fixture(state['deployment'], args[2], i)[0]
                if args[:2] == ['get', 'replicaset']:
                    return runtime_fixture(state['deployment'])[1]
                if args[:1] == ['create']:
                    state['new_cm'] = json.loads(body)
                    state['new_cm']['metadata'].update(uid='new-cm-uid', resourceVersion='2')
                    return copy.deepcopy(state['new_cm'])
                if args[:1] == ['patch']:
                    state['deployment']['metadata'].update(generation=416, resourceVersion='3')
                    state['deployment']['spec'] = copy.deepcopy(self.expected)
                    state['deployment']['status']['observedGeneration'] = 416
                    return copy.deepcopy(state['deployment'])
                if args[:2] == ['get', 'endpointslices']:
                    return {'items': [{'metadata': {'name': 'gateway-ep', 'namespace': host.NS, 'labels': {'kubernetes.io/service-name': 'gateway'},
                        'ownerReferences': [{'kind': 'Service', 'name': 'gateway', 'uid': 'service-uid'}]},
                        'addressType': 'IPv4', 'ports': [{'name': 'http', 'port': 8001, 'protocol': 'TCP'}],
                        'endpoints': [{'addresses': ['10.1.0.%d' % (i + 1)], 'conditions': {'ready': True}, 'targetRef': {'kind': 'Pod', 'name': 'gateway-p%02d' % i,
                            'uid': '00000000-0000-4000-8000-%012x' % i}} for i in range(70)]}]}
                raise AssertionError(args)

            def qualification(self, pod, candidate=False):
                return {'config_sha256': host.OLD_CONFIG_SHA, 'candidate_sha256': self.plan['new_config_sha256'],
                    'scheduler_recursion_limit': 300, 'candidate_scheduler_recursion_limit': 10000,
                    'max_recursion_limit': 10000, 'source_files_verified': 13, 'versions': child.PACKAGES,
                    'semantic_change_only_scheduler_recursion_limit': True}

        with tempfile.TemporaryDirectory() as temporary:
            control = Path(temporary) / 'control'
            def readonly_file(path, maximum=4194304):
                raw = Path(path).read_bytes()
                self.assertLessEqual(len(raw), maximum)
                return raw
            with mock.patch.object(host, 'CONTROL', control), \
                 mock.patch.object(host, 'OLD_CONFIG_BYTES', len(config_raw)), \
                 mock.patch.object(host, 'OLD_CONFIG_SHA', host.sha(config_raw)), \
                 mock.patch.object(host, 'OPENSSL_SHA', host.sha(b'x' * 189)), \
                 mock.patch.object(host, 'EXPECTED_SOURCES', synthetic_source_baseline(maps)), \
                 mock.patch.object(host, 'BEFORE_SPEC_SHA', host.sha(host.canonical(original['spec']))), \
                 mock.patch.object(host, 'private_read', side_effect=readonly_file), mock.patch('builtins.print'):
                operation = Fake('fixed-child')
                operation.run()
                self.assertEqual(operation.report['status'], 'SCHEDULER_BUDGET_10000_DEPLOYED_RUNTIME_ACCEPTED')
                self.assertEqual(len([row for row in calls if row[0] == 'patch']), 1)
                self.assertEqual(len([row for row in calls if row[0] == 'create']), 1)
                prior = {p.name: p.read_bytes() for p in control.iterdir()}
                before_calls = len(calls)
                # Readonly control directory metadata is separately tested by
                # source guards; UID1000 cannot claim ownership of a root backup.
                original_load = Fake.load_plan
                def load_without_root_mode(instance):
                    # Inject only this fixed directory metadata observation;
                    # preserve the original stat behavior for every other path.
                    fake_stat = type('RootDirectory', (), {'st_mode': 0o40700, 'st_uid': 0})()
                    with mock.patch.object(Path, 'stat', autospec=True, side_effect=lambda path, *a, **k: fake_stat if path == control else os.stat(str(path), *a, **k)):
                        original_load(instance)
                verify = Fake('fixed-child', readonly=True)
                with mock.patch.object(verify, 'load_plan', side_effect=lambda: load_without_root_mode(verify)):
                    verify.run()
                self.assertEqual(verify.report['status'], 'SCHEDULER_BUDGET_10000_DEPLOYED_RUNTIME_ACCEPTED')
                self.assertTrue(all(row[0] == 'get' for row in calls[before_calls:]))
                self.assertEqual({p.name: p.read_bytes() for p in control.iterdir()}, prior)


def admitted_runtime(label='currenthash', volume_name='kube-api-access-dynamic'):
    deployment = fixture()
    requested = deployment['spec']['template']['spec']
    requested['initContainers'] = [{'name': name, 'image': host.IMAGE, 'command': ['python', 'DO_NOT_PRINT_COMMAND'],
        'env': [{'name': 'private_reference', 'value': 'DO_NOT_PRINT_INIT_VALUE'}],
        'volumeMounts': copy.deepcopy(requested['containers'][0]['volumeMounts'][:1])}
        for name in ('initialize-compatibility', 'initialize-runtime')]
    requested['topologySpreadConstraints'] = [{'maxSkew': 1, 'topologyKey': 'kubernetes.io/hostname',
        'whenUnsatisfiable': 'ScheduleAnyway', 'matchLabelKeys': ['pod-template-hash'],
        'labelSelector': {'matchLabels': {'app': 'gateway'}, 'matchExpressions': [{'key': 'preserve', 'operator': 'Exists'}]}}]
    pod, rs = runtime_fixture(deployment)
    pod['metadata']['labels']['pod-template-hash'] = label
    rs['metadata']['labels']['pod-template-hash'] = label
    rs['spec']['template']['metadata']['labels']['pod-template-hash'] = label
    rs['spec']['selector'] = {'matchLabels': {'app': 'gateway', 'pod-template-hash': label}}
    pod['spec']['volumes'].append({'name': volume_name, 'projected': {'defaultMode': 420, 'sources': [
        {'serviceAccountToken': {'path': 'token', 'expirationSeconds': 3607}},
        {'configMap': {'name': 'kube-root-ca.crt', 'items': [{'key': 'ca.crt', 'path': 'ca.crt'}]}},
        {'downwardAPI': {'items': [{'path': 'namespace', 'fieldRef': {'apiVersion': 'v1', 'fieldPath': 'metadata.namespace'}}]}}]}})
    mount = {'name': volume_name, 'mountPath': host.SA_PATH, 'readOnly': True}
    pod['spec']['containers'][0]['volumeMounts'].append(copy.deepcopy(mount))
    for init in pod['spec']['initContainers']:
        init['volumeMounts'].append(copy.deepcopy(mount))
    pod['spec']['topologySpreadConstraints'][0]['labelSelector']['matchExpressions'].append(
        {'key': 'pod-template-hash', 'operator': 'In', 'values': [label]})
    return deployment, pod, rs


class AdmissionTests(unittest.TestCase):
    def test_evidenced_combined_projection_uses_each_pod_label_and_volume_without_mutation(self):
        for label, volume in (('firsthash', 'kube-api-access-first'), ('secondhash', 'kube-api-access-second')):
            deployment, pod, rs = admitted_runtime(label, volume)
            before = copy.deepcopy((deployment, pod, rs))
            host.pod_guard(pod, rs, deployment, 'fixed-image-id')
            self.assertEqual((deployment, pod, rs), before)

    def test_init_image_env_commands_order_or_any_other_mount_change_refused(self):
        mutations = [
            lambda pod: pod['spec']['initContainers'][0].update(image='changed-image'),
            lambda pod: pod['spec']['initContainers'][0]['env'][0].update(value='changed-private-value'),
            lambda pod: pod['spec']['initContainers'][0].update(command=['changed-command']),
            lambda pod: pod['spec']['initContainers'].reverse(),
            lambda pod: pod['spec']['initContainers'][0]['volumeMounts'].reverse(),
            lambda pod: pod['spec']['initContainers'][0]['volumeMounts'][-1].update(readOnly=False),
            lambda pod: pod['spec']['initContainers'][0]['volumeMounts'][-1].update(name='kube-api-access-other'),
            lambda pod: pod['spec']['initContainers'][0]['volumeMounts'].append({'name': 'extra', 'mountPath': '/extra'}),
        ]
        for change in mutations:
            deployment, pod, rs = admitted_runtime()
            change(pod)
            with self.assertRaises(host.Refused) as error:
                host.pod_guard(pod, rs, deployment)
            self.assertEqual(error.exception.field, 'initContainers')
            self.assertTrue(error.exception.path.startswith('/spec/initContainers/'))
            self.assertNotIn('private-value', str(error.exception))

    def test_spread_wrong_key_operator_value_extra_expression_or_other_fields_refused(self):
        for key, value in (('key', 'wrong-key'), ('operator', 'NotIn'), ('values', ['oldhash'])):
            deployment, pod, rs = admitted_runtime()
            pod['spec']['topologySpreadConstraints'][0]['labelSelector']['matchExpressions'][-1][key] = value
            with self.assertRaisesRegex(host.Refused, 'TOPOLOGY_SELECTOR_ADMISSION_CHANGED'):
                host.pod_guard(pod, rs, deployment)
        for mutate in (
            lambda pod, rs: pod['spec']['topologySpreadConstraints'][0].update(maxSkew=2),
            lambda pod, rs: pod['spec']['topologySpreadConstraints'][0].update(topologyKey='changed'),
            lambda pod, rs: pod['spec']['topologySpreadConstraints'][0]['labelSelector']['matchExpressions'].append({'key': 'unrequested', 'operator': 'Exists'}),
            lambda pod, rs: rs['metadata']['labels'].update({'pod-template-hash': 'otherhash'}),
            lambda pod, rs: rs['spec']['selector']['matchLabels'].update({'pod-template-hash': 'otherhash'}),
        ):
            deployment, pod, rs = admitted_runtime()
            mutate(pod, rs)
            with self.assertRaises(host.Refused): host.pod_guard(pod, rs, deployment)
        deployment, pod, rs = admitted_runtime()
        for spec in (deployment['spec']['template']['spec'], rs['spec']['template']['spec'], pod['spec']):
            spec['topologySpreadConstraints'][0].pop('matchLabelKeys')
        with self.assertRaisesRegex(host.Refused, 'TOPOLOGY_HASH_MATCH_KEY_NOT_DECLARED'):
            host.pod_guard(pod, rs, deployment)

    def test_sa_sources_token_path_count_and_automount_false_refused(self):
        for modify in (
            lambda sources: sources.clear(),
            lambda sources: sources[0]['serviceAccountToken'].update(path='other'),
            lambda sources: sources.append(copy.deepcopy(sources[0])),
        ):
            deployment, pod, rs = admitted_runtime()
            modify(pod['spec']['volumes'][-1]['projected']['sources'])
            with self.assertRaisesRegex(host.Refused, 'SA_PROJECTED_TOKEN_REQUIRED'):
                host.pod_guard(pod, rs, deployment)
        deployment, pod, rs = admitted_runtime()
        for spec in (deployment['spec']['template']['spec'], rs['spec']['template']['spec'], pod['spec']):
            spec['automountServiceAccountToken'] = False
        with self.assertRaisesRegex(host.Refused, 'SA_PROJECTED_TOKEN_REQUIRED'):
            host.pod_guard(pod, rs, deployment)

    def test_preoccupied_sa_path_duplicate_selector_key_and_missing_rs_selector_refused(self):
        deployment, pod, rs = admitted_runtime()
        occupied = {'name': 'config', 'mountPath': host.SA_PATH, 'readOnly': True}
        for spec in (deployment['spec']['template']['spec'], rs['spec']['template']['spec']):
            spec['initContainers'][0]['volumeMounts'].append(copy.deepcopy(occupied))
        pod['spec']['initContainers'][0]['volumeMounts'].insert(-1, copy.deepcopy(occupied))
        with self.assertRaisesRegex(host.Refused, 'INIT_MOUNT_DECLARATION_CHANGED'):
            host.pod_guard(pod, rs, deployment)
        deployment, pod, rs = admitted_runtime()
        for spec in (deployment['spec']['template']['spec'], rs['spec']['template']['spec'], pod['spec']):
            spec['topologySpreadConstraints'][0]['labelSelector']['matchLabels']['pod-template-hash'] = 'currenthash'
        with self.assertRaisesRegex(host.Refused, 'TOPOLOGY_HASH_SELECTOR_ALREADY_DECLARED'):
            host.pod_guard(pod, rs, deployment)
        deployment, pod, rs = admitted_runtime()
        rs['spec']['selector']['matchLabels'].pop('pod-template-hash')
        with self.assertRaisesRegex(host.Refused, 'TOPOLOGY_POD_RS_HASH_MISMATCH'):
            host.pod_guard(pod, rs, deployment)

    def test_failure_report_contains_field_path_but_no_compared_value(self):
        error = host.Refused('INIT_CONTAINER_ADMISSION_CHANGED')
        error.field, error.path = 'initContainers', '/spec/initContainers/0/env/0/value'
        output = __import__('io').StringIO()
        original_mask = os.umask(0o077)
        try:
            with mock.patch.object(host.os, 'name', 'posix'), mock.patch.object(host.os, 'uname', create=True, return_value=type('Uname', (), {'nodename': 'datax-01'})()), \
                 mock.patch.object(host.os, 'geteuid', create=True, return_value=0), mock.patch.object(host, 'load_child', return_value='fixed-child'), \
                 mock.patch.object(host.Operator, 'run', side_effect=error), __import__('contextlib').redirect_stdout(output):
                self.assertEqual(host.main(['--activate-scheduler-budget-once']), 1)
        finally:
            os.umask(original_mask)
        value = json.loads(output.getvalue())
        self.assertEqual(value['error_field'], 'initContainers')
        self.assertEqual(value['error_path'], error.path)
        self.assertEqual(value['commands'], 0)
        self.assertNotIn('DO_NOT_PRINT', output.getvalue())


class SparseListTests(unittest.TestCase):
    def valid(self, number=0, phase='Running', deleting='<none>'):
        return 'gateway-p%02d 00000000-0000-4000-8000-%012x %s %s\n' % (number, number, phase, deleting)

    def test_exact_projection_parses_active_rows_and_retains_deletion_for_guard(self):
        raw = self.valid(0) + self.valid(1, 'Failed') + self.valid(2, 'Succeeded') + self.valid(3, 'Pending', '2026-11-12T00:00:00Z')
        parsed = host.parse_pod_list(raw)
        self.assertEqual([row['metadata']['name'] for row in parsed], ['gateway-p00', 'gateway-p03'])
        self.assertEqual(parsed[1]['metadata']['deletionTimestamp'], '2026-11-12T00:00:00Z')
        self.assertEqual(set(parsed[0]['metadata']), {'name', 'uid', 'deletionTimestamp'})

    def test_identity_phase_deleting_shape_duplicates_and_bounds_fail_closed(self):
        for raw in ('header NAME UID\n', self.valid().replace('gateway-p00', '../wrong'),
                    self.valid().replace('00000000-0000-4000-8000-000000000000', 'not-a-uid'),
                    self.valid().replace('Running', 'unrecognized'), self.valid().replace('<none>', 'untrusted'),
                    self.valid() + self.valid(), ''.join(self.valid(i) for i in range(513)), 'x' * (262144 + 1)):
            with self.assertRaises(host.Refused):
                host.parse_pod_list(raw)

    def test_json_list_command_is_no_longer_allowed(self):
        operation = host.Operator('fixed-child', readonly=True)
        with mock.patch.object(host.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(host.Refused, 'COMMAND_SCOPE'):
                operation.command(['get', 'pods', '-l', 'app=gateway', '-o', 'json'])
            launch.assert_not_called()


class RealTransportTests(unittest.TestCase):
    def command(self, operation, arguments, script, body=None):
        real_popen = subprocess.Popen
        def isolated_process(received, **kwargs):
            self.assertEqual(received[0], 'kubectl')
            # Exercise the actual process, spool limits and failure cleanup,
            # replacing only the executable. No Kubernetes/network is started.
            kwargs['env'] = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
            return real_popen([sys.executable, '-I', '-B', '-c', script], **kwargs)
        with mock.patch.object(host.subprocess, 'Popen', side_effect=isolated_process):
            return operation.command(arguments, body=body, timeout=20)

    def test_real_full_list_over_4m_fails_r2_and_r3_sparse_list_transports(self):
        old_path = HERE.parent / 'scheduler-budget-20261112-r2/activate-scheduler-budget-20261112-r2.py'
        spec = importlib.util.spec_from_file_location('budget_r2_transport_comparison', str(old_path))
        old = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old)
        source = "import json,sys; raw=json.dumps({'items':[{'metadata':{'name':'gateway-p%02d'%i},'spec':{'large':'x'*70000}} for i in range(70)]}); assert len(raw)>4194304; sys.stdout.write(raw); sys.stdout.flush()"
        with self.assertRaisesRegex(old.Refused, 'COMMAND_OUTPUT_BOUND'):
            self.command(old.Operator('fixed-child', readonly=True), ['get', 'pods', '-l', 'app=gateway', '-o', 'json'], source)
        source = "import sys; sys.stdout.write(''.join('gateway-p%02d 00000000-0000-4000-8000-%012x Running <none>\\n'%(i,i) for i in range(70))); sys.stdout.flush()"
        operation = host.Operator('fixed-child', readonly=True)
        value = self.command(operation, host.POD_LIST_ARGS, source)
        self.assertEqual(len(host.parse_pod_list(value)), 70)
        self.assertLess(len(value.encode()), 262144)
        self.assertEqual(operation.report['last_command_kind'], 'LIST_GATEWAY_POD_IDENTITIES')
        self.assertNotIn('command_output_failure', operation.report)

    def test_real_single_pod_limit_remains_4m_with_redacted_bound_metadata(self):
        operation = host.Operator('fixed-child', readonly=True)
        operation.known_pods.add('gateway-fixed')
        source = 'import sys; sys.stdout.buffer.write(b"x"*4194305); sys.stdout.flush()'
        with self.assertRaisesRegex(host.Refused, 'COMMAND_OUTPUT_BOUND'):
            self.command(operation, ['get', 'pod', 'gateway-fixed', '-o', 'json'], source)
        observed = operation.report['command_output_failure']
        self.assertEqual(set(observed), {'command_kind', 'stream', 'limit', 'observed_bytes'})
        self.assertEqual((observed['command_kind'], observed['stream'], observed['limit']), ('GET_GATEWAY_POD', 'stdout', 4194304))
        self.assertGreater(observed['observed_bytes'], observed['limit'])

    def test_real_child_limit_remains_64k_and_stderr_128k(self):
        operation = host.Operator('fixed-child', readonly=True)
        operation.known_pods.add('gateway-fixed')
        args = ['exec', '-i', 'gateway-fixed', '-c', 'gateway', '--', '/app/backend/.venv/bin/python', '-I', '-B', '-u', '-c', 'fixed-child']
        with self.assertRaisesRegex(host.Refused, 'COMMAND_OUTPUT_BOUND'):
            self.command(operation, args, 'import sys; sys.stdout.buffer.write(b"x"*65537); sys.stdout.flush()', b'{}')
        self.assertEqual(operation.report['command_output_failure']['limit'], 65536)
        self.assertEqual(operation.report['command_output_failure']['command_kind'], 'READONLY_GATEWAY_QUALIFICATION')
        with self.assertRaisesRegex(host.Refused, 'COMMAND_OUTPUT_BOUND'):
            self.command(operation, ['get', 'pod', 'gateway-fixed', '-o', 'json'],
                         'import sys; sys.stderr.buffer.write(b"x"*131073); sys.stderr.flush(); print("{}")')
        observed = operation.report['command_output_failure']
        self.assertEqual((observed['stream'], observed['limit']), ('stderr', 131072))


if __name__ == '__main__':
    unittest.main()
