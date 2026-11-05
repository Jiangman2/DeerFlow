# Streaming reply token usage display

The reported GLM reply showed visible reasoning/answer text while the per-turn
footer showed three zero counters, then populated them at completion. The
footer ignored its loading prop and treated an all-zero snapshot as settled
usage. The caller also passed the whole thread's loading state to every turn.

The enterprise GLM profile already enables `stream_usage`. Its supplied 5.3
documentation describes empty-choice statistics frames but does not promise
per-chunk token counters. Neither the screenshot nor the existing canary summary
establishes exactly when the live provider sent usage; no new production model
request was made for this UI correction.

The per-turn footer now distinguishes pending, received and unavailable usage:

- A streaming reply with missing or all-zero usage displays a waiting message.
- Nonzero usage is displayed as soon as it arrives, including before the turn
  ends, with an explicit indication that collection continues.
- A finished/stopped reply with no usage displays unavailable. Explicit final
  zero usage remains zero.
- Historical turns use their own loading state, independent of a new reply.

No token estimates, provider requests, persisted counters, accounting rules or
backend code changed. If the provider returns usage only at the end, the UI
cannot show earlier exact counts. This correction avoids presenting that wait
as zero consumption.

Validation: 25 focused frontend tests passed, including six rendered component
tests for waiting-to-live-to-final transitions, cancellation/unavailable usage,
explicit zero, multi-call turns, historical turns and disabled controls.
TypeScript no-emit and targeted ESLint checks passed. These are local regression
results, not production GLM stream or browser acceptance. Operator instruction:
hold deployment; collect the remaining requested refinements before cutover.


## DeepSeek comparison and field observation

GLM inherits the same RobustChatOpenAI conversion/streaming path used by the
DeepSeek V4 integration. The GLM deadline and terminal/DONE guard do not buffer
usage until completion. Four real-SDK, synthetic fragmented SSE comparisons
cover both adapters with either midstream cumulative usage or terminal-only
usage. With explicit cumulative mode, both yield the first usage immediately
and aggregate the repeated final snapshot without double counting. All 70 tests
in the completion/robust adapter suites passed.

The `cumulative_stream_usage` flag changes aggregation of repeated upstream
snapshots; it cannot make a provider emit usage more frequently. Do not enable
it for GLM just to make counters move without checking the response contract.
The current production timing remains unverified.

`deploy/enterprise/package-glm-usage-timing.py` packages the standalone controller
and observer. It reads non-sensitive usage flags, selects a stable owned Gateway
Pod and invokes the installed factory/adapter in a short-lived process. A single
synthetic GLM request is bounded to 512 output tokens, 120 seconds and 2 MiB.
The diagnostic disables retries/tracing, preserves configured usage/routing,
checks the exact enterprise endpoint, and records only numeric counters and
relative timestamps at SDK-normalized, converted and yielded stages. It does
not export response text/reasoning or credentials and never updates workloads.
A once marker and a local report are written on the controller. The report is
one-request adapter evidence, not an end-to-end browser acceptance or a promise
about all GLM requests. The observer does not inject replacement provider code.

Fifteen offline diagnostic/controller tests passed (plus five readiness
subtests), including real-SDK live/final usage observation, provider 503 without
retry, changed endpoint refusal, redaction and no-request preflight. The host
controller targets Python 3.7; the embedded observer runs in Gateway Python.
Production deployment remains on hold.

## Authorized combined frontend release

The operator subsequently confirmed with the GLM service team that this endpoint
returns a final usage summary rather than live token counters. This is an
operator-reported provider contract, not a captured production response trace.
The waiting/received/final UI therefore remains the intended behavior; no
estimated counters or backend usage-setting changes are required.

The operator authorized including this display correction with Data QA progress
after the ongoing Gateway feature rollout completes. The previously preloaded
Frontend `15d1327740e688faf0617d1d8fb9f662618a963624ba7280a6d0efb54ed70475`
does not contain this correction and must not be described as the combined
candidate. A new source snapshot applies only commit
`1c74cfe3a37a9d85cf38bdde4e002c5f722adf26`'s five frontend runtime files and its
component tests to that qualified build context. It retains the Data QA code
and all other source bytes. Source provenance is recorded in
`deploy/enterprise/data-qa-token-frontend-source-20261022-r1.json`.

The combined source passed 25 token-usage tests and 6 Data QA progress tests;
targeted ESLint also passed. The new image must separately complete build,
runtime and OCI qualification, then be preloaded on both frontend nodes.
Activation remains ordered after the accepted Gateway receipt, because the
currently running Gateway activator verifies that Frontend stays unchanged.
The frontend-only correction neither enables OA v2 nor modifies provider usage
collection, billing, database schema or GLM request configuration.

## Runtime deployment accepted 2026-10-23

The combined image
`sha256:b6c17e31ebcadb3c514fd2c3f87752e0eb84ee3e28ccc921366bac6a0bf91cd5`
completed build/runtime/OCI qualification, both node preloads and the
receipt-bound frontend cutover. The operator reports Frontend generation 226
with both Pods passing immutable-source and HTTP JavaScript verification;
Gateway 384, Runner 55 and other service specs/configuration are preserved.
Evidence: `deploy/enterprise/data-qa-token-frontend-activation-field-20261023-r1.json`.

Authenticated browser acceptance remains pending: after refreshing the page,
a GLM reply without live usage should show the waiting label, then display
the provider's summary once returned. A Data QA question should display live
progress before the final answer. Do not describe this as real-time token
counting or a completed production business test.
