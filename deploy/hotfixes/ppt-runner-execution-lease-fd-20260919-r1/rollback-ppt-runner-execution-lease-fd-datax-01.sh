#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
test "$(hostname -s)" = datax-01
test "$(id -u)" -eq 0
test "${PPT_RUNNER_EXECUTION_LEASE_FD_ROLLBACK_ACK:-}" = YES
export KUBECONFIG=/etc/kubernetes/admin.conf
unset SSL_CERT_FILE REQUESTS_CA_BUNDLE CURL_CA_BUNDLE
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT}"
sha256sum -c PACKAGE.SHA256
python3 verify_execution_lease_hotfix.py --root "${ROOT}"
NS=deer-flow
EVIDENCE="$(realpath -e "${1:?supply the successful rollout evidence directory}")"
case "${EVIDENCE}" in
  /opt/deerflow-hotfix-evidence/ppt-runner-execution-lease-fd-20260919-r1.*) ;;
  *) echo "invalid_evidence_directory=${EVIDENCE}"; exit 1 ;;
esac
(cd "${EVIDENCE}"; sha256sum -c ROLLBACK.SHA256)

PG_POD="$(kubectl -n "${NS}" get pods -l app=postgres --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')"
ACTIVE_PPT_RUNS="$(kubectl -n "${NS}" exec "${PG_POD}" -c postgres -- sh -ec '
  export PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=10000"
  exec psql -X -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT count(*) FROM runs WHERE status IN ('\''pending'\'','\''running'\'') AND model_name='\''ppt-master-agent-host'\'';"
')"
echo "active_ppt_master_runs=${ACTIVE_PPT_RUNS}"
test "${ACTIVE_PPT_RUNS}" -eq 0

CM_NAME="$(cat "${EVIDENCE}/configmap.name")"
kubectl -n "${NS}" get deployment ppt-master-runner -o json > "${EVIDENCE}/runner.rollback-live.json"
python3 render_runner_overlay.py rollback \
  "${EVIDENCE}/runner.before.json" \
  "${EVIDENCE}/runner.rollback-live.json" \
  "${CM_NAME}" \
  "${EVIDENCE}/rollback.operator.patch.json"
kubectl -n "${NS}" patch deployment ppt-master-runner --type=json --patch-file "${EVIDENCE}/rollback.operator.patch.json"
kubectl -n "${NS}" rollout status deployment/ppt-master-runner --timeout=300s
kubectl -n "${NS}" get deployment ppt-master-runner -o wide
kubectl -n "${NS}" get pods -l app=ppt-master-runner -o wide
echo "ppt_runner_execution_lease_fd_rollback=PASS"
echo "configmap_retained=${CM_NAME}"
echo "production_database_mutation=NONE"
