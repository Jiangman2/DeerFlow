# Backend reconciliation: pagination, Guardrail tests, BoxLite tests

Applies only to the unfinished isolated enterprise merge, branch
`fix/upload_test_env-enterprise-merge-20260930`, HEAD `33688ae5`, incoming
`523f1f0f`, after the artifact repair. This does not commit or deploy the merge.

The next diagnostic stopped normally after 20 failures, with 621 passes, in
75.41 seconds. It did not identify the earlier wait near 8%.

## Assistant pagination

The new compatibility tests require `limit` in 1–1000 and `offset` >= 0, but
the runtime request model did not enforce either bound. Add Pydantic `Field`
constraints so invalid HTTP bodies produce validation errors before handler
execution. Defaults remain `limit=10`, `offset=0`; filtering, positive offsets,
the maximum valid limit, and filesystem offloading remain unchanged. Keep the
complete incoming assistant compatibility test module unchanged.

## Deferred extended Guardrail identity propagation

The incoming authorization-provider tests changed their shared request helper
to pass `channel_user_id`, `is_internal`, and `authz_attributes` to a dataclass
that does not accept them. They also expect an adapter `default_role` argument
not present in the reviewed baseline. The corresponding middleware tests assert
those same missing fields. Guardrail request, middleware, and adapter runtime
files are byte-identical to their pre-merge versions; adding just the dataclass
fields would not complete the enforcement chain.

Defer only this unmatched integration increment by restoring the full
pre-merge authorization-provider and Guardrail middleware test modules,
including the original identity mapping, non-internal default, sync/async
decisions, metadata, exception propagation, attribution, and fail-closed tests.
No test module is removed or skipped. Keep the separately implemented Principal
builder and its incoming tests unchanged, without claiming its integration into
the old adapter. Do not alter authorization runtime or disable enforcement.
Previously repaired Gateway provenance/sender scrubbing remains protected.

## Deferred BoxLite status-marker protocol

Five newly added BoxLite tests require structured find/search status markers,
untrimmed filenames, and incomplete-traversal/truncation handling that the
unchanged runtime does not implement. Restore the entire pre-merge BoxLite
test module, retaining every original test definition. Do not inject a new
remote shell protocol into this merge or change Sandbox images based on mocks.
Preserve the incoming provider warm-pool lifecycle change separately; it is
unrelated to the BoxliteBox find/search framing mismatch.

## Verification

Local checks cover the actual Pydantic pagination model, existing pure adapter
regressions, preserved pre-merge test definitions, unchanged enforcement/runtime
guards, and patch/backup/drift/reverse mechanics. They do not replace the server's
dependency-complete pytest run. Re-run focused authorization, middleware,
pagination, BoxLite, and existing Gateway trust-boundary tests, then continue the
full suite. This batch alone is not a final merge or deployment gate.

The local checks retained 110 original test definitions byte-for-byte, executed
26 existing pure authorization test bodies against the actual stdlib-only
runtime modules, and checked 11 pagination model/handler cases. FastAPI HTTP
validation and the dependency-complete middleware/Sandbox tests remain server
verification obligations; these local checks are not a full pytest run.
