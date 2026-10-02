# Backend reconciliation batch 13 (2026-10-02)

The r14 server run reached 8,387 passes with 20 failures at its configured
failure cap. It is not a full-suite pass. This repair applies only to the pinned
unfinished merge; it neither creates a merge commit nor changes deployment.

## Restore the pre-merge isolation boundary

`thread_runs.py` restores the exact pre-merge `_thread_ownership_established`,
`_run_scope_user_id` and `_require_run_visible_to_scope` implementations and
their existing call sites. Ordinary sessions remain scoped to their own user;
the internal owner header is not accepted from a browser session. An already
authorized administrator retains the existing read-only unscoped view.
Internal requests can read an established-owner thread only after the normal
route authorization, while missing/NULL-owner metadata retains exact raw-owner
scope and rejects another owner's run subresource with 404. Restore those
checks for cancellation, joining/streaming, messages, events, workspace changes,
lists and usage. Do not replace this with unconditional internal-token bypass.

Thread token usage again passes the resolved owner to storage. The base store
contract and SQL implementation now also accept `user_id`, matching the
previously repaired memory store. SQL filters the row set before reducing every
headline, per-model and per-caller bucket, for both completed and active reads.
`None` is the explicitly authorized unscoped contract; an empty string is not
treated as unscoped. This completes a latent SQL interface mismatch without
changing token accounting, run indexing, model attribution or active-run rules.

Messages/events `limit`, `before_seq` and `after_seq` require values >=1 where
supported. Existing defaults, maximum limits, backward-page and run-message
pagination semantics remain unchanged. Query validation precedes store access.

## Narrowly defer incoming integrations without their runtime counterpart

One incoming test for regenerating a hidden structured confirmation answer is
deferred: the merged regeneration path still selects the latest visible user
message and removes UI-hidden control metadata as before. Do not pretend to
support hidden-answer replay by selecting arbitrary hidden HumanMessages. All
pre-merge regeneration tests and the three newly compatible malformed/summary/
goal control-message rejection cases remain. This is a declared feature gap,
not a claim that the hidden-answer replay case has been fixed.

The runtime tool-output budget is unchanged. Restore the two exact pre-merge
tests that were renamed/replaced by incompatible typed-preview expectations:
`TestBuildPreview.test_contains_head_and_tail_and_reference` and
`TestWrapToolCallExternalize.test_preview_contains_head_and_tail`. Individually
defer these six incoming renderer-integration tests:

- `TestBuildPreview.test_json_preview_includes_structure_and_raw_sample`
- `TestBuildPreview.test_json_preview_reports_nested_paths_and_line_hints`
- `TestBuildPreview.test_json_paths_are_emitted_without_line_hints`
- `TestBuildPreview.test_table_preview_extracts_columns`
- `TestToolOutputSynopsis.test_review_6_json_paths_are_emitted_without_byte_offset`
- `TestToolOutputSynopsis.test_review_10_preview_includes_raw_head_and_tail_sample`

Keep the standalone synopsis helper and its independent tests. Also retain the
compatible operational head/tail-budget test. No pre-merge module, test name or
assertion is dropped. In particular the WeKnora valid JSON envelope, citations,
authenticated image references, output-file containment and bounded fallback
remain protected; no generic synopsis replaces that enterprise output path.

## Validation boundary

Added regression tests cover session/admin/internal scope, foreign-run rejection
before reads or control operations, active/completed usage propagation, real
SQLite mixed-owner totals and PostgreSQL query compilation. Existing query,
internal scope and token shape tests are unchanged.

Local native checks execute selected real AST callbacks and a standard-library
SQLite query adapter. They are not the complete FastAPI/SQLAlchemy/LangGraph
suite. Require focused pytest on dmdb, then the complete backend suite. Frontend
check/full unit, build and deployment-specific smoke/integration checks remain
separate acceptance gates. The 67 previously skipped cases are not verified by
this repair; use isolated test credentials/databases when later enabling them,
never copy production Secrets into this merge tree.
