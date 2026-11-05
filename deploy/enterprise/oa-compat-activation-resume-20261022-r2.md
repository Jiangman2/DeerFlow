# Compatibility activation: no-op r1 failure and r2 continuation

Historical r2 record: field execution passed node coverage but stopped on active
tasks before any PATCH. The operator then explicitly authorized interruption;
see `oa-compat-activation-resume-20261022-r4.md` for the current continuation.

The operator executed r1 with `--tasks-drained`. Its report stopped at
`NODE_MATRIX_CHANGED`, before any PATCH or database inspection. The private
report is 408 bytes with SHA256
`8a36ddc5375a5a49d64d03f1745093f30a479842e5098718998d4f17fba81c41`, at
`/var/lib/portal-oa-compat-activation-20261022-r1/REPORT.json`.
It records `patches=[]`, `production_workload_mutation=false`, and no schema
migration. Preserve this directory and report.

The subsequent operator-provided node table contains 18 nodes, including every
one of the 15 preloaded nodes with its pinned UID, Ready=True and
DiskPressure=False. Three nodes were omitted from the preload matrices because
they do not host these workloads:

| Node | UID | Observed scheduling restriction |
| --- | --- | --- |
| ecs-zdz-pro-0000 | 42686547-6db2-41b0-be47-ac75c6c070ff | unschedulable=true and node.kubernetes.io/unschedulable:NoSchedule |
| ecs-zdz-pro-0005 | e977432d-e039-4914-b0ab-0f9f871e36b5 | unschedulable=true and node.kubernetes.io/unschedulable:NoSchedule |
| ecs-zdz-pro-0008 | 5eb3e2cf-053f-4158-b118-c3c3dde06d8f | dedicated=gateway-redis:NoSchedule |

The r1 check incorrectly equated the union of the role-specific preload
matrices with the entire cluster. The correction retains all original
Deployment and image pins and separately verifies conservative scheduling
coverage. Gateway's pinned required hostname affinity restricts it to its five
preloaded nodes. Runner has no node selector or required affinity and does not
tolerate either the extra nodes' hard taints or the dedicated Gateway taint.
The observed workload label on datax-01 alone does not make it a Gateway target.

## r2 behavior

`package-oa-compat-activation.py` now embeds `oa-compat-node-gate.py` and generates
`activate-portal-oa-schema-compatibility-20261022-r2.py`. The original generated
r1 artifact is unchanged (SHA256
`946d112ba0479d8049f6928a25d4674634f8eef28c32d4272c1844d77c88face`).

r2 requires the exact, root-owned, regular, non-symlink r1 failure report and
checks its no-op fields before claiming a separate r2 once directory. It pins
all 18 observed node identities; missing, unexpected, or replaced nodes still
require review. It preserves the 15 preloaded nodes' health checks and requires
every potentially schedulable role/node pair to have that role's exact preload.

Only required node selectors/affinity and untolerated hard taints establish an
exclusion. Soft preferences, spare capacity, extra-node health and cordon alone
are not sufficient. Standard health/pressure taints are not exclusion proof,
because admission can add implicit tolerations absent from the Deployment.
The explicit unschedulable NoSchedule taint on the two reviewed nodes is checked
as an exception. Unsupported scheduler/binding or rule shapes fail closed.

Checks run before either PATCH, during rollout and at final acceptance. A
restriction change stops further patches and acceptance; it does not undo a
PATCH already sent or stop the Deployment controller. Operators must keep the
reviewed node scheduling restrictions unchanged throughout rollout.

The image-only sequence remains Runner then Gateway, preserving the frontend,
configuration metadata, full-spec compare-and-swap patches, active task gates,
all-Pod source and health checks, and the unchanged 0020 database revision.
There is no automatic PATCH retry, rollback, database migration, model request
or OA callback. OA v2 and Data QA progress release remain separate pending steps.

## Operator command

After tasks have ended and while new task submissions are held:

```bash
sudo python3 -I -B -u /home/cnooc/activate-portal-oa-schema-compatibility-20261022-r2.py --resume-compatibility-once --tasks-drained
```

`--tasks-drained` is a fresh operator attestation, not an admission lock. Preserve
the r2 report on any failure; never delete either once directory to rerun.

## Local validation

The exact generated r2 file is tested under Linux Python 3.7.17 in a networkless,
read-only container with temporary test files. Seventeen activation/recovery
tests and 33 node coverage tests cover no-op report binding, the 18-node rollout,
active tasks, changed baselines, uncertain PATCH, prior receipt preservation,
extra-node admission/taint cases and restrictions changed mid-rollout.
These are local synthetic tests. r2 has not yet been run on production.

- r2 bytes: 60778
- r2 SHA256: `3795005a40134cb2feb484802f580b95b97cee19a29d36dda14b5d7e16acd08f`
