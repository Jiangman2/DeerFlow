# Isolated merge reconciliation, batch six — 2026-10-01

Baseline: dmdb isolated merge branch, HEAD `33688ae5`, MERGE_HEAD `523f1f0f`,
with batches one through five applied. No merge commit or deployment is made.
The r7 full-suite run stopped at 20 failures, with 2546 passed and 19 skipped;
it did not establish full-suite success.

## Narrow implemented corrections

- DingTalk rejects a missing/blank/non-string routing key before `/connect`,
  card bookkeeping, or publication. P2P requires a sender ID; groups require
  a conversation ID. A group with valid conversation ID and unknown sender
  remains accepted, subject to existing allowed-user and trusted-identity rules.
- Feishu removes leading mentions only when the remaining text is a recognized
  command. Binding stays explicit `/connect`; ordinary/unknown slash text keeps
  its mentions. Shared command classification, topic routing, and auth are unchanged.
- Dashboard tests check the exact six implemented agent names, including the
  mail assistant. The aggregator, source isolation, dates, and metrics are untouched.
- Sandbox Nginx tests exercise the tracked Python patcher for unexpanded and
  expanded ports, LF/CRLF, old timeout replacement, idempotence, unrelated route
  preservation, and fail-closed target counts/duplicate blocks. They no longer
  require an obsolete inline shell implementation.
- Kubernetes bundle tests retain existing manifest/Secret/NO_PROXY checks and
  verify all six proxy/CA environment placeholders in the tracked README and
  commented config template instead of reading a gitignored `config.yaml`.
  This proves the repository contract only, NOT a deployed/release config.

## Explicitly deferred unsupported incoming increments

All pre-merge test functions are retained. No runtime in these four domains is
changed. Removed incoming tests remain in the pinned source commit and the
automatic pre-apply backup; restore them with a complete reviewed implementation.
These known limitations MUST NOT be described as fixed or covered by passing tests:

| File | Deferred incoming tests | Unsupported behavior |
| --- | --- | --- |
| `test_db_event_store_lock_lifecycle.py` | Entire incoming-only module (one test) | Write-lock waiter handoff can outlive eviction during event deletion. |
| `test_discord_channel.py` | `test_discord_thread_mapping_persists_across_restart`, `test_discord_stop_flushes_loaded_thread_mappings`, `test_discord_stop_does_not_clobber_store_before_load` | New restart/load/stop-flush expectations are not implemented. Existing mapping behavior remains unchanged. |
| `test_durable_context_middleware.py` | `test_new_user_turn_closes_delegation_an_earlier_run_left_in_progress`, `test_stopped_delegation_is_not_reported_in_progress_to_the_next_turn` | Earlier-run unfinished delegations are not automatically closed by this implementation. Passing reverse cases and all old tests remain. |
| `test_e2b_sandbox_provider.py` | `test_list_dir_uses_status_marker_and_preserves_trailing_space`, `test_list_dir_rejects_incomplete_remote_traversal`, `test_list_dir_closed_client_fails_loudly`, `test_glob_rejects_missing_status_marker`, `test_glob_propagates_raw_output_truncation_after_filtering`, `test_grep_rejects_partial_remote_failure` | E2B does not implement the proposed terminal status-marker/partial-failure protocol. Existing search/secret tests remain. |

## Required next gates

Run focused routing, dashboard, Discord, durable-context, E2B, and enterprise
bundle tests, then resume the full backend suite and frontend checks. The Node/npm
ReadabiliPy release-image readiness noted in the earlier batch is still unverified.
Before any deployment, separately run `scripts/validate_enterprise_release.py`
against the exact locally retained release `config.yaml` and enterprise runtime
manifest. Do not copy production secrets into source, diagnostic archives, or tests.
Evaluate the deferred limitations against test/release usage before deployment;
an all-green remaining test suite does not eliminate them.
