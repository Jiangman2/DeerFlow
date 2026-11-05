# Backend reconciliation batch ten

The r11 backend run reached approximately 72% before its failure cap:
6869 passed, 20 failed, 58 skipped, 27 subtests passed. It is not a complete
backend acceptance. This package follows the applied path-mask/search repairs.

## Runtime repairs

- Both Run message endpoints reject non-positive before_seq/after_seq cursors.
  Existing paging, backward-only history, run/thread/user resolution and
  permission decorators remain unchanged.
- Stateless thread selection and build_run_config accept configurable=null.
  Preserve context preference, private-context scrubbing, recursion caps,
  authenticated identity injection and other configuration behavior.
- On parent cancellation, the ordinary Gateway worker cancels and awaits its
  owned graph-stream child before leaving _stream_once_inner. asyncio.wait
  does not cancel children automatically. This allows the stream's existing
  checkpoint-lock and batching cleanup to finish before rollback/finalization;
  do not replace batching, cancellation outcomes or cross-worker lease logic.
- SQLite scheduled-run claims acquire BEGIN IMMEDIATE before reading the global
  budget. Count, FIFO check and launch CAS remain inside that short transaction.
  Postgres keeps its existing transaction-scoped advisory lock unchanged.
- Serper rejects malformed IPv6 URL parsing without throwing. All other URL
  filtering remains unchanged; this does not establish DNS-rebinding protection.
- The AIO virtual-skills safety check imports the existing runtime constant
  instead of repeating its literal. The value and command/env behavior are the
  same; the applied glob cap and output-mask corrections remain unchanged.
- Restore the exact pre-merge package readers, binary snapshot bytes and
  byte-for-byte SkillScan materialization. Exclude only deliberately unsafe eval
  fixture SKILL.md documents, not fixture scripts/binaries. Preserve size/path
  limits, symlink exclusion and exclusive output creation. Render unknown
  frontmatter keys as strings before sorting so mixed YAML keys do not crash.

## Fixture reconciliation and explicit deferrals

The failing startup-recovery test injected a status-write failure into
update_status, but recovery now uses claim_for_takeover's atomic terminal CAS.
Add the same injected failure at that interface, keeping the original test,
assertions, retries and status-update failure coverage. Production takeover is
not changed or split into an unsafe read/write sequence.

Restore the preexisting test_cleanup and its eviction contract. Defer only the
incoming test_cleanup_without_store_preserves_history: indefinitely retaining
memory-only terminal rows contradicts the existing cleanup/index contract and
has no matching runtime implementation. Retain the store-backed cleanup test.

Moderation tracing defaults/signature are not integrated in this source.
Restore the exact pre-merge retry test (attach_tracing=False), and defer only
the two incoming tracing-wiring tests until scanner/factory/callers are audited
together. The real scanner's retry, fail-closed decision and no-model-tracing
behavior are unchanged. Do not silently enable content-bearing telemetry.

No preexisting test module/name/assertion is deleted or skipped. Explicitly
deferred incoming tests are not declared fixed. No new secret, production
configuration, database migration, final merge commit or deployment is made.

## Validation scope

Local callback fixtures reproduce the reported defects, exercise repaired
callbacks/child cancellation/real SQLite serialization and native review bytes,
and preserve all prior guards. They use narrow dependency fakes and are not
real-dependency pytest or release acceptance. The package also verifies pinned
branch/HEAD/MERGE_HEAD, file/index hashes, drift rejection, backup and reversible
application in a disposable merge fixture.

Run the focused real-dependency regressions on dmdb and then the full suite.
Live-model, real-Postgres and unavailable-browser skips remain unverified, not
passes. Keep the live test checkout and deployed services untouched.
