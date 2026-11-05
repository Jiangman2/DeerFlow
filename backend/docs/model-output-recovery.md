# Model-output incident recovery

Source candidate based on `origin/codex/enterprise-consolidation-20260716`,
commit `84557b0f2e6f83ae9bdffa8a919d0a2e4dc0c93d`.
No production deployment, model request, database mutation, or knowledge-base
recovery is part of this change.

## Behavior

The lead-agent thinking-style prompt now adds soft behavioral guidance: reuse
available evidence, avoid circular reasoning and no-progress exploration,
perform the next necessary action when evidence is sufficient, and report
concrete blockers truthfully. It never instructs the model to estimate a precise
hidden-token threshold or bypass user requirements, clarification or Skill
validation/confirmation gates. This guidance complements, not replaces, the
local output guards and does not affect the isolated external PPT Agent.

| Incident | Behavior after this change |
| --- | --- |
| JSON object ends before a trailing field | Diagnose locally with JSON error type and line/column/offset, without argument values. Request one complete regenerated object. Never silently strip the tail or execute repaired arguments. A second invalid response ends the run. |
| Delivered prose contains “我将” / “I will” | Only short (at most 600 characters), wholly plan-like visible prose can trigger the generic unfinished-plan continuation. Document text, code, quotations, and non-text blocks no longer trigger merely by containing those words. Skill-owned gates still apply. |
| Provider reports `finish_reason=length` | End the run visibly, clear partial tool calls, and do not request another full completion. Distinguish hidden-only exhaustion, repetitive text, and ordinary truncation. |
| Visible output repeats indefinitely | The robust adapter checks an 8,192-character suffix every 512 new visible characters. At least 2,048 characters and eight exact cycles (period 8–1,024 characters) are required. Abort before passing the offending chunk downstream. |
| Hidden reasoning continues excessively | Stop when streamed `reasoning_content` reaches the configured local character budget. This does not increase or reinterpret the provider's output-token limit. |
| Recovery causes a second expensive loop | Guard errors are non-retriable. Todo, artifact, and terminal middleware honor the terminal flag; Skill workflow already honors it. Malformed-call, plan-only, and empty-response exhaustion is terminal. |

Malformed arguments may originate in the model, provider gateway, or adaptation
chain; the incident logs did not establish which. This patch improves diagnosis
and recovery safety, not the provider's generation quality. A JSON error message
is not proof of a timeout or a permissions failure.

Previously successful tools may already have changed files. The invalid calls
themselves were not executed; errors must not claim that the whole run had no
effects. No automatic rollback is attempted.

## Adapter-local configuration

For an existing model whose `use` is
`deerflow.models.robust_openai:RobustChatOpenAI`, these optional top-level model
settings are consumed locally, never forwarded as provider request parameters:

```yaml
repetition_guard_enabled: true
max_reasoning_chars: 65536
```

These are the defaults. `max_reasoning_chars: 0` disables the local reasoning
guard. This number is **Unicode characters, not 65,536 tokens**. Tune it only
after evaluating legitimate long-thinking tasks. `repetition_guard_enabled:
false` disables the adapter's early repetition stop; the terminal middleware
still rejects severely repetitive completed responses and length truncation.
Non-streaming results are checked when received, so there is no early-saving
guarantee in non-streaming mode. A completed non-streaming visible answer is
not rejected solely for long hidden reasoning. The isolated external PPT Agent transport is
not routed through this adapter and is unchanged.

The detector is deliberately conservative: it is not semantic loop detection,
does not inspect tool-argument strings, and cannot prevent every long or slow
completion. Tool-call loops retain their existing independent middleware.
Closing the HTTP stream releases local resources; provider-side billing/compute
cancellation depends on the upstream service. Final usage may be absent when
aborting before the provider's last usage frame; do not invent token totals.

At the terminal fallback boundary, the abnormal body is replaced with a short
diagnostic so a later continuation does not inherit the enormous repeated
answer. Existing original-response journal events are not modified by this
patch. Early streaming cancellation may have only the existing partial stream
and error evidence, not a complete provider response.

## Validation

Run from `backend` in the normal locked project environment:

```sh
uv run pytest tests/test_model_output_incidents.py tests/test_model_output_safety.py tests/test_dangling_tool_call_middleware.py tests/test_terminal_response_middleware.py tests/test_artifact_delivery_middleware.py tests/test_llm_error_handling_middleware.py tests/test_robust_openai.py tests/test_model_stream_path_timing.py tests/test_loop_detection_stop_reason.py tests/test_loop_detection_middleware.py tests/test_skill_workflow_middleware.py tests/test_todo_middleware.py tests/test_safety_finish_reason_middleware.py tests/test_lead_agent_prompt.py -q
```

Tests use synthetic documents and mocked model streams, not real users'
documents, credentials, production databases, or paid model calls.

Local candidate validation on 2026-10-14: **502 passed** for the above 14
files, using isolated Python 3.12.14 with LangChain 1.2.15,
langchain-core 1.3.3, langchain-openai 1.2.1 and LangGraph 1.1.9.
The packaged fixed-scope test runner passed with only the asyncio and anyio
plugins explicitly loaded. Ruff checks and `git diff --check` passed. On this Windows host, tests needed
a fresh dedicated `--basetemp` directory under the workspace because the
machine-wide pytest temporary directory was inaccessible. These are source
candidate results, not deployed-service acceptance.

The operator requests related tests only on the server: do not run the full
suite or `make test` for this package. The earlier full-suite attempt was
blocked by missing dependencies; it is not a required step in this handoff.
Do not treat focused test success as deployed-service acceptance.

Before rollout, validate in a test environment with the same image/dependency
lock as production:

1. Confirm the configured model actually uses `RobustChatOpenAI`; otherwise
   only the terminal middleware protection applies.
2. Exercise ordinary chat, valid multi-tool editing, legitimate long reasoning,
   a quoted “我将” document, and a Skill confirmation wait.
3. Replay synthetic malformed JSON and repeating/hidden-only streams. Confirm
   one bounded retry for bad JSON and no retry for output safety stops, correct
   run error persistence, and released stream connections.
4. Verify no earlier successfully edited artifact is reported as rolled back.
5. Review the new stop reasons and false-positive rate before wider rollout.

Do not change live model limits, sampling parameters, credentials, or production
images merely to install this source candidate.
