-- Enterprise policy: route PDF/PPT/PPTX through the in-cluster adapter.
-- The adapter keeps searchable PDFs on WeKnora's builtin parser and sends only
-- scanned/hybrid PDFs plus presentations to MinerU. Preserve every explicitly
-- configured file-type rule instead of replacing the user's parser choice.

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

    required_file_types := jsonb_build_array('pdf', 'ppt', 'pptx');

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

-- Normalize existing rows idempotently through the same trigger.
UPDATE public.knowledge_bases
SET chunking_config = COALESCE(chunking_config, '{}'::jsonb);
