#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


OWNER = "builtin-skill-publisher"
NAMESPACE = "global"


def sql_literal(value: object | None) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def load_descriptions(path: Path) -> list[tuple[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        rows = [(row[0].strip(), row[1].strip()) for row in csv.reader(stream, delimiter="\t") if row]
    if len(rows) != 17 or len({slug for slug, _ in rows}) != 17:
        raise ValueError("the built-in description manifest must contain 17 unique slugs")
    for slug, description in rows:
        if not slug or not description or not any("\u4e00" <= char <= "\u9fff" for char in description):
            raise ValueError(f"invalid Chinese description for {slug!r}")
    return rows


def values_sql(rows: list[tuple[str, str]]) -> str:
    return ",\n".join(f"  ({sql_literal(slug)}, {sql_literal(description)})" for slug, description in rows)


def emit_schema_check() -> str:
    return """
SET default_transaction_read_only = on;
WITH required(table_name, column_name) AS (VALUES
  ('namespace', 'id'),
  ('namespace', 'slug'),
  ('skill', 'id'),
  ('skill', 'slug'),
  ('skill', 'summary'),
  ('skill', 'updated_by'),
  ('skill', 'updated_at'),
  ('skill', 'namespace_id'),
  ('skill', 'owner_id'),
  ('skill', 'latest_version_id'),
  ('skill_version', 'id'),
  ('skill_version', 'status'),
  ('skill_search_document', 'id'),
  ('skill_search_document', 'skill_id'),
  ('skill_search_document', 'summary'),
  ('skill_search_document', 'updated_at')
), checked AS (
  SELECT r.table_name, r.column_name, c.column_name IS NOT NULL AS present
  FROM required r
  LEFT JOIN information_schema.columns c
    ON c.table_schema = 'public'
   AND c.table_name = r.table_name
   AND c.column_name = r.column_name
)
SELECT jsonb_build_object(
  'required', count(*),
  'present', count(*) FILTER (WHERE present),
  'missing', jsonb_agg(table_name || '.' || column_name ORDER BY table_name, column_name)
    FILTER (WHERE NOT present)
)::text
FROM checked;
""".lstrip()


def emit_snapshot(rows: list[tuple[str, str]]) -> str:
    slugs = ", ".join(sql_literal(slug) for slug, _ in rows)
    return f"""
SET default_transaction_read_only = on;
WITH selected AS (
  SELECT
    s.id AS skill_id,
    s.slug,
    s.summary AS skill_summary,
    s.updated_by AS skill_updated_by,
    s.updated_at::text AS skill_updated_at,
    d.id AS document_id,
    d.summary AS document_summary,
    d.updated_at::text AS document_updated_at,
    v.status AS latest_version_status
  FROM skill s
  JOIN namespace n ON n.id = s.namespace_id
  JOIN skill_version v ON v.id = s.latest_version_id
  LEFT JOIN skill_search_document d ON d.skill_id = s.id
  WHERE n.slug = {sql_literal(NAMESPACE)}
    AND s.owner_id = {sql_literal(OWNER)}
    AND s.slug IN ({slugs})
)
SELECT jsonb_build_object(
  'schema_version', 1,
  'namespace', {sql_literal(NAMESPACE)},
  'owner', {sql_literal(OWNER)},
  'rows', COALESCE(jsonb_agg(to_jsonb(selected) ORDER BY slug), '[]'::jsonb)
)::text
FROM selected;
""".lstrip()


def emit_update(rows: list[tuple[str, str]]) -> str:
    return f"""
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '60s';
LOCK TABLE skill IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE skill_search_document IN SHARE ROW EXCLUSIVE MODE;
CREATE TEMP TABLE hotfix_builtin_description (
  slug text PRIMARY KEY,
  summary text NOT NULL
) ON COMMIT DROP;
INSERT INTO hotfix_builtin_description (slug, summary) VALUES
{values_sql(rows)};
DO $$
DECLARE
  skill_rows integer;
  document_rows integer;
  published_rows integer;
BEGIN
  SELECT count(*) INTO skill_rows
  FROM skill s
  JOIN namespace n ON n.id = s.namespace_id
  JOIN hotfix_builtin_description t ON t.slug = s.slug
  WHERE n.slug = {sql_literal(NAMESPACE)} AND s.owner_id = {sql_literal(OWNER)};
  SELECT count(*) INTO document_rows
  FROM skill_search_document d
  JOIN skill s ON s.id = d.skill_id
  JOIN namespace n ON n.id = s.namespace_id
  JOIN hotfix_builtin_description t ON t.slug = s.slug
  WHERE n.slug = {sql_literal(NAMESPACE)} AND s.owner_id = {sql_literal(OWNER)};
  SELECT count(*) INTO published_rows
  FROM skill s
  JOIN namespace n ON n.id = s.namespace_id
  JOIN skill_version v ON v.id = s.latest_version_id
  JOIN hotfix_builtin_description t ON t.slug = s.slug
  WHERE n.slug = {sql_literal(NAMESPACE)}
    AND s.owner_id = {sql_literal(OWNER)}
    AND v.status = 'PUBLISHED';
  IF skill_rows <> 17 OR document_rows <> 17 OR published_rows <> 17 THEN
    RAISE EXCEPTION 'built-in scope mismatch skills=% documents=% published=%', skill_rows, document_rows, published_rows;
  END IF;
END $$;
UPDATE skill s
SET summary = t.summary,
    updated_at = clock_timestamp()
FROM namespace n, hotfix_builtin_description t
WHERE n.id = s.namespace_id
  AND n.slug = {sql_literal(NAMESPACE)}
  AND s.owner_id = {sql_literal(OWNER)}
  AND s.slug = t.slug;
UPDATE skill_search_document d
SET summary = t.summary,
    updated_at = clock_timestamp()
FROM skill s, namespace n, hotfix_builtin_description t
WHERE d.skill_id = s.id
  AND n.id = s.namespace_id
  AND n.slug = {sql_literal(NAMESPACE)}
  AND s.owner_id = {sql_literal(OWNER)}
  AND s.slug = t.slug;
DO $$
DECLARE
  mismatches integer;
BEGIN
  SELECT count(*) INTO mismatches
  FROM hotfix_builtin_description t
  LEFT JOIN skill s ON s.slug = t.slug AND s.owner_id = {sql_literal(OWNER)}
  LEFT JOIN namespace n ON n.id = s.namespace_id AND n.slug = {sql_literal(NAMESPACE)}
  LEFT JOIN skill_search_document d ON d.skill_id = s.id
  WHERE n.id IS NULL OR s.summary IS DISTINCT FROM t.summary OR d.summary IS DISTINCT FROM t.summary;
  IF mismatches <> 0 THEN
    RAISE EXCEPTION 'post-update built-in description mismatches=%', mismatches;
  END IF;
END $$;
COMMIT;
""".lstrip()


def emit_verify(rows: list[tuple[str, str]]) -> str:
    return f"""
SET default_transaction_read_only = on;
WITH target(slug, summary) AS (VALUES
{values_sql(rows)}
), checked AS (
  SELECT t.slug, s.id AS skill_id, d.id AS document_id,
         s.summary IS NOT DISTINCT FROM t.summary AS skill_ok,
         d.summary IS NOT DISTINCT FROM t.summary AS document_ok
  FROM target t
  LEFT JOIN namespace n ON n.slug = {sql_literal(NAMESPACE)}
  LEFT JOIN skill s ON s.namespace_id = n.id AND s.slug = t.slug AND s.owner_id = {sql_literal(OWNER)}
  LEFT JOIN skill_search_document d ON d.skill_id = s.id
)
SELECT jsonb_build_object(
  'rows', count(*),
  'skills_present', count(skill_id),
  'documents_present', count(document_id),
  'localized', count(*) FILTER (WHERE skill_ok AND document_ok)
)::text
FROM checked;
""".lstrip()


def make_rollback(backup_path: Path, rows: list[tuple[str, str]]) -> str:
    backup = json.loads(backup_path.read_text(encoding="utf-8"))
    records = backup.get("rows") or []
    targets = dict(rows)
    if backup.get("namespace") != NAMESPACE or backup.get("owner") != OWNER or len(records) != 17:
        raise ValueError("unexpected SkillHub backup scope")
    if {row.get("slug") for row in records} != set(targets):
        raise ValueError("SkillHub backup slugs do not match the manifest")
    output = ["BEGIN;", "SET LOCAL lock_timeout = '10s';", "SET LOCAL statement_timeout = '60s';"]
    for row in records:
        target = targets[row["slug"]]
        output.append(
            "UPDATE skill SET summary={old}, updated_by={updated_by}, updated_at={updated_at}::timestamptz "
            "WHERE id={identifier} AND summary IS NOT DISTINCT FROM {target};".format(
                old=sql_literal(row.get("skill_summary")),
                updated_by=sql_literal(row.get("skill_updated_by")),
                updated_at=sql_literal(row["skill_updated_at"]),
                identifier=int(row["skill_id"]),
                target=sql_literal(target),
            )
        )
        output.append(
            "UPDATE skill_search_document SET summary={old}, updated_at={updated_at}::timestamptz "
            "WHERE id={identifier} AND summary IS NOT DISTINCT FROM {target};".format(
                old=sql_literal(row.get("document_summary")),
                updated_at=sql_literal(row["document_updated_at"]),
                identifier=int(row["document_id"]),
                target=sql_literal(target),
            )
        )
    output.append("COMMIT;")
    return "\n".join(output) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--snapshot-sql", action="store_true")
    group.add_argument("--update-sql", action="store_true")
    group.add_argument("--verify-sql", action="store_true")
    group.add_argument("--schema-sql", action="store_true")
    group.add_argument("--make-rollback", type=Path)
    args = parser.parse_args()
    rows = load_descriptions(args.manifest)
    if args.schema_sql:
        print(emit_schema_check(), end="")
    elif args.snapshot_sql:
        print(emit_snapshot(rows), end="")
    elif args.update_sql:
        print(emit_update(rows), end="")
    elif args.verify_sql:
        print(emit_verify(rows), end="")
    else:
        print(make_rollback(args.make_rollback, rows), end="")


if __name__ == "__main__":
    main()
