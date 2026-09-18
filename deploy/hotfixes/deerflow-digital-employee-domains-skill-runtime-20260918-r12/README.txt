DEERFLOW DIGITAL EMPLOYEE DOMAINS + SKILL RUNTIME HOTFIX R12
===========================================================

Purpose
-------

R12 replaces R1-R10 and combines three reviewed changes in one guarded
maintenance transaction:

1. Digital-employee business domains
   - fixed corporate catalog: 26 domains, 17 marked "统" and 9 marked "分";
   - removes administrator category mutation APIs and UI;
   - normalizes legacy database and file-backed category IDs;
   - patches only raw category_id in Agent configuration, preserving every
     unknown/new field that the deployed model may not understand.

2. Public research-fund-settlement Skill
   - atomically installs the complete checksummed Skill tree;
   - validates the exact group-company XLSX structure and formulas;
   - rejects path traversal, duplicate/encrypted ZIP members, oversized
     archives, excessive expansion, excessive XML parts, wrong relationships,
     dimensions, worksheet names, and merged-cell layouts;
   - proves check, build, and inspect against the bundled complete case and
     requires a nontrivial DOCX output.

3. SkillHub-installed custom Skill runtime
   - preserves safe extraction, complete-tree hashing, and atomic install;
   - mounts each user's custom Skill tree into that user's managed Sandbox;
   - binds existing Sandbox reuse to exact user, thread, legacy-Skill mode,
     and live volume-mount identity;
   - runs a real custom Skill Python script and the public financial-settlement
     check/build/inspect chain through the managed Sandbox AIO command API;
   - verifies that the target Gateway discovers the installed custom Skill at
     its canonical /mnt/skills/custom path.

Production baseline and mutation boundary
-----------------------------------------

The images are layered on the exact observed production baseline:

- Gateway: deerflow.local/deer-flow-gateway:20260914-enterprise-arm64-rc15-digitalemployee-promptfix1
- Gateway init: deerflow.local/deer-flow-gateway:20260811-enterprise-arm64-rc15-consolidated2
- Frontend: deerflow.local/deer-flow-frontend:20260914-enterprise-arm64-rc15-digitalemployee-ui2-r1
- Provisioner: deerflow.local/deer-flow-provisioner:20260817-enterprise-arm64-rc15-sandboxstorage1
- Skills utility: deerflow.local/deer-flow-skills:20260901-enterprise-arm64-rc15-researchfund1

R12 builds and distributes three new images:

- deerflow.local/deer-flow-gateway:20260918-enterprise-arm64-rc15-digitaldomains-skillruntime12
- deerflow.local/deer-flow-frontend:20260918-enterprise-arm64-rc15-digitaldomains-skillruntime12
- deerflow.local/deer-flow-provisioner:20260918-enterprise-arm64-rc15-digitaldomains-skillruntime12

The Gateway init image remains independent and unchanged. Existing Sandbox
Pods are not selected or deleted. Nginx, PPT runner, WeKnora, scheduler,
database schema, and gVisor are not mutated.

Production writes are limited to:

- Gateway, Frontend, and Provisioner Deployment image references;
- agents.config.category_id and file-backed config.yaml category_id;
- /mnt/skills/public/research-fund-settlement on deer-flow-skills-pvc.

Rollout requires zero active DeerFlow runs and zero active OA jobs. Exact prior
images, Skill tree, SQL rows, and file-backed Agent configurations are captured
before mutation. Automatic rollback restores images and the Skill tree.
Category normalization is schema-compatible and intentionally not automatically
reversed; the evidence preserves exact before-state data for incident recovery.

1. Verify and extract on datax-01
---------------------------------

cd /home/cnooc
sha256sum -c deerflow-digital-employee-domains-skill-runtime-20260918-r12.tar.gz.sha256

sudo -s
test ! -e /opt/deerflow-hotfixes/deerflow-digital-employee-domains-skill-runtime-20260918-r12
tar -xzf /home/cnooc/deerflow-digital-employee-domains-skill-runtime-20260918-r12.tar.gz \
  -C /opt/deerflow-hotfixes
cd /opt/deerflow-hotfixes/deerflow-digital-employee-domains-skill-runtime-20260918-r12
sha256sum -c PACKAGE.SHA256
chmod 0755 ./*.sh ./*.py
python3 verify-digital-employee-domains.py --root "$(pwd)"

Required verifier markers include:

digital_employee_domains_skill_runtime_r12_static_contracts=PASS
raw_agent_category_patch_contract=PASS backends=file,sql
managed_sandbox_custom_skill_execution_canary_contract=PASS
managed_sandbox_identity_reuse_guard_contract=PASS
research_fund_settlement_xlsx_security_contract=PASS
combined_automatic_rollback_contract=PASS

2. Read-only production survey
------------------------------

export KUBECONFIG=/etc/kubernetes/admin.conf
unset SSL_CERT_FILE REQUESTS_CA_BUNDLE CURL_CA_BUNDLE
set -o pipefail
./inspect-digital-employee-domains-datax-01.sh 2>&1 | \
  tee /home/cnooc/deerflow-digital-employee-domains-skill-runtime-r12-survey.log
SURVEY_RC="${PIPESTATUS[0]}"
echo "survey_exit_code=${SURVEY_RC}"
test "${SURVEY_RC}" -eq 0

The survey is read-only and fails closed unless live images, Gateway init image,
PVCs, Provisioner settings, rolling strategy, database, node state, and shared
Skill agree with the locked baseline. Target images are intentionally not
required here: all three are outputs of the following build stage.

3. Build and distribute the R12 images
--------------------------------------

Do not build or import images concurrently on the same node.

cd /opt/deerflow-hotfixes/deerflow-digital-employee-domains-skill-runtime-20260918-r12
set -o pipefail
./build-stage-datax-01.sh 2>&1 | \
  tee /home/cnooc/deerflow-digital-employee-domains-skill-runtime-r12-build.log
BUILD_RC="${PIPESTATUS[0]}"
echo "build_exit_code=${BUILD_RC}"
test "${BUILD_RC}" -eq 0

Expected final marker:

digital_employee_domains_skill_runtime_image_build=PASS images=gateway,frontend,provisioner local_oci_base=yes registry_access=none

Copy the complete directory below to the same path on datax-02 and datax-03:

/opt/deerflow-build-artifacts/20260918-deerflow-digital-domains-skillruntime12

Then, on datax-02 and datax-03 separately, wait for each import to return:

cd /opt/deerflow-build-artifacts/20260918-deerflow-digital-domains-skillruntime12
set -o pipefail
./import-digital-employee-domains-images-node.sh "$(pwd)" 2>&1 | \
  tee "/home/cnooc/deerflow-digital-employee-domains-skill-runtime-r12-import-$(hostname -s).log"
IMPORT_RC="${PIPESTATUS[0]}"
echo "image_import_exit_code=${IMPORT_RC}"
test "${IMPORT_RC}" -eq 0

Each node must end with:

digital_employee_domains_skill_runtime_image_import=PASS node=datax-0N

4. Read-only preflight on datax-01
---------------------------------

cd /opt/deerflow-hotfixes/deerflow-digital-employee-domains-skill-runtime-20260918-r12
export KUBECONFIG=/etc/kubernetes/admin.conf
unset SSL_CERT_FILE REQUESTS_CA_BUNDLE CURL_CA_BUNDLE
set -o pipefail

./run-digital-employee-domains-datax-01.sh --preflight-only 2>&1 | \
  tee /home/cnooc/deerflow-digital-employee-domains-skill-runtime-r12-preflight.log
PREFLIGHT_RC="${PIPESTATUS[0]}"
echo "preflight_exit_code=${PREFLIGHT_RC}"
test "${PREFLIGHT_RC}" -eq 0

Preflight probes all three target images on all three nodes, validates every
retained Agent configuration read-only, validates the shared Skill PVC, and
checks the target Provisioner's mount and identity contract. It does not patch
a Deployment, database row, Agent file, or Skill file. It may report PASS while
work is active, but rollout readiness requires:

active_deerflow_runs=0
active_oa_jobs=0
digital_employee_domains_preflight=PASS
maintenance_gate_ready=yes

5. Combined rollout
-------------------

Run only when the immediately preceding preflight reports maintenance_gate_ready=yes.

export DEERFLOW_DIGITAL_DOMAINS_ROLLOUT_ACK=YES
LOG=/home/cnooc/deerflow-digital-employee-domains-skill-runtime-r12-rollout.log

./run-digital-employee-domains-datax-01.sh --rollout 2>&1 | tee "${LOG}"
ROLLOUT_RC="${PIPESTATUS[0]}"
unset DEERFLOW_DIGITAL_DOMAINS_ROLLOUT_ACK
echo "rollout_exit_code=${ROLLOUT_RC}"
echo "rollout_log=${LOG}"
test "${ROLLOUT_RC}" -eq 0

Required success markers include:

deerflow_digital_employee_domains_rollout=PASS
database_agent_category_migration=PASS
file_agent_category_migration=PASS
skillhub_custom_skill_sandbox_execution_canary=PASS
skillhub_custom_skill_gateway_discovery=PASS
skillhub_custom_skill_runtime=DISCOVERED_AND_EXECUTED_THROUGH_MANAGED_SANDBOX_API
research_fund_settlement_runtime=CHECK_BUILD_INSPECT_PASS
existing_sandbox_mutation=NONE

6. Status and acceptance
------------------------

./status-digital-employee-domains-datax-01.sh 2>&1 | \
  tee /home/cnooc/deerflow-digital-employee-domains-skill-runtime-r12-status.log
STATUS_RC="${PIPESTATUS[0]}"
echo "status_exit_code=${STATUS_RC}"
test "${STATUS_RC}" -eq 0

Status must include:

research_fund_settlement_skill_runtime=CHECK_BUILD_INSPECT_PASS
skillhub_custom_skill_sandbox_identity_contract=PASS
deerflow_digital_employee_domains_status=PASS

Then verify with one ordinary user and one administrator:

- all 26 domains, 17 "统" and 9 "分", render and persist correctly;
- category-management actions remain absent;
- an existing Agent with extra configuration fields retains those fields after
  category normalization;
- a SkillHub-installed custom Skill is discovered on the next eligible run;
- research-fund-settlement accepts the locked standard workbook, rejects a
  structurally altered workbook, and creates a valid nontrivial DOCX.

7. Exact rollback
-----------------

Use only the exact rollback_state printed by rollout:

./rollback-digital-employee-domains-datax-01.sh \
  --state /opt/deerflow-hotfix-evidence/digital-employee-domains-skill-runtime-20260918-r12/TIMESTAMP/state.env

Retain the evidence directory and rollout/status logs for audit.
