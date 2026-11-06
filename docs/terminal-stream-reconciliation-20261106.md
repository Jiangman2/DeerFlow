# Terminal run stream reconciliation

Status: source candidate, not deployed. Frontend only; no Gateway, database,
model, proxy, or Kubernetes configuration change.

A browser could remain in Thinking after the durable run reached success when
its SSE connection never closed. Existing recovery skipped a locally loading
stream. Refreshing loaded the completed checkpoint, but leaving the page open
did not correct the stale state.

The API stream adapter now checks the exact current thread/run every 30 seconds
and on visible-page focus/visibility return. Both ordinary runs and the isolated
PPT runtime use their owning API. Reads have an 8-second combined deadline and
do not overlap. Pending/running, unknown statuses, missing records and read
failures do not finish a stream. Polling begins only after authoritative run
identity is available.

After success/error/timeout/interrupted is confirmed, fetch the final checkpoint
values before closing only the local stream transport. Yield those values and
return normally through the SDK, preserving history hydration, finish listeners,
artifact state, token-usage cache invalidation and loading cleanup. This path
never calls the cancellation endpoint. Explicit user Stop still cancels the
run. Abort/unmount removes timers/listeners and prevents a late lookup from
settling another run. Completion signals do not accumulate promise handlers
per token on long streams.

Validation covers stalled reads, all terminal statuses, failed status/checkpoint
reads, deadlines, identity mismatch, cancellation, focus and periodic recovery,
final answer/artifact visibility, a subsequent independent run, and manual Stop.
Existing API/threads tests and artifact/init-ordering browser regressions remain
part of qualification. Synthetic browser tests are not production acceptance.

The source registration under `release/terminal-stream-reconciliation-20261106`
preserves the prior API client verbatim and verifies the exact candidate before
projecting the old bytes for historical audits. Historical release inventories
are not modified. Deploy a newly built Frontend to activate the change; Gateway
does not need a rollout for this fix.
