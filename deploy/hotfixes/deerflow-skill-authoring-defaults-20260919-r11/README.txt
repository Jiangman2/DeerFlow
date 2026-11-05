DeerFlow Skill Authoring, Unified Model, Defaults, Mail Agent, and WeKnora Labels Hotfix R11
===============================================================================

Purpose
-------
This is an independent hotfix. It is intentionally separate from the media-
generation R1 package, but its Gateway image is built on top of the successfully
deployed media-generation1 Gateway image so image/video generation remains
present.

R11 supersedes R1 through R10. The R10 rollout successfully applied every
bounded business mutation and rolled out every intended workload, but its final
anonymous WeKnora backend check required HTTP 200 from the authentication-
protected `/api/v1/system/info` route. Production correctly returned HTTP 401,
which proves the Service and application HTTP stack are live but caused a false
failure and successful automatic exact rollback. R11 accepts only HTTP 200 or
the expected anonymous HTTP 401 for that one route; connection, timeout, 5xx,
and every other status still fail closed. R11 otherwise preserves the reviewed
R10 business payload, images, mutation boundaries, and rollback behavior.

The R9 rollout reached live validation, then a
silent combined file check returned nonzero and triggered a successful automatic
exact rollback. R11 retains the R2 Python 3.7,
JSON-stdin, direct TLS, and probe fixes, and adapts the rollout to the expanded
production topology observed on 2026-09-19. In particular, it discovers and
probes every scheduler-eligible image node, requires both Gateway replicas and
their two ready endpoints, uses the live NFS RWX Skills PVC instead of the old
local RWO PVC, and preserves the PPT Runner execution-lease `a+` repair.

R2 must not be rolled out: its Skill maintenance Pod mounts the retired local
PVC, its image probe omits the new Gateway node, and its rollback path rejects
its own evidence directory. R11 corrects those defects and also uses the Node.js
runtime already present in the production Frontend image to apply its source
patch; the real image does not include Python. R11 additionally verifies the
PPT Runner repair through the runtime behavior of `_open_lock_file()` rather
than looking for the open mode in the separate flock helper, and makes the
image importer honor a relocated transfer directory. The image payloads,
three target image names, and artifact directory are intentionally unchanged,
so the already-built and distributed OCI artifacts are reused. R11 also replaces
the brittle byte-for-byte DeerFlow model-block match with a fail-closed structural
match for the single named `deepseek-v4-flash` item. It accepts the exact live
2026-09-19 field order and `max_tokens: 32768`, rejects unknown or duplicate
fields, and emits the same reviewed canonical target block.
R11 also keeps the WeKnora Unicode asset probes literal (`grep -Fq`): basic
regular-expression grep consumes the backslash in JavaScript `\uXXXX` escapes
and caused the R6 immutable-image probe to reject a correct image.
R11 retains the R8 correction for the R7 rollout failure: production `skill_search_document`
relates to `skill` through `skill_id` and has no duplicated `slug`, namespace,
or owner columns. Both count and update SQL now resolve the authoritative slug,
namespace, and owner through `skill`; preflight also verifies all 16 required
live columns before any backup or mutation. R11 also corrects the only R8 rollout
failure: `skill.updated_by` is a foreign key to `user_account`, so localization
preserves its existing valid value and updates only `summary` and `updated_at`.
Static contracts reject the former synthetic audit identifier.

R11 removes the R9 marker-only restart of `weknora-app`. Its model change is an
atomic database-row update and does not require a Pod-template mutation; the R9
restart created a real interval of upstream connection-refused 502 responses.
The PPT Runner restart remains intentional because it must reload the updated
Secret-backed model credential. R11 also replaces opaque chained live checks
with named checks and bounded WeKnora frontend/backend proxy retries, so any
future failure identifies the exact contract instead of returning only exit 1.

The package makes six bounded changes:

1. Replaces the ambiguous skill_manage authoring interface with action-specific
   skill_create/edit/patch/delete/write_file/remove_file schemas. Arguments such
   as content and path are structurally required where applicable.
2. Makes Flash the default only when a user has no saved mode preference.
3. Localizes exactly 17 official SkillHub built-in descriptions to Chinese.
4. Unifies DeepSeek-V4-Flash across DeerFlow, WeKnora, and the PPT Master
   Responses relay using the operator-supplied configuration file. The existing
   deer-flow-env.DeepSeek_V4_API_KEY field is updated in place; no second Secret
   is created. The API key is never packaged or printed.
5. Adds the sixth fixed external digital employee:
   邮件智能整理助手
   https://cnoocclaw.cnooc.com.cn/yjzs/
6. Renames only the two WeKnora left-sidebar navigation entries identified by
   their stable data-guide attributes: 新对话 becomes 知识问答, and 智能体 becomes
   自定义问答. Other chat, agent, and knowledge-base text is unchanged.

Prerequisites
-------------
- Run only on datax-01 as root with KUBECONFIG=/etc/kubernetes/admin.conf.
- The media-generation R1 rollout and status check must already be PASS. The
  live Gateway image must be:
  deerflow.local/deer-flow-gateway:20260918-enterprise-arm64-rc15-media-generation1
- Upload the exact operator model file separately to:
  /home/cnooc/DeepSeek-V4-Flash-stream-thinking-on-off.py
  Its required SHA-256 is locked inside extract_model_config.py. Set mode 0600.
  Never copy that file into this package or a Git repository.
- The live WeKnora Frontend image must be:
  deerflow.local/weknora-ui:0.8.0-enterprise-sso2-r5-arm64-20260913
- The target Gateway image must be imported on every Ready schedulable node
  labelled `workload=gateway`, currently datax-01 and ecs-zdz-pro-0022.
- The target Frontend and WeKnora Frontend images must be imported on every
  Ready schedulable untainted general node, currently datax-01, datax-02, and
  datax-03.
- Gateway must remain two replicas with RollingUpdate maxUnavailable=0, and
  Gateway, PPT Runner, and Provisioner must agree on the NFS RWX user-data and
  Skills PVCs.
- The live PPT Runner must still expose the repaired execution-lease open mode
  `a+`; R11 does not replace or remove its ConfigMap overlay.
- Formal rollout requires zero pending/running DeerFlow runs and zero active OA
  jobs. Preflight remains non-mutating and may run while work is active.

Build and image distribution
----------------------------
On datax-01 after package verification:

  ./build-stage-datax-01.sh 2>&1 | tee /home/cnooc/deerflow-skill-authoring-defaults-r11-build.log

Copy the complete artifact directory printed by the build to datax-02 and
datax-03, then run on each of those nodes:

  ./import-skill-authoring-defaults-images-node.sh "$(pwd)" --all

Copy at least the Gateway OCI archive, its checksum, IMAGE_LOCKS.env, and the
import script to ecs-zdz-pro-0022, then run there:

  ./import-skill-authoring-defaults-images-node.sh "$(pwd)" --gateway-only

The build already imports all three target images on datax-01.

Preflight
---------
Preflight validates package digests, the locked model configuration file, live
streaming calls with thinking both off and on, scheduler-eligible immutable images,
the expanded two-replica Gateway topology, NFS RWX storage agreement, the live
PPT Runner execution-lease repair,
the exact WeKnora sidebar selectors and labels, Skill Creator PVC compatibility,
the exact 17 SkillHub rows, the single WeKnora
model row, and the active-work gate. The live model calls consume only a small
bounded inference request; they do not mutate production configuration.

  export KUBECONFIG=/etc/kubernetes/admin.conf
  unset SSL_CERT_FILE REQUESTS_CA_BUNDLE CURL_CA_BUNDLE
  set -o pipefail
  ./run-skill-authoring-defaults-datax-01.sh --preflight-only 2>&1 | \
    tee /home/cnooc/deerflow-skill-authoring-defaults-r11-preflight.log

Required conclusion:

  deerflow_skill_authoring_defaults_preflight=PASS
  maintenance_gate_ready=yes

Rollout
-------
Rollout creates a root-only evidence directory and backs up the exact Gateway,
Frontend, WeKnora Frontend, PPT Runner, ConfigMap, shared Secret field, WeKnora model row,
SkillHub rows, and Skill Creator file before the first mutation. It repeats the
active-work check after backup and immediately before workload changes. The
WeKnora backend Deployment is availability-checked but is not mutated or
restarted.

  export DEERFLOW_SKILL_AUTHORING_DEFAULTS_ACK=YES
  set -o pipefail
  LOG=/home/cnooc/deerflow-skill-authoring-defaults-r11-rollout.log
  ./run-skill-authoring-defaults-datax-01.sh --rollout 2>&1 | tee "${LOG}"
  RC="${PIPESTATUS[0]}"
  unset DEERFLOW_SKILL_AUTHORING_DEFAULTS_ACK
  echo "rollout_exit_code=${RC}"
  test "${RC}" -eq 0

Status
------

  ./status-skill-authoring-defaults-datax-01.sh 2>&1 | \
    tee /home/cnooc/deerflow-skill-authoring-defaults-r11-status.log

Rollback
--------
Use the exact rollback_state printed by a successful rollout or by an automatic
rollback diagnostic:

  ./rollback-skill-authoring-defaults-datax-01.sh --state \
    /opt/deerflow-hotfix-evidence/deerflow-skill-authoring-defaults-20260919-r11/<UTC>/state.env

Rollback restores the prior shared key without printing it, the prior DeerFlow
model config, the exact WeKnora model parameters, all 17 prior SkillHub
descriptions, the Skill Creator file, all three images, and the previous PPT relay
URL. Existing Sandbox Pods are not modified by this package.

Residual risk
-------------
The two Gateway replicas still use the existing in-memory stream bridge and do
not enable run-ownership heartbeat. R11 does not attempt that separate
architecture change. This is non-blocking only because rollout requires zero
pending/running DeerFlow work and zero active OA jobs. Do not override that
gate. NFS remains a single-server dependency and is likewise unchanged.
