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

Historical messages are retained; this patch prevents newly duplicated injections.
It does not delete historical images, rewrite PostgreSQL checkpoints, or change
summarization settings, request timeouts or retry budgets. The metadata registry
contains no base64, but the injected HumanMessage does contain checkpointed image
data. Six distinct new reads now append six image blocks, not 21. This is an
image-count regression, not a measured provider token or latency improvement.

Tests cover sync/async paths, failed and mixed batches, rereads of changed files,
serialized messages and a real StateGraph with an in-memory checkpoint saver.
Full backend collection was attempted locally before and after the patch, but was
blocked by the unavailable blockbuster dependency. Focused source tests do not
replace installed-image or live conversation acceptance. The original production
upstream timeout root cause is unproven.
