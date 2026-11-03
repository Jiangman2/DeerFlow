# Browser-only smooth streaming: 20261003-r2

## Release baseline

This patch is reconciled against `fix/upload_test_env` commit
`e2510d6c6f9287f314ebdfc87c6691fe5a77aa5c`. Do not use the earlier r1
archive on that checkout. The read-only preflight verifies every existing
payload file against its exact baseline or already-installed replacement;
it tolerates only LF/CRLF differences, not newer source edits.

## Behavior

Identified live assistant Markdown reveals appended text over a 300 ms local
window at the existing 50 ms cadence. Unlike the previous minimum-64-character
step, a ten-character batch spans several frames. Incoming batches do not
restart the pending tick or extend its deadline. A large burst catches up
immediately to within 256 trailing UTF-16 units. Grapheme boundaries are used
where Intl.Segmenter is available, with a code-point fallback.

Mounted history, completion, cancellation, non-append replacements, a different
message/thread, hidden tabs and reduced-motion preferences show all received
text immediately. Effects clean up timers and accessibility listeners.
The existing useDeferredValue scheduling is retained with keyed snapshots so
navigation cannot render an old deferred answer.

SDK state, copy/export text, durable history, tool receipts and artifact cards
remain authoritative and immediate. This patch preserves history virtualization,
stable group references, clarification rendering, Markdown security, live preview
link handling and final code highlighting. No generated AI-elements component
is changed.

There are no Gateway, Redis, database, SSE event-count or model-call changes.
Extra display frames are browser work, not server capacity improvements.
The browser cannot reveal text the upstream has not sent, so upstream pauses
are still pauses. This is bounded batch smoothing, not a guaranteed one-letter
typing speed.

## Verification

On the isolated exact-baseline snapshot, the related Rstest suite passed 137
tests in 14 files, including 10 real React/Happy DOM lifecycle tests under Strict
Mode. Full `tsc --noEmit` passed; scoped ESLint and Prettier passed.
No complete Next production build, enterprise browser test or deployment is
claimed. Dependencies and lockfiles are not changed by this package.

## Release verification

Run the package preflight, back up the affected files, then rebuild only the
frontend using the normal network/CA/proxy settings. Building is not deployment.
Do not restart Gateway/Redis for this feature and do not publish during a load test.

After deployment, verify Chinese text, fences, tables, preview/artifact links,
copy/export, stop, navigation, reduced motion and background tabs. The final text
must be exact. The Network panel must still show the ordinary existing stream,
not per-character requests. Compare request and Redis publication metrics under
the same workload. Keep the saved pre-release frontend image for rollback.
