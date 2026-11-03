# November 3 enterprise source follow-up

The enterprise branch merged PR #26 at `095594709`, then changed the bounded
failure dashboard, WeKnora request budgets, model performance logging and browser
navigation registration. Its `1fbc07fcf6a7a7258d15e84e040042da4b5c8748` tree could
not pass the historical November 2 source check: the dashboard no longer matched
the old formatter-only qualification. The two new backend modules also needed
an explicit successor inventory.

This follow-up records the exact 7 runtime files and 11 tests changed since
`5a29e5aa66b4190024bfd55be14cbd1cc8b16f77`. The new additive manifest is
`release/enterprise-source-followup-20261103/SOURCE.json`; its 10 parent snapshots
are copied from that fixed base commit. Application behavior is preserved.

Verification first checks the sealed manifest, every current file and every
parent snapshot. Only then can an older verifier read those exact parent bytes.
The two registered new runtime modules are separately hash-checked and excluded
only from the historical file-set comparison; any additional unregistered module
still fails. Original release inventories, operator receipts, published sources
and the existing PR26 qualification remain immutable. The frontend formatter
check independently validates the current dashboard hash and then proves its
historical parent is still the pinned formatter's exact output.

The first CI run additionally exposed 26 existing backend lint diagnostics and
13 unformatted files in the enterprise successor. `CI_FORMATTING.json` separately
records Ruff corrections to 5 runtime files and 9 tests, with all 14 original
sources preserved under `ci-baseline/`. The verifier checks both hashes and AST
equivalence allowing contiguous import ordering/splitting; only the two named
test files permit removal of their unused `json` / `asyncio` imports. No lint
rules or runtime tests are disabled. The original `SOURCE.json` remains intact.

This seal records committed source, **not a new deployment or business
acceptance**. The historical Gateway 403 / Frontend 239 observations remain
historical. This change does not modify production configuration, resume the
paused Gateway, run an archived operator script, change the 0022 database head,
or publish Work DataLake authorization.

Offline checks (Python 3.12+) are the existing November 1/2 and October 29
verifiers, plus `test_enterprise_source_followup_20261103.py`. The frontend runs
`node --test scripts/test-source-followup.mjs` before its formatter provenance
check. Tests reject changed current sources, parent snapshots and manifests.

PR #30 separately carries the unmodified four-file V4.1 CI fix from PR #27.
Both PRs target `codex/enterprise-consolidation-20260716`; neither is a production
rollout request. Close the superseded #26/#27 only after both fixes are merged
and their final source coverage is rechecked.
