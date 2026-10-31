# Current-batch image injection

The view_image registry accumulates validated metadata across the thread. Previously,
each successful tool batch injected the entire registry again. For six distinct,
sequential image reads this appended 21 image blocks to conversation history.

ViewImageMiddleware now joins the latest assistant batch to its following tool
receipts by tool_call_id, requires the exact successful view_image receipt and
success status, then selects only those image_path entries from validated metadata.
Paths in model arguments are selectors, never direct filesystem read targets.

Mixed batches still wait for all tools. Multiple successful reads of one path in
a batch produce one image block. Failed reads cannot reuse stale metadata. Explicit
later re-views remain supported and read the current validated file. Existing size,
file-change checks, async thread offload, hidden UI metadata and replay dedup remain.
The tool protocol and authorization boundaries are unchanged.

Integrated with `codex/incident-recovery-20261031`: before_model stores only a
hidden textual receipt and path references. Sync/async model wrappers hydrate
only the current successful batch, with a 20 MiB aggregate image-byte budget.
The successful tool receipt must match the actual view_image protocol exactly;
unknown or failed receipts cannot reuse older metadata. Repeated paths within
a batch are deduplicated. Explicit later re-views read the current validated file.

For six sequential reads, six image blocks are sent across the six immediate
model requests, and no image bytes are added to checkpoints by this middleware.
Subsequent requests strip legacy host-injected image bytes from copied messages,
retaining their text/IDs and actual user-uploaded image messages. Saved historical
checkpoints are not rewritten. Summary settings, timeouts and retry budgets remain
unchanged. This is a structural regression result, not a measured provider token
or latency improvement. The production timeout root cause remains unproven.

Tests cover sync/async paths, failed and mixed batches, rereads of changed files,
serialized messages and a real StateGraph with an in-memory checkpoint saver.
Focused tests do not replace installed-image, PostgreSQL integration or live
conversation acceptance. See the integration validation receipt for actual coverage.
