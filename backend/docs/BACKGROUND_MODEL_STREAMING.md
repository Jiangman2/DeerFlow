# Background model transport

Memory updates, context summaries, input polishing/follow-up suggestions, goal
evaluation and skill security scans use upstream streaming. Their callers still
receive a completed response, not individual tokens. This does not change any
browser API into SSE and does not expose internal prompts or memory in chat.

The host helper `deerflow.utils.streaming_llm` preserves callback/trace config,
adds LangGraph's `nostream` tag for public message isolation, and copies models
and bindings before enabling transport streaming. Both `disable_streaming` and
an explicit `streaming=False` must be overridden on that copy. The call also
passes `stream=True`. No failure path retries through `invoke`/`ainvoke`.

DeerMem retains an equivalent synchronous-only helper in its own `core/llm.py`
so the vendored backend remains host-independent. Its existing worker/thread
path and tracing callback are preserved; it must not create another event loop
or reuse the main agent's cached asynchronous HTTP client.

All chunks are merged before parsing or persistence, including structured text
and usage metadata. Iterators close on completion, failure and asynchronous
cancellation. Empty streams are errors. OpenAI-compatible Chat Completions must
include `finish_reason=stop`; early EOF, token-limit truncation or filtering do
not count as complete JSON even when the received prefix parses successfully.
Native OpenAI Responses streams instead accept the terminal `status=completed`
and reject incomplete statuses.
Existing caller failure policies remain: no partial memory write, no partial
summary replacing context, and no security approval after a failed stream.
The security scanner's existing 45-second deadline and retry budget are unchanged.

Title generation and the OA streaming transport are not modified. Graph
`stream_mode` remains distinct from provider streaming. Audio transcription,
embeddings, external services and model-independent tools are outside this patch.
Streaming is not a remedy for every provider 503 or capacity failure.

Regression coverage includes actual ChatOpenAI/RobustChatOpenAI HTTP payloads
through offline MockTransport, all six invocation paths (summary sync/async),
public graph token isolation, shared model immutability, structured blocks,
usage merging, cancellation/disconnection, truncated responses and fail-closed
security/memory/summary behavior. No real provider credentials or requests are
required. Run these tests again in the Linux ARM64 release image before rollout.
