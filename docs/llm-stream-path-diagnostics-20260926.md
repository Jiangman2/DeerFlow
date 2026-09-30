# Gateway model-stream path diagnostics

Enable `DEERFLOW_LLM_TIMING_LOGS=true` on Gateway to produce a content-free
`LLM_STREAM_PATH event=end` record for each asynchronous `RobustChatOpenAI`
stream. It has the same `llm_call_id`, `deerflow_run_id`, and
`deerflow_thread_id` as the existing `LLM_TIMING` records. The change does
not alter the configured model timeout, admission limit, or retry policy.

The summary separates three intervals:

- `upstream_next_*`: time spent awaiting the next **raw** provider chunk,
  excluding time spent in the callback or downstream consumer. A failed await
  is included and its duration is also `upstream_error_wait_ms`.
- `callback_*`: time spent awaiting `run_manager.on_llm_new_token`.
- `downstream_resume_*`: time suspended after yielding a processed chunk until
  its consumer requests the next one. This includes downstream work and task
  scheduling, not only execution within one callback.

Each group reports a count, total and maximum milliseconds, and a slow count.
The threshold is 1 second for `upstream_next_over_1s` and 100 milliseconds
for `callback_over_100ms` / `downstream_resume_over_100ms`.

Interpret a failed call by joining `LLM_TIMING end` and `LLM_STREAM_PATH`
on `llm_call_id`. A long `upstream_error_wait_ms` with small callback and
downstream maxima supports a provider/transport stall. A long callback or
downstream maximum supports local backpressure, but does not by itself prove
that backpressure caused the provider to disconnect. All measurements include
ordinary event-loop scheduling; `upstream_next_*` is not a wire-level packet
gap. If the SDK fails before the raw-chunk loop starts, all three counts can
be zero. Abrupt worker termination may prevent an end summary entirely.

No prompt, reasoning text, answer text, API key, or raw exception message is
included. Avoid printing response bodies when sharing the logs.
