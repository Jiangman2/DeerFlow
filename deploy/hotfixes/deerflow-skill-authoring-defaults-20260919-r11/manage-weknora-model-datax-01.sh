#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:-}"
EVIDENCE_DIR="${2:-}"
MODEL_CONFIG_FILE="${DEERFLOW_PRIMARY_MODEL_CONFIG_FILE:-/home/cnooc/DeepSeek-V4-Flash-stream-thinking-on-off.py}"
NS=deer-flow

die() { echo "weknora_unified_deepseek_model=FAIL detail=$*" >&2; exit 1; }
case "${MODE}" in
  --preflight|--status) ;;
  --backup|--install|--restore)
    case "${EVIDENCE_DIR}" in /opt/deerflow-hotfix-evidence/deerflow-skill-authoring-defaults-20260919-r11/*) ;; *) die 'unsafe_evidence_dir' ;; esac
    test -d "${EVIDENCE_DIR}" || die 'evidence_dir_missing'
    ;;
  *) die 'usage: --preflight|--status|--backup DIR|--install DIR|--restore DIR' ;;
esac
case "${MODE}" in
  --preflight|--status|--install)
    test -s "${MODEL_CONFIG_FILE}" || die "model_config_file_missing=${MODEL_CONFIG_FILE}"
    ;;
esac
PG_POD="$(kubectl -n "${NS}" get pods -l app=weknora-postgres --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')"
test -n "${PG_POD}" || die 'running_weknora_postgres_pod_not_found'
psql_stream() {
  kubectl -n "${NS}" exec -i "${PG_POD}" -c postgres -- sh -ec 'export PGPASSWORD="$POSTGRES_PASSWORD"; exec psql -X -At -P pager=off -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
}
json_line() { python3 -c 'import json,sys; rows=[x.strip() for x in sys.stdin if x.strip().startswith("{")]; assert len(rows)==1, rows; print(json.dumps(json.loads(rows[0]),ensure_ascii=False,sort_keys=True))'; }

if test "${MODE}" = '--preflight'; then
  result="$(python3 "${ROOT_DIR}/weknora_model_config.py" --metadata-sql | psql_stream | json_line)"
  python3 -c 'import json,sys; d=json.loads(sys.argv[1]); assert d.get("rows")==1,d; assert d.get("api_key_present") is True,d; assert d.get("provider")=="generic",d; assert isinstance(d.get("base_url"),str) and d["base_url"],d; print("weknora_deepseek_model_scope=PASS base_url="+d["base_url"])' "${result}"
  echo 'weknora_unified_deepseek_model_preflight=PASS'
  echo 'production_weknora_database_mutated=no'
  exit 0
fi

BACKUP="${EVIDENCE_DIR}/weknora-deepseek-model.before.json"
if test "${MODE}" = '--backup'; then
  test ! -e "${BACKUP}" || die 'backup_already_exists'
  python3 "${ROOT_DIR}/weknora_model_config.py" --snapshot-sql | psql_stream | json_line > "${BACKUP}"
  python3 - "${BACKUP}" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]); d=json.loads(p.read_text(encoding="utf-8"))
assert d.get("schema_version")==1 and (d.get("row") or {}).get("id")=="deerflow-cnooc-deepseek-v4-flash"
PY
  python3 "${ROOT_DIR}/weknora_model_config.py" --make-rollback "${BACKUP}" > "${EVIDENCE_DIR}/weknora-deepseek-model.rollback.sql"
  echo "weknora_unified_deepseek_model_backup=PASS file=${BACKUP}"
  exit 0
fi

if test "${MODE}" = '--install' || test "${MODE}" = '--status'; then
  if test "${MODE}" = '--install'; then
    test -s "${BACKUP}" || die 'backup_missing'
    python3 "${ROOT_DIR}/weknora_model_config.py" --model-config "${MODEL_CONFIG_FILE}" --update-sql | psql_stream >/dev/null
  fi
  result="$(python3 "${ROOT_DIR}/weknora_model_config.py" --model-config "${MODEL_CONFIG_FILE}" --verify-sql | psql_stream | json_line)"
  python3 -c 'import json,sys; d=json.loads(sys.argv[1]); assert d=={"api_key_match":1,"base_url_match":1,"provider_generic":1,"rows":1}, d; print("weknora_unified_deepseek_model_runtime=PASS")' "${result}"
  exit 0
fi

test -s "${EVIDENCE_DIR}/weknora-deepseek-model.rollback.sql" || die 'rollback_sql_missing'
psql_stream < "${EVIDENCE_DIR}/weknora-deepseek-model.rollback.sql" >/dev/null
echo 'weknora_unified_deepseek_model_restore=PASS'
