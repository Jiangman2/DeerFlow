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
)
ON CONFLICT (id) DO UPDATE SET
    tenant_id = EXCLUDED.tenant_id,
    name = EXCLUDED.name,
    type = EXCLUDED.type,
    source = EXCLUDED.source,
    description = EXCLUDED.description,
    parameters = EXCLUDED.parameters,
    is_default = true,
    status = 'active',
    is_builtin = true,
    managed_by = EXCLUDED.managed_by,
    updated_at = CURRENT_TIMESTAMP,
    deleted_at = NULL;

COMMIT;
