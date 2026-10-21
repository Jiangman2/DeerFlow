"""Pure Host/Frontend binding and terminal validation for the unified native runner.

No Kubernetes client or server entrypoint. All runtime adapters remain byte exact.
The caller owns network proof, admitted-spec checks, deadlines and UID-scoped cleanup.
"""
import base64
import copy
import hashlib
import json
import re
import zlib

ASSET_SHA = '__ASSET_SHA__'
ASSET_Z64 = '__ASSET_Z64__'
OWNER_LABEL = 'portal-ppt-network-owner'
NODE = dict(name='datax-02', uid='3148d4c1-6a97-42ed-a78c-950dcea5cbec', os='linux', architecture='arm64')
PREFLIGHT_SHA = '96d971e837140ec1cd019d1f6e2b833b17a67c6f8066628c7510fbe50de5bafc'
IMAGES = {
    'host': dict(manifest='sha256:540c96e05aa5c0c7709871494dd094047223eaaf6393b2117e9149c0d1467971', config='sha256:aabaa4f970580fb5edac54745bedd558ddedc0768bebaeb663130272e76f043d'),
    'frontend': dict(manifest='sha256:c6efeeb4b562d824ea70a6b6ff9797f4f480617fd32cc9aac17515914c96eedb', config='sha256:00ee27b123fc60b2ded37310a580b1df6f45d3c1586499fef7b73b8f5319ef49')}
FROZEN_HTTP_SHA = '639727217e0544ded5fa8f67bbe158067431b073d1d14a12966eabdc5e0cbe5e'


def require(ok, code):
    if not ok: raise ValueError(code)


def decode_json(raw, maximum=1024*1024):
    require(isinstance(raw, bytes) and len(raw) <= maximum, 'json_input_bound')
    def pairs(values):
        output = {}
        for key, value in values:
            require(key not in output, 'duplicate_json_key'); output[key] = value
        return output
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite_json')))


def load_assets():
    decoder = zlib.decompressobj()
    raw = decoder.decompress(base64.b64decode(ASSET_Z64, validate=True), 1024*1024+1)
    require(len(raw) <= 1024*1024 and decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail
            and hashlib.sha256(raw).hexdigest() == ASSET_SHA, 'stage_assets_identity')
    return decode_json(raw)


def host_runtime_inputs(value):
    require(isinstance(value, dict) and set(value) == {'state','image_config_digest','runtime_paths','codex_selector'}, 'host_runtime_keys')
    require(value['state'] == 'SAFE_IMAGE_PATHS_BOUND' and value['image_config_digest'] == IMAGES['host']['config'], 'host_runtime_identity')
    paths = value['runtime_paths']
    require(isinstance(paths, dict) and set(paths) <= {'PATH','HOME','XDG_CACHE_HOME','PLAYWRIGHT_BROWSERS_PATH'}, 'host_runtime_path_keys')
    require(all(isinstance(item,str) and len(item) <= 4096 and '\x00' not in item and '\n' not in item for item in paths.values()), 'host_runtime_path_shape')
    require(paths.get('PATH') and all(item.startswith('/') and '..' not in item.split('/') for item in paths['PATH'].split(':')), 'host_runtime_path_unbound')
    require(value['codex_selector'] in (None,'codex','/usr/local/bin/codex','/usr/bin/codex'), 'host_codex_selector_unsupported')
    return copy.deepcopy(value)


def render(owner, namespace, runtime_inputs):
    require(isinstance(owner,str) and re.fullmatch('[a-f0-9]{32}',owner), 'stage_owner')
    require(namespace == 'portal-ppt-' + owner, 'stage_namespace')
    assets = load_assets()
    binding = copy.deepcopy(assets['BINDING.template.json'])
    binding.update(state='BOUND_FOR_ISOLATED_RUNTIME', namespace=namespace)
    runtime = host_runtime_inputs(runtime_inputs)
    configs = copy.deepcopy(assets['configmaps.template.json']['items'])
    host = copy.deepcopy(assets['host-pod.template.json']); frontend = copy.deepcopy(assets['frontend-pod.template.json'])
    for obj in configs + [host,frontend]:
        require(obj['metadata']['namespace'] == '__UNBOUND_NAMESPACE__', 'template_namespace_drift')
        obj['metadata']['namespace'] = namespace
        obj['metadata']['labels'][OWNER_LABEL] = owner
    data = next(obj for obj in configs if obj['metadata']['name'] == 'native-probe-binding')['binaryData']
    data['BINDING.json'] = base64.b64encode((json.dumps(binding,sort_keys=True)+'\n').encode()).decode()
    data['HOST-RUNTIME-INPUTS.json'] = base64.b64encode((json.dumps(runtime,sort_keys=True)+'\n').encode()).decode()
    stages = []
    shared_binding = next(obj for obj in configs if obj['metadata']['name'] == 'native-probe-binding')
    for role, pod, seconds in (('host',host,90),('frontend',frontend,480)):
        stage_binding = copy.deepcopy(shared_binding)
        stage_binding['metadata']['name'] = 'native-' + role + '-binding'
        next(volume for volume in pod['spec']['volumes'] if volume['name'] == 'binding')['configMap']['name'] = stage_binding['metadata']['name']
        probe = next(obj for obj in configs if obj['metadata']['name'] == 'native-' + role + '-probe')
        images = {container['name']: dict(manifest_digest=IMAGES[role]['manifest'],config_digest=IMAGES[role]['config'],
                                         digest_reference=container['image']) for container in pod['spec'].get('initContainers',[]) + pod['spec']['containers']}
        stages.append(dict(name=role,objects=[probe,stage_binding,pod],pod_name=pod['metadata']['name'],images=images,
                           terminal_container=pod['spec']['containers'][0]['name'],deadline_seconds=seconds,pending_seconds=120))
    return dict(stages=stages, binding=binding,
                requirements=dict(network_proof='VERIFIED_OWNED_SUBJECT_PEER_PROOF_BEFORE_CANDIDATE_START',
                                  network_policy='UNCHANGED_NAMESPACE_DENY_ALL_NO_UNEXPECTED_ADDITIVE_POLICY',
                                  execution_order=['host','frontend'], pending_seconds=120,
                                  pod_deadline_seconds=dict(host=90,frontend=480)),
                source_assets_sha256=ASSET_SHA)


def validate_stage_objects(stage, owner, namespace):
    """Check rendered requests only; the controller separately checks admission."""
    require(stage['name'] in ('host','frontend'), 'unknown_stage_name')
    binding = next(obj for obj in stage['objects'] if obj['kind'] == 'ConfigMap' and obj['metadata']['name'] == 'native-' + stage['name'] + '-binding')
    inputs = decode_json(base64.b64decode(binding['binaryData']['HOST-RUNTIME-INPUTS.json'],validate=True),65536)
    expected = next(item for item in render(owner,namespace,inputs)['stages'] if item['name'] == stage['name'])
    require(stage == expected, 'stage_request_manifest_changed')
    return dict(status='EXACT_STAGE_REQUESTS', objects=len(stage['objects']), pod_name=stage['pod_name'])


def terminal_identity(role, value, observed, namespace):
    require(role in IMAGES, 'unknown_runtime_role')
    require(set(observed) == {'pod_uid','node_name','node_uid','image_id','image_reference','pod_phase','exit_code','restart_count','container_id'}, 'runtime_observed_keys')
    require(re.fullmatch(r'[a-f0-9-]{36}',observed['pod_uid']) and observed['node_name'] == NODE['name']
            and observed['node_uid'] == NODE['uid'] and observed['pod_phase'] == 'Succeeded'
            and observed['exit_code'] == 0 and observed['restart_count'] == 0, 'runtime_observed_identity_or_exit')
    require(isinstance(observed['container_id'],str) and re.fullmatch(r'containerd://[a-f0-9]{64}',observed['container_id']), 'runtime_container_id')
    image_id = observed['image_id'].rsplit('@',1)[-1].split('://')[-1]
    require(image_id in (IMAGES[role]['manifest'],IMAGES[role]['config']), 'runtime_image_id')
    repository = 'deerflow.local/deer-flow-aio-sandbox' if role == 'host' else 'deerflow.local/deer-flow-frontend'
    require(observed['image_reference'] == repository + '@' + IMAGES[role]['manifest'], 'runtime_image_reference')
    require(value['pod_uid'] == observed['pod_uid'] and value['requested_binding'] == dict(namespace=namespace,node=NODE,**IMAGES[role]), 'runtime_receipt_binding')
    require(value['native_arm64_qualified'] is False and value['ready_for_rollout'] is False and value['model_submitted'] is False, 'runtime_adapter_scope')


def validate_terminal(role, raw, observed, namespace):
    """Validate an adapter receipt plus controller-observed terminal identity.

    This does not prove CNI or admitted Pod spec equality; the unified controller
    must independently hold those gates before interpreting stage checks as native.
    """
    value = decode_json(raw,262144)
    terminal_identity(role,value,observed,namespace)
    assets = load_assets()
    if role == 'host':
        require(value['schema'] == 'portal-ppt-native-host-adapter.v1'
                and value['status'] == 'ISOLATED_CHECKS_PASS_EXTERNAL_NATIVE_IDENTITY_BINDING_REQUIRED'
                and value['child_exit_code'] == 0, 'host_adapter_incomplete')
        probe = value['probe']
        require(probe['status'] == value['status'] and probe['source_file_count'] == 13
                and probe['platform_machine_observed'] in ('arm64','aarch64') and probe['model_submitted'] is False,
                'host_probe_identity')
        expected = [dict(row,status='MATCH') for row in assets['SOURCE-EXPECTATIONS.json']['files']]
        require(probe['source_checks'] == expected, 'host_source_checks')
        checks = probe['checks']
        require(set(checks) == {'imports_and_driver','default_chromium_fixture','codex_version'}
                and all(check['status'] == 'PASS' for check in checks.values()), 'host_runtime_checks')
        browser = checks['default_chromium_fixture']
        require(browser['external_requests'] == 0 and browser['context_closed'] is True and browser['browser_closed'] is True
                and browser['cache_mapping_active_driver_verified'] is True
                and browser['default_launch'] == dict(headless=True,timeout=5000,executable_path=None), 'host_browser_scope')
        require(checks['codex_version']['arguments'] == ['--version'] and checks['codex_version']['model_submitted'] is False, 'host_codex_scope')
        check_count = 3
    else:
        require(value['schema'] == 'portal-ppt-native-frontend-adapter.v1'
                and value['status'] == 'HTTP_14_PASS_EXTERNAL_NATIVE_IDENTITY_BINDING_REQUIRED'
                and value['frozen_harness_sha256'] == FROZEN_HTTP_SHA, 'frontend_adapter_incomplete')
        harness = value['frozen_harness_receipt']
        require(harness['schema'] == 'portal-ppt-built-frontend-http.v3' and harness['status'] == 'PASS'
                and harness['checks'] == assets['FRONTEND-CHECK-NAMES'] and len(harness['checks']) == 14
                and harness['production_accessed'] is False and harness['model_requests'] == 0, 'frontend_http_checks')
        require(value['layout']['exit_code'] == 0 and value['http']['exit_code'] == 0
                and [phase['phase'] for phase in value['phases']] == ['layout','http']
                and all(phase['exit_code'] == 0 and phase['signal'] is None for phase in value['phases']), 'frontend_child_exits')
        check_count = 14
    return dict(role=role, status='STAGE_CHECKS_AND_TERMINAL_IDENTITY_PASS_EXTERNAL_NETWORK_AND_ADMISSION_REQUIRED',
                checks=check_count, observed=observed, receipt_sha256=hashlib.sha256(raw).hexdigest(),
                native_arm64_qualified=False, ready_for_rollout=False, model_submitted=False)
