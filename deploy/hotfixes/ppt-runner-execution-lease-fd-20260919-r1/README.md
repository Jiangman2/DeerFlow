# PPT Runner execution-lease FD hotfix R1

This package repairs the production `/ppt-master` failure:

`OSError: [Errno 9] Bad file descriptor`

The Runner acquires a shared POSIX `flock` while a PPT Agent Host turn is in
progress. The deployed provider opens the lease file with Python mode `a`,
which is write-only. Linux rejects `LOCK_SH` on that descriptor. The target
mode is `a+`: readable, writable, append-only, and non-truncating.

## Scope

- Reads the exact provider source from the live `ppt-master-runner` Pod.
- Fails closed unless the shared/exclusive lease contract and the exact `a`
  opener are present.
- Replaces only that mode with `a+` and compiles the result.
- Mounts the patched module into `ppt-master-runner` through an immutable,
  content-addressed ConfigMap.
- Restarts only `ppt-master-runner`.
- Preserves its image, init containers, existing migration/startup overlays,
  probes, resources, scheduling, NFS PVCs and subPaths.
- Does not restart or patch Gateway, Provisioner, Nginx, Frontend or Sandboxes.
- Does not mutate PostgreSQL, WeKnora or Skill data.

Ordinary active DeerFlow/OA work is reported but does not block this isolated
Runner restart. A pending/running `ppt-master-agent-host` run blocks rollout,
because restarting its owning Runner would interrupt that PPT task.

## Current production survey

Run on `datax-01`:

```bash
./inspect-production-state-datax-01.sh 2>&1 | \
  tee /home/cnooc/ppt-runner-production-state-20260919.log
```

The survey is read-only. It reports live nodes, scheduling, workloads,
EndpointSlices, PVC/PV/NFS topology, exact Runner/Gateway mounts and images,
selected non-secret runtime settings, database run state, Sandboxes, health,
the live lock opener, and recent Runner errors. It also flags a dual-Gateway
deployment using the in-memory stream bridge or disabled ownership heartbeats;
that is a separate multi-instance hardening item and is not changed here.

## Preflight

```bash
./run-ppt-runner-execution-lease-fd-datax-01.sh --preflight-only 2>&1 | \
  tee /home/cnooc/ppt-runner-execution-lease-fd-r1-preflight.log
```

Preflight makes no workload or database changes. It creates and removes one
uniquely named temporary file under `.sandbox-execution-leases` to prove that
the real shared NFS path enforces a cross-process shared-vs-exclusive lock.
Evidence is retained under `/opt/deerflow-hotfix-evidence`.

## Rollout

Only when preflight reports `maintenance_gate_ready=yes`:

```bash
export PPT_RUNNER_EXECUTION_LEASE_FD_ACK=YES
./run-ppt-runner-execution-lease-fd-datax-01.sh --rollout 2>&1 | \
  tee /home/cnooc/ppt-runner-execution-lease-fd-r1-rollout.log
RC="${PIPESTATUS[0]}"
unset PPT_RUNNER_EXECUTION_LEASE_FD_ACK
echo "rollout_exit_code=${RC}"
test "${RC}" -eq 0
```

The script performs a final active-PPT race check immediately before patching.
If startup or validation fails, it resourceVersion-checks the live Runner spec
and automatically restores the exact pre-rollout Pod template. The immutable
ConfigMap is retained as evidence.

## Status and acceptance

```bash
./status-ppt-runner-execution-lease-fd-datax-01.sh 2>&1 | \
  tee /home/cnooc/ppt-runner-execution-lease-fd-r1-status.log
```

Status verifies `a+`, the real NFS flock behavior, Runner/Gateway health and
absence of a new `Bad file descriptor`. Final acceptance still requires one
new real `/ppt-master` request that reaches the Agent Host, produces a current
PPTX, produces its matching preview, and returns both through DeerFlow. The old
failed run remains historical evidence and is not rewritten.

## Operator rollback

Use the exact `rollback_state` printed by a successful rollout, with zero
active PPT runs:

```bash
export PPT_RUNNER_EXECUTION_LEASE_FD_ROLLBACK_ACK=YES
./rollback-ppt-runner-execution-lease-fd-datax-01.sh \
  /opt/deerflow-hotfix-evidence/ppt-runner-execution-lease-fd-20260919-r1.XXXXXXXX
unset PPT_RUNNER_EXECUTION_LEASE_FD_ROLLBACK_ACK
```

## Explicit non-goals

This incident patch does not implement distributed Sandbox ownership, switch
the SSE stream bridge to Redis, enable run ownership heartbeats, migrate NFS,
change replica counts, or modify Agent Host port 8788 behavior. The observed
failure occurs before the Runner attempts the Agent Host transport. A real PPT
acceptance run is still required to expose any later independent 8788 issue.
