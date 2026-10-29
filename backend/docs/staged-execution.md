# Sequential-stage candidate

Status: local candidate, disabled by default; no production/model/business
acceptance. Based on `be1fa082ea4d7e018ebb3a74c7a317fde7272d53`.
Rebased by fast-forwarding the isolated worktree on 2026-10-27; the upstream
WeKnora provenance restoration has no changed-path overlap with this candidate.

## Routing

| Route | When | Execution |
| --- | --- | --- |
| Direct | Bounded answer or a short existing tool flow | Existing lead loop; no obligatory planning call |
| Sequential | Substantial dependent work needs independently checked intermediate results | `start_execution_stages`, current-stage tools, then a lone `advance_execution_stage` |
| Independent parallel | Independent workstreams and `task` already permitted | Existing delegation middleware, concurrency limits and synthesis; not reimplemented here |

Routing is model-assisted, not a deterministic complexity classifier. Creating
stages does not grant tools or permissions. No UI mode or `reasoning_effort`
value changes. Flash (`thinking_enabled: false`) is excluded at graph creation.
An active Skill owns its own stages and confirmation surface, so this controller
does not compete with it. The isolated PPT Runner is not modified.

The routing guidance considers material size, constraint/verification workload,
shared inputs and deliverables, not merely dependency between two tool calls.
Small record calculations and short lookup/check flows may stay unstaged.
Long document revision, multi-source conflict reconciliation and long-policy
structured extraction should use bounded evidence/production/validation stages.
There is no deterministic keyword classifier or additional routing-model call.
A bounded discovery read may establish scope before adopting a plan. An expanding
ordinary flow may stage only its remaining work, without replaying completed
effects. Each stage should retain a coverage checklist and concise results plus
artifact paths/receipt IDs. These are model instructions, not enforced limits on
document size, a context-isolation mechanism or proof of lower reasoning use.

Each stage may involve several model calls, not exactly one. A transition is a
real checkpoint-writing tool call followed by a separate model invocation. The
checkpoint preserves objectives, acceptance criteria, current stage, short
results, successful tool-call IDs and counters. It survives normal message
compaction and graph checkpoints. It does not delete original history or reduce
the provider context window. Stage data is supplied as untrusted evidence, not
new system authority.

Receipt capture is a separate middleware node before summarization; the stage
enforcement node sits after generic completion recovery in registration order
(therefore before it during `after_model`). A newly selected Skill supersedes
an old generic plan instead of accidentally resuming it after the Skill ends.

## Canary configuration

Only the trusted `AppConfig.staged_execution` block enables this candidate:

```yaml
staged_execution:
  enabled: false
  thread_ids: []
  max_stages: 6
  max_stage_model_calls: 6
  max_total_model_calls: 24
```

Both `enabled: true` and an exact thread ID are required, plus a run ID in the
normal runtime context. Empty allowlist means nobody. Do not put incident
production thread IDs into an example or automatically rerun failed jobs.
Configuration is captured when the lead graph is created; embedded cached
graphs need rebuilding. Run-context fields cannot enable the server gate.

## Enforcement and limits

- At most 2–8 stages, 1–12 lead model invocations per stage and 1–64 total
  invocations for an adopted plan (defaults 6/6/24). Planning, transition and
  final-synthesis calls count. Provider retries and hidden tokens are not this
  counter: existing retry, output, token and recursion guards still apply.
- A stage transition must be alone in a model tool batch. Mixed control/business
  batches stop before execution. A plan cannot be replaced to reset budgets.
- Advance requires the current 1-based stage number, a bounded result and at
  least one successful current-stage tool-call ID. Planning/todo/clarification,
  failed or structured partial-success receipts do not count. Historical and
  earlier-stage IDs cannot be reused. Keep up to 32 recent receipt IDs per stage.
- Receipt presence does not prove a semantic claim or validate arbitrary shell
  output. A tool with no structured error metadata can only be checked by its
  ToolMessage success status. The model still has to inspect acceptance criteria;
  existing domain validators remain authoritative.
- Stage exhaustion, repeated invalid control calls or premature terminal output
  pauses visibly without automatic retry or rollback. Completed side effects
  are not replayed. Explicit `继续`/`continue`/`resume` retains the saved stage
  and counters; it grants no extra budget. A distinct user request starts a new
  planning context. This first candidate recognizes only those exact continuation
  phrases (also `继续。` and `继续吧`), not arbitrary free-form confirmation;
  other user messages start a fresh planning context. Skill-owned confirmations
  are excluded from this controller. A budget-exhausted exact continuation will
  stop again, not silently reset its allowance.
- Existing terminal reasoning/safety stops are retained. A stage cannot interrupt
  a provider inside one long reasoning invocation: first-call overthinking can
  still fail. This candidate does not raise the configured reasoning-character guard,
  promise lower cost, or assert that upstream billing stops on cancellation.
- OA's requirement to publish one valid JSON through `present_files` is unchanged.
  A model-reported stage result is never a substitute for file delivery. Generic
  stages do not verify the JointJS schema; the existing OA acceptance path must.

## Validation

`tests/test_staged_execution.py` uses the real LangChain/LangGraph tool and
checkpoint plumbing with scripted models and inert tools. No provider request,
upload, database business operation or production deployment is needed.

Before any separate rollout decision, require relevant regression tests,
independent review, long-input synthetic runs, actual mode/Skill coexistence and
content-free telemetry review. Live effective model/effort/limit capture and
proactive per-invocation soft cutoffs are not implemented by this patch. Do not
infer that the three historical incidents would now succeed.

### Local synthetic receipt — 2026-10-26

- Python 3.12, dependencies installed from the unchanged `backend/uv.lock`.
- New stage tests: 32 passed, including compiled sync/async graphs, persisted
  resume without replay, compaction, mixed-batch refusal, schema-error bounds,
  mode gate, Skill handoff and the real `ModelOutputSafetyError` exception path.
- Fourteen related test files: **372 passed, 1 skipped**. The list is
  `test_staged_execution`, `test_model_output_safety`,
  `test_model_output_incidents`, `test_explicit_parallel_delegation_middleware`,
  `test_lead_agent_model_resolution`, `test_terminal_response_middleware`,
  `test_todo_middleware`, `test_subagent_limit_middleware`,
  `test_skill_workflow_middleware`, `test_artifact_delivery_middleware`,
  `test_app_config_reload`, `test_reload_boundary`, `test_oa_policy_flow`, and
  `test_oa_policy_flow_v2` (all under `backend/tests/`, with `.py` suffix).
- Ruff check/format and `git diff --check`: passed for the candidate changes.
- Full non-integration suite attempted with a ten-failure stop: **44 passed,
  10 failed, 3 skipped, 8 deselected** before stopping. Failures are in existing
  channel-store/release tests requiring Linux `fcntl` (including its child
  process test). This is **not** a full-suite pass; Linux validation is pending.
- Initial Windows test attempts also hit the shared temporary-directory ACL
  and long-path hard-link limitations. The final targeted run used a fresh,
  short task-specific temp path, without changing permissions or application code.

No incident thread was resumed, no provider/business request was made, and no
commit, push, image build or production change was performed.

### Routing refinement — 2026-10-27

Operator receipts for R1 show installed related tests passing (372 passed,
1 skipped), explicit two-stage synthetic live acceptance passing, and 15 real
middleware-chain tests passing with a scripted model. The later unhinted six-row
test returned a correct independently validated result in five model calls but
did not adopt stages; one tool had two attempts and an error receipt occurred.
The saved metadata does not establish the exact failed argument or cause.
This is not evidence that the three historical reasoning failures are fixed.

R2 refines only routing/tool descriptions and adds behavioral regressions for
short ordinary tool flows, correcting a rejected no-effect validation, and
staging remaining work without replaying earlier effects. It does not modify
the stage transaction logic, guards, budgets, enablement or provider settings.
The six-row R1 requirement to always stage is not retained as a universal
acceptance rule. Future live cases must separately report answer validity,
route adoption and confirmed duplicate successful effects; a rejected attempt
followed by a correct retry is not itself a duplicate successful mutation.
Long-input live routing, full Gateway acceptance and incident resolution remain
unproven. Earlier image/live receipts do not qualify the revised source/image.

### Package revalidation — 2026-10-27

On baseline `be1fa082ea4d7e018ebb3a74c7a317fde7272d53`, the same fourteen
related test files passed again: **372 passed, 1 skipped**, with one existing
FastAPI/Starlette deprecation warning. Ruff check/format passed on all six
candidate Python files. This is local Windows synthetic verification; Linux
and live-model acceptance remain pending. The source-only distribution locks
the base commit and before/after file hashes, preserves a backup before apply,
and never installs dependencies, changes live configuration or deploys images.
