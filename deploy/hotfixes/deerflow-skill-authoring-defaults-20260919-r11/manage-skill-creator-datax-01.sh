#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:-}"
EVIDENCE_DIR="${2:-}"
NS=deer-flow
POD=skill-authoring-defaults-maintenance
TARGET=/mnt/skills/public/skill-creator/SKILL.md

die() { echo "skill_creator_pvc=FAIL detail=$*" >&2; exit 1; }
cleanup() { kubectl -n "${NS}" delete pod "${POD}" --ignore-not-found=true --wait=false >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM

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
test "$(id -u)" = '0' || die 'must_run_as_root'
test "$(hostname -s)" = 'datax-01' || die 'must_run_on_datax-01'

image="$(kubectl -n "${NS}" get deployment gateway -o jsonpath='{.spec.template.spec.containers[?(@.name=="gateway")].image}')"
test -n "${image}" || die 'gateway_image_missing'
skills_pvc="$(kubectl -n "${NS}" get deployment gateway -o json | python3 -c '
import json,sys
d=json.load(sys.stdin); spec=d["spec"]["template"]["spec"]
volumes={v["name"]:v for v in spec.get("volumes") or []}
mounts=[]
for container in (spec.get("initContainers") or []) + (spec.get("containers") or []):
    for mount in container.get("volumeMounts") or []:
        if mount.get("mountPath") == "/app/skills": mounts.append(mount["name"])
assert mounts and len(set(mounts)) == 1, mounts
claim=(volumes[mounts[0]].get("persistentVolumeClaim") or {}).get("claimName")
assert claim, volumes[mounts[0]]
print(claim)
')"
provisioner_skills_pvc="$(kubectl -n "${NS}" get deployment provisioner -o json | python3 -c '
import json,sys
d=json.load(sys.stdin); c=next(x for x in d["spec"]["template"]["spec"]["containers"] if x["name"]=="provisioner")
rows=[x.get("value","") for x in c.get("env") or [] if x.get("name")=="SKILLS_PVC_NAME"]
assert len(rows)==1 and rows[0], rows
print(rows[0])
')"
test "${skills_pvc}" = "${provisioner_skills_pvc}" || die "skills_pvc_mismatch gateway=${skills_pvc} provisioner=${provisioner_skills_pvc}"
kubectl -n "${NS}" get pvc "${skills_pvc}" -o json | python3 -c '
import json,sys
d=json.load(sys.stdin)
assert d.get("status",{}).get("phase")=="Bound", d.get("status")
assert "ReadWriteMany" in (d.get("spec",{}).get("accessModes") or []), d.get("spec",{}).get("accessModes")
'
echo "skill_creator_live_skills_pvc=${skills_pvc} access_mode=RWX"
kubectl -n "${NS}" delete pod "${POD}" --ignore-not-found=true --wait=true --timeout=120s >/dev/null
cat <<YAML | kubectl -n "${NS}" apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: ${POD}
spec:
  nodeName: datax-01
  restartPolicy: Never
  automountServiceAccountToken: false
  containers:
    - name: maintenance
      image: ${image}
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
        claimName: ${skills_pvc}
YAML
deadline="$(( $(date +%s) + 180 ))"
while :; do
  ready="$(kubectl -n "${NS}" get pod "${POD}" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || true)"
  reasons="$(kubectl -n "${NS}" get pod "${POD}" -o jsonpath='{range .status.containerStatuses[*]}{.state.waiting.reason}{" "}{end}' 2>/dev/null || true)"
  test "${ready}" = 'True' && break
  case "${reasons}" in *ErrImageNeverPull*|*ImagePullBackOff*|*CreateContainerError*|*CrashLoopBackOff*) kubectl -n "${NS}" describe pod "${POD}" >&2 || true; die "maintenance_pod_failed=${reasons}" ;; esac
  test "$(date +%s)" -lt "${deadline}" || { kubectl -n "${NS}" describe pod "${POD}" >&2 || true; die 'maintenance_pod_timeout'; }
  sleep 2
done
kubectl -n "${NS}" exec "${POD}" -c maintenance -- test -s "${TARGET}"

if test "${MODE}" = '--preflight'; then
  kubectl -n "${NS}" exec -i "${POD}" -c maintenance -- python3 - --path "${TARGET}" --check < "${ROOT_DIR}/patch_skill_creator.py"
  echo 'skill_creator_pvc_preflight=PASS'
  echo 'production_skill_mutated=no'
  exit 0
fi

BACKUP="${EVIDENCE_DIR}/skill-creator.before.tar"
if test "${MODE}" = '--backup'; then
  test ! -e "${BACKUP}" || die 'backup_already_exists'
  kubectl -n "${NS}" exec "${POD}" -c maintenance -- tar -C /mnt/skills/public -cf - skill-creator/SKILL.md > "${BACKUP}.partial"
  test -s "${BACKUP}.partial" || die 'empty_backup'
  mv -f -- "${BACKUP}.partial" "${BACKUP}"
  tar -tf "${BACKUP}" >/dev/null
  echo "skill_creator_backup=PASS archive=${BACKUP}"
  exit 0
fi

if test "${MODE}" = '--install'; then
  test -s "${BACKUP}" || die 'backup_missing'
  kubectl -n "${NS}" exec -i "${POD}" -c maintenance -- python3 - --path "${TARGET}" --apply < "${ROOT_DIR}/patch_skill_creator.py"
  echo 'skill_creator_install=PASS'
  exit 0
fi

if test "${MODE}" = '--status'; then
  kubectl -n "${NS}" exec "${POD}" -c maintenance -- grep -q 'skill_write_file(name="my-skill"' "${TARGET}"
  kubectl -n "${NS}" exec "${POD}" -c maintenance -- sh -ec '! grep -q "skill_manage(action=" "$1"' sh "${TARGET}"
  echo 'skill_creator_pvc_status=PASS'
  exit 0
fi

test -s "${BACKUP}" || die 'backup_missing'
stage=/mnt/skills/public/.skill-creator-hotfix-restore
kubectl -n "${NS}" exec "${POD}" -c maintenance -- sh -ec 'rm -rf -- "$1"; mkdir -p -- "$1"' sh "${stage}"
cat "${BACKUP}" | kubectl -n "${NS}" exec -i "${POD}" -c maintenance -- tar -C "${stage}" -xf -
kubectl -n "${NS}" exec "${POD}" -c maintenance -- mv -f -- "${stage}/skill-creator/SKILL.md" "${TARGET}"
kubectl -n "${NS}" exec "${POD}" -c maintenance -- rm -rf -- "${stage}"
echo 'skill_creator_restore=PASS'
