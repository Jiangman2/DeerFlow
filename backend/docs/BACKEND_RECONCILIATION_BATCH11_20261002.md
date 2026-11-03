# Backend reconciliation batch eleven

The r12 run reached approximately 77% and stopped at its failure cap:
7265 passed, 20 failed, 58 skipped, 27 subtests passed. It is not a complete
backend acceptance. This package follows the applied batch-ten repairs.

## Package safety and discovery

- ZIP extraction rejects any colon in a member name, including NTFS alternate
  data streams and drive-relative spellings, on every operating system. Native
  archive preflight also reports package-ads-stream-name as CRITICAL, preserving
  the structured SkillSecurityScanError before extraction/model scanning.
- Extraction independently caps members at 4096. Count directory entries as
  well as files; check before making directories or writing any member. Exactly
  the cap remains allowed. This holds when skill_scan.enabled is false; the
  optional scanner is not the sole filesystem resource-exhaustion boundary.
- Existing containment, symlink rejection, executable magic-byte checks,
  actual-byte size enforcement, staging integrity and async/offloaded phases
  remain unchanged. No archive or model scan is bypassed to make tests pass.
- Recursive namespace directories still discover skills. Once a directory owns
  SKILL.md, its descendants are support files/examples, not separately enabled
  skills. Preserve category precedence, hidden-directory filtering and the
  existing evaluation-fixture SKILL.md exclusion.
- Install-time frontmatter diagnostics stringify unknown YAML keys before
  sorting. Mixed integer/string keys remain rejected, not accepted or hidden.

## Test environment and already-approved deferrals

The SkillHub executable-tree regression used an unqualified python alias and
failed to import pathlib. Select sys.executable with shell quoting, as other
subprocess tests already do. Keep its actual installed tree, read-only mapping,
discovery, executed script and output assertion. No Sandbox runtime/environment
policy is changed or weakened; the fixture now uses pytest's Python 3.

Restore the exact pre-merge Slash test module, including its equality check
between reserved names and channel commands. Incoming changes added an absent
slash_source_owner_token parameter to almost every old test, despite the prior
approved deferral of that entire permission chain. Do not add a dummy accepted
parameter or turn on incomplete runtime authority to satisfy those calls.
All preexisting test definitions/assertions are preserved byte-for-byte.
The incoming-only context compact alias regression is deferred with its absent
backend parser implementation; it is not claimed fixed.

Remove only the incoming /api/skills/reload tuple from the administrator endpoint
matrix, restoring the exact pre-merge executable assertions. That absent endpoint
was already deferred in batch two. A 405 is not evidence of an authorization
bypass or a reason to weaken all endpoint expectations to accept 405. Keep the
implemented endpoint matrix requiring 403 before business logic for non-admins.

## Still pending: shared Slash contract

r12 separately reports context in the shared JSON reserved-name set, while the
reviewed backend/parser baseline does not implement its compact-only exception.
This package does not modify the root contract or frontend parser/tests without
their exact current snapshots. Export and reconcile those three non-secret
files separately, preserving the pre-merge behavior until the full feature is
implemented. Do not treat that remaining contract failure as passed or skipped.

## Validation limits and release gates

Local fixtures reproduce the defects using actual ZIP extraction/preflight,
filesystem discovery, storage preparation and validator callbacks. They verify
an actual 50,001-entry ZIP aborts before extraction, the cap holds with optional
scanning disabled, and all other runtime callbacks are unchanged. Narrow
dependency fakes isolate unavailable framework/model/YAML dependencies: these
checks are not complete real-dependency pytest or release acceptance.

The package pins branch/HEAD/MERGE_HEAD, protects earlier repairs and matching
file/index hashes, rejects drift, backs up prior bytes and applies reversibly in
a disposable merge fixture. It does not commit, deploy, migrate the database,
read runtime configuration/secrets or change the live test checkout.

Run real-dependency focused tests on dmdb, reconcile the shared contract, and
then finish the full backend suite and frontend checks. Live-model, real-Postgres,
Windows-only and unavailable-browser skips remain unverified, not passes.
