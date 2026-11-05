# Run-stage timing logs

Set `DEERFLOW_RUN_TIMING_LOGS=true` on the Gateway during a performance test.
It emits compact, correlated records for the stages surrounding a model call:

```text
RUN_TIMING event=run_start deerflow_thread_id=... deerflow_run_id=... worker_pid=...
RUN_TIMING event=agent_stream_start stream_kind=initial ...
RUN_TIMING event=checkpoint_lock_wait_start stream_kind=initial ...
RUN_TIMING event=checkpoint_lock_wait_end elapsed_ms=... outcome=completed stream_kind=initial ...
RUN_TIMING event=agent_graph_stream_start stream_kind=initial ...
RUN_TIMING event=graph_task_start node_name=model task_index=... stream_kind=initial ...
RUN_TIMING event=graph_task_end elapsed_ms=... outcome=completed node_name=model task_index=... stream_kind=initial ...
RUN_TIMING event=middleware_after_agent_start middleware=sandbox ...
RUN_TIMING event=middleware_after_agent_end elapsed_ms=... outcome=completed middleware=sandbox ...
RUN_TIMING event=agent_graph_stream_tail elapsed_ms=... stream_kind=initial ...
RUN_TIMING event=agent_graph_stream_idle_wait_watchdog elapsed_ms=... pending_task_count=... pending_task_kinds=... pending_task_top_counts=... active_task_count=0 ...
RUN_TIMING event=agent_graph_stream_idle_wait_end elapsed_ms=... outcome=stream_exhausted active_task_count=0 ...
RUN_TIMING event=agent_stream_bridge_publish_slow elapsed_ms=... event_mode=messages stream_kind=initial ...
RUN_TIMING event=agent_graph_stream_end elapsed_ms=... outcome=completed stream_kind=initial ...
RUN_TIMING event=agent_stream_end elapsed_ms=... outcome=completed stream_kind=initial ...
LLM_TIMING start llm_call_id=... deerflow_thread_id=... deerflow_run_id=...
RUN_TIMING event=goal_evaluate_start ...
RUN_TIMING event=goal_evaluate_end elapsed_ms=... outcome=completed ...
RUN_TIMING event=goal_checkpoint_read_start ...
RUN_TIMING event=goal_checkpoint_read_end elapsed_ms=... outcome=completed ...
RUN_TIMING event=sandbox_acquire_start ...
RUN_TIMING event=sandbox_create_start sandbox_id=...
RUN_TIMING event=sandbox_create_submitted elapsed_ms=... sandbox_id=...
RUN_TIMING event=sandbox_ready elapsed_ms=... sandbox_id=...
RUN_TIMING event=tool_start tool_name=...
RUN_TIMING event=tool_end elapsed_ms=... tool_name=... outcome=completed
RUN_TIMING event=todo_completion_retry_queued retry_count=...
RUN_TIMING event=artifact_delivery_retry_queued retry_count=...
RUN_TIMING event=terminal_plan_retry_queued
RUN_TIMING event=skill_workflow_retry_queued
RUN_TIMING event=context_summarize_start ...
RUN_TIMING event=context_summarize_end elapsed_ms=... outcome=completed ...
RUN_TIMING event=title_generation_start ...
RUN_TIMING event=title_generation_end elapsed_ms=... outcome=completed ...
RUN_TIMING event=memory_enqueue_start ...
RUN_TIMING event=memory_enqueue_end elapsed_ms=... outcome=completed ...
RUN_TIMING event=run_finalize_start ...
RUN_TIMING event=journal_flush_start ...
RUN_TIMING event=journal_flush_end elapsed_ms=... outcome=completed ...
RUN_TIMING event=run_completion_persist_start ...
RUN_TIMING event=run_completion_persist_end elapsed_ms=... outcome=completed ...
RUN_TIMING event=title_sync_start ...
RUN_TIMING event=title_sync_end elapsed_ms=... outcome=completed ...
RUN_TIMING event=duration_persist_start ...
RUN_TIMING event=duration_persist_end elapsed_ms=... outcome=completed ...
RUN_TIMING event=thread_status_update_start ...
RUN_TIMING event=thread_status_update_end elapsed_ms=... outcome=completed ...
RUN_TIMING event=on_run_completed_start ...
RUN_TIMING event=on_run_completed_end elapsed_ms=... outcome=completed ...
RUN_TIMING event=run_finalize_end elapsed_ms=... outcome=completed ...
RUN_TIMING event=run_end elapsed_ms=... status=success worker_pid=... sandbox_tool_execution_total_ms=... sandbox_tool_acquire_overlap_ms=... sandbox_tool_count=... sandbox_tool_noncompleted_count=... non_sandbox_tool_total_ms=... non_sandbox_tool_count=... non_sandbox_tool_noncompleted_count=... checkpoint_aput_count=... checkpoint_aput_total_ms=... checkpoint_pool_acquire_count=... checkpoint_pool_acquire_total_ms=... checkpoint_sql_execute_count=... checkpoint_sql_execute_total_ms=...
```

`sandbox_create_*` appears only for a cold create. A reused or discovered
Sandbox has `sandbox_acquire_start` and `sandbox_acquire_end` but no create
events. `sandbox_ready.elapsed_ms` spans provider submission and readiness
polling; `sandbox_acquire_end.elapsed_ms` also includes any per-thread lock
wait. Every tool invocation is logged by name only, allowing the first actual
tool execution to be found with the earliest `tool_start` for the run.

`sandbox_tool_execution_total_ms` includes every built-in operation actually
executed through the Sandbox API: `bash`, `ls`, `glob`, `grep`, `read_file`,
`write_file`, and `str_replace`. If a first Sandbox tool lazily creates or
acquires its Sandbox inside that tool call, the overlapping acquisition interval
is removed from this total. The removed duplicate is exposed only as
`sandbox_tool_acquire_overlap_ms` for diagnostics, so `SB_TOOLS_S` and
`SB_ACQ_S` are mutually exclusive. `non_sandbox_tool_*` is the complementary
total for tools that run in Gateway or through an external connector, such as
search, task delegation, presentation, or image-view helpers. Tool totals are
cumulative rather than elapsed wall time. The corresponding
`*_noncompleted_count` includes failed and interrupted calls. No tool
arguments, command text, file paths, URLs, or credentials are recorded.

`goal_evaluate_*` appears only when the thread has an active Goal. The goal
evaluator intentionally uses a non-stream model request, which is emitted as
an `LLM_TIMING` triplet with `mode=async_nonstream`; its `first_chunk_ms` is
the non-stream response-completion latency because no provider chunk is exposed.
`goal_checkpoint_read_*` brackets each checkpoint read used by an active Goal;
it is absent when no active Goal needs evaluation. `run_finalize_*` brackets
post-graph persistence and stream-close work. Within finalization,
`journal_flush_*`, `run_completion_persist_*`, `title_sync_*`,
`duration_persist_*`, `thread_status_update_*`, and `on_run_completed_*`
separately expose the durable event flush, run-record update, title sync,
duration checkpoint write, thread-status write, and optional completion hook.
Each optional pair appears only when its corresponding dependency and code path
are active. These stage pairs make a long residual attributable to a concrete
post-graph operation rather than silently reporting it only as `UNATTR_S`.

`agent_stream_*` brackets one complete worker stream turn. `stream_kind=initial`
is the user-request turn; `stream_kind=goal` is a goal continuation. Within it,
`checkpoint_lock_wait_*` isolates contention on DeerFlow's per-thread durable
checkpoint lock, while `agent_graph_stream_*` measures time actually consumed by
`agent.astream()` after that lock has been obtained. Consequently, a long gap
after the last visible model response can be classified without inspecting any
conversation content: a long lock pair is checkpoint contention; a long graph
pair is work still happening inside the LangGraph middleware/graph. The
`agent_graph_stream_tail.elapsed_ms` value is the quiet period from the final
published graph update until `agent.astream()` exits; it pinpoints a post-answer
hang even when there is no later tool, model, or finalization record. Neither
condition means a non-stream final answer by itself.

When this quiet wait lasts at least 30 seconds, the worker emits
`agent_graph_stream_idle_wait_watchdog` while the live `agent.astream()` call
continues untouched. It identifies whether the last observed graph task had
already ended (`active_task_count=0`) and gives a bounded list of pending
coroutine implementation labels (`pending_task_kinds`) plus a bounded
`pending_task_top_counts` distribution (for example
`35836:_checkpointer_put_after_previous`), never task names,
frames, graph state, messages, or request data. On the eventual next graph item
or generator exhaustion, `agent_graph_stream_idle_wait_end` records the full
wait and its outcome. This separates a real post-task async-generator stall
from an ordinary long model/tool node. The thresholds are intentionally
conservative to avoid per-token log volume: set
`DEERFLOW_RUN_TIMING_IDLE_WAIT_SECONDS` to a positive number to override the
30-second idle snapshot threshold. The pending-task distribution is a snapshot
of the whole Gateway event loop, so it is a contention signal rather than proof
that every listed task belongs to the current Run. The checkpoint timing totals
below are instead recorded through the inherited Run context and are per-Run.

Gateway also measures each browser bridge publish but logs only an exceptional
error/cancellation or a completion slower than one second as
`agent_stream_bridge_publish_slow`. It does not emit a start/end pair for every
streamed token because that would distort a high-concurrency performance test.
Set `DEERFLOW_RUN_TIMING_SLOW_BRIDGE_PUBLISH_MS` to a positive number to tune
that slow-publish threshold. These two optional thresholds affect diagnostics
only and do not cancel, time out, or otherwise change a running graph.

While this diagnostic flag is enabled, Gateway privately adds LangGraph's
`tasks` stream mode and emits `graph_task_start` / `graph_task_end` pairs for
each graph node. The added task stream is consumed only by Gateway and is not
sent to the browser. `node_name` is the fixed internal graph-node label and
`task_index` is a local sequence number; task input, result, task id, and graph
state are never logged. A long `graph_task_end.elapsed_ms` identifies the node
which occupied the graph. If a graph stream exits with a task event that did not
provide a matching terminal receipt, `agent_graph_stream_tail` contains the
content-free `active_task_count` and its sole `active_node` when unambiguous.

Every DeerFlow-owned `after_agent` / `aafter_agent` implementation also emits
the uniform `middleware_after_agent_start` / `middleware_after_agent_end` pair.
The fixed `middleware` value is one of `artifact_delivery`,
`dangling_tool_call`, `loop_detection`, `memory`, `sandbox`, `skill_workflow`,
`terminal_response`, `todo`, or `token_budget`. This specifically makes a long
graph `after_agent` node attributable to its internal cleanup or sandbox-release
hook. An `error` or `cancelled` end preserves the exception outcome and class;
neither event includes hook state, messages, task data, paths, or credentials.
Third-party LangChain middleware is intentionally not wrapped by DeerFlow.

The retry and injection records are markers, not spans. They are emitted only
when a middleware schedules or injects a hidden model re-entry: Todo completion
(`todo_*`), artifact-delivery recovery (`artifact_delivery_*`), terminal empty
or unfinished-plan recovery (`terminal_*`), and selected Skill workflow control
(`skill_workflow_*`). `context_summarize_*` and `title_generation_*` bracket
direct hidden model calls which are deliberately not rendered to the chat
stream. `memory_enqueue_*` only measures queue handoff; it does not mean that
the asynchronous durable-memory summarization has completed. These records
contain counts, outcomes, and error class only—never prompts, output, tool
arguments, file paths, or credentials.

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

For PostgreSQL checkpointers, the same opt-in flag also adds content-free
per-run aggregates to `run_end`: `checkpoint_aput_*`,
`checkpoint_aput_writes_*`, `checkpoint_pool_acquire_*`, and
`checkpoint_pool_hold_*`, and `checkpoint_sql_execute_*`. The first two measure
LangGraph persistence API calls; pool acquisition measures only the wait to
borrow a psycopg connection; pool hold measures how long a borrowed connection
remains checked out; SQL execution measures only the awaited database round
trip. Each group has
`count`, `total_ms`, `max_ms`, `noncompleted_count`, and `inflight_max`.
`inflight_max` is the peak number of the same operation concurrently awaited
by this Run; it is not the whole Gateway event-loop count or an exact count of
LangGraph predecessor-chain waiters. Slow operation records also include
`inflight_at_start`, which distinguishes one slow write from a write entering
an already busy per-Run API path. No SQL text, bind parameters, checkpoint
data, connection ID, or user content is recorded.
While a Run is still active, an operation at or above the positive
`DEERFLOW_CHECKPOINTER_TIMING_SLOW_MS` threshold (default `1000`) also emits
`checkpoint_<operation>_slow`; this is the only per-operation event, so normal
checkpoint activity does not create a log line per SQL statement.

For a bounded dual-sided investigation, additionally set
`DEERFLOW_CHECKPOINTER_DB_SESSION_LABELS=true` on Gateway. On each pooled
checkpointer borrow, Gateway issues one diagnostic-only PostgreSQL
`set_config('application_name', ...)` call with `dfcp:<run_id>`; it contains
only the existing Run UUID, not a user ID, thread ID, SQL, prompt, or checkpoint
data. The label is replaced on the next borrow and may remain visible on an
idle pooled connection, so database-side samples must distinguish `state=active`
from idle sessions. The setting is disabled by default and adds one small SQL
round trip per pool borrow.

With labels enabled, take a point-in-time database view without selecting SQL
text or parameters:

```sql
SELECT
  application_name AS run_label,
  state,
  COALESCE(wait_event_type, '-') AS wait_type,
  COALESCE(wait_event, '-') AS wait_event,
  count(*) AS connections
FROM pg_stat_activity
WHERE application_name LIKE 'dfcp:%'
GROUP BY 1, 2, 3, 4
ORDER BY connections DESC, run_label;
```

The `active` rows show connections currently executing or waiting inside
PostgreSQL; `idle` rows show released pool sessions and must not be treated as
ongoing writes. This is a sample, not historical telemetry. Repeat it at a
bounded interval during the test if a time series is needed. Combine it with
the per-Run `checkpoint_pool_hold_*` totals: high pool hold plus active labelled
sessions points to database-side work; a large `aput_writes` duration with low
pool hold and few active labelled sessions points to waiting above PostgreSQL.

When that evidence shows the saver instance lock, rather than PostgreSQL or
pool acquisition, is the bottleneck, a controlled experiment may set
`DEERFLOW_CHECKPOINTER_POSTGRES_POOL_CONCURRENT_CURSOR=true` and restart
Gateway. The setting is disabled by default. It replaces the saver lock only
when the saver is backed by an `AsyncConnectionPool` and has no pipeline;
single-connection and pipeline savers retain the upstream lock. It is not a
durability change: checkpoint calls remain LangGraph calls and errors still
propagate normally. Keep timing and database-session labels enabled during the
experiment, compare completion/recovery behavior with the baseline, and roll
back by setting the value to `false` before restarting Gateway.

## Load-test report

Capture only these content-free records, then render one row for every finished
run without querying user prompts, tool arguments, file paths, or credentials:

```bash
kubectl -n deer-flow logs deploy/gateway -c gateway --timestamps --since=60m \
  | grep -E 'RUN_TIMING|LLM_TIMING' > gateway-timing.log
python3 scripts/deerflow_timing_report.py gateway-timing.log
```

The report shows cumulative model time as `LLM_SUM_S`; it intentionally omits
the first-request-to-last-response span because that overlaps other stages and
does not improve load-test attribution. `SB_ACQ_S` is the time to acquire a
Sandbox (warm reuse or cold creation), while `SB_READY_S` is the cold-create
readiness subset. `SB_TOOLS_S` is true Sandbox execution time with any nested
Sandbox acquisition removed, `OUT_TOOLS_S` covers tools outside the Sandbox,
and `TOOLS_S` is their sum. `UNATTR_S` is the run wall-clock time not covered
by a model, Sandbox-acquire, or completed-tool interval; overlapping work is
unioned rather than subtracted twice. `SB_ACQ#` is the number of acquisition
attempts, not the number of distinct Sandbox Pods. `LLM_ORPHAN#` counts model
end records whose matching start was absent from the captured log window; they
remain in `LLM_SUM_S`, but are excluded from `COV%`. Thus `COV%` is always at
most 100% and measures matching start/end IDs only. The report applies the
same overlap removal to old logs that lack the new `run_end` field, provided
their `sandbox_acquire_end` and `tool_end` records were captured.
