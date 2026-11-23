"""In-memory config transformation. Input/output stay in private host receipts."""
import copy
import hashlib
import json
import os
import sys
from pathlib import Path

import yaml

FIELDS = {
    'api_url': 'http://10.77.124.142:9080',
    'app_id': 'app-jeg6ud59',
    'conversation_prefix': 'SquareWorkflow_',
    'api_key': '$APPFORGE_DATA_QA_API_KEY',
}


def transform(source):
    before = yaml.safe_load(source)
    if not isinstance(before, dict) or not isinstance(before.get('tools'), list):
        raise ValueError('TOOLS_LIST_REQUIRED')
    matches = [i for i, tool in enumerate(before['tools']) if isinstance(tool, dict) and tool.get('name') == 'datalake_data_qa']
    if len(matches) != 1:
        raise ValueError('DATAQA_TOOL_NOT_UNIQUE')
    index = matches[0]
    tool = before['tools'][index]
    if tool.get('use') not in ('deerflow.community.appforge.tools:datalake_data_qa_tool', 'deerflow.community.appforge:datalake_data_qa_tool'):
        raise ValueError('UNEXPECTED_DATAQA_PROVIDER')
    # Reject shared YAML alias objects rather than silently changing another tool.
    after = copy.deepcopy(before)
    replacement = dict(after['tools'][index], **FIELDS)
    after['tools'][index] = replacement
    encoded = yaml.safe_dump(after, allow_unicode=True, sort_keys=False)
    decoded = yaml.safe_load(encoded)
    if decoded != after:
        raise ValueError('YAML_ROUNDTRIP_CHANGED_VALUES')
    restored = copy.deepcopy(decoded)
    restored['tools'][index] = copy.deepcopy(before['tools'][index])
    if restored != before:
        raise ValueError('UNRELATED_CONFIG_CHANGED')
    if {k: v for k, v in replacement.items() if k not in FIELDS} != {k: v for k, v in tool.items() if k not in FIELDS}:
        raise ValueError('UNRELATED_TOOL_FIELD_CHANGED')
    return encoded, decoded


def main():
    os.chdir('/app/backend')
    sys.path[:0] = ['/app/backend', '/app/backend/packages/harness']
    request = json.load(sys.stdin)
    actual = Path('/app/backend/config.yaml').read_bytes()
    if hashlib.sha256(actual).hexdigest() != request['before_sha256']:
        raise ValueError('LIVE_CONFIG_DIFFERS_FROM_CONFIGMAP')
    source, candidate = transform(request['source'])
    from deerflow.config.app_config import AppConfig
    config = AppConfig.model_validate(AppConfig.resolve_env_variables(candidate))
    tool = config.get_tool_config('datalake_data_qa')
    extra = tool.model_extra or {}
    if extra.get('app_id') != FIELDS['app_id'] or extra.get('api_url') != FIELDS['api_url']:
        raise ValueError('CANDIDATE_EFFECTIVE_CONFIG_MISMATCH')
    if extra.get('api_key') != os.environ.get('APPFORGE_DATA_QA_API_KEY'):
        raise ValueError('CANDIDATE_KEY_ENV_RESOLUTION_FAILED')
    print(json.dumps({'source': source, 'before_sha256': request['before_sha256'],
        'after_sha256': hashlib.sha256(source.encode()).hexdigest(), 'validated': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
