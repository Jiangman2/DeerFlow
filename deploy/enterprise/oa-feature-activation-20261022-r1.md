# OA/Data QA Gateway feature activation — 2026-10-22 r1

The production expansion is accepted at `0021_oa_result_sets`. This release
switches the 70 Gateway replicas from compatibility image `361592fe…` to the
already preloaded and installed-image-checked feature image:

`deerflow.local/deer-flow-gateway@sha256:0a47db87acd2b219ca7bd548b8635b6f4cabd85ce34161b1935817786811006c`

The operator authorized interruption of unfinished tasks. The command records
that policy explicitly; it never asserts that tasks have drained. The single
JSON PATCH tests Deployment UID, fresh resourceVersion and the complete
current spec, then replaces only the Gateway container image. Configuration,
Frontend and PPT Runner have independent unchanged-state checks.

## Evidence and current baseline

- Gateway UID `a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1`, generation 383, spec SHA256
  `5b4b2868c2d2a7a9cebf990aa9bf55f7defb6528f8fb2e1b9da319a163bec63f`.
- PPT Runner UID `4115f178-0599-445e-89c3-9d9452ddc26a`, generation 55, compatible
  image `0b0cdbbb…`; Frontend generation 225 remains `ee145e63…`.
- Production expansion full receipt:
  `/var/lib/portal-oa-schema-expansion-20261022-r1/REPORT.json`, 53,986 bytes,
  SHA256 `1bd741e9db0465a8274c95e9d2e3d21f9ea1770b66b5d00af476dd761a98cba5`.
- Installed feature image full receipt:
  `/var/lib/portal-oa-data-qa-image-check-20261022-r1/REPORT.json`, 1,759 bytes,
  SHA256 `04f0f94236e3941898c974f45e8351b5764b3f972eaf2f79cde6791c2496fc25`.
- All five Gateway node preloads were accepted; their historical node UIDs are
  freshly revalidated against the eligible scheduling matrix before and during
  rollout. No additional image import is requested by the activation command.

## Gates and runtime acceptance

The tool verifies the hash-bound expansion and installed-image receipts, all
current Pod image IDs and Service endpoints, exact Deployment specs, configuration
metadata continuity from expansion, and fresh node coverage. Every old Gateway
Pod must report OA v2 disabled before any PATCH is sent. The current Gateway and
PPT Runner must accept the expanded schema through read-only inspection.

After rollout, every replacement Gateway must pass bounded source/configuration
and loopback-health checks. Ten feature files and thirteen retained Data QA/GLM
files are pinned to their original build bytes. OA v2 remains disabled, effective
OA recursion budget is 1000, and scheduler budget remains 300. A read-only check
verifies schema 0021 and both additive column shapes. PPT Runner retains its
original compatible Pod and is rechecked after Gateway replacement.

The feature image was built on the accepted `eeeb1901…` business image, with ten
tail files. It contains migration head 0021 and the original bootstrap
`b852ea3b…`, which recognizes that head normally. It does not contain the 0020
compatibility helper. Requiring that helper on feature Pods would be incorrect;
the retained PPT Runner and rollback Gateway still use the narrow compatibility
backport.

## Once-only behavior

An exclusive root-owned control directory and fsynced report record intent
before the sole PATCH. Kubernetes reads can retry, but PATCH and exec are never
retried. An unacknowledged request retains its possible mutation state. There
is no automatic rollback or database migration in this tool. Preserve the
report and control directory after any failure or interrupted connection.

Success means the backend image and all runtime gates passed. Data QA progress
UI acceptance, OA receiver/business acceptance and explicit v2 enablement
remain subsequent release steps. The exact success status is
`GATEWAY_OA_DATA_QA_RUNTIME_ACCEPTED_FRONTEND_AND_OA_PENDING`.

## Qualified operator package

`activate-portal-gateway-oa-data-qa-20261022-r1.py`, 91,829 bytes, SHA256
`4fd46a6290c455545f3f43879fd6f5a6fdb1e302b911abfe7bf65c01ef3b9e18`.
The exact packaged host passed 31 tests in isolated Linux Python 3.7.17.
The runtime child passed 17 Linux tests using hash-verified installed-source
fixtures, plus four final schema-comparison cases. Independent review found
no blocking issue. No local test contacted production, a model, or OA.

```bash
sudo python3 -I -B -u /home/cnooc/activate-portal-gateway-oa-data-qa-20261022-r1.py --activate-gateway-oa-data-qa-once --allow-interrupt-active-tasks
```

Copy the package to datax-01 `/home/cnooc/`. Preserve the final complete JSON
and `/var/lib/portal-gateway-oa-data-qa-activation-20261022-r1/` after execution.
Do not repeat a failed or uncertain attempt or delete its once directory.

The two frontend nodes have now also returned accepted r2 cache preloads for
`sha256:15d1327740e688faf0617d1d8fb9f662618a963624ba7280a6d0efb54ed70475`.
Their summaries are archived in
`data-qa-progress-frontend-preloads-field-20261022-r2.json`. Frontend activation
will bind the successful Gateway receipt and freshly check both node UIDs.

## Accepted operator receipt received 2026-10-23

The complete pasted summary is archived in
`oa-feature-activation-field-20261023-r1.json`. It reports 70 accepted Gateway
Pods at generation 384, spec SHA256
`20f242c560879f8eb70b77c0d85367f614fe839bad76917637bcefc9d67eebd3`,
with all 24 reported source hashes matching the candidate pins. The private
server report is 302,782 bytes, SHA256
`9748159776fc431cdbaedb21da8c96eda68534532bf5a97581d03a8dd51669b0`, at
`/var/lib/portal-gateway-oa-data-qa-activation-20261022-r1/REPORT.json`.
This is operator-supplied evidence, not a fresh local-agent cluster read.

Frontend, Runner, other Gateway spec fields and configuration metadata are
preserved. Schema 0021 was checked read-only; no new migration, provider call,
or OA callback occurred. OA v2 remains disabled. Frontend browser acceptance
and OA receiver/business acceptance remain pending. The planned frontend now
uses the combined Data QA + Token package (`b6c17e31…`); the earlier Data QA-only
image and its preloads do not authorize that new frontend cutover.

## Rollback boundary

Use compatible Gateway image `361592fe…` for application rollback, retaining
schema 0021 and result snapshots. Before returning to a v1-only worker, stop
new v2 submissions and drain any v2 generation/callback tasks. The pre-compatibility
0020 image cannot be used as the direct rollback target.
