#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from extract_model_config import load_runtime


MODEL_ID = "deerflow-cnooc-deepseek-v4-flash"


def sql_literal(value: object | None) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def snapshot_sql() -> str:
    return f"""
SET default_transaction_read_only = on;
SELECT jsonb_build_object(
  'schema_version', 1,
  'row', to_jsonb(selected)
)::text
FROM (
  SELECT id, parameters, managed_by, updated_at::text AS updated_at
  FROM models
  WHERE id = {sql_literal(MODEL_ID)} AND deleted_at IS NULL
) selected;
""".lstrip()


def metadata_sql() -> str:
    return f"""
SET default_transaction_read_only = on;
SELECT jsonb_build_object(
  'rows', count(*),
  'base_url', min(parameters->>'base_url'),
  'api_key_present', bool_and(length(COALESCE(parameters->>'api_key', '')) >= 32),
  'provider', min(parameters->>'provider')
)::text
FROM models
WHERE id = {sql_literal(MODEL_ID)} AND deleted_at IS NULL;
""".lstrip()


def update_sql(runtime: dict[str, object]) -> str:
    return f"""
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '60s';
LOCK TABLE models IN SHARE ROW EXCLUSIVE MODE;
UPDATE models
SET parameters = jsonb_set(
      jsonb_set(parameters, '{{base_url}}', to_jsonb({sql_literal(runtime['base_url'])}::text), true),
      '{{api_key}}', to_jsonb({sql_literal(runtime['api_key'])}::text), true
    ),
    managed_by = 'deerflow-unified-model-hotfix-r1',
    updated_at = clock_timestamp()
WHERE id = {sql_literal(MODEL_ID)}
  AND deleted_at IS NULL;
DO $$
BEGIN
  IF (SELECT count(*) FROM models WHERE id = {sql_literal(MODEL_ID)} AND deleted_at IS NULL
      AND parameters->>'base_url' = {sql_literal(runtime['base_url'])}
      AND parameters->>'api_key' = {sql_literal(runtime['api_key'])}) <> 1 THEN
    RAISE EXCEPTION 'WeKnora DeepSeek model update verification failed';
  END IF;
END $$;
COMMIT;
""".lstrip()


def verify_sql(runtime: dict[str, object]) -> str:
    return f"""
SET default_transaction_read_only = on;
SELECT jsonb_build_object(
  'rows', count(*),
  'base_url_match', count(*) FILTER (WHERE parameters->>'base_url' = {sql_literal(runtime['base_url'])}),
  'api_key_match', count(*) FILTER (WHERE parameters->>'api_key' = {sql_literal(runtime['api_key'])}),
  'provider_generic', count(*) FILTER (WHERE parameters->>'provider' = 'generic')
)::text
FROM models
WHERE id = {sql_literal(MODEL_ID)} AND deleted_at IS NULL;
""".lstrip()


def rollback_sql(path: Path) -> str:
    document = json.loads(path.read_text(encoding="utf-8"))
    row = document.get("row") or {}
    if document.get("schema_version") != 1 or row.get("id") != MODEL_ID:
        raise ValueError("unexpected WeKnora model backup")
    parameters = json.dumps(row.get("parameters") or {}, ensure_ascii=False, separators=(",", ":"))
    return f"""
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '60s';
UPDATE models
SET parameters = {sql_literal(parameters)}::jsonb,
    managed_by = {sql_literal(row.get('managed_by'))},
    updated_at = {sql_literal(row['updated_at'])}::timestamptz
WHERE id = {sql_literal(MODEL_ID)} AND deleted_at IS NULL;
COMMIT;
""".lstrip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-config", type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--metadata-sql", action="store_true")
    group.add_argument("--snapshot-sql", action="store_true")
    group.add_argument("--update-sql", action="store_true")
    group.add_argument("--verify-sql", action="store_true")
    group.add_argument("--make-rollback", type=Path)
    args = parser.parse_args()
    if args.metadata_sql:
        print(metadata_sql(), end="")
    elif args.snapshot_sql:
        print(snapshot_sql(), end="")
    elif args.make_rollback:
        print(rollback_sql(args.make_rollback), end="")
    else:
        if not args.model_config:
            raise SystemExit("--model-config is required")
        runtime = load_runtime(args.model_config.resolve())
        print(update_sql(runtime) if args.update_sql else verify_sql(runtime), end="")


if __name__ == "__main__":
    main()
