# Gateway Run admission parallelism trial

`RunManager.create_or_reject()` previously held its process-wide registry lock
while awaiting `RunStore.create_run_atomic()`. An unrelated thread therefore
waited for that SQL call before it could begin its own Run insertion.

This trial uses a per-thread admission lock across the local active-Run check,
store insert, and in-memory registration. A process-local semaphore permits at
most eight concurrent store insertions for distinct threads. The registry lock
still protects the in-memory Run index; the database's partial unique index
remains the cross-process single-active-Run authority. The legacy `create()`
helper shares the per-thread gate but keeps its existing conservative upsert
path. No Redis, model, or SSE event semantics change.

## Validation before rollout

1. Compare the packaged `manager.py` and test file against the deployed source.
   Stop if the differences include unrelated changes.
2. Back up those two deployed files before applying the package.
3. Run `ruff check`, `ruff format --check`, and
   `pytest tests/test_multi_worker_run_ownership.py -q --tb=short` in the
   backend environment. The new tests verify eight overlapping insertions,
   same-thread rejection, registry responsiveness during a blocked insert, and
   preservation of a Run that completes while an interrupting insert waits.
4. After rollout, start with a small admission smoke test. Compare JMeter
   `/runs` or `/runs/stream` response-header latency, PostgreSQL client count,
   new Run count, and Run error rate against the previous image. Client read
   timeout must exceed the measured admission tail latency; a three-second
   timeout is not a reliable server-capacity verdict.

Eight is a ceiling per Gateway process, not a reserved database pool. With 14
single-worker Pods the theoretical application-side ceiling is 112 simultaneous
inserts, but actual concurrency is also bounded by connection pools and SQL
latency. Do not increase the ceiling further without measuring those limits.
