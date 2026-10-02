# Backend reconciliation batch twelve

The r13 run stopped at the twenty-failure cap around 83%: 7842 passed, 20 failed,
67 skipped, 27 subtests passed. This is not a completed full-suite acceptance.
The five affected test modules and runtime files were already captured in the
backend review. Guard hashes require those exact current files before applying.

## Independent execution repairs

SubagentExecutor._aexecute now owns its astream with a narrow async context
manager. It calls aclose when available on normal exhaustion, early cooperative
return, stream error and task cancellation. A normal close failure reaches the
existing FAILED path before success is published. A secondary Exception from
cleanup is logged without replacing an active stream/recursion exception;
CancelledError is not swallowed. Existing first-terminal-write behavior remains
authoritative, so a late cleanup failure cannot overwrite cancellation/timeout.

The final-result, structured LLM fallback and turn-cap partial-result paths treat
only content=None as empty rather than stringifying it into the literal None.
Existing nonempty text, mixed content, last-assistant selection, stale-fallback
exclusion and stop-reason priority are preserved. The shared message utility,
identity fields, constructor, agent factory, scheduling/thread pool, token
collector and checkpoint lineage are unchanged. Keep all incoming close/None
regressions and enhanced cooperative-cancellation assertions; add explicit
task-cancellation and recursion-with-secondary-close-failure tests.

## Hermetic and localized fixtures

Provider discovery must still advertise enabled IAM in SSO-only mode while
hiding generic OIDC providers. Do not change the production endpoint to return
early and accidentally hide IAM. The existing test supplies an explicit fake
app config with IAM disabled and OIDC enabled; its 200/empty-list assertions
remain exact. An added test covers IAM enabled with generic OIDC still hidden.
No config.yaml, API key, Secret or production database is copied into tests.

The two Telegram binding tests retain all connection ownership, external-user,
workspace, metadata and reply-count assertions. Replace only the stale English
substring assertion with exact equality to the existing Chinese reply built
from ENTERPRISE_PRODUCT_NAME. The production Telegram message and binding
implementation remain unchanged; this is localization alignment, not relaxed
authorization or a reason to bypass the allowed-user/deep-link checks.

## Individually deferred incoming-only expectations

The actual executor/task-tool implementation does not accept or forward the
new is_internal/authz_attributes constructor fields. They require a complete
audited attribution/policy integration, not dummy parameters to silence tests.
Defer exactly these eight incoming-only tests, preserving all pre-merge tests
and the existing user_id/user_role/oauth/channel/run identity assertions:

- test_aexecute_writes_is_internal_true
- test_aexecute_writes_is_internal_false
- test_aexecute_copies_attributes_on_writeback
- test_executor_rejects_non_mapping_attributes
- test_task_tool_forwards_is_internal_true_to_executor
- test_task_tool_forwards_is_internal_false_to_executor
- test_task_tool_copies_attributes_to_executor
- test_task_tool_rejects_non_mapping_attributes

Tavily runtime is identical to the pre-merge enterprise baseline: its verified
scoped TLS session and existing credentials/query behavior must not be changed
as part of this merge. The source adds tests without the corresponding
per-tool credential/domain-filter implementation. Defer exactly four incoming
tests, including the title fallback test that also requires the absent
per-tool client selector; retain all three original TLS/session tests and the
new compatible model-schema-only test:

- test_tavily_clients_use_their_own_tool_credentials
- test_web_search_forwards_deployment_domain_filters
- test_web_search_preserves_explicit_empty_domain_filters
- test_web_fetch_falls_back_to_result_url_when_title_is_missing

No preexisting test name or test module is removed or skipped. Deferred features
are not claimed complete. Old behavior/security assertions remain except the
two stronger exact localized reply checks described above.

## Validation and release boundary

Local dependency-light checks execute the actual AST-extracted executor method,
result state machine and extraction callbacks with narrow graph/message fakes.
They reproduce stream-close/None failures first and exercise error priority,
first terminal state, task/cooperative cancellation and turn caps after repair.
The actual provider-discovery callback is checked with hermetic SSO/IAM/OIDC
configs, and structural comparisons preserve all other runtime functions and
test assertions. These are not full pytest or real-model integration tests.

The pinned patch helper protects prior repairs, both Slash follow-ups and
unchanged identity/Tavily/SSO/Telegram code, checks working/index hashes, backs
up modified files, rejects drift and checks forward/reverse application. It
creates no merge commit or deployment. Run the focused real-dependency suite
on dmdb, then resume full backend validation. Skips, remaining later tests,
frontend release/build/browser checks and live deployment compatibility remain
separate gates; do not finalize or deploy before acceptance.
