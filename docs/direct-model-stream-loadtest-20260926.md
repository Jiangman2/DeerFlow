# Direct DeepSeek stream test (standalone)

`scripts/direct_model_stream_loadtest.py` calls the supplied OpenAI-compatible
Chat Completions URL directly from the machine running it. It does not import or
call DeerFlow, Redis, PostgreSQL, or a Sandbox. Running it on the Kubernetes
host therefore tests a **different network path** from running it in a Gateway
Pod; compare both paths only after collecting independent results.

Use the Python environment with `httpx` installed, e.g.
`/opt/DeerFlow/backend/.venv/bin/python` on the test server. TLS verification
stays enabled. The script honors the host's normal proxy and `SSL_CERT_FILE`
environment configuration. It never prints or stores request text, model text,
reasoning text, or the API key. When run interactively it prompts for the key
without echo; for non-interactive operation set `DEEPSEEK_API_KEY` privately.

## Run order

1. Finish the DeerFlow/JMeter test first. Do not overlap these loads.
2. Calibrate the synthetic input against the provider's actual `prompt_tokens`:

   ```bash
   /opt/DeerFlow/backend/.venv/bin/python scripts/direct_model_stream_loadtest.py --calibrate-only
   ```

   Record `calibrated_repeats`; the script refuses to continue if the observed
   prompt-token count is more than 10% from 15,000. Usage metadata must be
   present. The calibration makes two cheap streaming requests capped at 16
   output tokens each. These short **calibration-only** calls disable thinking
   because a 32,768-token thinking budget conflicts with that 16-token output
   ceiling; the full-length test calls enable thinking.
3. Run one **full-length** connection first, replacing `REPEATS` with the
   calibrated value:

   ```bash
   /opt/DeerFlow/backend/.venv/bin/python scripts/direct_model_stream_loadtest.py \
     --repeats REPEATS --concurrency 1 \
     --output /home/cnooc/deerflow-log-archive/direct-model-single-20260926.jsonl
   ```

   A clean SSE termination is `protocol_complete=true`. The stricter
   `target_met=true` requires the provider-reported output usage to reach
   55,000 **total completion tokens**, potentially including hidden reasoning.
   By default the full-length calls match DeerFlow's current model settings:
   `enable_thinking=true`, `thinking_budget=32768`, `max_tokens=65536`, and
   600-second read-inactivity timeout. The visible answer may be substantially
   shorter than 55,000 tokens. `max_tokens` is only a ceiling, not a guarantee
   of that many tokens. The synthetic instruction scales its approximate line
   count with `--output-target`; that option is a goal, **not a generation cap**.
   For a shorter exploratory call, lower `--max-tokens` and `--thinking-budget`
   together, e.g. `--output-target 5000 --max-tokens 8192 --thinking-budget
   2048`. That no longer matches DeerFlow's full-length thinking settings.
   If the full single request stops short, adjust the synthetic output task
   before interpreting a 200-way run.
4. Only after the single stream is validated, run the requested 200 direct
   calls. This may request approximately **11 million output tokens**. Ensure
   the model service permits that load and there is enough time/cost budget:

   ```bash
   /opt/DeerFlow/backend/.venv/bin/python scripts/direct_model_stream_loadtest.py \
     --repeats REPEATS --concurrency 200 --confirm-large-test \
     --output /home/cnooc/deerflow-log-archive/direct-model-200-20260926.jsonl
   ```

Use unique output paths for every attempt. The script refuses to overwrite an
existing results file, sends all calls through one HTTPX client with a pool
large enough for the selected concurrency, disables automatic retries, and
prints aggregate SSE progress every 60 seconds (and content-free reasoning and
answer character counts for a single call). Per-call wall timeout defaults to two hours
and the HTTP read-inactivity timeout is 600 seconds; both can be adjusted
explicitly. A stream lost mid-answer is **not** transparently resumed.

## Reading results

Each JSONL line contains one request's HTTP status, request id (when supplied),
first and last SSE data times, elapsed time, finish reason, provider usage,
whether `[DONE]` arrived, and the transport error class. A success with
`target_met=false` means the HTTP stream ended correctly but did not exercise
the requested output length. Compare error timing and request IDs with the
model-provider logs. Connection failures from the host alone do not prove
the Gateway Pod path has the same cause, and 200 simultaneous 55k-output
requests can also reveal model-service capacity or rate limits rather than a
network fault.
