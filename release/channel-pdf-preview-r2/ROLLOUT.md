# R2 explicit maintenance rollout

This separate orchestration helper does not rebuild or change the immutable R2
package. Only `gateway` and `frontend` Deployment main-container image fields are
changed permanently. Gateway's rollout strategy is temporarily replaced with
`Recreate` and restored after verification. No replica/worker count, Secret,
ConfigMap, scheduling, resources, probes, init container, storage, PPT Runner,
Provisioner, Sandbox, Redis or WeKnora configuration is patched. No direct
database mutation is performed; existing application startup may still perform
its normal bookkeeping.

Kubernetes documents that `Recreate` upgrades wait for the old revision Pods to
terminate before creating new ones. This prevents a surge of two Gateway
processes for this release, but causes a real availability gap. It does not
guarantee exclusivity against unrelated controllers or manual Pod deletion.
Reference: https://kubernetes.io/docs/concepts/workloads/controllers/deployment/#recreate-deployment

## Prerequisites

- Run as root on datax-01. Use a quiet maintenance window: stop submitting page
  and 海能 Work messages during the release. A zero-row snapshot does not lock
  admission and cannot guarantee that another source will not submit new work.
- The four candidate tests must already have passed. Supply their unique
  evidence directory from this task. Evidence older than six hours is rejected.
- Keep the existing `deerflow-channel-pdf-r2-validate-candidates.py` at
  `/home/cnooc/`; the rollout helper verifies its fixed SHA256 before importing
  constants/check functions, without launching candidate test Pods again.
- Gateway remains pinned to ECS with one replica; Frontend remains one replica.
  Both exact base images and both candidate images have already been preloaded
  on their eligible nodes. No image import, download or build is performed here.
- No concurrent hotfix, scaling or configuration edit is permitted. UID and
  whole-spec JSON Patch test operations fail closed on concurrent changes.

## Execute

Set `CHANNEL_PDF_R2_ACK=SHORT_OUTAGE` and run the new script with `--rollout
--candidate-evidence /opt/deerflow-hotfix-evidence/channel-pdf-r2-candidate-...`.
Capture the script and tee statuses immediately. No force-active-work option
exists. Active `pending`/`running` rows are checked in a read-only transaction
before backup/health checks and again immediately before the first mutation.

The script verifies the live baseline against the recorded candidate baseline,
node conditions, HPA absence, Service routes, original image identities and
existing Service HTTP health. Backups are created in a unique root-only release
evidence directory and protected by a checksum manifest. They can contain
inline deployment environment/configuration: do not paste or upload raw backup
JSON or protected diagnostic logs.

Gateway is switched with Recreate. The script waits for the new sole Pod,
owner chain, exact runtime digest, Deployment readiness, no old terminating
Pods, and EndpointSlice references to the new Pod. Two Service HTTP success
samples plus all modified source hashes are required. Frontend is then switched
with its existing strategy; HTTP checks cover the actual new logo and a built
JavaScript resource through the production Service. Gateway's original strategy
is restored without changing its Pod template. Final health is checked again.

Any post-patch failure triggers a best-effort rollback to the recorded exact
specs and images, with Gateway restored first and without overlapping Gateway
revisions. Rollback itself can fail (missing images, infrastructure failure,
concurrent changes); success is reported only after real health checks. A
concurrent unowned spec edit is not overwritten. Interrupted SSH sessions or
SIGKILL can defeat automatic cleanup/recovery: keep the printed evidence path.

## Manual recovery

If a manual rollback is needed, explicitly set `CHANNEL_PDF_R2_ACK=ROLLBACK` and
run the same helper with `--rollback <printed release evidence directory>`.
This also causes a maintenance gap and can interrupt tasks that started after
release. It does not replay or resume interrupted tasks. Never use an arbitrary
`kubectl rollout undo` revision or replace the deployment from a local manifest.

## Acceptance boundary

Local mocked tests verify orchestration, atomic field scope, active-work gates,
backup integrity, HTTP-script syntax, uncertainty recovery and conflict-safe
rollback. Real production execution is not claimed by these tests. Successful
release health still requires authenticated browser acceptance: reopen the
original generated PDF, confirm 海能 Work text/icon and connect instructions,
send a normal chat/IM message, and confirm unrelated document functionality.
