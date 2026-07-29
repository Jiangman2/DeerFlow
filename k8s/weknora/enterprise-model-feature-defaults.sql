\set ON_ERROR_STOP on

BEGIN;

DO $$
DECLARE
    missing_models text;
BEGIN
    SELECT string_agg(required.id, ', ' ORDER BY required.id)
    INTO missing_models
    FROM (
        VALUES
            ('__DEERFLOW_DEFAULT_CHAT_MODEL_ID__'),
            ('__DEERFLOW_DEFAULT_EMBEDDING_MODEL_ID__'),
            ('__DEERFLOW_DEFAULT_RERANK_MODEL_ID__'),
            ('deerflow-dashscope-vlm'),
            ('deerflow-dashscope-asr')
    ) AS required(id)
    LEFT JOIN models
      ON models.id = required.id
     AND models.deleted_at IS NULL
     AND models.status = 'active'
     AND length(COALESCE(models.parameters->>'api_key', '')) > 0
    WHERE models.id IS NULL;

    IF missing_models IS NOT NULL THEN
        RAISE EXCEPTION 'Required active model credentials are missing: %', missing_models;
    END IF;
END
$$;

UPDATE models
SET is_default = true,
    status = 'active',
    updated_at = CURRENT_TIMESTAMP
WHERE id IN (
    '__DEERFLOW_DEFAULT_EMBEDDING_MODEL_ID__',
    '__DEERFLOW_DEFAULT_RERANK_MODEL_ID__',
    'deerflow-dashscope-vlm',
    'deerflow-dashscope-asr'
)
  AND deleted_at IS NULL;

-- Hide superseded chat/embedding/rerank rows after the selected enterprise
-- replacements are ready. Rows selected as defaults in another deployment
-- profile are preserved.
UPDATE models
SET is_default = false,
    status = 'inactive',
    deleted_at = COALESCE(deleted_at, CURRENT_TIMESTAMP),
    updated_at = CURRENT_TIMESTAMP
WHERE id IN (
    'deerflow-qwen-plus',
    'deerflow-deepseek-v4-pro',
    'deerflow-dashscope-embedding',
    'deerflow-dashscope-rerank'
)
  AND id NOT IN (
    '__DEERFLOW_DEFAULT_CHAT_MODEL_ID__',
    '__DEERFLOW_DEFAULT_EMBEDDING_MODEL_ID__',
    '__DEERFLOW_DEFAULT_RERANK_MODEL_ID__'
)
  AND deleted_at IS NULL;

CREATE OR REPLACE FUNCTION deerflow_apply_enterprise_model_defaults()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.id IN (
        'deerflow-qwen-plus',
        'deerflow-deepseek-v4-pro',
        'deerflow-dashscope-embedding',
        'deerflow-dashscope-rerank'
    )
       AND NEW.id NOT IN (
           '__DEERFLOW_DEFAULT_CHAT_MODEL_ID__',
           '__DEERFLOW_DEFAULT_EMBEDDING_MODEL_ID__',
           '__DEERFLOW_DEFAULT_RERANK_MODEL_ID__'
       ) THEN
        NEW.is_default := false;
        NEW.status := 'inactive';
        NEW.deleted_at := COALESCE(NEW.deleted_at, CURRENT_TIMESTAMP);
    ELSIF NEW.type = 'KnowledgeQA' THEN
        NEW.is_default :=
            NEW.id = '__DEERFLOW_DEFAULT_CHAT_MODEL_ID__';
        IF NEW.is_default THEN
            NEW.status := 'active';
        END IF;
    ELSIF NEW.id IN (
        '__DEERFLOW_DEFAULT_EMBEDDING_MODEL_ID__',
        '__DEERFLOW_DEFAULT_RERANK_MODEL_ID__',
        'deerflow-dashscope-vlm',
        'deerflow-dashscope-asr'
    ) THEN
        NEW.is_default := true;
        NEW.status := 'active';
    END IF;

    RETURN NEW;
END
$function$;

DROP TRIGGER IF EXISTS deerflow_enterprise_model_defaults_before_write
ON models;

CREATE TRIGGER deerflow_enterprise_model_defaults_before_write
BEFORE INSERT OR UPDATE ON models
FOR EACH ROW
EXECUTE FUNCTION deerflow_apply_enterprise_model_defaults();

-- Normalize chat defaults only after replacing the guard function. This order
-- also makes a later environment switch (local qwen -> enterprise DeepSeek)
-- deterministic when an older version of the trigger already exists.
UPDATE models
SET is_default = CASE
        WHEN id = '__DEERFLOW_DEFAULT_CHAT_MODEL_ID__' THEN true
        ELSE false
    END,
    updated_at = CURRENT_TIMESTAMP
WHERE type = 'KnowledgeQA'
  AND deleted_at IS NULL;

UPDATE knowledge_bases
SET summary_model_id = '__DEERFLOW_DEFAULT_CHAT_MODEL_ID__',
    embedding_model_id = CASE
        WHEN NOT EXISTS (
            SELECT 1
            FROM knowledges
            WHERE knowledges.knowledge_base_id = knowledge_bases.id
              AND knowledges.deleted_at IS NULL
        )
        THEN '__DEERFLOW_DEFAULT_EMBEDDING_MODEL_ID__'
        ELSE knowledge_bases.embedding_model_id
    END,
    image_processing_config =
        COALESCE(image_processing_config, '{}'::jsonb)
        || jsonb_build_object(
            'enable_multimodal', true,
            'model_id', 'deerflow-dashscope-vlm'
        ),
    vlm_config =
        COALESCE(vlm_config, '{}'::jsonb)
        || jsonb_build_object(
            'enabled', true,
            'model_id', 'deerflow-dashscope-vlm'
        ),
    asr_config =
        COALESCE(asr_config, '{}'::jsonb)
        || jsonb_build_object(
            'enabled', true,
            'model_id', 'deerflow-dashscope-asr',
            'language', 'zh'
        ),
    wiki_config = CASE
        WHEN wiki_config IS NULL THEN NULL
        ELSE wiki_config || jsonb_build_object(
            'synthesis_model_id', '__DEERFLOW_DEFAULT_CHAT_MODEL_ID__'
        )
    END,
    updated_at = CURRENT_TIMESTAMP
WHERE COALESCE(is_temporary, false) = false;

UPDATE custom_agents
SET config =
        COALESCE(config, '{}'::jsonb)
        || jsonb_build_object(
            'model_id', '__DEERFLOW_DEFAULT_CHAT_MODEL_ID__',
            'rerank_model_id', '__DEERFLOW_DEFAULT_RERANK_MODEL_ID__',
            'query_understand_model_id', '__DEERFLOW_DEFAULT_CHAT_MODEL_ID__',
            'image_upload_enabled', true,
            'vlm_model_id', 'deerflow-dashscope-vlm',
            'attachment_image_understanding', true,
            'audio_upload_enabled', true,
            'asr_model_id', 'deerflow-dashscope-asr'
        ),
    updated_at = CURRENT_TIMESTAMP
WHERE id IN (
    'builtin-data-analyst',
    'builtin-quick-answer',
    'builtin-smart-reasoning',
    'builtin-wiki-researcher'
)
  AND deleted_at IS NULL;

CREATE OR REPLACE FUNCTION deerflow_apply_enterprise_knowledge_base_defaults()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF length(COALESCE(NEW.summary_model_id, '')) = 0 THEN
        NEW.summary_model_id := '__DEERFLOW_DEFAULT_CHAT_MODEL_ID__';
    END IF;

    IF length(COALESCE(NEW.embedding_model_id, '')) = 0 THEN
        NEW.embedding_model_id := '__DEERFLOW_DEFAULT_EMBEDDING_MODEL_ID__';
    END IF;

    NEW.image_processing_config :=
        COALESCE(NEW.image_processing_config, '{}'::jsonb)
        || jsonb_build_object(
            'enable_multimodal', true,
            'model_id', 'deerflow-dashscope-vlm'
        );

    NEW.vlm_config :=
        COALESCE(NEW.vlm_config, '{}'::jsonb)
        || jsonb_build_object(
            'enabled', true,
            'model_id', 'deerflow-dashscope-vlm'
        );

    NEW.asr_config :=
        COALESCE(NEW.asr_config, '{}'::jsonb)
        || jsonb_build_object(
            'enabled', true,
            'model_id', 'deerflow-dashscope-asr',
            'language', 'zh'
        );

    IF NEW.wiki_config IS NOT NULL
       AND length(COALESCE(NEW.wiki_config->>'synthesis_model_id', '')) = 0 THEN
        NEW.wiki_config :=
            NEW.wiki_config
            || jsonb_build_object(
                'synthesis_model_id',
                '__DEERFLOW_DEFAULT_CHAT_MODEL_ID__'
            );
    END IF;

    RETURN NEW;
END
$function$;

DROP TRIGGER IF EXISTS deerflow_enterprise_defaults_before_insert
ON knowledge_bases;

CREATE TRIGGER deerflow_enterprise_defaults_before_insert
BEFORE INSERT ON knowledge_bases
FOR EACH ROW
EXECUTE FUNCTION deerflow_apply_enterprise_knowledge_base_defaults();

CREATE OR REPLACE FUNCTION deerflow_apply_enterprise_custom_agent_defaults()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.id IN (
        'builtin-data-analyst',
        'builtin-quick-answer',
        'builtin-smart-reasoning',
        'builtin-wiki-researcher'
    ) THEN
        NEW.config :=
            COALESCE(NEW.config, '{}'::jsonb)
            || jsonb_build_object(
                'model_id', '__DEERFLOW_DEFAULT_CHAT_MODEL_ID__',
                'rerank_model_id', '__DEERFLOW_DEFAULT_RERANK_MODEL_ID__',
                'query_understand_model_id', '__DEERFLOW_DEFAULT_CHAT_MODEL_ID__',
                'image_upload_enabled', true,
                'vlm_model_id', 'deerflow-dashscope-vlm',
                'attachment_image_understanding', true,
                'audio_upload_enabled', true,
                'asr_model_id', 'deerflow-dashscope-asr'
            );
    ELSE
        NEW.config :=
            jsonb_build_object(
            'model_id', '__DEERFLOW_DEFAULT_CHAT_MODEL_ID__',
            'rerank_model_id', '__DEERFLOW_DEFAULT_RERANK_MODEL_ID__',
            'query_understand_model_id', '__DEERFLOW_DEFAULT_CHAT_MODEL_ID__',
            'image_upload_enabled', true,
            'vlm_model_id', 'deerflow-dashscope-vlm',
            'attachment_image_understanding', true,
            'audio_upload_enabled', true,
            'asr_model_id', 'deerflow-dashscope-asr'
            )
            || COALESCE(NEW.config, '{}'::jsonb);
    END IF;

    RETURN NEW;
END
$function$;

DROP TRIGGER IF EXISTS deerflow_enterprise_defaults_before_insert
ON custom_agents;

CREATE TRIGGER deerflow_enterprise_defaults_before_insert
BEFORE INSERT OR UPDATE ON custom_agents
FOR EACH ROW
EXECUTE FUNCTION deerflow_apply_enterprise_custom_agent_defaults();

COMMIT;

SELECT type,
       id,
       name,
       is_default,
       status,
       is_builtin
FROM models
WHERE deleted_at IS NULL
ORDER BY type, is_default DESC, name, id;

SELECT count(*) AS total_knowledge_bases,
       count(*) FILTER (
           WHERE summary_model_id = '__DEERFLOW_DEFAULT_CHAT_MODEL_ID__'
       ) AS default_summary_count,
       count(*) FILTER (
           WHERE embedding_model_id = '__DEERFLOW_DEFAULT_EMBEDDING_MODEL_ID__'
       ) AS default_embedding_count,
       count(*) FILTER (
           WHERE COALESCE((vlm_config->>'enabled')::boolean, false)
             AND vlm_config->>'model_id' = 'deerflow-dashscope-vlm'
       ) AS vlm_enabled_count,
       count(*) FILTER (
           WHERE COALESCE((asr_config->>'enabled')::boolean, false)
             AND asr_config->>'model_id' = 'deerflow-dashscope-asr'
       ) AS asr_enabled_count
FROM knowledge_bases
WHERE COALESCE(is_temporary, false) = false;

SELECT count(*) AS total_custom_agents,
       count(*) FILTER (
           WHERE config->>'model_id' = '__DEERFLOW_DEFAULT_CHAT_MODEL_ID__'
       ) AS default_chat_count,
       count(*) FILTER (
           WHERE config->>'rerank_model_id' = '__DEERFLOW_DEFAULT_RERANK_MODEL_ID__'
       ) AS default_rerank_count,
       count(*) FILTER (
           WHERE COALESCE((config->>'image_upload_enabled')::boolean, false)
             AND config->>'vlm_model_id' = 'deerflow-dashscope-vlm'
             AND COALESCE(
                 (config->>'attachment_image_understanding')::boolean,
                 false
             )
       ) AS image_enabled_count,
       count(*) FILTER (
           WHERE COALESCE((config->>'audio_upload_enabled')::boolean, false)
             AND config->>'asr_model_id' = 'deerflow-dashscope-asr'
       ) AS audio_enabled_count
FROM custom_agents
WHERE deleted_at IS NULL;

SELECT event_object_table,
       trigger_name,
       action_timing,
       event_manipulation
FROM information_schema.triggers
WHERE trigger_name IN (
    'deerflow_enterprise_model_defaults_before_write',
    'deerflow_enterprise_defaults_before_insert'
)
ORDER BY event_object_table;
