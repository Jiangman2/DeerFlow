# Backend batch five: malformed credentials and incomplete tool-ID recovery

Only for the isolated unfinished dmdb merge on
`fix/upload_test_env-enterprise-merge-20260930`, HEAD `33688ae5`,
MERGE_HEAD `523f1f0f`, after batch four. No commit or deployment is performed.
The preceding focused run passed 669 cases and 8 subtests. The full-suite
retry stopped at the 20-failure limit at 19% with 1847 passes, 18 skips and
19 subtests; it is not a complete suite result.

## Minimal credential-reader hardening

The reviewed pre-merge, source and merged credential_loader.py are identical.
The incoming six malformed-container test cases expose an existing unchecked
`.get()` on a non-object JSON root or claudeAiOauth value. Preserve those tests
and all preexisting tests. Reject either non-dictionary container as an
unavailable credential and continue the existing override-to-default lookup.
The patch adds only two type checks and broadens the extractor annotation to
Any; valid-container processing, direct environment/FD precedence, expiry,
Codex loading and existing error handling remain unchanged. Debug messages
contain no credential values, bodies or paths. This is narrowly added defensive
behavior, not a claim of restored pre-merge implementation. Token-field type
validation is outside this patch.

## Individually deferred incoming-only recovery group

The entire `TestMalformedToolCallIdRecovery` class was absent before the merge.
Its 16 methods (18 parameterized cases) require ID allocation and turn-scoped,
name-aware/ambiguity-safe result reassignment that neither incoming source nor
merged runtime implements. The runtime difference between source and merge
is only retained timing instrumentation. Never replace those hooks or invent
a broad replay/causal-pairing algorithm simply to clear a merge gate.

Under the approved incomplete-feature deferral, omit only this incoming-new
class; retain every other class, helper, decorator and test AST unchanged.
Some cases beyond the 20-failure cutoff were not executed; this defers the
incomplete feature as a group, not just the first reported failures. Runtime
names/argument sanitization, invalid-call retry, valid-ID pairing and timings
are unchanged and protected by the helper. This does not fix malformed-ID
history: missing/blank IDs remain a known unsupported recovery case.

Reintroduce the exact class from source commit `523f1f0f` or the automatic
before-repair archive together with its completed implementation. The methods
are: malformed_structured_tool_call_id_is_normalized,
empty_tool_call_id_keeps_its_paired_tool_result,
multiple_empty_ids_get_distinct_ids_and_pair_in_order,
empty_id_invalid_tool_call_is_normalized, empty_id_raw_provider_tool_call_is_normalized,
only_the_serialized_view_of_a_call_gets_a_recovered_id,
raw_view_shadowed_by_invalid_calls_gets_no_placeholder,
none_id_call_reclaims_its_own_none_id_result,
dangling_call_does_not_consume_a_later_turns_result,
orphan_result_is_not_adopted_by_a_later_malformed_call,
sibling_call_does_not_consume_its_neighbours_result,
nameless_result_is_dropped_when_several_siblings_are_eligible,
identical_parallel_calls_pair_with_their_results_in_order,
identical_parallel_calls_drop_a_lone_ambiguous_result,
result_naming_no_call_is_dropped_rather_than_misattributed,
valid_ids_are_left_byte_for_byte_unchanged (all prefixed `test_`).
No pre-merge test or module is removed; no skip/xfail is introduced.

## Verification boundary

Local dependency-light checks exercise the real credential loader with isolated
synthetic inputs and verify unchanged preexisting test ASTs. Patch fixture
checks cover branch/HEAD/MERGE_HEAD/index/hash guards, backup, protected drift
rejection and reverse-check mechanics. They do not establish remote pytest,
real credential validity or model/provider behavior. Run the retained credential,
dangling-call, Codex and factory tests remotely, then resume full-suite review.
Do not create the final merge commit or deploy yet.
