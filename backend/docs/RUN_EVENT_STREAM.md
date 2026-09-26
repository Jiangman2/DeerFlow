# Persisted run-event contract

The versioned payload reference is
[`contracts/run_event_stream_contract.json`](../../contracts/run_event_stream_contract.json).
Producer event names/categories come from
`deerflow.runtime.events.catalog`. Column widths remain 32/16 characters;
this alignment does not change the database schema or rewrite history.

## Producers and projections

| Producer | Events | Projection |
| --- | --- | --- |
| RunJournal | `llm.human.input`, `llm.ai.response`, `llm.tool.result` | Message history |
| RunJournal | `run.start`, `run.end`, `run.error`, `llm.error`, `context:memory`, `middleware:{tag}` | Trace, outputs, errors, hidden-memory hash, audit |
| Worker / orphan recovery | Enterprise `run.delivery` singleton | Observed artifact paths and terminal delivery verdict |
| Subagent step projector | `subagent.start`, `subagent.step`, `subagent.end` | Durable `task_*` progress, including the isolated PPT Bridge |
| Workspace recorder | `workspace_changes` | Versioned bounded filesystem changes |

LLM responses contain the canonical `AIMessage.model_dump()` shape, not an
OpenAI response envelope. `run.end` is opaque graph output: memory retains
Python values while JSONL/SQL serialize unsupported nested values to strings.
It is not authoritative run status. `RunRow.status` owns lifecycle state.
`run.delivery` is an observed fact, not proof of quality; enterprise PPT content
acceptance and preview verification remain separate. Token/cost totals are
accumulated by the journal, not reconstructed from message text.

Malformed subagent lifecycle chunks are rejected before persistence. Each
requires a nonempty string task ID; a running step additionally requires a
nonnegative integer (not boolean) message index and a plain message dictionary.
PPT transport must normalize LangChain messages before reaching this boundary.
Middleware suffixes must fit the existing column (1–21 characters). Audit
errors are logged without disabling safety or skill enforcement.

## Compatibility and isolation

The mixture of dot/colon/bare event names is intentional. Renaming an event,
changing its category or removing a required field is a breaking change.
New optional fields/events are additive. Readers ignore unknown event types
without deleting their rows. `ai_message` remains a read-only historical alias;
new producers emit `llm.ai.response`. The catalog is not a read-side allowlist.
Message lists filter by category; subagent/audit/delivery rows must not become
ordinary chat messages. Retain existing owner authorization on all read routes.

The contract tests exercise memory/JSONL stores and ORM widths. They do not
establish JSONL multi-process safety, production PostgreSQL/Redis readiness,
cross-worker ownership, or an end-to-end evidence verification protocol.

## Remaining integration

The pinned v2.1.0 catalog reserves `loop_detection`, `tool_promotion` and
`tool_progress` tags. Their presence is not evidence that those producers or
child-loop recorders are wired in the enterprise candidate. Those integrations
must preserve server-owned attribution, published Skill/knowledge scope and
existing failure gates; they are tracked in the batch ledger. Tool-call intent
is still embedded in AI messages, and a lost worker may have no terminal event.
