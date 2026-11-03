#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:-}"
EVIDENCE_DIR="${2:-}"
NS=deer-flow
POD=media-generation-skill-maintenance
PUBLIC_ROOT=/mnt/skills/public
SKILLS=(image-generation video-generation)

die() {
  echo "media_generation_skills_pvc=FAIL detail=$*" >&2
  exit 1
}

cleanup() {
  kubectl -n "${NS}" delete pod "${POD}" --ignore-not-found=true --wait=false >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

case "${MODE}" in
  --preflight|--status) ;;
  --backup|--install|--restore)
    case "${EVIDENCE_DIR}" in
      /opt/deerflow-hotfix-evidence/deerflow-media-generation-20260918-r1/*) ;;
      *) die 'unsafe_evidence_dir' ;;
    esac
    test -d "${EVIDENCE_DIR}" || die 'evidence_dir_missing'
    ;;
  *) die 'usage: --preflight|--status|--backup EVIDENCE_DIR|--install EVIDENCE_DIR|--restore EVIDENCE_DIR' ;;
esac

test "$(id -u)" = '0' || die 'must_run_as_root'
test "$(hostname -s)" = 'datax-01' || die 'must_run_on_datax-01'
command -v kubectl >/dev/null || die 'kubectl_not_found'
command -v sha256sum >/dev/null || die 'sha256sum_not_found'
command -v tar >/dev/null || die 'tar_not_found'
for skill in "${SKILLS[@]}"; do
  test -s "${ROOT_DIR}/payload/skills/public/${skill}/SKILL.md" || die "package_skill_missing=${skill}"
done

running_gateway_image="$(kubectl -n "${NS}" get deployment gateway -o jsonpath='{.spec.template.spec.containers[?(@.name=="gateway")].image}')"
test -n "${running_gateway_image}" || die 'gateway_image_missing'
kubectl -n "${NS}" delete pod "${POD}" --ignore-not-found=true --wait=true --timeout=120s >/dev/null
cat <<YAML | kubectl -n "${NS}" apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: ${POD}
  labels:
    app: media-generation-skill-maintenance
spec:
  nodeName: datax-01
  restartPolicy: Never
  automountServiceAccountToken: false
  containers:
    - name: maintenance
      image: ${running_gateway_image}
      imagePullPolicy: Never
      command: ["sh", "-ec"]
      args: ["sleep 3600"]
      volumeMounts:
        - name: skills
          mountPath: /mnt/skills
          readOnly: false
  volumes:
    - name: skills
      persistentVolumeClaim:
        claimName: deer-flow-skills-pvc
YAML

deadline="$(( $(date +%s) + 180 ))"
while :; do
  phase="$(kubectl -n "${NS}" get pod "${POD}" -o jsonpath='{.status.phase}' 2>/dev/null || true)"
  ready="$(kubectl -n "${NS}" get pod "${POD}" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || true)"
  reasons="$(kubectl -n "${NS}" get pod "${POD}" -o jsonpath='{range .status.containerStatuses[*]}{.state.waiting.reason}{" "}{end}' 2>/dev/null || true)"
  if test "${ready}" = 'True'; then
    echo "skill_maintenance_pod_wait=PASS pod=${POD}"
    break
  fi
  case "${phase}:${reasons}" in
    Failed:*|*:ErrImageNeverPull*|*:ImagePullBackOff*|*:InvalidImageName*|*:CreateContainerConfigError*|*:CreateContainerError*|*:CrashLoopBackOff*)
      kubectl -n "${NS}" get pod "${POD}" -o wide >&2 || true
      kubectl -n "${NS}" describe pod "${POD}" >&2 || true
      die "maintenance_pod_failed phase=${phase:-unknown} reasons=${reasons:-none}"
      ;;
  esac
  test "$(date +%s)" -lt "${deadline}" || {
    kubectl -n "${NS}" describe pod "${POD}" >&2 || true
    die 'maintenance_pod_timeout'
  }
  sleep 2
done

for skill in "${SKILLS[@]}"; do
  kubectl -n "${NS}" exec "${POD}" -c maintenance -- test -r "${PUBLIC_ROOT}/${skill}/SKILL.md"
done

if test "${MODE}" = '--preflight' || test "${MODE}" = '--status'; then
  for skill in "${SKILLS[@]}"; do
    live_sha="$(kubectl -n "${NS}" exec "${POD}" -c maintenance -- sha256sum "${PUBLIC_ROOT}/${skill}/SKILL.md" | awk '{print $1}')"
    package_sha="$(sha256sum "${ROOT_DIR}/payload/skills/public/${skill}/SKILL.md" | awk '{print $1}')"
    echo "skill=${skill} live_sha256=${live_sha} package_sha256=${package_sha}"
    if test "${MODE}" = '--status'; then
      test "${live_sha}" = "${package_sha}" || die "skill_digest_mismatch=${skill}"
    fi
  done
  echo "media_generation_skills_pvc_${MODE#--}=PASS"
  echo 'production_skill_mutated=no'
  exit 0
fi

BACKUP_TAR="${EVIDENCE_DIR}/media-generation-skills.before.tar"
if test "${MODE}" = '--backup'; then
  test ! -e "${BACKUP_TAR}" || die 'skill_backup_already_exists'
  kubectl -n "${NS}" exec "${POD}" -c maintenance -- \
    tar -C "${PUBLIC_ROOT}" -cf - \
      image-generation/SKILL.md video-generation/SKILL.md > "${BACKUP_TAR}.partial"
  test -s "${BACKUP_TAR}.partial" || die 'empty_skill_backup'
  mv -f -- "${BACKUP_TAR}.partial" "${BACKUP_TAR}"
  tar -tf "${BACKUP_TAR}" >/dev/null
  echo "media_generation_skills_backup=PASS archive=${BACKUP_TAR}"
  exit 0
fi

test -s "${BACKUP_TAR}" || die 'skill_backup_missing'
tar -tf "${BACKUP_TAR}" >/dev/null

if test "${MODE}" = '--install'; then
  for skill in "${SKILLS[@]}"; do
    source_file="${ROOT_DIR}/payload/skills/public/${skill}/SKILL.md"
    target_file="${PUBLIC_ROOT}/${skill}/SKILL.md"
    expected_sha="$(sha256sum "${source_file}" | awk '{print $1}')"
    kubectl -n "${NS}" exec -i "${POD}" -c maintenance -- \
      sh -ec 'umask 022; cat > "$1.new"; test "$(sha256sum "$1.new" | awk "{print \$1}")" = "$2"; mv -f -- "$1.new" "$1"' \
      sh "${target_file}" "${expected_sha}" < "${source_file}"
    actual_sha="$(kubectl -n "${NS}" exec "${POD}" -c maintenance -- sha256sum "${target_file}" | awk '{print $1}')"
    test "${actual_sha}" = "${expected_sha}" || die "installed_skill_digest_mismatch=${skill}"
    echo "media_generation_skill_install=PASS skill=${skill} mode=atomic_file_replace"
  done
  exit 0
fi

transaction="$(basename -- "${EVIDENCE_DIR}")"
case "${transaction}" in *[!0-9TZ]*) die 'invalid_transaction_id' ;; esac
stage="${PUBLIC_ROOT}/.media-generation-${transaction}.restore"
kubectl -n "${NS}" exec "${POD}" -c maintenance -- sh -ec 'rm -rf -- "$1"; mkdir -p -- "$1"' sh "${stage}"
cat "${BACKUP_TAR}" | kubectl -n "${NS}" exec -i "${POD}" -c maintenance -- tar -C "${stage}" -xf -
for skill in "${SKILLS[@]}"; do
  kubectl -n "${NS}" exec "${POD}" -c maintenance -- \
    sh -ec 'test -s "$1"; mv -f -- "$1" "$2"' sh \
    "${stage}/${skill}/SKILL.md" "${PUBLIC_ROOT}/${skill}/SKILL.md"
done
kubectl -n "${NS}" exec "${POD}" -c maintenance -- rm -rf -- "${stage}"
echo 'media_generation_skills_restore=PASS'
