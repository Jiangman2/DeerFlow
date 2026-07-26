\set ON_ERROR_STOP on

BEGIN;

INSERT INTO models (
    id,
    tenant_id,
    name,
    display_name,
    type,
    source,
    description,
    parameters,
    is_default,
    status,
    is_builtin,
    managed_by,
    updated_at,
    deleted_at
)
VALUES
(
    :'summary_model_id',
    :'builtin_tenant_id'::integer,
    :'llm_model_name',
    '',
    'KnowledgeQA',
    'remote',
    'DeerFlow WeKnora chat model',
    jsonb_build_object(
        'base_url', :'llm_base_url',
        'api_key', :'llm_api_key',
        'provider', :'llm_provider'
    ),
    false,
    'active',
    true,
    'deerflow-sso',
    CURRENT_TIMESTAMP,
    NULL
),
(
    'deerflow-deepseek-v4-pro',
    :'builtin_tenant_id'::integer,
    'deepseek-v4-pro',
    '',
    'KnowledgeQA',
    'remote',
    'DeerFlow WeKnora DeepSeek chat model',
    jsonb_build_object(
        'base_url', 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        'api_key', :'llm_api_key',
        'provider', 'aliyun'
    ),
    false,
    'active',
    true,
    'deerflow-sso',
    CURRENT_TIMESTAMP,
    NULL
),
(
    'deerflow-cnooc-deepseek-v4-flash',
    :'builtin_tenant_id'::integer,
    'DeepSeek-V4-Flash',
    '',
    'KnowledgeQA',
    'remote',
    'DeerFlow WeKnora CNOOC DeepSeek chat model',
    jsonb_build_object(
        'base_url', 'http://model.ai.cnooc/member1/deepseek-v4-flash-291b-1m/v1',
        'api_key', :'cnooc_deepseek_api_key',
        'provider', 'generic'
    ),
    true,
    'active',
    true,
    'deerflow-sso',
    CURRENT_TIMESTAMP,
    NULL
),
(
    :'embedding_model_id',
    :'builtin_tenant_id'::integer,
    :'embedding_model_name',
    '',
    'Embedding',
    'remote',
    'DeerFlow WeKnora embedding model',
    jsonb_build_object(
        'base_url', :'embedding_base_url',
        'api_key', :'embedding_api_key',
        'provider', :'embedding_provider',
        'embedding_parameters', jsonb_build_object(
            'dimension', :'embedding_dimension'::integer,
            'truncate_prompt_tokens', :'embedding_truncate_prompt_tokens'::integer
        )
    ),
    true,
    'active',
    true,
    'deerflow-sso',
    CURRENT_TIMESTAMP,
    NULL
),
(
    'deerflow-dashscope-rerank',
    :'builtin_tenant_id'::integer,
    'qwen3-rerank',
    '',
    'Rerank',
    'remote',
    'DeerFlow WeKnora rerank model',
    jsonb_build_object(
        'base_url', 'https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank',
        'api_key', :'llm_api_key',
        'provider', 'aliyun'
    ),
    true,
    'active',
    true,
    'deerflow-sso',
    CURRENT_TIMESTAMP,
    NULL
),
(
    'deerflow-dashscope-vlm',
    :'builtin_tenant_id'::integer,
    'qwen3-vl-plus',
    '',
    'VLLM',
    'remote',
    'DeerFlow WeKnora vision model',
    jsonb_build_object(
        'base_url', 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        'api_key', :'llm_api_key',
        'provider', 'aliyun'
    ),
    true,
    'active',
    true,
    'deerflow-sso',
    CURRENT_TIMESTAMP,
    NULL
),
(
    'deerflow-dashscope-asr',
    :'builtin_tenant_id'::integer,
    'qwen3-asr-flash',
    '',
    'ASR',
    'remote',
    'DeerFlow WeKnora speech recognition model',
    jsonb_build_object(
        'base_url', 'http://gateway:8001/api/weknora/bailian/v1',
        'api_key', :'llm_api_key',
        'provider', 'generic'
    ),
    true,
    'active',
    true,
    'deerflow-sso',
    CURRENT_TIMESTAMP,
    NULL
)
ON CONFLICT (id) DO UPDATE SET
    tenant_id = EXCLUDED.tenant_id,
    name = EXCLUDED.name,
    type = EXCLUDED.type,
    source = EXCLUDED.source,
    description = EXCLUDED.description,
    parameters = EXCLUDED.parameters,
    is_default = EXCLUDED.is_default,
    status = 'active',
    is_builtin = true,
    managed_by = EXCLUDED.managed_by,
    updated_at = CURRENT_TIMESTAMP,
    deleted_at = NULL;

COMMIT;
