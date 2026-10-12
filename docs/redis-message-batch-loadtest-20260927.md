# Redis message publication batching: validation plan

This experimental change applies only to Redis-backed Gateway graph `messages`.
It retains one Redis Stream entry and SSE ID per graph event; it does not merge
model token payloads. A Run flushes consecutive messages at 64 events or after
200 ms of buffering, whichever comes first, and before any other event or stream termination.
Different Runs can write concurrently, with at most 32 in-flight write requests
per single-worker Gateway process. Memory bridge delivery is unchanged.

Before applying the archive to a server, compare its existing versions of
`runtime/runs/worker.py`, `runtime/stream_bridge/redis.py`, and
`runtime/stream_bridge/base.py` and `app/gateway/services.py` to the archive. The archive contains their
complete files, including earlier local timing and Redis recovery changes;
do not overwrite a server with additional modifications. Back up all target
files before applying. The Redis ACL must allow `EVALSHA`, `SCRIPT LOAD`,
`XADD`, `XREVRANGE`, `XRANGE`, `TTL`, and `EXPIRE`. A cached Lua script checks
the same batch marker and expected predecessor Stream ID before writing on
every attempt, so a delayed original request cannot duplicate its retry or
append after a later event. A partial batch fails closed. The predecessor
lookup adds one read round trip per batch; benchmark the net effect.
This hardening covers batched `messages`; the pre-existing single-event
publish path still uses marker read-back after an ambiguous reset and needs
separate review before claiming end-to-end exactly-once delivery.

Run the targeted backend tests before building an image:

```bash
cd /opt/DeerFlow/backend
uv run --no-sync pytest tests/test_stream_bridge.py tests/test_stream_publish_batch.py tests/test_run_worker_rollback.py tests/test_wait_disconnect_handling.py -q --tb=short
uv run --no-sync ruff check app/gateway/services.py packages/harness/deerflow/runtime/stream_bridge/base.py packages/harness/deerflow/runtime/stream_bridge/redis.py packages/harness/deerflow/runtime/runs/worker.py packages/harness/deerflow/runtime/runs/stream_publish_batch.py tests/test_stream_bridge.py tests/test_stream_publish_batch.py tests/test_wait_disconnect_handling.py
uv run --no-sync ruff format --check app/gateway/services.py packages/harness/deerflow/runtime/stream_bridge/base.py packages/harness/deerflow/runtime/stream_bridge/redis.py packages/harness/deerflow/runtime/runs/worker.py packages/harness/deerflow/runtime/runs/stream_publish_batch.py tests/test_stream_bridge.py tests/test_stream_publish_batch.py tests/test_wait_disconnect_handling.py
```

Use a small Redis integration test and a few real Runs before 200-concurrency
testing. Preserve the same model, prompt, `stream_mode`, Gateway replica count,
and model-admission settings between A/B rounds. Compare terminal Run success
rates, model read/protocol errors, Redis retry/failure counts, `LLM_STREAM_PATH`
downstream resume time per chunk, and SSE first-token latency. With
`DEERFLOW_RUN_TIMING_LOGS=true`, each graph stream emits one
`agent_stream_bridge_batch_summary` with attempted `batch_count`, `event_count`,
and `max_batch_size`; slow publish logs include `event_count`. The change
reduces write round trips, not the total number of Redis `XADD` commands.

For the diagnostic image, `REDIS_STREAM_WRITE_TIMING` emits one content-free
summary per 1,000 attempts of each operation on each Gateway process. Compare
`slot_wait_total_ms/calls` and `redis_await_total_ms/calls` for
`batch_predecessor`, `batch_write`, and `single_write`. The former is local
contention for the 32 write slots; the latter includes redis-py connection-pool
wait, network, and Redis server time. A large client await with short sanitized
Redis `SLOWLOG` durations suggests time outside server command execution, but
the slowlog is a bounded sample, not a full distribution. This diagnostic
release also raises the local write limit from 16 to 32 slots; instrumentation
itself does not change batching, retries, event IDs, or SSE timing.
Do not print raw `SLOWLOG GET` command arguments; they may include message data.

For the enterprise test Redis Deployment (not represented by a manifest in this
repository), the proposed capacity experiment changes the Redis container's
CPU request/limit from `100m`/`500m` to `200m`/`1`, and memory request/limit
from `128Mi`/`768Mi` to `256Mi`/`1Gi`. Leave Redis `maxmemory=512Mi` unchanged:
observed usage was far below that ceiling. Changing Deployment resources
recreates the Redis Pod. First confirm no active Runs and record the current
Deployment specification and whether Redis Streams survive Pod replacement;
otherwise defer this change. Roll back resources and the Gateway image together
for a controlled comparison. The earlier Redis slowlog's 128 most recent
entries covered only a subset of the 200-concurrent run, so do not interpret
its maximum as a bound on all server-side latency.

The Redis integration tests must actually connect to a disposable Redis DB;
`skipped` integration tests do not validate the Lua script. Run
`uv run --no-sync pytest tests/test_stream_bridge.py -m integration -q -rs`
with `DEER_FLOW_TEST_REDIS_URL` pointing to that DB, and require passing tests
before a Gateway rollout.

Do not consider the experiment successful merely because Redis retry warnings
decline. Confirm that every completed Run has a matching terminal SSE event,
that reconnect with `Last-Event-ID` neither duplicates nor loses messages while
the cursor remains within the retained window; older cursors must produce a
visible `StreamReplayGapError` SSE event. This check runs at reconnect and does
not prove that a slow *already connected* subscriber never falls behind,
and that failure/cancellation flushes buffered events before the terminal
marker. A persistent Redis outage should still fail the Run visibly. Redis
write time and subscriber delay are additional to the 200 ms buffering target.
