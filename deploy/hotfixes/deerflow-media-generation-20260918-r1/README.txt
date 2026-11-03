DEERFLOW PLATFORM IMAGE + VIDEO GENERATION HOTFIX R1
===================================================

Scope
-----
- Adds Gateway-local `generate_image` and `generate_video` tools.
- Binds the official public image-generation and video-generation Skills to
  those tools while preserving their existing script/template files.
- Configures the supplied internal endpoints:
  - http://10.57.2.43:30008/v1/images/generations
  - http://10.57.2.43:30009/v1/videos
- Streams audio/video artifacts with FileResponse to avoid buffering a large
  MP4 in Gateway memory.

Safety boundaries
-----------------
- No production database mutation or migration.
- No WeKnora, Frontend, Provisioner, Sandbox, Nginx, or Service mutation.
- Builds one Gateway layer from the exact live Gateway image observed on
  datax-01; the build records that image and rollout refuses a changed base.
- The target image must be imported and runtime-probed on datax-01/02/03.
- Preflight is read-only with respect to production data/workloads and may pass
  while work is active; it reports maintenance_gate_ready=no.
- Rollout requires zero active DeerFlow runs and zero active OA jobs and checks
  the gate again after backup and immediately before the Gateway patch.
- The two Skill instruction files are backed up and replaced atomically.
- Any post-mutation failure or signal attempts exact Gateway image/env and Skill
  instruction rollback from checksum-verified evidence.

Build on datax-01
-----------------
  export KUBECONFIG=/etc/kubernetes/admin.conf
  unset SSL_CERT_FILE REQUESTS_CA_BUNDLE CURL_CA_BUNDLE
  chmod 0755 ./*.sh ./*.py
  set -o pipefail
  ./build-stage-datax-01.sh 2>&1 | tee /home/cnooc/deerflow-media-generation-r1-build.log

Copy ARTIFACT_DIR to datax-02 and datax-03, then on each node run:
  sudo -s
  cd <copied-artifact-directory>
  chmod 0755 import-media-generation-image-node.sh
  ./import-media-generation-image-node.sh "$(pwd)"

Preflight on datax-01
---------------------
  cd /opt/deerflow-hotfixes/deerflow-media-generation-20260918-r1
  export KUBECONFIG=/etc/kubernetes/admin.conf
  unset SSL_CERT_FILE REQUESTS_CA_BUNDLE CURL_CA_BUNDLE
  unset DEERFLOW_MEDIA_GENERATION_ROLLOUT_ACK
  set -o pipefail
  ./run-media-generation-datax-01.sh --preflight-only 2>&1 | \
    tee /home/cnooc/deerflow-media-generation-r1-preflight.log

Rollout only after preflight PASS and maintenance_gate_ready=yes
---------------------------------------------------------------
  export DEERFLOW_MEDIA_GENERATION_ROLLOUT_ACK=YES
  set -o pipefail
  ./run-media-generation-datax-01.sh --rollout 2>&1 | \
    tee /home/cnooc/deerflow-media-generation-r1-rollout.log
  RC="${PIPESTATUS[0]}"
  unset DEERFLOW_MEDIA_GENERATION_ROLLOUT_ACK
  echo "rollout_exit_code=${RC}"
  test "${RC}" -eq 0

Status
------
  ./status-media-generation-datax-01.sh 2>&1 | \
    tee /home/cnooc/deerflow-media-generation-r1-status.log

Acceptance
----------
1. Start a new ordinary DeerFlow chat and ask:
   请生成一张海上风电平台日出的写实图片，1024x1024。
2. Start another new chat (or use `/video-generation`) and ask:
   请生成一段海上平台上空缓慢航拍的短视频，画面稳定，832x480，24fps。
3. Confirm the first result opens as an image and the second opens/plays as an
   MP4 artifact. A pending video is normal; the agent must reuse the returned
   job_id and must not create a duplicate submission.

Rollback
--------
Use the exact rollback_state printed by a successful rollout:
  ./rollback-media-generation-datax-01.sh --state <state.env>
