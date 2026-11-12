# Model monitoring lifecycle correction

The LangChain 1.3.3 `agenerate` path can omit terminal callbacks when the caller
cancels its gather. The provider has exited, but the callback collector retains
its active token. This is reproduced with a synthetic provider, including
`ainvoke(stream=True)`, without model or production access.

Wrap the configured provider's async generation boundary with an invocation-local
scope. Record only tokens started in that invocation; on exit finish any token
that terminal callbacks have not already removed. Cleanup is idempotent, preserves
the provider's result/exception and does not clear another concurrent invocation.
A timeout caught in the same caller task is covered. Direct async streams retain
their generator-close lifecycle and cross-task handoff. Provider configuration,
serialization identity and synchronous invocation behavior are preserved.

The dashboard calls this metric “当前未结束调用” and explains that it counts
instrumented application calls, not running tasks or provider-side concurrency.
Add the oldest active call age, computed with a monotonic clock and aggregated
across fresh process snapshots. Missing age from an old collector remains unknown.
Do not expire legitimate long calls merely because they are old. Cancelling an
application call does not prove the upstream provider has stopped computation.

Validation: 136 targeted backend tests passed, including real LangChain synthetic
normal/error/cancel/timeout/batch/stream cases and provider factory compatibility.
Frontend TypeScript and changed-file ESLint/Prettier checks passed. Local testing
uses LangChain Core 1.3.3; no real model, Redis or production database is contacted.
The sealed finite source delta projects the exact parent for historical checks;
existing release receipts and original baseline files remain unchanged.

This is source validation, not deployment acceptance. Release Gateway and Frontend
through their normal image pipelines. Restarted collectors begin with fresh local
active state; old Redis process snapshots age out normally. Historical samples
already recorded by the previous version are not rewritten. Confirm post-rollout
normal/cancelled calls and long-call ages before interpreting the new numbers.
