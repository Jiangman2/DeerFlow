# BoxLite warm-pool lifecycle reconciliation

Applies only to the unfinished isolated dmdb merge, branch
`fix/upload_test_env-enterprise-merge-20260930`, HEAD `33688ae5`, incoming
`523f1f0f`, after batch three. This repair does not commit or deploy the merge.

The focused server run passed 248 tests and 8 subtests, with four existing
BoxLite lifecycle failures. Pagination and authorization regressions passed.
The four failures share a concrete runtime contract mismatch: the preserved
shared `WarmPoolLifecycleMixin` treats a false result from
`_destroy_warm_entry` as deferred destruction and restores tracking, but the
incoming BoxLite callback dropped its pre-merge `True`/`False` results and
returned `None` even after normal close. Thus reset/idle/capacity cleanup
reparked resources whose wrapper had already closed.

Restore only the pre-merge BoxLite callback: boolean return annotation,
`True` after a normal close, `False` after an escaping exception. The provider
file then exactly matches its reviewed pre-merge bytes. Do not modify the
shared lifecycle mixin: its refusal/retry behavior protects AIO Sandboxes
with active cross-process execution leases. Do not broaden `BoxliteBox.close`
semantics or claim this repair independently confirms SDK stop success;
the existing wrapper's handling of underlying SDK errors is unchanged.

All original BoxLite, shared lifecycle, Gateway, authorization, pagination,
security and performance files are protected against working/index drift.
No tests are removed or skipped. Add eight dependency-light regression cases
for the real callback and shared lifecycle: return values, successful/failed
idle cleanup, oldest eviction, failed-candidate retry, bounded all-failed
eviction, and combined active/warm capacity. Successful cleanup must not
repark closed entries; unsuccessful cleanup must retain retry tracking.

Local checks extract the actual callback, execute the unmodified shared mixin,
prove the new tests detect the previous callback mismatch, and check patch,
backup, drift, and reverse-application mechanics. They do not replace the
server's dependency-complete pytest run. Re-run BoxLite, shared lifecycle,
AIO provider and previously passed focused modules before continuing the full
suite. The earlier whole-suite wait is still not localized; this is not a
final merge/deployment gate.
