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
            ('deerflow-cnooc-deepseek-v4-flash'),
            ('deerflow-dashscope-embedding'),
            ('deerflow-dashscope-rerank'),
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
SET parameters = jsonb_set(
        jsonb_set(
            parameters,
            '{base_url}',
            to_jsonb('http://gateway:8001/api/weknora/bailian/v1'::text),
            true
        ),
        '{provider}',
        to_jsonb('generic'::text),
        true
    ),
    updated_at = CURRENT_TIMESTAMP
WHERE id = 'deerflow-dashscope-embedding'
  AND deleted_at IS NULL
  AND status = 'active';

UPDATE models
SET is_default = CASE
        WHEN id = 'deerflow-cnooc-deepseek-v4-flash' THEN true
        ELSE false
    END,
    updated_at = CURRENT_TIMESTAMP
WHERE type = 'KnowledgeQA'
  AND deleted_at IS NULL;

UPDATE models
SET is_default = true,
    updated_at = CURRENT_TIMESTAMP
WHERE id IN (
    'deerflow-dashscope-embedding',
    'deerflow-dashscope-rerank',
    'deerflow-dashscope-vlm',
    'deerflow-dashscope-asr'
)
  AND deleted_at IS NULL;

CREATE OR REPLACE FUNCTION deerflow_apply_enterprise_model_defaults()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.type = 'KnowledgeQA' THEN
        NEW.is_default :=
            NEW.id = 'deerflow-cnooc-deepseek-v4-flash';
    ELSIF NEW.id IN (
        'deerflow-dashscope-embedding',
        'deerflow-dashscope-rerank',
        'deerflow-dashscope-vlm',
        'deerflow-dashscope-asr'
    ) THEN
        NEW.is_default := true;
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

UPDATE knowledge_bases
SET summary_model_id = 'deerflow-cnooc-deepseek-v4-flash',
    embedding_model_id = 'deerflow-dashscope-embedding',
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
            'synthesis_model_id', 'deerflow-cnooc-deepseek-v4-flash'
        )
    END,
    updated_at = CURRENT_TIMESTAMP
WHERE COALESCE(is_temporary, false) = false;

UPDATE custom_agents
SET config =
        COALESCE(config, '{}'::jsonb)
        || jsonb_build_object(
            'model_id', 'deerflow-cnooc-deepseek-v4-flash',
            'rerank_model_id', 'deerflow-dashscope-rerank',
            'query_understand_model_id', 'deerflow-cnooc-deepseek-v4-flash',
            'image_upload_enabled', true,
            'vlm_model_id', 'deerflow-dashscope-vlm',
            'attachment_image_understanding', true,
            'audio_upload_enabled', true,
            'asr_model_id', 'deerflow-dashscope-asr'
        ),
    updated_at = CURRENT_TIMESTAMP
WHERE deleted_at IS NULL;

CREATE OR REPLACE FUNCTION deerflow_apply_enterprise_knowledge_base_defaults()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF length(COALESCE(NEW.summary_model_id, '')) = 0 THEN
        NEW.summary_model_id := 'deerflow-cnooc-deepseek-v4-flash';
    END IF;

    IF length(COALESCE(NEW.embedding_model_id, '')) = 0 THEN
        NEW.embedding_model_id := 'deerflow-dashscope-embedding';
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
                'deerflow-cnooc-deepseek-v4-flash'
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
    NEW.config :=
        COALESCE(NEW.config, '{}'::jsonb)
        || jsonb_build_object(
            'model_id', 'deerflow-cnooc-deepseek-v4-flash',
            'rerank_model_id', 'deerflow-dashscope-rerank',
            'query_understand_model_id', 'deerflow-cnooc-deepseek-v4-flash',
            'image_upload_enabled', true,
            'vlm_model_id', 'deerflow-dashscope-vlm',
            'attachment_image_understanding', true,
            'audio_upload_enabled', true,
            'asr_model_id', 'deerflow-dashscope-asr'
        );

    RETURN NEW;
END
$function$;

DROP TRIGGER IF EXISTS deerflow_enterprise_defaults_before_insert
ON custom_agents;

CREATE TRIGGER deerflow_enterprise_defaults_before_insert
BEFORE INSERT ON custom_agents
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
           WHERE summary_model_id = 'deerflow-cnooc-deepseek-v4-flash'
       ) AS deepseek_summary_count,
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
           WHERE config->>'model_id' = 'deerflow-cnooc-deepseek-v4-flash'
       ) AS deepseek_chat_count,
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
