# Wave 1 frontend 1 -> 2 expansion R2

This is the reviewed next step after the 0015 isolated canary PASS. It changes
only the `deer-flow/frontend` Deployment. The same ARM64 image and Next build
are used on datax-02 and ecs-zdz-pro-0015. It is not a 2000-concurrent-task
capacity certificate or a Gateway/Multiworker release.

R1 failed because required anti-affinity matched old AND new frontend revisions
on the only two eligible nodes. One new Pod started on 0015 but the other could
not coexist with the old Pod on datax-02. With two desired replicas and
maxUnavailable=0, the controller could not remove the old healthy Pod to make
room. Production events explicitly reported pod anti-affinity rejection.

R2 scopes hard anti-affinity to the same `pod-template-hash`, using Kubernetes'
`matchLabelKeys` field. Same-revision replicas remain on separate nodes while
old/new revisions may coexist temporarily. The server-side dry-run must preserve
this field exactly or the script blocks. No rebuild, image re-export, image
re-import, node uncordon, Gateway rollout, or dependency installation is needed.
Use R2 only after R1 has converged back to the exact original healthy baseline.

R2 also replaces `kubectl rollout status` with bounded current-generation,
replica, Pod and endpoint checks. A stale ProgressDeadlineExceeded condition
from the failed revision is not treated as proof that rollback failed. Progress
prints safe counts/conditions; failure diagnostics retain safe event categories,
never raw event messages, application logs, environment values or secrets.

## Baseline and expiry

The immutable preparation plan is
`/home/cnooc/wave1-cluster-20260924T104413Z.json`, the preload report is
`/home/cnooc/wave1-preload-0015-20260924T105418Z.json`, and the actual canary
evidence is
`/opt/deerflow-hotfix-evidence/capacity-wave1-canary-20260924T111730Z-7dff55d0`.
The package checks the canary result, temporary cleanup, original and final
cluster snapshots, Next runtime and matching hashes for three HTTP routes.
The plan/preload reports expire six hours after collection: the plan's
deadline is 2026-09-24 16:44 UTC / 2026-09-25 00:44 Beijing time. A fresh
preflight expires after 30 minutes. If they expire, stop and collect a new
read-only baseline; do not edit the JSON timestamps.

## Install and read-only preflight on datax-01

Upload this package and its `.sha256` to `/home/cnooc/`. Run as root, and
never overwrite an existing extraction directory:

```bash
cd /home/cnooc
sha256sum -c deerflow-capacity-wave1-expand-20260924-r2.tar.gz.sha256
test ! -e /home/cnooc/deerflow-capacity-wave1-expand-20260924-r2
tar -xzf deerflow-capacity-wave1-expand-20260924-r2.tar.gz -C /home/cnooc
cd /home/cnooc/deerflow-capacity-wave1-expand-20260924-r2
sha256sum -c PACKAGE.SHA256
sudo python3 -B ./expand-wave1.py preflight \
  --plan /home/cnooc/wave1-cluster-20260924T104413Z.json \
  --preload /home/cnooc/wave1-preload-0015-20260924T105418Z.json
```

This prints `frontend_expansion_preflight=PASS` and the exact path of a small
report under `/opt/deerflow-hotfix-evidence/`. It performs a server-side
Deployment patch dry-run without persisting it.

## Rollout

Set `PREFLIGHT_REPORT` to the exact path just printed. Avoid overlapping
frontend maintenance while this runs:

```bash
cd /home/cnooc/deerflow-capacity-wave1-expand-20260924-r2
sudo python3 -B ./expand-wave1.py rollout \
  --plan /home/cnooc/wave1-cluster-20260924T104413Z.json \
  --preload /home/cnooc/wave1-preload-0015-20260924T105418Z.json \
  --preflight-report "$PREFLIGHT_REPORT" \
  --ack FRONTEND_TWO_REPLICAS
```

The script rechecks baseline, target health and canary evidence, repeats the
server dry-run, and sends one JSON patch with Deployment UID and resourceVersion
tests. It changes `spec.replicas` to 2 and the rolling policy to maxSurge 1,
maxUnavailable 0. New Pods have required node affinity for exactly datax-02
and 0015, plus required pod anti-affinity on `app=frontend` by hostname, scoped
to the incoming Pod's revision with `matchLabelKeys: [pod-template-hash]`.
Only this frontend template tolerates the cordon on 0015. The node remains
cordoned; unrelated workloads cannot use it through this release.

The rolling policy permits at most three non-terminating desired replicas
(two plus one surge). A terminating Pod can briefly consume additional
resources. The new revision converges to exactly one Pod per selected node;
the original ReplicaSet retains its old scheduling rules during the transition.
Allow for old and new frontend containers temporarily sharing datax-02.
Each container retains its original CPU/memory requests and limits.
The old frontend is retained by the controller until its availability budget
permits removal; this is not a guarantee against unrelated node/Pod failures.
In-flight browser
requests on a terminating old frontend Pod may still need a retry. This package
does not cancel or auto-resume agent runs.

After rollout it requires two ready Pods on the two distinct nodes, two exact
Service endpoints, identical image IDs and Next build/action key fingerprints,
and unchanged Gateway/runner specs. Browser SSO and real user traffic acceptance
remain separate. If a check fails, it attempts to restore the original one
replica Deployment spec and verifies its image, Next runtime and single endpoint.
Restoring the original spec also restores its original scheduler freedom; if the
old Pod has already been removed, its replacement is not guaranteed to return
to datax-02. A competing spec
change blocks automatic restoration and requires review. No force Pod deletion
or node changes are used.

## Explicit restoration

If a later browser check fails after a PASS, use the same preflight report:

```bash
sudo python3 -B ./expand-wave1.py rollback \
  --plan /home/cnooc/wave1-cluster-20260924T104413Z.json \
  --preload /home/cnooc/wave1-preload-0015-20260924T105418Z.json \
  --preflight-report "$PREFLIGHT_REPORT" \
  --ack FRONTEND_RESTORE_ONE
```

The restoration is allowed only while the Deployment UID and exact expected
two replica spec still match. Send any `NOT_PASSED`, `BLOCKED` or
`automatic_rollback=INCOMPLETE_*` output for review before another operation.

## Local verification

Python synthetic tests reproduce the R1 deadlock with a two-node label/scheduling
model and the rolling controller's availability formula, then verify the R2
path. They also cover exact resourceVersion patching, stdin transport, redacted
progress, stale-generation rollback, bounded pending waits, endpoint convergence
and restoration of the original spec. The
delivered archive is checked for SHA256, LF/no-BOM and Python 3.6 syntax.
These tests do not exercise a real Kubernetes scheduler or certify capacity.

Official references:
- https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#matchlabelkeys
- https://github.com/kubernetes/kubernetes/blob/v1.36.2/pkg/controller/deployment/rolling.go
