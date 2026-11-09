# WeKnora chunk pagination follow-up

The November 9 incident reached `loop_capped` after five identical calls to
`weknora_list_document_chunks` with an omitted page and page_size=100. Four
completed results reported page=1 and total=1273; the fifth call was stopped.
This supports repeated page-one retrieval, not a pagination hash collision or
a Pod restart. The operator's diagnostic `truncated` flag describes its bounded
database export, not necessarily the exact model-visible tool result.

The tool already exposes a one-based page argument. This follow-up adds explicit
next-call arguments, returned-count and page-only scope. Large-result previews
preserve navigation as valid bounded JSON and retain the complete current-page
file. If persistence fails, the preview discloses that missing excerpts are not
available in a saved file. Empty, unknown-total, last-page and cursor-ceiling
cases do not invent a next call. Scope checks and loop detection remain intact.

This improves the model's navigation contract; it does not guarantee that every
model will follow it, read the entire document or complete the original task.
Do not silently advance identical calls or automatically rerun the stopped job.

Base source tree: `3ab826f4f6c9332b8477f257239e777deb181877` (PR42 source,
also the operator's merged d1467e4906e99ccf74e37c7974a394684efaa263 tree).
No dependency, frontend, runtime configuration, upload mount or loop threshold
change. Existing PR42 image receipts apply only to the previous image.

Validation: WeKnora tools, tool-output budget, loop detection and new pagination
tests: 232 passed. Ruff check and format check passed. Use the installed-image
test stage with the existing upload snapshots and private startup checks again
after rebuilding the combined candidate. Original-thread live acceptance is
still pending; do not claim the task or rollout has completed.
