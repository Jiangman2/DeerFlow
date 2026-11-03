-- Enterprise policy: route visual office documents through the in-cluster
-- adapter. The adapter still uses WeKnora's builtin parser for searchable
-- PDF/Word content, but adds multilingual vector-figure capture before it
-- returns the shared Markdown + images contract. Scanned/image-dominated input
-- continues to MinerU. Non-default parser choices remain untouched.

CREATE OR REPLACE FUNCTION public.deerflow_ensure_mineru_parser_rule()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    config jsonb;
    rules jsonb;
    required_file_types jsonb;
    missing_file_types jsonb;
BEGIN
    config := COALESCE(NEW.chunking_config, '{}'::jsonb);
    IF jsonb_typeof(config) <> 'object' THEN
        config := '{}'::jsonb;
    END IF;

    rules := config->'parser_engine_rules';
    IF rules IS NULL OR jsonb_typeof(rules) <> 'array' THEN
        rules := '[]'::jsonb;
    END IF;

    required_file_types := jsonb_build_array('pdf', 'doc', 'docx', 'ppt', 'pptx');

    -- Native WeKnora supplies builtin/markitdown defaults for these file types,
    -- which would bypass the visual-preservation adapter. Remove only those
    -- deployment defaults while retaining unrelated types on the same rule and
    -- preserving explicit third-party/custom engines.
    SELECT COALESCE(
        jsonb_agg(normalized.rule ORDER BY normalized.ordinality),
        '[]'::jsonb
    )
    INTO rules
    FROM (
        SELECT
            item.ordinality,
            lower(COALESCE(item.rule->>'engine', '')) AS engine,
            CASE
                WHEN lower(COALESCE(item.rule->>'engine', '')) IN ('builtin', 'markitdown')
                THEN jsonb_set(
                    item.rule,
                    '{file_types}',
                    filtered.file_types,
                    true
                )
                ELSE item.rule
            END AS rule
        FROM jsonb_array_elements(rules)
             WITH ORDINALITY AS item(rule, ordinality)
        CROSS JOIN LATERAL (
            SELECT COALESCE(
                jsonb_agg(file_type.value ORDER BY file_type.ordinality),
                '[]'::jsonb
            ) AS file_types
            FROM jsonb_array_elements_text(
                CASE
                    WHEN jsonb_typeof(item.rule->'file_types') = 'array'
                    THEN item.rule->'file_types'
                    ELSE '[]'::jsonb
                END
            ) WITH ORDINALITY AS file_type(value, ordinality)
            WHERE lower(COALESCE(item.rule->>'engine', '')) NOT IN ('builtin', 'markitdown')
               OR lower(file_type.value) NOT IN ('pdf', 'doc', 'docx', 'ppt', 'pptx')
        ) AS filtered
    ) AS normalized
    WHERE normalized.engine NOT IN ('builtin', 'markitdown')
       OR jsonb_array_length(normalized.rule->'file_types') > 0;

    SELECT COALESCE(
        jsonb_agg(required_type.value ORDER BY required_type.ordinality),
        '[]'::jsonb
    )
    INTO missing_file_types
    FROM jsonb_array_elements_text(required_file_types)
         WITH ORDINALITY AS required_type(value, ordinality)
    WHERE NOT EXISTS (
        SELECT 1
        FROM jsonb_array_elements(rules) AS item(rule)
        CROSS JOIN LATERAL jsonb_array_elements_text(
            CASE
                WHEN jsonb_typeof(item.rule->'file_types') = 'array'
                THEN item.rule->'file_types'
                ELSE '[]'::jsonb
            END
        ) AS existing_type(value)
        WHERE lower(existing_type.value) = lower(required_type.value)
    );

    IF jsonb_array_length(missing_file_types) > 0 THEN
        rules := rules || jsonb_build_array(
            jsonb_build_object(
                'file_types', missing_file_types,
                'engine', 'mineru'
            )
        );
        NEW.chunking_config := jsonb_set(
            config,
            '{parser_engine_rules}',
            rules,
            true
        );
    END IF;

    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS deerflow_mineru_parser_defaults
ON public.knowledge_bases;

CREATE TRIGGER deerflow_mineru_parser_defaults
BEFORE INSERT OR UPDATE OF chunking_config
ON public.knowledge_bases
FOR EACH ROW
EXECUTE FUNCTION public.deerflow_ensure_mineru_parser_rule();

-- Normalize existing rows once through the temporary UPDATE trigger.
UPDATE public.knowledge_bases
SET chunking_config = COALESCE(chunking_config, '{}'::jsonb);

-- Future user edits remain authoritative. Only newly created knowledge bases
-- receive the deployment default automatically.
DROP TRIGGER deerflow_mineru_parser_defaults
ON public.knowledge_bases;

CREATE TRIGGER deerflow_mineru_parser_defaults
BEFORE INSERT
ON public.knowledge_bases
FOR EACH ROW
EXECUTE FUNCTION public.deerflow_ensure_mineru_parser_rule();
