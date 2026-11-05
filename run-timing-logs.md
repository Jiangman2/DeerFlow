# Run-stage timing logs

Set `DEERFLOW_RUN_TIMING_LOGS=true` on the Gateway during a performance test.
It emits compact, correlated records for the stages surrounding a model call:

```text
RUN_TIMING event=run_start deerflow_thread_id=... deerflow_run_id=... worker_pid=...
LLM_TIMING start llm_call_id=... deerflow_thread_id=... deerflow_run_id=...
RUN_TIMING event=sandbox_acquire_start ...
RUN_TIMING event=sandbox_create_start sandbox_id=...
RUN_TIMING event=sandbox_create_submitted elapsed_ms=... sandbox_id=...
RUN_TIMING event=sandbox_ready elapsed_ms=... sandbox_id=...
RUN_TIMING event=tool_start tool_name=...
RUN_TIMING event=tool_end elapsed_ms=... tool_name=... outcome=completed
RUN_TIMING event=run_end elapsed_ms=... status=success worker_pid=... sandbox_command_total_ms=... sandbox_command_count=... sandbox_command_noncompleted_count=...
```

`sandbox_create_*` appears only for a cold create. A reused or discovered
Sandbox has `sandbox_acquire_start` and `sandbox_acquire_end` but no create
events. `sandbox_ready.elapsed_ms` spans provider submission and readiness
polling; `sandbox_acquire_end.elapsed_ms` also includes any per-thread lock
wait. Every tool invocation is logged by name only, allowing the first actual
tool execution to be found with the earliest `tool_start` for the run.

`sandbox_command_*` counts only the sandbox `bash` tool: it is the tool that
submits an actual shell command to the Sandbox. The total is the sum of each
`bash` call's wall-clock `tool_end.elapsed_ms`, so parallel shell calls are
intentionally summed (cumulative command time, not elapsed wall time).
`sandbox_command_noncompleted_count` includes failed and interrupted commands.
File read/write and other Sandbox API operations remain visible in their own
`tool_end` records but are excluded from this command total.

All records include DeerFlow `thread_id` and `run_id` when they originate from
a Gateway run. Each LLM start/first/end triplet additionally shares one
`llm_call_id`; use it to deduplicate retries and calculate the count and total
duration of completed model calls for each `deerflow_run_id`. Attribution uses
LangChain metadata as a fallback when a child task does not inherit the worker
task's context. Records never include prompts, model output, tool arguments,
command text, file paths, URLs, request headers, or credential values. The new
flag is disabled by default and can be removed or set to `false` after the test.
`DEERFLOW_LLM_TIMING_LOGS=true` remains separately required for the model
request/first-token/complete sequence described in
[llm-timing-logs.md](llm-timing-logs.md).

## Load-test report

Capture only these content-free records, then render one row for every finished
run without querying user prompts, tool arguments, file paths, or credentials:

```bash
kubectl -n deer-flow logs deploy/gateway -c gateway --timestamps --since=60m \
  | grep -E 'RUN_TIMING|LLM_TIMING' > gateway-timing.log
python3 scripts/deerflow_timing_report.py gateway-timing.log
```

The report distinguishes model-call cumulative time (`LLM_SUM_S`) from the
first-request-to-last-response wall-clock span (`LLM_SPAN_S`). `SB_ACQ_S` is
the time to acquire a Sandbox (warm reuse or cold creation), while
`SB_READY_S` is the cold-create readiness subset. `SB_CMD_S` is the aggregate
of submitted `bash` command execution. `UNATTR_S` is the run wall-clock time
not covered by a model, Sandbox-acquire, or completed-tool interval; overlapping
work is unioned rather than subtracted twice. `COV%` is finished model calls
divided by observed starts.
