# OA schema expansion — 2026-10-22 r1

This is the controlled production `0020_ppt_confirmation` →
`0021_oa_result_sets` expansion. It follows the accepted compatibility rollout
and installed joint-image verification. It does not deploy the feature image,
restart a service, change configuration, or enable OA v2 submissions.

## Accepted inputs

- Compatibility rollout r4: Gateway generation 383, 70 ready compatible Pods;
  PPT Runner generation 55, one ready compatible Pod. Full private receipt:
  `/var/lib/portal-oa-compat-activation-20261022-r4/REPORT.json`, 35,310 bytes,
  SHA256 `623d1c0b3154c5b203d68a866337df57ed8d749ef4798a7ad10996ee1ee4e9af`.
- Joint feature image `sha256:0a47db87acd2b219ca7bd548b8635b6f4cabd85ce34161b1935817786811006c`
  passed 12 installed-image checks in the server's isolated Job. Full receipt:
  `/var/lib/portal-oa-data-qa-image-check-20261022-r1/REPORT.json`, 1,759 bytes,
  SHA256 `04f0f94236e3941898c974f45e8351b5764b3f972eaf2f79cde6791c2496fc25`.
- Frontend remains generation 225. Its spec, both backend specs, relevant
  ConfigMap/Secret metadata, service specs, Pod identities/readiness and eligible
  node coverage are rechecked before and after expansion. Current same-image
  replacement Pods are allowed; historic Pod UIDs are not permanent pins.
- One source/database inspection per Gateway node plus the Runner validates the
  installed compatibility bootstrap/helper, all 23 original migration sources,
  database endpoint fingerprint, exact 0020 revision, and original OA columns.

## Exactly what changes

The only migration revision is the reviewed, 801-byte `0021_oa_result_sets.py`,
SHA256 `f0f4e5571251359f12ca023b864542a69fda97f990bf476dbf366d9039657347`:

- `result_protocol VARCHAR(8) NOT NULL DEFAULT 'v1'`
- `result_files JSON NULL`

The existing Alembic transaction records revision 0021 with the two additions.
Existing rows retain v1 semantics. No task status, result content, account,
history, callback, or provider request is written by this tool.

An isolated Python process in one current Gateway stages the hash-bound
migration tree in a temporary directory and invokes the installed application's
bootstrap/Alembic path with its original PostgreSQL advisory lock. Installed
files are unchanged. The exact 0020 state is checked under that lock. Empty,
legacy, partial and unknown migration states are refused before dispatch;
manual stamping and other upgrade paths are forbidden.

Both actual database connections receive a five-second lock timeout and a
30-second statement timeout. A 120-second whole-process deadline also bounds
execution. A timeout can interrupt an uncertain transaction; the controller
therefore never treats missing acknowledgement as proof of rollback.

The operator has already authorized proceeding with active tasks. No false
`--tasks-drained` attestation is required. DDL can briefly wait for or hold a
table lock; the bounded timeouts apply while ordinary services continue.

## Operator command and receipt

Generated artifact: `expand-portal-oa-schema-20261022-r1.py`, 64,545 bytes,
SHA256 `ea0f559f45461c5443dd8241959246a872be19026784735068c5504b78849e7c`.

Copy the generated `expand-portal-oa-schema-20261022-r1.py` to
`datax-01:/home/cnooc/`, then run:

```bash
sudo python3 -I -B -u /home/cnooc/expand-portal-oa-schema-20261022-r1.py --expand-oa-schema-once
```

All preflight checks precede the once claim. Immediately before the sole
expansion request, the controller persists and fsyncs an uncertain-intent
journal under `/var/lib/portal-oa-schema-expansion-20261022-r1/`. Neither exec nor
DDL is retried. Preserve this directory and the complete output after any
failure; do not remove the once claim to force another attempt.

Success is `OA_SCHEMA_EXPANSION_ACCEPTED_FEATURE_NOT_DEPLOYED` with six
post-expansion, read-only compatibility checks and unchanged deployment state.
A committed migration followed by a verification failure remains incomplete,
with the commit recorded. An interrupted or unacknowledged request remains
uncertain and requires a separate read-only reconciliation before recovery.

## Local qualification

The exact packaged host passed 21 synthetic cluster/child tests under Linux
Python 3.7.17, with networking disabled, a read-only root and temporary `/tmp`.
Tests cover receipt hashes, deployment drift, current Pod coverage, legitimate
same-image Pod replacement, no-drain behavior, the fsynced once-only dispatch,
nonzero child failure receipts, uncertain outcomes and post-commit failures.
The same tests also passed on the local Python 3.11 host. The packaged `--help`
entry point was separately exercised under Linux Python 3.7.

Seventeen child/process tests passed using an isolated PostgreSQL 16 fixture,
with both exact installed compatibility
bootstraps, the helper and 23 migration sources. It exercises the real
bootstrap/Alembic transaction, old-row defaults and preservation, both consumers'
post-expansion startup, rejected migration/source states, lock/statement timeout
rollback and a process deadline during actual DDL. Fixture passwords are local,
fixed test values; application/production configuration is never read.

## Following release steps

Bind the accepted expansion receipt before switching the Gateway to the joint
feature image. Keep OA v2 opt-in disabled through the worker rollout. Data QA
live progress also needs its prepared frontend image and UI acceptance. Verify
the OA receiver's multifile manifest, signatures and retry behavior before
enabling v2 submissions. Preserve the new columns and snapshots on rollback;
the accepted compatible Gateway image is the rollback target.

## Production receipt

The operator executed this exact package and returned
`OA_SCHEMA_EXPANSION_ACCEPTED_FEATURE_NOT_DEPLOYED`: the single expansion
committed revision 0021, all six preflight inspections and six read-only
post-expansion compatibility checks passed. Configuration metadata and Frontend
were preserved; no production Deployment was changed. The private report is
`/var/lib/portal-oa-schema-expansion-20261022-r1/REPORT.json`, 53,986 bytes,
SHA256 `1bd741e9db0465a8274c95e9d2e3d21f9ea1770b66b5d00af476dd761a98cba5`.
Its operator summary is archived in `oa-schema-expansion-field-20261022-r1.json`.
This expansion is complete and must not be rerun. Gateway feature rollout,
frontend progress rollout and real OA acceptance remain pending.
