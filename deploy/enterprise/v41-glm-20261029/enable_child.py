"""Validate the optional model without invoking a provider or starting the app."""
import asyncio
import base64
import contextlib
import copy
import hashlib
import json
import logging
import os
from pathlib import Path
import sys

PROFILE = None  # Packaged from model.yaml.
BASE_SHA = 'dbde51e71421be4ee4544684faa77baf18a522ecd87db663b2bcb4280589c36b'
ADAPTER_SHA = 'a79d5b0e8a117223ca8b04e23e8bf6b36f737b32f43ea62c747c9193629b5930'
CA_SHA = '84c58afec9e48e1bd7b41d832ff6986b74a6565b4473f7cc77b868900b4d76af'
KEY = 'GLM_5_3_FLASH_API_KEY'
NAMES = ['deepseek-v4-flash', 'glm-5.3-flash', 'deepseek-v4.1-flash-1017']


class Refused(RuntimeError): pass


def require(ok, code):
    if not ok: raise Refused(code)


def sha(raw): return hashlib.sha256(raw).hexdigest()


def load(raw):
    import yaml
    class Unique(yaml.SafeLoader): pass
    def mapping(loader, node, deep=False):
        result = {}
        for key_node, value in node.value:
            key = loader.construct_object(key_node, deep=deep)
            require(key not in result, 'DUPLICATE_YAML_KEY')
            result[key] = loader.construct_object(value, deep=deep)
        return result
    Unique.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    return yaml.load(raw, Loader=Unique)


def append_profile(raw, profile):
    import yaml
    old = load(raw)
    require(isinstance(old, dict) and isinstance(old.get('models'), list), 'CONFIG_SHAPE')
    require([m.get('name') for m in old['models']] == NAMES[:2], 'BASE_MODELS_CHANGED')
    require(old['models'][1].get('api_key') == '$' + KEY, 'GLM_REFERENCE_CHANGED')
    root = yaml.compose(raw)
    seq = [value for key, value in root.value if key.value == 'models']
    require(len(seq) == 1 and isinstance(seq[0], yaml.SequenceNode) and not seq[0].flow_style,
            'MODELS_BLOCK_REQUIRED')
    seq = seq[0]
    text = raw.decode('utf-8')
    # Insert at the next top-level key's line (or EOF), preserving every old byte.
    pos = seq.end_mark.index
    require(pos == len(text) or (pos == 0 or text[pos - 1] == '\n'), 'MODEL_INSERTION_BOUNDARY')
    # Keep comments attached to the following section, rather than inserting
    # the new model between that section and its documentation.
    while pos and text[pos - 1] == '\n':
        start = text.rfind('\n', 0, pos - 1) + 1
        previous = text[start:pos].strip()
        if previous and not previous.startswith('#'): break
        pos = start
    block = yaml.safe_dump([profile], allow_unicode=True, sort_keys=False)
    indent = ' ' * seq.start_mark.column
    block = ''.join(indent + line + '\n' for line in block.splitlines())
    if pos and text[pos - 1] != '\n': block = '\n' + block
    candidate = (text[:pos] + block + text[pos:]).encode('utf-8')
    expected = copy.deepcopy(old); expected['models'].append(profile)
    require(load(candidate) == expected, 'CONFIG_SEMANTICS_CHANGED')
    require([m['name'] for m in expected['models']] == NAMES, 'DEFAULT_MODEL_CHANGED')
    return candidate


async def validate_model(raw, profile):
    from deerflow.config.app_config import AppConfig
    from deerflow.models.factory import create_chat_model
    from deerflow.models.cnooc_deepseek_v41 import CnoocDeepSeekV41Chat
    data = load(raw)
    require([m.get('name') for m in data['models']] == NAMES and data['models'][-1] == profile,
            'CANDIDATE_MODELS_CHANGED')
    require(data['models'][1]['api_key'] == data['models'][-1]['api_key'] == '$' + KEY, 'GLM_REFERENCE_CHANGED')
    key = os.environ.get(KEY, '')
    require(8 <= len(key) <= 4096 and key == key.strip(), 'GLM_KEY_ABSENT')
    config = AppConfig.model_validate(AppConfig.resolve_env_variables(data))
    require(config.models[0].name == NAMES[0], 'DEFAULT_MODEL_CHANGED')
    for thinking in (False, True):
        model = create_chat_model(NAMES[-1], thinking_enabled=thinking, app_config=config, attach_tracing=False)
        try:
            require(isinstance(model, CnoocDeepSeekV41Chat), 'ADAPTER_CLASS_CHANGED')
            require(model.openai_api_key.get_secret_value() == key, 'ADAPTER_KEY_CHANGED')
            require(model.extra_body.get('thinking') == {'type': 'enabled' if thinking else 'disabled'}, 'THINKING_CONFIG_CHANGED')
        finally:
            if model.http_client: model.http_client.close()
            if model.http_async_client: await model.http_async_client.aclose()
    return {'default_model': NAMES[0], 'models': NAMES, 'credential_source': KEY,
            'factory_thinking_modes_verified': True}


async def runtime_http():
    import httpx
    result = []
    async with httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False,
                                headers={'Host': 'cnoocclaw.cnooc.com.cn', 'Accept-Encoding': 'identity'}) as client:
        for path, status in (('/health', 200), ('/api/models', 401)):
            async with client.stream('GET', 'http://127.0.0.1:8001' + path) as response:
                data = bytearray()
                async for chunk in response.aiter_bytes(4096):
                    require(len(data) + len(chunk) <= 65536, 'HTTP_BODY_BOUND')
                    data.extend(chunk)
                require(response.status_code == status, 'HEALTH_OR_AUTH_REJECTED')
                if path == '/health':
                    require(json.loads(data) == {'status': 'healthy', 'service': 'deer-flow-gateway'}, 'HEALTH_BODY_CHANGED')
                result.append({'path': path, 'status': status})
    return result


async def check(args):
    require(args.get('mode') in ('prepare', 'runtime') and isinstance(PROFILE, str), 'INPUT_INVALID')
    raw = Path('/app/backend/config.yaml').read_bytes()
    require(len(raw) <= 1048576 and sha(raw) == args['expected_sha256'], 'MOUNTED_CONFIG_CHANGED')
    require(sha(Path('/app/backend/packages/harness/deerflow/models/cnooc_deepseek_v41.py').read_bytes()) == ADAPTER_SHA,
            'ADAPTER_SOURCE_CHANGED')
    ca = os.environ.get('SSL_CERT_FILE')
    require(ca and sha(Path(ca).read_bytes()) == CA_SHA, 'CA_CHANGED')
    profile = load(PROFILE)
    if args['mode'] == 'prepare':
        require(sha(raw) == BASE_SHA, 'BASE_CONFIG_CHANGED')
        candidate = append_profile(raw, profile)
    else: candidate = raw
    result = {'status': 'PASS', 'config_sha256': sha(candidate), 'provider_requests': 0,
              'database_access': False, 'files_modified': False, 'credential_values_exported': False,
              'application_started': False, 'adapter_sha256': ADAPTER_SHA, 'ca_sha256': CA_SHA}
    result.update(await validate_model(candidate, profile))
    if args['mode'] == 'prepare':
        # Private subprocess transport only. The host must never print this field.
        result['private_candidate_b64'] = base64.b64encode(candidate).decode('ascii')
    else: result['http_checks'] = await runtime_http()
    return result


def main():
    args = json.loads(sys.stdin.buffer.read(4096))
    logging.disable(logging.CRITICAL)
    sys.path[:0] = ['/app/backend', '/app/backend/packages/harness']
    with open(os.devnull, 'w') as sink:
        try:
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                result = asyncio.run(check(args))
        except Exception as exc:
            result = {'status': 'INCOMPLETE', 'error_class': type(exc).__name__,
                      'error_code': str(exc) if isinstance(exc, Refused) else 'CONFIG_PROBE_FAILED'}
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__': raise SystemExit(main())
