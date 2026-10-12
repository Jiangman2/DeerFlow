# Backend batch four: factory, Codex, channel/SDK alignment

Only for the unfinished isolated dmdb merge, HEAD `33688ae5`, incoming
`523f1f0f`, branch `fix/upload_test_env-enterprise-merge-20260930`, after
Browserless test isolation. This does not create a merge commit or deploy.

The full-suite run passed the previous 8% wait and reached 17%, stopping at
the configured 20-failure limit with 1669 passes, 15 skips and 19 subtests.
This is not evidence of whole-suite success or real parser dependency readiness.

## Restore overwritten existing runtime safeguards

`agents/factory.py` is restored to its exact pre-merge bytes. Its entire delta
from the reviewed merge is the missing DurableContextMiddleware and
SystemMessageCoalescingMiddleware assembly before summarization, associated
documentation, and `TokenBudgetConfig(enabled=True)` for explicit boolean
feature opt-in. The existing full-chain test is retained unchanged. Do not
remove its durable-context expectations just to pass the test.

`models/openai_codex_provider.py` is restored to its exact pre-merge bytes.
Its entire delta is serialization of valid and invalid AI tool calls with
non-empty names and call IDs so the existing dangling-call placeholder result
does not become an orphaned Responses function output. The preexisting Codex
provider regression is retained unchanged. Relay/provider auth, namespace
argument validation and streaming configuration remain protected.

## Align test expectations without weakening behavior

Four channel-router tests keep all credential availability, owner-scoped OAuth
state, binding mode, URL, code and persistence checks; only their expected
display strings change to the deployed Chinese enterprise instructions and
`海能 Work` label. Runtime branding is unchanged. The Responses namespace
test additionally expects its declared parameters schema on ResponseToolTarget,
which the implemented adapter now retains for argument validation. Do not
strip that schema from production to match an outdated equality assertion.

## Individually defer incomplete incoming-only increments

The following tests were absent before the merge and require behavior absent
from both the reviewed source and merged runtime. Their implementation owners
must complete and reintroduce these exact tests together. This is deferral,
not a fix or a waiver of a preexisting regression.

- `test_channel_service_startup_cancellation.py` contains only
  `test_cancelled_start_drains_partial_service_and_clears_singleton`. It requires
  drain/shield/rollback startup semantics absent from unchanged start code.
  The incoming-only module is omitted; pre-merge modules are never deleted.
- `test_channels.py`:
  `test_thread_create_waiters_keep_one_lock_generation_after_first_aborts`
  (error/cancel parameters); shared lock-generation retention is not implemented.
- The same module's six Feishu business-response checks:
  `test_send_file_returns_false_on_reply_business_failure`,
  `test_send_file_returns_false_on_create_business_failure`,
  `test_reply_card_raises_on_business_failure_response`,
  `test_create_card_raises_on_business_failure_response`,
  `test_update_card_raises_on_business_failure_response`,
  `test_add_reaction_logs_warning_on_business_failure_without_raising`.
  Existing send/card/upload checks and implemented new positive cases stay.
- `test_client.py`:
  `test_stream_emits_streamed_tool_calls_once_with_complete_args`,
  `test_stream_emits_text_appended_by_a_later_node` (True/False parameters),
  `test_stream_emits_metadata_added_by_a_later_node`. These require SDK stream
  assembly/update increments absent from the unchanged client. Keep all old
  stream/metadata tests and the implemented new non-extending replacement case.

Every pre-merge test definition in these modules remains (only the intentional
branding expectations change). Other incoming tests and all existing assertions
outside those four display expectations remain. No tests are marked skip/xfail.
The omitted incoming tests remain recoverable from the source commit and the
automatic before-repair archive. Defer ten new methods plus one incoming-only
module (13 parameterized failing cases), not entire mixed test modules.

## Verification obligations

Local checks verify retained test ASTs, narrowly restored runtime bytes, Codex
pairing conversion, middleware assembly with dependency-light stand-ins,
localized instructions, and patch/index/backup/drift/reverse mechanics. They
do not replace server pytest or a real model/provider/IM integration test.
Run factory, Codex provider/adapter, channel router/manager/client, existing
Gateway provenance, and Browserless/readability tests before continuing the
full suite. Do not commit or deploy until remaining failures and release
integration obligations have been reviewed.
