# Wave 1 frontend 1 -> 2 expansion

This is the reviewed next step after the 0015 isolated canary PASS. It changes
only the `deer-flow/frontend` Deployment. The same ARM64 image and Next build
are used on datax-02 and ecs-zdz-pro-0015. It is not a 2000-concurrent-task
capacity certificate or a Gateway/Multiworker release.

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
sha256sum -c deerflow-capacity-wave1-expand-20260924-r1.tar.gz.sha256
test ! -e /home/cnooc/deerflow-capacity-wave1-expand-20260924-r1
tar -xzf deerflow-capacity-wave1-expand-20260924-r1.tar.gz -C /home/cnooc
cd /home/cnooc/deerflow-capacity-wave1-expand-20260924-r1
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
cd /home/cnooc/deerflow-capacity-wave1-expand-20260924-r1
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
and 0015, plus required pod anti-affinity on `app=frontend` by hostname.
Only this frontend template tolerates the cordon on 0015. The node remains
cordoned; unrelated workloads cannot use it through this release.

The old frontend remains available until a new one is ready. In-flight browser
requests on a terminating old frontend Pod may still need a retry. This package
does not cancel or auto-resume agent runs.

After rollout it requires two ready Pods on the two distinct nodes, two exact
Service endpoints, identical image IDs and Next build/action key fingerprints,
and unchanged Gateway/runner specs. Browser SSO and real user traffic acceptance
remain separate. If a check fails, it attempts to restore the original one
replica Deployment spec and verifies the original endpoint. A competing spec
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

Python synthetic tests cover the two-node scheduling shape, exact resource
version patch, stdin transport and restoration of the original spec. The
delivered archive is checked for SHA256, LF/no-BOM and Python 3.6 syntax.
These tests do not exercise a real Kubernetes scheduler or certify capacity.
