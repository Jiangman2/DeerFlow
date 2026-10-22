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
