#!/usr/bin/env bash
set -Eeuo pipefail

NAMESPACE="${SKILLHUB_NAMESPACE:-deer-flow}"
SERVER_DEPLOYMENT="${SKILLHUB_SERVER_DEPLOYMENT:-skillhub-server}"
PROVIDER_CODE="${SKILLHUB_IDENTITY_PROVIDER:-deerflow-session}"
ACTION="${1:-list}"
SUBJECT="${2:-}"
ROLE_CODE="${3:-}"

case "${ACTION}" in
  list|grant|revoke) ;;
  *) echo "usage: $0 list [subject] | grant <subject> <role> | revoke <subject> <role>" >&2; exit 2 ;;
esac

if [[ "${ACTION}" != "list" ]]; then
  test -n "${SUBJECT}" || { echo "subject is required" >&2; exit 2; }
  case "${ROLE_CODE}" in
    SUPER_ADMIN|SKILL_ADMIN|USER_ADMIN|AUDITOR) ;;
    *) echo "role must be SUPER_ADMIN, SKILL_ADMIN, USER_ADMIN, or AUDITOR" >&2; exit 2 ;;
  esac
fi

command -v kubectl >/dev/null
command -v base64 >/dev/null
command -v python3 >/dev/null

SERVER_JSON="$(kubectl -n "${NAMESPACE}" get deployment "${SERVER_DEPLOYMENT}" -o json)"
readarray -t DB_SETTINGS < <(python3 -c '
import json, sys
d = json.load(sys.stdin)
c = next(c for c in d["spec"]["template"]["spec"]["containers"] if c["name"] == "server")
env = {item["name"]: item for item in c.get("env", [])}
url = env["SPRING_DATASOURCE_URL"]["value"]
user = env["SPRING_DATASOURCE_USERNAME"]["value"]
ref = env["SPRING_DATASOURCE_PASSWORD"]["valueFrom"]["secretKeyRef"]
database = url.rsplit("/", 1)[-1].split("?", 1)[0]
print(database)
print(user)
print(ref["name"])
print(ref["key"])
' <<<"${SERVER_JSON}")

DB_NAME="${DB_SETTINGS[0]}"
DB_USER="${DB_SETTINGS[1]}"
DB_SECRET="${DB_SETTINGS[2]}"
DB_SECRET_KEY="${DB_SETTINGS[3]}"
DB_PASSWORD="$(kubectl -n "${NAMESPACE}" get secret "${DB_SECRET}" -o "jsonpath={.data.${DB_SECRET_KEY}}" | base64 -d)"
DB_POD="$(kubectl -n "${NAMESPACE}" get pods \
  -l 'app.kubernetes.io/name=postgresql,app.kubernetes.io/component=primary' \
  --field-selector=status.phase=Running \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
if [[ -z "${DB_POD}" ]]; then
  DB_POD="$(kubectl -n "${NAMESPACE}" get pods \
    -l 'app.kubernetes.io/name=postgresql' \
    --field-selector=status.phase=Running \
    -o jsonpath='{.items[0].metadata.name}')"
fi
test -n "${DB_POD}" || { echo "running SkillHub PostgreSQL pod not found" >&2; exit 3; }

run_psql() {
  kubectl -n "${NAMESPACE}" exec -i "${DB_POD}" -- \
    env PGPASSWORD="${DB_PASSWORD}" psql -X -v ON_ERROR_STOP=1 \
      -U "${DB_USER}" -d "${DB_NAME}" "$@"
}

if [[ "${ACTION}" == "list" ]]; then
  run_psql -v provider="${PROVIDER_CODE}" -v subject="${SUBJECT}" <<'SQL'
SELECT
  ib.subject,
  COALESCE(ib.login_name, ''),
  ua.id,
  ua.display_name,
  COALESCE(ua.email, ''),
  ua.status,
  COALESCE(string_agg(r.code, ',' ORDER BY r.code), '') AS roles
FROM identity_binding ib
JOIN user_account ua ON ua.id = ib.user_id
LEFT JOIN user_role_binding urb ON urb.user_id = ua.id
LEFT JOIN role r ON r.id = urb.role_id
WHERE ib.provider_code = :'provider'
  AND (:'subject' = '' OR ib.subject = :'subject')
GROUP BY ib.subject, ib.login_name, ua.id, ua.display_name, ua.email, ua.status
ORDER BY ua.display_name, ib.subject;
SQL
  exit 0
fi

MATCH_COUNT="$(run_psql -At -v provider="${PROVIDER_CODE}" -v subject="${SUBJECT}" <<'SQL'
SELECT count(*)
FROM identity_binding
WHERE provider_code = :'provider' AND subject = :'subject';
SQL
)"
[[ "${MATCH_COUNT}" == "1" ]] || {
  echo "expected exactly one ${PROVIDER_CODE} identity for subject=${SUBJECT}; found ${MATCH_COUNT}" >&2
  exit 4
}

if [[ "${ACTION}" == "grant" ]]; then
  run_psql -v provider="${PROVIDER_CODE}" -v subject="${SUBJECT}" -v role="${ROLE_CODE}" <<'SQL'
BEGIN;
WITH target AS (
  SELECT user_id FROM identity_binding
  WHERE provider_code = :'provider' AND subject = :'subject'
), selected_role AS (
  SELECT id FROM role WHERE code = :'role'
), inserted AS (
  INSERT INTO user_role_binding (user_id, role_id)
  SELECT target.user_id, selected_role.id FROM target CROSS JOIN selected_role
  ON CONFLICT (user_id, role_id) DO NOTHING
  RETURNING user_id
)
INSERT INTO audit_log (actor_user_id, action, target_type, detail_json)
SELECT user_id, 'OPERATOR_ROLE_GRANTED', 'USER',
       jsonb_build_object('provider', :'provider', 'subject', :'subject', 'role', :'role')
FROM inserted;
COMMIT;
SQL
else
  run_psql -v provider="${PROVIDER_CODE}" -v subject="${SUBJECT}" -v role="${ROLE_CODE}" <<'SQL'
BEGIN;
WITH target AS (
  SELECT user_id FROM identity_binding
  WHERE provider_code = :'provider' AND subject = :'subject'
), deleted AS (
  DELETE FROM user_role_binding urb
  USING target, role
  WHERE urb.user_id = target.user_id
    AND urb.role_id = role.id
    AND role.code = :'role'
  RETURNING urb.user_id
)
INSERT INTO audit_log (actor_user_id, action, target_type, detail_json)
SELECT user_id, 'OPERATOR_ROLE_REVOKED', 'USER',
       jsonb_build_object('provider', :'provider', 'subject', :'subject', 'role', :'role')
FROM deleted;
COMMIT;
SQL
fi

run_psql -v provider="${PROVIDER_CODE}" -v subject="${SUBJECT}" <<'SQL'
SELECT ib.subject, ua.display_name, COALESCE(string_agg(r.code, ',' ORDER BY r.code), '') AS roles
FROM identity_binding ib
JOIN user_account ua ON ua.id = ib.user_id
LEFT JOIN user_role_binding urb ON urb.user_id = ua.id
LEFT JOIN role r ON r.id = urb.role_id
WHERE ib.provider_code = :'provider' AND ib.subject = :'subject'
GROUP BY ib.subject, ua.display_name;
SQL
