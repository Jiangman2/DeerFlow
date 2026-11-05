# Compatibility activation with explicitly authorized task interruption

The operator's r2 attempt passed all 18-node coverage checks and then refused
with `TASKS_STILL_ACTIVE`. No PATCH was sent; no production workload or schema
was changed. Its exact 2671-byte private report has SHA256
`4842725be3362a76e7745b83450b1d80f44f8c67b9875299c4dd09e92dfce55b`, stored at
`/var/lib/portal-oa-compat-activation-20261022-r2/REPORT.json`. The locally
archived `oa-compat-activation-field-20261022-r2.json` was reconstructed from the
operator's complete result and matches that exact byte count and digest.

The operator subsequently explicitly authorized updating without waiting for
unfinished tasks: “我们可以不管当前未完成的任务，直接更新”. r4 therefore supports
two mutually exclusive operator policies:

- `--tasks-drained`: refuse any active run, OA processable job, or live callback lease.
- `--allow-interrupt-active-tasks`: record these counts and proceed with the
  existing image-only rollout, allowing service replacement to interrupt work.

The same tool has a separate `--inspect-tasks-readonly` mode. The earlier r3
read-only inspection request is no longer a prerequisite after the explicit
interruption authorization. The operator subsequently supplied its result:
three running records with unexpired leases, zero active OA jobs/callback
leases, and unchanged 0020 schema; it made no changes and claimed no once
directory. r4 still binds the proven no-op r2 report and does not require an
r3 report. Preserve all earlier once directories.

## Operator artifact

File: `activate-portal-oa-schema-compatibility-20261022-r4.py`

- Bytes: 67886
- SHA256: `165a5d3c04836e216331d0f73d493117b1b29dd5af3f19d0f1f09f81faf1d21b`
- Control directory: `/var/lib/portal-oa-compat-activation-20261022-r4`

```bash
sudo python3 -I -B -u /home/cnooc/activate-portal-oa-schema-compatibility-20261022-r4.py --resume-compatibility-once --allow-interrupt-active-tasks
```

This is still the compatibility release: Runner then Gateway, with the original
accepted image digests and unchanged 0020 migration head. It requests no schema
migration or feature activation. OA v2 and Data QA progress remain subsequent
release steps. No explicit task cancellation or task-record update is performed;
ordinary service shutdown/startup and recovery continue to have their normal
effects on active tasks.

## Preserved gates and recovery

The changed task policy controls only whether a nonzero active count refuses
rollout. Root/host identity, exact r2 no-op report, installed-image acceptance,
preload receipts, all 18 node UIDs and role-specific coverage, deployment
readiness/full-spec pins, frontend/config preservation, schema revision and
typed nonnegative counts remain mandatory. PATCH uses UID, resourceVersion and
complete-spec tests and is never automatically retried after an uncertain result.

Counts are retained in `database_before`, each PATCH's `active_task_observation`,
and `database_after`. Active OA states include `launching` as well as `queued`
and `running`; terminal jobs with pending callbacks and an unexpired lease are
counted separately. Missing/expired leases never exempt a nonterminal task from
the drained policy. The optional read-only inspector exports aggregate counts,
dates and lease buckets only, not task identifiers, user content or credentials.

The once directory is claimed immediately before journaling the first PATCH,
after read-only preflight and the final task check. A refusal before claiming it
prints `once_claimed=false` and writes no new control files. Any claimed or
uncertain attempt still requires report-based recovery; do not delete markers.
Read-only task inspection uses a repeatable-read transaction and does not reserve
a maintenance window or prevent new tasks.

## Validation

Linux Python 3.7.17, networkless/read-only containers, exact generated artifact:

- 25 activation/recovery tests, including active-task rollout for both roles,
  per-PATCH count retention, unchanged node gates and uncertain PATCH stopping.
- 10 task-inventory tests covering fixed read-only aggregate SQL and no raw data.
- 13 policy tests covering explicit flags, incompatible flags, count/schema
  validation and propagation of child failures.

The unchanged node matcher separately retains its 33 passing node coverage
tests. Host and embedded child sources retain Python 3.7 syntax compatibility.
These are local synthetic checks, not a production rollout receipt.

## Field acceptance

The later operator summary is archived in
`oa-compat-activation-field-20261022-r4.json`. Status is
`COMPATIBILITY_ROLLOUT_ACCEPTED_SCHEMA_UNCHANGED`, with all 70 Gateway and one
Runner runtime checks accepted. Gateway is generation 383; Runner is 55.
Both patches were acknowledged, frontend and configuration metadata preserved,
and the database remained `0020_ppt_confirmation`. No schema migration was
requested. The three active-run counts before and after are aggregate metadata,
not evidence of those tasks' individual outcomes.

The server's full 35,310-byte report has SHA256
`623d1c0b3154c5b203d68a866337df57ed8d749ef4798a7ad10996ee1ee4e9af` at
`/var/lib/portal-oa-compat-activation-20261022-r4/REPORT.json`. It has not been
downloaded; the local archive is explicitly the pasted summary. Do not rerun
the successful activation or delete its control directory.
