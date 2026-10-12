# DeepSeek stream-admission load-test candidate (2026-09-26)

This candidate changes only `RobustChatOpenAI` async streaming. It adds an
optional, content-free per-worker admission gate before the outbound LLM
request. It does **not** change Redis, request timeouts, retry behavior, or
non-streaming calls. It is disabled unless both environment variables are set:

```text
DEERFLOW_LLM_STREAM_LIMIT_MODEL=DeepSeek-V4-Flash
DEERFLOW_LLM_STREAM_LIMIT_PER_PROCESS=30
```

The test deployment has five single-worker Gateway Pods. A per-worker limit of
`30` therefore bounds simultaneous DeepSeek streams to at most `150`. This is a static aggregate bound, not a distributed
semaphore. Do not change the replica or worker count during the comparison.
Other models are unaffected. Unset the limit or set it to `0` to disable.
Previously observed 30-plus successful Runs per Pod are cumulative counts, not
measurements of 30 concurrent model streams; this tier needs its own load test.

## Apply checks

The archive contains these code/test payload files plus this guide:

- `backend/packages/harness/deerflow/models/robust_openai.py`
- `backend/packages/harness/deerflow/models/stream_admission.py`
- `backend/app/gateway/app.py`
- `backend/tests/test_model_stream_admission.py`

Before replacing `robust_openai.py` on the test server, verify its current
SHA-256 is exactly:

```text
8a5539dedbc1d9cf529d7da67005bb8df271ac7b2ec42b627591f9157882ab04
```

If it differs, stop and merge the small wrapper change instead of overwriting
server-side edits. Back up the existing file first. The candidate's new
`robust_openai.py` SHA-256 is:

```text
7794fd2ff003279b5d6965e2d3148d3450cadd9e08b66616249d2daac11c2574
```

Run the new unit tests and existing robust-model tests before building an
image. Do not roll out mixed old/new Gateway Pods while Runs are active. The
setting is startup-only; change it through the Gateway Deployment environment
and wait for all five Pods to be ready before starting each load-test tier.
Gateway startup rejects an invalid enabled limit or a missing model selector.
Because the archive also contains `app.py`, verify that file's baseline SHA-256
against this expected test-server value before replacing it:

```text
b732cb618f31a289fcb94a5814cbceff8bbe4dab3d3fe857cf59b627acdfe9f5
```

Stop if either baseline hash differs; do not overwrite a server-side edit.

## Comparison

Keep 200 intended tasks and the task content constant. Compare the current
unlimited baseline with per-worker limit `30` in a clean run.
For each tier, record distinct threads, final Run status, model-call success
and error counts, p95/p99 on-wire LLM duration, `LLM_ADMISSION wait_ms`, queued/active counts, total
completion time, and throughput. `LLM_TIMING total_ms` deliberately excludes
queue wait; `LLM_ADMISSION` reports that delay separately. A lower error rate
alone is not sufficient if the queue never drains.

Do not treat an incomplete model response as a resumable token stream. This
candidate queues complete requests and retains the existing retry/stream
semantics.
