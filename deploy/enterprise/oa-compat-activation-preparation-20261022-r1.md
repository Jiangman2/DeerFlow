# OA schema compatibility activation preparation

The operator reports that tasks are still running and requests preparation only.
No activation or production schema migration was executed while preparing this
package. Do not run the activation until the operator confirms the task window.

All 15 effective node preloads passed (five Gateway, ten Runner). Both actual
preloaded images then passed five installed-image checks in isolated server Jobs.
The field summary and exact private-report digest are archived in
`oa-compat-installed-images-field-20261022-r1.json`. These checks use temporary
SQLite and do not constitute PostgreSQL migration or OA business acceptance.

## Prepared artifact

`package-oa-compat-activation.py` builds the standalone operator script from
`activate-oa-compat.py`, `oa-compat-live-check.py`, and the pinned build/preload
receipts. Output: `activate-portal-oa-schema-compatibility-20261022-r1.py`.

- Bytes: 48966.
- SHA256: `946d112ba0479d8049f6928a25d4674634f8eef28c32d4272c1844d77c88face`.
- Validation: 12 Linux Python 3.7 tests passed, using the exact generated file.
- Field activation: not performed.

The script requires both `--activate-compatibility-once` and `--tasks-drained`.
The latter is an operator attestation, not a task-draining operation. The script
also checks active runs and queued/running OA jobs in a read-only database
transaction before each patch. Operators must prevent new task submissions
during the maintenance window; the counts do not enforce an admission lock.

## Scope and stopping behavior

The sequence is PPT Runner first, then Gateway. Each patch changes only the
container image and tests the Deployment UID, resourceVersion and complete
previous spec. Gateway generation 382 and Runner generation 54 are bound to
their reviewed spec digests; frontend generation 225 is preserved. Baseline
drift requires review rather than silently rebasing the rollout.

The private installed-image report must match its exact size and SHA256. Node
UIDs and readiness are checked against the complete 15-node preload matrix;
additional or replaced nodes cause refusal. Referenced ConfigMap/Secret metadata
must remain unchanged. Runtime checks cover all 70 Gateway replicas and the
single Runner, including installed source hashes, retained 0020 migration head,
loopback health, Pod/container identity and Service EndpointSlice membership.

The script never requests a schema migration, a provider call or an OA callback.
Read-only database inspection verifies revision `0020_ppt_confirmation` before
and after rollout. Ordinary service startup still occurs when Pods are replaced.
Existing configuration and frontend are retained. Kubernetes GET reads may retry;
PATCH and exec calls do not retry automatically. Uncertain writes are journaled
before submission, stop subsequent changes, and require report-based recovery.
There is no automatic rollback. Preserve the once-control directory and report
on failure instead of deleting them and repeating the command.

## Remaining release work

After operator-approved compatibility activation and receipt review, qualify and
perform the separate PostgreSQL 0021 expansion and OA/Data QA functional rollout.
The frontend progress release and actual OA v2 multi-file/callback business
acceptance remain separate pending steps. Compatibility success alone does not
enable OA v2 or establish that the complete joint release is accepted.
