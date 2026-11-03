# dmdb enterprise merge reconciliation — 2026-09-30

This is an isolated merge review, not a production release or deployment approval.
The pre-merge snapshot is `33688ae5724bff20c5a5fddf060b77c97b9c1b01`;
the incoming baseline is `523f1f0fbc6145c454b5a1430d60775fe7215150`.

## Restored existing protections

- Restore the snapshot's input sanitization and server-owned message metadata
  boundary in `input_sanitization_middleware.py`, `utils/messages.py`, and
  Gateway `services.py`. External presentation markers must not bypass the guard.
- Restore `strip_server_owned_state_metadata` on direct thread-state writes.
- Restore the deterministic SkillScan implementation using shared code-path and
  executable-prefix classification, undecodable-code findings, and the existing
  environment-dump analysis. Existing blocking, redaction, and installer gates
  remain intact. The independent nested-eval-fixture regression is retained.

## Deferred incomplete incoming increments

- Model RPM `request_admission`: no typed config or model-factory integration.
  Defer only the incoming module and its new test module, both absent before merge.
  This is not the existing `models/stream_admission.py` performance control.
- Sandbox output-path mask extensions: keep the existing implementation and all
  pre-merge tests; defer new tests requiring missing normalization/replacement
  helpers and changed delimiter semantics.
- Scheduled-run status filtering: no shared status enum or SQL/API filter.
  Defer only the new status-filter test; retain existing run history, ownership,
  pagination, and scheduler behavior. The frontend follow-up restores the
  existing pagination consumer without adding this incomplete status filter.
- SkillScan instance-client/scope/work-budget analysis and scanner-side ADS
  preflight additions: retain all pre-merge native scanner tests and postpone
  new assertions requiring unavailable analyzer functionality. Installer-side
  path checks are not relaxed. These additions require a complete implementation
  and their tests together in a future change.

No preexisting test module is deleted or skipped. Original definitions in the
two reconciled test modules are verified structurally against the snapshot.
Deferred files and assertions remain recoverable from the incoming commit and
the pre-application backup. The earlier activation-policy deferral remains intact.

## Unchanged boundaries and required validation

The merge's existing run placement, stream admission, stream publication batching,
Redis bridge, worker ownership, robust model transport, and persistence changes
are outside this patch. The reviewed production lead-agent construction,
activation, and secret-context files remain byte-identical.

Run focused security/scanner/performance regressions, then collect and run the
entire backend suite using the isolated merge virtual environment. Frontend lint,
type checks, and relevant tests must also pass before a final merge commit.
Known outstanding static candidates include cancellation-drain helpers and an
IM mention helper; this patch does not claim to resolve those unreviewed groups.
Do not deploy or silently skip failing tests to bypass the remaining gates.

## Frontend and scheduled-task route follow-up

Restore the snapshot's schedule API/types/cron helpers, interval form, owned
card-grid/modal page, pagination hook, and original tests. Restore the matching
Gateway scheduled-task route: custom-agent owner validation, cron preview,
ThreadId checks, runs:create permission checks, active-occurrence guards, and
atomic pause/delete queue cancellation. The complete pre-merge scheduler service,
repositories, and migration files were already preserved and are not patched.

Restore the exact pre-merge package manifest and lock, including Testing Library
and happy-dom. Their removal broke existing DOM tests; no dependency is upgraded
to a newly selected version. Preserve the snapshot's newer PostCSS resolution
rather than the incoming unrelated downgrade. The Haineng frontend, test-host
aliases, and PPT runtime/confirmation/usage changes remain unchanged, except
for the summary display lint fix with explicit empty/missing-value regression
coverage. The earlier import-order fixes remain intact.

Defer only these incomplete incoming increments:

- The added occurrence-status test in the existing run-history DOM test module.
- Exact one-time-instant edit preservation: the new schedule-edit-instant DOM
  test and the appended instant/task-switch cases in the existing E2E module.
- Nonexistent-wall-time validation: new schedule-dst-gap DOM, valid-local-time
  unit, and scheduled-tasks-dst E2E test modules. `validZonedLocalToUtcIso`
  was never present in the snapshot or incoming implementation. The snapshot's
  existing DST offset correction is restored, not replaced or claimed to reject
  gaps/folds or preserve original seconds during edits.
- Task-text search: the isolated new helper/unit/E2E files; neither snapshot
  nor incoming page wires the helper or renders the tested search controls.
  Existing task status/type filters and thread scope remain supported.
- The appended leaked-system-tag tests/import in the existing preprocessing test
  module. The underlying tag helper does not exist; all pre-merge math, nesting,
  code-fence, and Mermaid tests remain byte-identical.

No existing test file is deleted or skipped. Restored test modules match the
pre-merge snapshot byte-for-byte. New deferred files remain in the incoming
commit and exact pre-application backup. This package follows the backend
reconciliation package, requires its reviewed state, and makes no commit,
live-directory, Kubernetes, database, or deployment mutation.

Required gates: `pnpm install --frozen-lockfile`, `pnpm check`, frontend unit/DOM
tests and scheduled-task/Haineng/PPT E2E tests; restored backend scheduled-task
router, preview, queue, lifecycle, repository, service, and end-to-end tests,
then the full backend suite. Local pure-function checks and patch fixtures are
not substitutes for these integration gates.
