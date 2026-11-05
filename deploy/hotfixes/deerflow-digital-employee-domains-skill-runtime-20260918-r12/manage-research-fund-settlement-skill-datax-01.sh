#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
. "${ROOT_DIR}/IMAGE_LOCKS.env"

MODE="${1:-}"
EVIDENCE_DIR="${2:-}"
NS=deer-flow
POD=digital-domains-skill-maintenance
SKILL_NAME=research-fund-settlement
SKILL_ROOT="${ROOT_DIR}/payload/skills/public/${SKILL_NAME}"
SKILL_MANIFEST="${ROOT_DIR}/SKILL.SHA256"
LIVE_ROOT="/mnt/skills/public/${SKILL_NAME}"
TRANSACTION_ID=''
STAGE_ROOT=''
BACKUP_ROOT=''
INSTALL_SWAPPED=no
INSTALL_COMPLETE=no

die() {
  echo "research_fund_settlement_skill_pvc=FAIL detail=$*" >&2
  exit 1
}

cleanup() {
  if test "${MODE}" = '--install' && test "${INSTALL_SWAPPED}" = 'yes' && test "${INSTALL_COMPLETE}" != 'yes'; then
    kubectl -n "${NS}" exec "${POD}" -c maintenance -- \
      sh -ec '
        target="$1"
        backup="$2"
        failed="${target}.failed-install"
        rm -rf -- "${failed}"
        if test -e "${target}"; then mv -- "${target}" "${failed}"; fi
        if test -e "${backup}"; then mv -- "${backup}" "${target}"; fi
        rm -rf -- "${failed}"
      ' sh "${LIVE_ROOT}" "${BACKUP_ROOT}" >/dev/null 2>&1 || true
  fi
  kubectl -n "${NS}" delete pod "${POD}" \
    --ignore-not-found=true --wait=false >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

case "${MODE}" in
  --preflight) ;;
  --backup|--install|--restore|--finalize)
    case "${EVIDENCE_DIR}" in
      /opt/deerflow-hotfix-evidence/digital-employee-domains-skill-runtime-20260918-r12/*) ;;
      *) die 'unsafe_evidence_dir' ;;
    esac
    test -d "${EVIDENCE_DIR}" || die 'evidence_dir_missing'
    TRANSACTION_ID="$(basename -- "${EVIDENCE_DIR}")"
    case "${TRANSACTION_ID}" in
      *[!0-9TZ]*) die 'invalid_transaction_id' ;;
    esac
    STAGE_ROOT="/mnt/skills/public/.${SKILL_NAME}.${TRANSACTION_ID}.stage"
    BACKUP_ROOT="/mnt/skills/public/.${SKILL_NAME}.${TRANSACTION_ID}.before"
    ;;
  *) die 'usage: --preflight | --backup EVIDENCE_DIR | --install EVIDENCE_DIR | --restore EVIDENCE_DIR | --finalize EVIDENCE_DIR' ;;
esac

test "$(id -u)" = '0' || die 'must_run_as_root'
test "$(hostname -s)" = 'datax-01' || die 'must_run_on_datax-01'
command -v kubectl >/dev/null || die 'kubectl_not_found'
command -v sha256sum >/dev/null || die 'sha256sum_not_found'
command -v tar >/dev/null || die 'tar_not_found'
test -d "${SKILL_ROOT}" || die 'package_skill_root_missing'
test -s "${SKILL_MANIFEST}" || die 'skill_manifest_missing'
(
  cd "${SKILL_ROOT}"
  sha256sum -c "${SKILL_MANIFEST}"
)

create_pod() {
  local read_only="$1"
  kubectl -n "${NS}" delete pod "${POD}" \
    --ignore-not-found=true --wait=true --timeout=120s >/dev/null
  cat <<YAML | kubectl -n "${NS}" apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: ${POD}
  labels:
    app: digital-domains-skill-maintenance
spec:
  nodeName: datax-01
  restartPolicy: Never
  automountServiceAccountToken: false
  containers:
    - name: maintenance
      image: ${BASE_SKILLS_IMAGE}
      imagePullPolicy: Never
      command: ["sh", "-ec"]
      args: ["sleep 3600"]
      volumeMounts:
        - name: skills
          mountPath: /mnt/skills
          readOnly: ${read_only}
  volumes:
    - name: skills
      persistentVolumeClaim:
        claimName: deer-flow-skills-pvc
YAML
  kubectl -n "${NS}" wait --for=condition=Ready "pod/${POD}" --timeout=180s
}

if test "${MODE}" = '--preflight'; then
  create_pod true
  kubectl -n "${NS}" exec "${POD}" -c maintenance -- \
    sh -ec 'test -r "$1/SKILL.md"; command -v tar >/dev/null; command -v sha256sum >/dev/null; find "$1" -type f | wc -l' sh "${LIVE_ROOT}"
  LIVE_SKILL_SHA="$(kubectl -n "${NS}" exec "${POD}" -c maintenance -- sha256sum "${LIVE_ROOT}/SKILL.md" | awk '{print $1}')"
  PACKAGE_SKILL_SHA="$(sha256sum "${SKILL_ROOT}/SKILL.md" | awk '{print $1}')"
  echo "live_research_fund_settlement_skill_sha256=${LIVE_SKILL_SHA}"
  echo "package_research_fund_settlement_skill_sha256=${PACKAGE_SKILL_SHA}"
  echo 'research_fund_settlement_skill_pvc_preflight=PASS'
  echo 'production_skill_mutated=no'
  exit 0
fi

if test "${MODE}" = '--backup'; then
  create_pod true
  BACKUP_TAR="${EVIDENCE_DIR}/${SKILL_NAME}.before.tar"
  BACKUP_PARTIAL="${BACKUP_TAR}.partial"
  test ! -e "${BACKUP_TAR}" || die 'skill_backup_already_exists'
  kubectl -n "${NS}" exec "${POD}" -c maintenance -- test -r "${LIVE_ROOT}/SKILL.md"
  kubectl -n "${NS}" exec "${POD}" -c maintenance -- \
    tar -C "${LIVE_ROOT}" -cf - . > "${BACKUP_PARTIAL}"
  test -s "${BACKUP_PARTIAL}" || die 'empty_skill_backup'
  mv -- "${BACKUP_PARTIAL}" "${BACKUP_TAR}"
  tar -tf "${BACKUP_TAR}" >/dev/null
  echo 'research_fund_settlement_skill_backup=PASS'
  echo "skill_backup_tar=${BACKUP_TAR}"
  exit 0
fi

create_pod false

if test "${MODE}" = '--install'; then
  BACKUP_TAR="${EVIDENCE_DIR}/${SKILL_NAME}.before.tar"
  test -s "${BACKUP_TAR}" || die 'skill_backup_tar_missing'
  tar -tf "${BACKUP_TAR}" >/dev/null

  kubectl -n "${NS}" exec "${POD}" -c maintenance -- \
    sh -ec 'rm -rf -- "$1" "$2"; mkdir -p -- "$1"' sh "${STAGE_ROOT}" "${BACKUP_ROOT}"
  tar -C "${SKILL_ROOT}" -cf - . | kubectl -n "${NS}" exec -i "${POD}" -c maintenance -- \
    tar -C "${STAGE_ROOT}" -xf -
  kubectl -n "${NS}" exec -i "${POD}" -c maintenance -- \
    sh -ec 'cd "$1"; sha256sum -c -' sh "${STAGE_ROOT}" < "${SKILL_MANIFEST}"
  kubectl -n "${NS}" exec "${POD}" -c maintenance -- chmod -R a+rX "${STAGE_ROOT}"
  kubectl -n "${NS}" exec "${POD}" -c maintenance -- \
    sh -ec '
      target="$1"
      stage="$2"
      backup="$3"
      mv -- "${target}" "${backup}"
      if ! mv -- "${stage}" "${target}"; then
        mv -- "${backup}" "${target}"
        exit 1
      fi
    ' sh "${LIVE_ROOT}" "${STAGE_ROOT}" "${BACKUP_ROOT}"
  INSTALL_SWAPPED=yes
  kubectl -n "${NS}" exec -i "${POD}" -c maintenance -- \
    sh -ec 'cd "$1"; sha256sum -c -' sh "${LIVE_ROOT}" < "${SKILL_MANIFEST}"
  INSTALL_COMPLETE=yes
  echo 'research_fund_settlement_skill_install=PASS mode=atomic_directory_swap'
  echo "skill_pvc_backup=${BACKUP_ROOT}"
  exit 0
fi

if test "${MODE}" = '--restore'; then
  BACKUP_TAR="${EVIDENCE_DIR}/${SKILL_NAME}.before.tar"
  test -s "${BACKUP_TAR}" || die 'skill_backup_tar_missing'
  RESTORE_ROOT="/mnt/skills/public/.${SKILL_NAME}.${TRANSACTION_ID}.restore"
  FAILED_ROOT="/mnt/skills/public/.${SKILL_NAME}.${TRANSACTION_ID}.failed"
  kubectl -n "${NS}" exec "${POD}" -c maintenance -- \
    sh -ec 'rm -rf -- "$1" "$2"; mkdir -p -- "$1"' sh "${RESTORE_ROOT}" "${FAILED_ROOT}"
  cat "${BACKUP_TAR}" | kubectl -n "${NS}" exec -i "${POD}" -c maintenance -- \
    tar -C "${RESTORE_ROOT}" -xf -
  kubectl -n "${NS}" exec "${POD}" -c maintenance -- test -r "${RESTORE_ROOT}/SKILL.md"
  kubectl -n "${NS}" exec "${POD}" -c maintenance -- \
    sh -ec '
      target="$1"
      restore="$2"
      failed="$3"
      backup="$4"
      if test -e "${target}"; then mv -- "${target}" "${failed}"; fi
      if ! mv -- "${restore}" "${target}"; then
        if test -e "${failed}"; then mv -- "${failed}" "${target}"; fi
        exit 1
      fi
      rm -rf -- "${failed}" "${backup}"
    ' sh "${LIVE_ROOT}" "${RESTORE_ROOT}" "${FAILED_ROOT}" "${BACKUP_ROOT}"
  echo 'research_fund_settlement_skill_restore=PASS'
  exit 0
fi

kubectl -n "${NS}" exec "${POD}" -c maintenance -- \
  sh -ec 'rm -rf -- "$1" "$2"' sh "${STAGE_ROOT}" "${BACKUP_ROOT}"
echo 'research_fund_settlement_skill_finalize=PASS'
