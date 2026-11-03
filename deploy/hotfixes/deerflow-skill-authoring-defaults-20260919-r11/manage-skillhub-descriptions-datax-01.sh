#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:-}"
EVIDENCE_DIR="${2:-}"
NS=deer-flow

die() { echo "skillhub_builtin_descriptions=FAIL detail=$*" >&2; exit 1; }
case "${MODE}" in
  --preflight|--status) ;;
  --backup|--install|--restore)
    case "${EVIDENCE_DIR}" in
      /opt/deerflow-hotfix-evidence/deerflow-skill-authoring-defaults-20260919-r11/*) ;;
      *) die 'unsafe_evidence_dir' ;;
    esac
    test -d "${EVIDENCE_DIR}" || die 'evidence_dir_missing'
    ;;
  *) die 'usage: --preflight|--status|--backup DIR|--install DIR|--restore DIR' ;;
esac

PG_POD="$(kubectl -n "${NS}" get pods -l 'app.kubernetes.io/instance=skillhub,app.kubernetes.io/name=postgresql,app.kubernetes.io/component=primary' --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')"
test -n "${PG_POD}" || die 'running_skillhub_postgresql_pod_not_found'

psql_stream() {
  kubectl -n "${NS}" exec -i "${PG_POD}" -c postgresql -- sh -ec '
    db_user="${POSTGRES_USER:-${POSTGRESQL_USERNAME:-postgres}}"
    db_name="${POSTGRES_DATABASE:-${POSTGRES_DB:-${POSTGRESQL_DATABASE:-skillhub}}}"
    if test -n "${POSTGRES_PASSWORD_FILE:-}" && test -r "${POSTGRES_PASSWORD_FILE}"; then
      PGPASSWORD="$(cat "${POSTGRES_PASSWORD_FILE}")"
    else
      PGPASSWORD="${POSTGRES_PASSWORD:-${POSTGRESQL_PASSWORD:-}}"
    fi
    export PGPASSWORD
    exec psql -X -At -P pager=off -v ON_ERROR_STOP=1 -h 127.0.0.1 -U "${db_user}" -d "${db_name}"
  '
}

verify_output() {
  python3 -c '
import json, sys
rows=[line.strip() for line in sys.stdin if line.strip().startswith("{")]
if len(rows) != 1:
    raise SystemExit("expected one JSON verification row")
doc=json.loads(rows[0])
if doc.get("rows") != 17 or doc.get("skills_present") != 17 or doc.get("documents_present") != 17:
    raise SystemExit("SkillHub built-in scope mismatch: " + json.dumps(doc, ensure_ascii=False))
require_localized=sys.argv[1] == "yes"
if require_localized and doc.get("localized") != 17:
    raise SystemExit("SkillHub localization mismatch: " + json.dumps(doc, ensure_ascii=False))
print("skillhub_builtin_description_scope=PASS rows=17 localized=%s" % doc.get("localized"))
' "$1"
}

verify_schema_output() {
  python3 -c '
import json, sys
rows=[line.strip() for line in sys.stdin if line.strip().startswith("{")]
if len(rows) != 1:
    raise SystemExit("expected one SkillHub schema verification row")
doc=json.loads(rows[0])
if doc.get("required") != 16 or doc.get("present") != 16 or doc.get("missing") not in (None, []):
    raise SystemExit("SkillHub schema mismatch: " + json.dumps(doc, ensure_ascii=False))
print("skillhub_database_schema=PASS required=16 association=skill_search_document.skill_id")
'
}

if test "${MODE}" != '--restore'; then
  python3 "${ROOT_DIR}/skillhub_descriptions.py" --manifest "${ROOT_DIR}/SKILLHUB_BUILTIN_DESCRIPTIONS.tsv" --schema-sql | psql_stream | verify_schema_output
fi

if test "${MODE}" = '--preflight' || test "${MODE}" = '--status'; then
  require=no
  test "${MODE}" = '--status' && require=yes
  python3 "${ROOT_DIR}/skillhub_descriptions.py" --manifest "${ROOT_DIR}/SKILLHUB_BUILTIN_DESCRIPTIONS.tsv" --verify-sql | psql_stream | verify_output "${require}"
  echo "skillhub_builtin_descriptions_${MODE#--}=PASS"
  test "${MODE}" = '--preflight' && echo 'production_skillhub_database_mutated=no'
  exit 0
fi

BACKUP="${EVIDENCE_DIR}/skillhub-builtins.before.json"
if test "${MODE}" = '--backup'; then
  test ! -e "${BACKUP}" || die 'backup_already_exists'
  python3 "${ROOT_DIR}/skillhub_descriptions.py" --manifest "${ROOT_DIR}/SKILLHUB_BUILTIN_DESCRIPTIONS.tsv" --snapshot-sql | psql_stream > "${BACKUP}.partial"
  python3 - "${BACKUP}.partial" <<'PY'
import json, sys
from pathlib import Path
path=Path(sys.argv[1])
lines=[line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip().startswith("{")]
if len(lines) != 1:
    raise SystemExit("expected one SkillHub backup object")
doc=json.loads(lines[0])
if doc.get("namespace") != "global" or doc.get("owner") != "builtin-skill-publisher" or len(doc.get("rows") or []) != 17:
    raise SystemExit("unexpected SkillHub backup scope")
path.write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
PY
  mv -f -- "${BACKUP}.partial" "${BACKUP}"
  python3 "${ROOT_DIR}/skillhub_descriptions.py" --manifest "${ROOT_DIR}/SKILLHUB_BUILTIN_DESCRIPTIONS.tsv" --make-rollback "${BACKUP}" > "${EVIDENCE_DIR}/skillhub-builtins.rollback.sql"
  echo "skillhub_builtin_descriptions_backup=PASS file=${BACKUP}"
  exit 0
fi

test -s "${BACKUP}" || die 'backup_missing'
if test "${MODE}" = '--install'; then
  python3 "${ROOT_DIR}/skillhub_descriptions.py" --manifest "${ROOT_DIR}/SKILLHUB_BUILTIN_DESCRIPTIONS.tsv" --update-sql | psql_stream >/dev/null
  python3 "${ROOT_DIR}/skillhub_descriptions.py" --manifest "${ROOT_DIR}/SKILLHUB_BUILTIN_DESCRIPTIONS.tsv" --verify-sql | psql_stream | verify_output yes
  echo 'skillhub_builtin_descriptions_install=PASS'
  exit 0
fi

test -s "${EVIDENCE_DIR}/skillhub-builtins.rollback.sql" || die 'rollback_sql_missing'
psql_stream < "${EVIDENCE_DIR}/skillhub-builtins.rollback.sql" >/dev/null
echo 'skillhub_builtin_descriptions_restore=PASS'
