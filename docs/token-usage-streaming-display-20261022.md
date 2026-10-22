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
