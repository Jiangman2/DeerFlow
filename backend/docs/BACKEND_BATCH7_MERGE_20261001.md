# Isolated merge reconciliation, batch seven — 2026-10-01

Target: isolated dmdb branch `fix/upload_test_env-enterprise-merge-20260930`,
HEAD `33688ae5`, MERGE_HEAD `523f1f0f`, with batch six applied. No final commit
or deployment. Full-suite r8 stopped at 20 failures after 3418 passed,
19 skipped, and 27 subtests passed (36%); it was not full-suite success.

## Implemented and fixture-only corrections

- `extract_outline` reads with `utf-8-sig`, accepting BOM and ordinary UTF-8
  without changing line numbers, heading recognition, limits, or conversion.
- JSONL's three record scanners split on physical LF, not Python's expanded
  Unicode line boundaries. U+0085/U+2028/U+2029 stay inside valid JSON strings.
  Encoding, append/batch protocol, sequence allocation, and locks are unchanged.
  All new Unicode round-trip/reopen/malformed-record tests remain.
- Only ordinary Gateway startup may reconcile orphaned runs, publish recovered
  stream endings, and mark thread errors. The normalized `ppt-master-runner`
  profile skips that recovery block; startup heartbeat and lifespan drain remain.
  All incoming profile tests remain, and the original ordinary-Gateway regression
  is restored by name through the same asserted ordinary-profile path.
- Three AIO token fixtures bypassing `__init__` now initialize `_default_env={}`.
  Their bash fixture matches existing SDK auto-created sessions, not unimplemented
  explicit create/close session calls. All token tests remain, including structured
  env forwarding, command exit marker, token-not-in-command, and invalid-key
  rejection before execution. AIO runtime and proxy/CA defaults are untouched.
- IAM tests inject fake config with empty or registered canonical/legacy callback
  paths. They exercise the real cookie-path resolver without requiring real
  `config.yaml`; no authentication or cookie implementation is changed.

## Explicit deferral, not runtime repair

Each removed test/function is absent from the pre-merge snapshot. The pinned
source and automatic pre-apply archive retain the removed bytes. All preexisting
tests remain. These limitations must be evaluated against actual runtime usage:

| Incoming-only scope | Deferred behavior |
| --- | --- |
| Two `test_await_drained_*` functions in `test_file_io.py` | The shared `await_drained` API is absent. Existing dedicated-pool/context propagation tests and implementation remain. |
| `app/gateway/memory_shutdown.py` plus `test_gateway_memory_shutdown_cancellation.py` | The new helper imports that absent API and has no callers in the reviewed Gateway startup/shutdown chain. Defer this unused incomplete module and its three tests; the existing lifespan memory flush remains unchanged. |
| First method in `test_gateway_run_drain_repeated_cancellation.py` | Multiple cancellations can detach the old shielded drain. Keep the two passing failure/first-cancellation tests and all original drain tests. |
| `test_jsonl_event_store_cancellation.py` | A cancelled async await can return before its disk worker finishes, allowing later mutations to race that worker. Cancellation-safe mutation ownership is not implemented. |
| `test_jsonl_event_store_lock_lifecycle.py` | Thread deletion may evict a lock generation while a waiter still references it. The corresponding DB limitation documented in batch six is also still unresolved. |

The JSONL Unicode correction does NOT fix cancellation ownership, partial batch
failure, multi-process safety, or lock eviction. Do not enable JSONL for concurrent
multi-process use. Removing unsupported incoming tests is scoped feature deferral,
not evidence those behaviors are safe; restore them with their complete runtime
changes and a concurrency review before relying on those guarantees.

## Verification and release boundary

Run focused conversion, JSONL Unicode/async I/O, file-I/O context, existing run
drain/lifespan/recovery, AIO/token, and IAM/OIDC tests. Then resume full backend
tests and frontend checks. Passing the supported suite does not resolve deferred
limitations. Retain the existing security/performance guards. Separately validate
the exact runtime/release configuration and deployment backend, storage, lease,
and termination-grace settings before deployment; do not export production
secrets or copy runtime config into source/tests. Actual ReadabiliPy Node/npm
release-image readiness remains a separate unverified gate from earlier batches.
