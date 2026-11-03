# LLM timing logs

Set `DEERFLOW_LLM_TIMING_LOGS=true` on the Gateway only when investigating
model latency. The `RobustChatOpenAI` adapter then emits one content-free log
sequence for each streaming request:

```text
LLM_TIMING start llm_call_id=...
LLM_TIMING first_chunk llm_call_id=... wait_ms=...
LLM_TIMING end llm_call_id=... total_ms=... first_chunk_ms=... chunks=... usage=...
```

The records contain a stable per-request `llm_call_id`, model name, sanitized
endpoint, LangChain call ID, DeerFlow `thread_id` and `run_id` when the call
belongs to a Gateway run, timings, token counts, status code when an SDK error
exposes one, and error class. `llm_call_id` is identical across the start,
first-chunk, and end record, so aggregation must group completed model calls by
that field rather than merely adding every matching log line. DeerFlow IDs use
the async-task-local context first and inherit from LangChain callback metadata
when a child task does not retain that context. They deliberately never log
prompts, generated text, request headers, API keys, or provider response bodies.

The flag defaults to disabled. Remove the environment variable or set it to
`false` after the test to avoid high-volume operational logs.
