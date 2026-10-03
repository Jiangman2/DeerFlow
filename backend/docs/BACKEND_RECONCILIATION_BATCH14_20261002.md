# Backend reconciliation batch 14 (2026-10-02)

The r15 backend suite reached its end: 9,469 passed, six failed, 67 skipped.
This batch addresses those failures without additional test deferrals, changing
only the pinned unfinished merge worktree. No final merge or deployment occurs.

- TUI `/quit` now uses its existing interruption path before exiting an active
  stream. Idle quit and Ctrl+C/Escape behavior remain unchanged. The blocked-
  stream integration test is preserved, with an additional call-order unit test.
- TUI command discovery and submission use the existing seven-name
  `RESERVED_SLASH_SKILL_NAMES` contract rather than treating reserved control
  names as Skills. Built-ins retain precedence. `context` is not a reserved
  Skill name: the two new registry tests now supply it separately as a positive
  fixture, retaining every assertion. Ordinary `/context task` remains a Skill;
  `/context compact` is an unsupported TUI control, not a task forwarded to a
  Skill. This does not add compaction support or change backend/frontend parsers.
- `message_content_to_text(None)` returns an empty string. String `"None"`, zero,
  False, newline joining and all other non-None conversions remain unchanged.
  Existing source restoration/deep-copy, original-user-content and security
  classifications are preserved.
- WeChat polling isolates an ordinary item failure, logs its bounded message ID
  with the exception, and attempts the remaining siblings. Cursor advancement
  and offloaded persistence occur only after all items have been attempted.
  Cancellation still propagates without acknowledging a partial batch, allowing
  replay after interruption/crash. Existing auth expiration, connection/owner
  identity, attachment validation/decryption and private-storage boundaries are
  untouched. Tests retain real corrupt AES ciphertext coverage and add cursor-
  order plus first/middle cancellation cases. This is not an exactly-once claim:
  a crash after delivery and before cursor persistence can replay a message.
- Workspace diff counters recognize hunk ranges, counting added/deleted content
  beginning with `++ ` or `-- ` instead of mistaking it for a file header.
  Exhausted hunk ranges also identify the following file's true headers. Existing
  output/snapshot limits, sensitive-path filtering and publication are unchanged.

All pre-merge test functions and assertions remain. No test module or test case
is removed or skipped. New tests exercise cancellation, cursor durability/order,
quit call order, compact-control discrimination and multi-file/zero-range diffs.

Local selected real callbacks with standard-library fakes reproduce the defects
before repair and test the scoped changes afterwards; these do not constitute
Textual, cryptography, FastAPI or full pytest acceptance. Run focused pytest on
dmdb followed by the complete backend suite. Frontend full-unit/check/build and
deployment-specific smoke/integration gates remain required before publication.
The 67 skipped cases have not been verified; they require separately reviewed,
isolated test dependencies/credentials/databases, not copied production Secrets.
