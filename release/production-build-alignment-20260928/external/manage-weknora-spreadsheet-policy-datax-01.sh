#!/usr/bin/env bash
set -Eeuo pipefail

MODE="${1:-}"
EVIDENCE_DIR="${2:-}"
NS=deer-flow
FUNCTION_NAME=weknora_document_stability_r2_spreadsheet_default
TRIGGER_NAME=weknora_document_stability_r2_spreadsheet_default

die() { echo "weknora_spreadsheet_policy=FAIL detail=$*" >&2; exit 1; }
test "$(id -u)" = 0 || die must_run_as_root
test "$(hostname -s)" = datax-01 || die must_run_on_datax-01
case "${MODE}" in --inspect|--apply|--restore) ;; *) die "usage=$0 --inspect|--apply|--restore [evidence_dir]" ;; esac
for command in kubectl python3 sha256sum realpath mv chmod; do
  command -v "${command}" >/dev/null || die "${command}_not_found"
done

PG_POD="$(kubectl -n "${NS}" get pods -l app=weknora-postgres --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')"
test -n "${PG_POD}" || die running_weknora_postgres_not_found

psql_stdin() {
  kubectl -n "${NS}" exec -i "${PG_POD}" -c postgres -- sh -ec \
    'exec psql -X -qAt -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
}

needs_normalization_predicate() {
  cat <<'SQL'
kb.deleted_at IS NULL
AND (
  EXISTS (
    SELECT 1
    FROM jsonb_array_elements(
      CASE
        WHEN jsonb_typeof(COALESCE(kb.chunking_config, '{}'::jsonb)->'parser_engine_rules') = 'array'
        THEN COALESCE(kb.chunking_config, '{}'::jsonb)->'parser_engine_rules'
        ELSE '[]'::jsonb
      END
    ) AS rule(value)
    WHERE lower(COALESCE(rule.value->>'engine', '')) = 'anydoc'
      AND EXISTS (
        SELECT 1
        FROM jsonb_array_elements_text(
          CASE WHEN jsonb_typeof(rule.value->'file_types') = 'array' THEN rule.value->'file_types' ELSE '[]'::jsonb END
        ) AS ft(value)
        WHERE lower(trim(leading '.' FROM ft.value)) IN ('xls', 'xlsx')
      )
  )
  OR EXISTS (
    SELECT required_type
    FROM unnest(ARRAY['xls','xlsx']) AS required(required_type)
    WHERE NOT EXISTS (
      SELECT 1
      FROM jsonb_array_elements(
        CASE
          WHEN jsonb_typeof(COALESCE(kb.chunking_config, '{}'::jsonb)->'parser_engine_rules') = 'array'
          THEN COALESCE(kb.chunking_config, '{}'::jsonb)->'parser_engine_rules'
          ELSE '[]'::jsonb
        END
      ) AS rule(value)
      CROSS JOIN LATERAL jsonb_array_elements_text(
        CASE WHEN jsonb_typeof(rule.value->'file_types') = 'array' THEN rule.value->'file_types' ELSE '[]'::jsonb END
      ) AS ft(value)
      WHERE lower(trim(leading '.' FROM ft.value)) = required_type
    )
  )
)
SQL
}

inspect_policy() {
  local predicate
  predicate="$(needs_normalization_predicate)"
  psql_stdin <<SQL
SET default_transaction_read_only = on;
SELECT 'weknora_knowledge_bases_needing_spreadsheet_normalization=' || count(*)
FROM knowledge_bases AS kb
WHERE ${predicate};
SELECT 'weknora_knowledge_bases_with_anydoc_spreadsheet=' || count(DISTINCT kb.id)
FROM knowledge_bases AS kb
CROSS JOIN LATERAL jsonb_array_elements(
  CASE
    WHEN jsonb_typeof(COALESCE(kb.chunking_config, '{}'::jsonb)->'parser_engine_rules') = 'array'
    THEN COALESCE(kb.chunking_config, '{}'::jsonb)->'parser_engine_rules'
    ELSE '[]'::jsonb
  END
) AS rule(value)
WHERE kb.deleted_at IS NULL
  AND lower(COALESCE(rule.value->>'engine', '')) = 'anydoc'
  AND EXISTS (
    SELECT 1 FROM jsonb_array_elements_text(
      CASE WHEN jsonb_typeof(rule.value->'file_types') = 'array' THEN rule.value->'file_types' ELSE '[]'::jsonb END
    ) AS ft(value)
    WHERE lower(trim(leading '.' FROM ft.value)) IN ('xls','xlsx')
  );
SELECT 'weknora_spreadsheet_default_function=' ||
       CASE WHEN to_regprocedure('public.${FUNCTION_NAME}()') IS NULL THEN 'absent' ELSE 'present' END;
SELECT 'weknora_spreadsheet_default_trigger=' ||
       CASE WHEN EXISTS (
         SELECT 1 FROM pg_trigger
         WHERE tgrelid = 'public.knowledge_bases'::regclass
           AND tgname = '${TRIGGER_NAME}' AND NOT tgisinternal
       ) THEN 'present' ELSE 'absent' END;
SELECT 'weknora_spreadsheet_default_trigger_scope=' || COALESCE(
  (SELECT CASE
     WHEN pg_get_triggerdef(oid) LIKE '% BEFORE INSERT ON %' AND pg_get_triggerdef(oid) NOT LIKE '% OR UPDATE %'
     THEN 'future_insert_only' ELSE 'unexpected' END
   FROM pg_trigger
   WHERE tgrelid = 'public.knowledge_bases'::regclass
     AND tgname = '${TRIGGER_NAME}' AND NOT tgisinternal),
  'absent'
);
SQL
  echo weknora_spreadsheet_policy_inspect=PASS
}

validate_evidence_dir() {
  test -n "${EVIDENCE_DIR}" || die evidence_dir_required
  EVIDENCE_DIR="$(realpath -e "${EVIDENCE_DIR}")"
  case "${EVIDENCE_DIR}" in
    /opt/deerflow-hotfix-evidence/weknora-document-stability-20260920-r2/*) ;;
    *) die evidence_dir_outside_release_root ;;
  esac
  test -s "${EVIDENCE_DIR}/state.env" || die state_env_missing
}

write_rollback_manifest() {
  (
    cd "${EVIDENCE_DIR}"
    files=( ./*.before.json state.env )
    sha256sum "${files[@]}" >ROLLBACK.SHA256
    sha256sum -c ROLLBACK.SHA256
  )
}

create_normalizer_sql() {
  cat <<SQL
CREATE OR REPLACE FUNCTION public.${FUNCTION_NAME}()
RETURNS trigger
LANGUAGE plpgsql
AS \$policy\$
DECLARE
  cfg jsonb;
  rules jsonb;
  normalized_rules jsonb;
  missing_types jsonb;
BEGIN
  cfg := COALESCE(NEW.chunking_config, '{}'::jsonb);
  IF jsonb_typeof(cfg) <> 'object' THEN
    cfg := '{}'::jsonb;
  END IF;
  rules := CASE
    WHEN jsonb_typeof(cfg->'parser_engine_rules') = 'array' THEN cfg->'parser_engine_rules'
    ELSE '[]'::jsonb
  END;

  SELECT COALESCE(jsonb_agg(item.rule ORDER BY item.ordinality), '[]'::jsonb)
  INTO normalized_rules
  FROM (
    SELECT source.ordinality,
           CASE
             WHEN lower(COALESCE(source.rule->>'engine','')) = 'anydoc'
             THEN jsonb_set(source.rule, '{file_types}', filtered.file_types, true)
             ELSE source.rule
           END AS rule
    FROM jsonb_array_elements(rules) WITH ORDINALITY AS source(rule, ordinality)
    CROSS JOIN LATERAL (
      SELECT COALESCE(jsonb_agg(ft.value ORDER BY ft.ordinality), '[]'::jsonb) AS file_types
      FROM jsonb_array_elements_text(
        CASE WHEN jsonb_typeof(source.rule->'file_types') = 'array' THEN source.rule->'file_types' ELSE '[]'::jsonb END
      ) WITH ORDINALITY AS ft(value, ordinality)
      WHERE lower(COALESCE(source.rule->>'engine','')) <> 'anydoc'
         OR lower(trim(leading '.' FROM ft.value)) NOT IN ('xls','xlsx')
    ) AS filtered
  ) AS item
  WHERE lower(COALESCE(item.rule->>'engine','')) <> 'anydoc'
     OR jsonb_array_length(item.rule->'file_types') > 0;

  SELECT COALESCE(jsonb_agg(to_jsonb(required_type) ORDER BY ordinality), '[]'::jsonb)
  INTO missing_types
  FROM unnest(ARRAY['xls','xlsx']) WITH ORDINALITY AS required(required_type, ordinality)
  WHERE NOT EXISTS (
    SELECT 1
    FROM jsonb_array_elements(normalized_rules) AS rule(value)
    CROSS JOIN LATERAL jsonb_array_elements_text(
      CASE WHEN jsonb_typeof(rule.value->'file_types') = 'array' THEN rule.value->'file_types' ELSE '[]'::jsonb END
    ) AS ft(value)
    WHERE lower(trim(leading '.' FROM ft.value)) = required_type
  );

  IF jsonb_array_length(missing_types) > 0 THEN
    normalized_rules := normalized_rules || jsonb_build_array(jsonb_build_object(
      'file_types', missing_types,
      'engine', 'builtin',
      'xlsx_first_row_as_header', true
    ));
  END IF;
  NEW.chunking_config := jsonb_set(cfg, '{parser_engine_rules}', normalized_rules, true);
  RETURN NEW;
END
\$policy\$;
SQL
}

apply_policy() {
  validate_evidence_dir
  test "${WEKNORA_SPREADSHEET_POLICY_ACK:-}" = YES || die set_WEKNORA_SPREADSHEET_POLICY_ACK=YES
  local predicate backup count apply_ts state_file state_partial function_state trigger_state
  predicate="$(needs_normalization_predicate)"
  function_state="$(psql_stdin <<SQL
SELECT CASE WHEN to_regprocedure('public.${FUNCTION_NAME}()') IS NULL THEN 'absent' ELSE 'present' END;
SQL
)"
  trigger_state="$(psql_stdin <<SQL
SELECT CASE WHEN EXISTS (
  SELECT 1 FROM pg_trigger WHERE tgrelid='public.knowledge_bases'::regclass
  AND tgname='${TRIGGER_NAME}' AND NOT tgisinternal
) THEN 'present' ELSE 'absent' END;
SQL
)"
  test "${function_state}" = absent || die release_function_already_exists
  test "${trigger_state}" = absent || die release_trigger_already_exists

  backup="${EVIDENCE_DIR}/weknora-spreadsheet-policy.before.json"
  psql_stdin >"${backup}" <<SQL
SET default_transaction_read_only = on;
SELECT COALESCE(jsonb_agg(to_jsonb(snapshot) ORDER BY id), '[]'::jsonb)::text
FROM (
  SELECT id, chunking_config, updated_at
  FROM knowledge_bases AS kb
  WHERE ${predicate}
  ORDER BY id
) AS snapshot;
SQL
  test -s "${backup}" || die policy_backup_missing
  count="$(python3 - "${backup}" <<'PY'
import json,sys
rows=json.load(open(sys.argv[1],encoding='utf-8'))
if not isinstance(rows,list): raise SystemExit('backup is not a JSON list')
ids=set()
for row in rows:
    value=str(row['id'])
    if not value or value in ids: raise SystemExit('invalid or duplicate knowledge base id')
    ids.add(value)
print(len(rows))
PY
)"
  apply_ts="$(psql_stdin <<'SQL'
SELECT clock_timestamp()::text;
SQL
)"
  test -n "${apply_ts}" || die policy_timestamp_missing

  state_file="${EVIDENCE_DIR}/weknora-spreadsheet-policy.state.env"
  state_partial="${state_file}.partial.$$"
  {
    echo WEKNORA_SPREADSHEET_POLICY_APPLIED=pending
    printf 'WEKNORA_SPREADSHEET_POLICY_AT=%q\n' "${apply_ts}"
    echo "WEKNORA_SPREADSHEET_POLICY_COUNT=${count}"
  } >"${state_partial}"
  chmod 0600 "${backup}" "${state_partial}"
  mv "${state_partial}" "${state_file}"
  write_rollback_manifest

  {
    echo BEGIN\;
    create_normalizer_sql
    cat <<SQL
CREATE TRIGGER ${TRIGGER_NAME}
BEFORE INSERT OR UPDATE OF chunking_config ON public.knowledge_bases
FOR EACH ROW EXECUTE FUNCTION public.${FUNCTION_NAME}();
DO \$policy\$
DECLARE affected bigint;
BEGIN
  UPDATE knowledge_bases AS kb
  SET chunking_config = kb.chunking_config,
      updated_at = '${apply_ts}'::timestamptz
  WHERE ${predicate};
  GET DIAGNOSTICS affected = ROW_COUNT;
  IF affected <> ${count} THEN
    RAISE EXCEPTION 'spreadsheet policy mismatch expected=${count} actual=%', affected;
  END IF;
END
\$policy\$;
DROP TRIGGER ${TRIGGER_NAME} ON public.knowledge_bases;
CREATE TRIGGER ${TRIGGER_NAME}
BEFORE INSERT ON public.knowledge_bases
FOR EACH ROW EXECUTE FUNCTION public.${FUNCTION_NAME}();
COMMIT;
SQL
  } | psql_stdin

  state_partial="${state_file}.partial.$$"
  {
    echo WEKNORA_SPREADSHEET_POLICY_APPLIED=yes
    printf 'WEKNORA_SPREADSHEET_POLICY_AT=%q\n' "${apply_ts}"
    echo "WEKNORA_SPREADSHEET_POLICY_COUNT=${count}"
  } >"${state_partial}"
  chmod 0600 "${state_partial}"
  mv "${state_partial}" "${state_file}"
  echo "weknora_spreadsheet_builtin_default=PASS normalized_knowledge_bases=${count}"
  echo weknora_spreadsheet_anydoc_xls_xlsx=REMOVED_FROM_EXISTING_DEFAULTS
  echo weknora_spreadsheet_future_default=BUILTIN_ON_INSERT
  echo weknora_spreadsheet_post_creation_user_edits=AUTHORITATIVE
}

restore_policy() {
  validate_evidence_dir
  local state_file backup count payload matched trigger_state state_partial
  state_file="${EVIDENCE_DIR}/weknora-spreadsheet-policy.state.env"
  if test ! -s "${state_file}"; then
    echo weknora_spreadsheet_policy_rollback=NOT_APPLIED
    return 0
  fi
  . "${state_file}"
  case "${WEKNORA_SPREADSHEET_POLICY_APPLIED:-no}" in
    yes|pending) ;;
    restored|no) echo weknora_spreadsheet_policy_rollback=NOT_APPLIED; return 0 ;;
    *) die invalid_policy_state ;;
  esac
  (cd "${EVIDENCE_DIR}" && sha256sum -c ROLLBACK.SHA256)
  backup="${EVIDENCE_DIR}/weknora-spreadsheet-policy.before.json"
  test -s "${backup}" || die policy_backup_missing
  count="$(python3 - "${backup}" "${WEKNORA_SPREADSHEET_POLICY_COUNT}" <<'PY'
import json,sys
rows=json.load(open(sys.argv[1],encoding='utf-8')); expected=int(sys.argv[2])
if len(rows)!=expected: raise SystemExit('policy backup count mismatch')
print(len(rows))
PY
)"
  payload="$(python3 - "${backup}" <<'PY'
import base64,sys
print(base64.b64encode(open(sys.argv[1],'rb').read()).decode('ascii'))
PY
)"
  trigger_state="$(psql_stdin <<SQL
SELECT CASE WHEN EXISTS (
  SELECT 1 FROM pg_trigger WHERE tgrelid='public.knowledge_bases'::regclass
  AND tgname='${TRIGGER_NAME}' AND NOT tgisinternal
) THEN 'present' ELSE 'absent' END;
SQL
)"
  if test "${trigger_state}" = absent && test "${WEKNORA_SPREADSHEET_POLICY_APPLIED}" = pending; then
    echo weknora_spreadsheet_policy_rollback=NOT_COMMITTED
    return 0
  fi
  test "${trigger_state}" = present || die spreadsheet_policy_trigger_missing
  matched="$(psql_stdin <<SQL
SELECT count(*)
FROM knowledge_bases AS kb
WHERE kb.id IN (
  SELECT row->>'id'
  FROM jsonb_array_elements(convert_from(decode('${payload}','base64'),'UTF8')::jsonb) AS row
)
AND kb.updated_at = '${WEKNORA_SPREADSHEET_POLICY_AT}'::timestamptz;
SQL
)"
  test "${matched}" = "${count}" || die "guarded policy rollback precheck expected=${count} actual=${matched}"

  psql_stdin <<SQL
BEGIN;
DROP TRIGGER ${TRIGGER_NAME} ON public.knowledge_bases;
DROP FUNCTION public.${FUNCTION_NAME}();
CREATE TEMP TABLE hotfix_weknora_kb_restore ON COMMIT DROP AS
SELECT * FROM jsonb_to_recordset(
  convert_from(decode('${payload}','base64'),'UTF8')::jsonb
) AS restored(id uuid, chunking_config jsonb, updated_at timestamptz);
DO \$policy\$
DECLARE affected bigint;
BEGIN
  UPDATE knowledge_bases AS kb
  SET chunking_config = r.chunking_config,
      updated_at = r.updated_at
  FROM hotfix_weknora_kb_restore AS r
  WHERE kb.id = r.id
    AND kb.updated_at = '${WEKNORA_SPREADSHEET_POLICY_AT}'::timestamptz;
  GET DIAGNOSTICS affected = ROW_COUNT;
  IF affected <> ${count} THEN
    RAISE EXCEPTION 'guarded policy rollback mismatch expected=${count} actual=%', affected;
  END IF;
END
\$policy\$;
COMMIT;
SQL
  state_partial="${state_file}.partial.$$"
  {
    echo WEKNORA_SPREADSHEET_POLICY_APPLIED=restored
    printf 'WEKNORA_SPREADSHEET_POLICY_AT=%q\n' "${WEKNORA_SPREADSHEET_POLICY_AT}"
    echo "WEKNORA_SPREADSHEET_POLICY_COUNT=${count}"
  } >"${state_partial}"
  chmod 0600 "${state_partial}"
  mv "${state_partial}" "${state_file}"
  echo "weknora_spreadsheet_policy_rollback=PASS restored_knowledge_bases=${count}"
}

case "${MODE}" in
  --inspect) inspect_policy ;;
  --apply) apply_policy ;;
  --restore) restore_policy ;;
esac
