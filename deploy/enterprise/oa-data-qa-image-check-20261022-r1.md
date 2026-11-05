# Joint OA/Data QA image qualification after the compatibility rollout

The compatibility rollout is accepted. This is the next bounded qualification
step, not a production schema migration or feature deployment.

The single Job uses the previously preloaded Gateway feature image
`sha256:0a47db87acd2b219ca7bd548b8635b6f4cabd85ce34161b1935817786811006c`
on `ecs-zdz-pro-0018`, whose pinned UID is
`0243d097-3ee7-4abc-899a-319e57934eee`. It uses `imagePullPolicy: Never`.
The Job has no production volumes, inherited Deployment environment, service
account token, provider credential or database configuration. Its filesystem
is read-only except bounded temporary storage. Python socket connections and
DNS lookup are disabled within the checker. No application startup is called.

The tool checks the exact full r4 report on datax-01 before creating the Job,
then checks stable Gateway 383, Runner 55 and Frontend 225 full-spec baselines
before and after execution. Unknown drift refuses acceptance. The host permits
one Job, no retries, 180 seconds execution and 600 seconds finished-Job TTL.
It verifies Pod owner, node, actual image ID, successful exit and all named
child checks. It does not patch a Deployment or remove existing receipts.

## Operator file

`verify-portal-oa-data-qa-image-20261022-r1.py` — 24,991 bytes, SHA256
`48d6d4aec2a72f280be69c75435ba3c83da9697db0347edbb8a14062fb426c00`.

```bash
sudo python3 -I -B -u /home/cnooc/verify-portal-oa-data-qa-image-20261022-r1.py --verify-preloaded-images-once
```

Private result: `/var/lib/portal-oa-data-qa-image-check-20261022-r1/REPORT.json`.
Success is `OA_DATA_QA_IMAGE_ACCEPTED_NOT_DEPLOYED`. Preserve any failed report
without repeating the command or removing the control directory.

The operator has now returned that success status. The Job
`portal-oa-feature-check-9e91a0b5da43` completed with the exact candidate image,
exit code zero and all 12 named checks. The 1,759-byte server report has SHA256
`04f0f94236e3941898c974f45e8351b5764b3f972eaf2f79cde6791c2496fc25`.
The archived `oa-data-qa-installed-image-field-20261022-r1.json` normalizes the
chat-rendered image link; reconstructing the original sorted JSON report
reproduces its exact byte count and hash. Production schema and Deployments
were not modified. This qualification step is complete and must not be rerun.

## Local validation

- All ten feature source files match the original preloaded image build record.
- The 12 named synthetic checks passed in a local Linux ARM64 dependency image
  with mounted, hash-verified candidate sources. This is not acceptance of the
  production OCI image; the server Job supplies that remaining evidence.
- The exact packaged Python 3.7 host passed 13 isolated tests covering receipt
  hash and symlink rejection, baseline drift, node readiness, actual image and
  owner mismatches, incomplete checks, unexpected DB/callback access, and the
  one-Job success path.

Child checks cover default v2 opt-out, scheduler 300/OA 1000 synthetic budgets,
three-file immutable snapshots, retry stability, invalid batches and failed
run rejection, observable Data QA progress before stream completion, closed
progress vocabulary, and the actual 0021 migration against a temporary SQLite
legacy-row fixture. No real OA business result is asserted.

## Subsequent migration and deployment boundaries

The production migration tool is not released by this document. Its preparation
must bind this accepted installed-image result, recheck
both compatible live consumers and the current 0020 database, and restrict the
migration to the audited `0021_oa_result_sets` successor. The only additions are
`result_protocol VARCHAR(8) NOT NULL DEFAULT 'v1'` and nullable `result_files JSON`.
Use the application's audited startup/migration path and its existing advisory
lock identity; enforce bounded lock/statement waits on the actual DDL connection.
Do not manually stamp a version or introduce a second migration authority.
Lock timeout or uncertain completion requires a preserved report and state
inspection, not an automatic retry. No external calls belong in the migration
transaction.

After the controlled expansion and verification, switch the Gateway feature
image with v2 submissions still disabled, then perform API/UI and OA receiver
acceptance before enabling explicit v2. Data QA UI progress also requires the
prepared frontend release; the current Frontend 225 is unchanged. The compatible
Gateway image is the rollback target, not the pre-compatibility image. Rollback
preserves result columns/snapshots and requires v2 processing/callback drainage
before returning to a v1-only worker. The operator's existing authorization to
interrupt tasks during rolling updates remains in force; do not substitute a
false tasks-drained attestation.
