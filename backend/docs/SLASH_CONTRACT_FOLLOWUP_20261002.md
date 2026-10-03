# Shared Slash contract follow-up

Batch eleven passed 379 focused tests on dmdb. Its shared-contract item was
still pending because r12 reported an extra context reservation in the JSON.
The user exported the exact current contract, frontend parser and contract
test in deerflow-slash-contract-review-20261002.tGfs1d.tar.gz. Inspection
confirms that only the JSON reservation set diverges: both runtime/display
parsers retain the seven-name pre-merge set.

## Reconciliation

Remove only context from contracts/slash_skill_contract.json. Keep its version,
description, remaining reserved-name order and exact name grammar unchanged.
Do not add context to either runtime parser: that would change existing context
skill behavior and still fail the incoming compact-only exception expectations.

Restore frontend/tests/unit/core/skills/slash.test.ts byte-for-byte from the
pre-merge snapshot. The only incoming addition was the test named "allows a
context skill except for the compact alias". Neither Skill parser implements
that exception; it is individually deferred, not claimed fixed. Preserve every
old test/name/assertion, and leave the frontend/backend contract tests intact.
The prior batch already restored the corresponding old backend Slash tests.
No test module is removed or skipped, and contract assertions are not relaxed.

This deferral concerns the Skill parser/display exception only. The preexisting
composer parseCompactCommand handler accepts /compact and /context compact,
and its tests already cover both. That behavior, thread ownership, abort guards,
in-flight exclusion and Gateway compaction endpoint are unchanged. No model,
activation middleware, secret binding, workflow state, channel command, API or
production configuration is modified.

## Verification scope

Local verification runs the actual backend contract test callbacks and the
frontend TypeScript parser/contract assertions through Node type stripping.
It reproduces both contract failures before the one-line JSON change, checks
the resulting reservation/grammar equality, and compares parser behavior across
ordinary/invalid/reserved/enabled/disabled inputs. It also verifies exact
preexisting frontend tests and the narrowly bounded JSON delta. These direct
checks do not replace real-dependency pytest/Rstest, frontend lint/typechecking,
browser release checks or the remaining full backend suite.

The patch helper requires the unfinished isolated merge's pinned branch,
HEAD/MERGE_HEAD and matching file/index hashes. It protects prior repairs and
both parser/contract test files, rejects drift, backs up the changed bytes and
checks reversible application. It creates no merge commit or deployment.

After application, run both sides' focused regressions plus pnpm check. Then
continue the full backend suite with verbose progress and a bounded failure
cap. Live-model, real-Postgres, Windows-only and missing-browser skips remain
unverified rather than passes. Do not finalize or deploy before release gates.
