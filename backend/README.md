# DeerFlow Backend

### Combined Gateway repair (2026-10-16)

The OA streaming / bounded DNS candidate also includes the Gateway XLSX
`defaultColWidthPt` compatibility fix from commit `b0ec3261`. It activates
lazily during XLSX conversion, retains the original file and all data rows,
and does not change dependency versions, startup hooks or parsed-text guards.
Rebuild and test the combined image before deployment; earlier OA-only test
receipts are not acceptance of this combined candidate. The actual incident
workbook and production HTTP upload still require separate acceptance.

OA policy-flow jobs opt into internal LangGraph `values` + `messages` streaming,
so supported chat models use their existing streaming adapter while the OA
submit/poll/result contract still delivers one complete validated JSON file.
Ordinary scheduled runs retain their default stream modes. Streaming reduces
long silent response periods but does not eliminate upstream 504s or first-token
timeouts; no retry/deadline/agent-prompt setting is changed.

Browser URL safety checks now run off both the Gateway and Playwright event
loops, with eight resolver workers per process, at most 64 outstanding checks,
and a five-second caller deadline including queue time.
Busy, timed-out or failed validation blocks the request. Running DNS operations
retain their capacity slot after caller timeout/cancellation; the existing SSRF
policy and redirect/subresource screening remain in place.

Model-output recovery now distinguishes malformed tool JSON, short plan-only
responses, provider length truncation, visible repetition, and excessive hidden
reasoning. It preserves delivered prose containing future-tense words, provides
content-free JSON syntax diagnostics for one regeneration attempt, and stops
pathological output without automatically spending another full model budget.
Earlier successful tool operations are not rolled back.
The lead-agent thinking prompt also discourages circular reasoning and
no-progress searches while preserving evidence, clarification and Skill gates.
See [model-output incident recovery](docs/model-output-recovery.md) for scope,
adapter-local thresholds, regression commands, and rollout limitations.

Work document/calendar integration is opt-in. `app/integrations/work/` separates
sender-bound authorization, typed capabilities and model planning. Production
uses encrypted shared SQL state; writes require owner-bound preview confirmation
and read-back verification. Tokens never reach models, Skills or the browser.
See [production setup and rollout](docs/WORK_PRODUCTION.md) and the
[release checklist](docs/WORK_RELEASE_CHECKLIST.md). Local UAT remains available.

The Work receiver closes SDK sockets/tasks on removal or reconfiguration and
serializes channel lifecycle changes. Availability reflects handshake health,
not merely a live thread. Work quota error `1000040350` also requires checking
old receivers; signed WebSocket URLs remain redacted in logs.

Batch fourteen repairs active TUI quit, reserved-command handling, contentless
message extraction, WeChat batch failure/cancellation handling and diff totals.
WeChat now attempts remaining messages after a bad attachment and advances its
cursor only after that batch; interrupted partial batches remain replayable.
Ordinary context Skills remain available, while unsupported TUI compaction
controls are not sent to the agent. No additional tests are deferred. See
[the reconciliation scope](docs/BACKEND_RECONCILIATION_BATCH14_20261002.md).
Server backend/frontend acceptance is still required before final merge/deploy.

Batch thirteen restores test-environment thread/run owner isolation and scoped
usage reads, completes the SQL token-aggregation user filter, and rejects
non-positive message/event pagination parameters. Hidden confirmation-answer
regeneration and the unmatched typed-preview integration remain deferred;
existing regeneration and raw head/tail behavior, WeKnora citations/images and
all pre-merge tests are preserved. See
[the reconciliation scope](docs/BACKEND_RECONCILIATION_BATCH13_20261002.md).
Do not finalize or deploy this unfinished merge until server acceptance passes.

Batch twelve reconciles subagent stream cleanup and contentless-result
diagnostics, makes SSO provider tests independent of live config, and aligns
Telegram reply checks with the existing Chinese product text. The incomplete
incoming attribution/Tavily expansion remains deferred; production identity,
SSO/IAM, TLS credentials and deployment configuration are unchanged. See
[the reconciliation scope](docs/BACKEND_RECONCILIATION_BATCH12_20261002.md).
Focused and complete server validation are still required before final merge.

The shared Slash contract now matches both unchanged runtime/display parsers.
Only its unmatched incoming context entry and the unsupported new frontend
Skill-parser test are deferred; all pre-merge tests and composer compact behavior
are preserved. See [the contract follow-up](docs/SLASH_CONTRACT_FOLLOWUP_20261002.md).
Run backend/frontend contract regressions before continuing full acceptance.

Batch eleven repairs unconditional archive entry limits, NTFS stream-name
rejection, skill package discovery and mixed frontmatter diagnostics. It restores
all pre-merge Slash tests and admin endpoint assertions without changing the
production activation chain or enabling the deferred reload endpoint. See
[the batch-eleven audit](docs/BACKEND_RECONCILIATION_BATCH11_20261002.md).
The shared Slash contract drift remains pending exact-file reconciliation.

Batch ten restores byte-preserving package review and reconciles Run API,
stream cancellation, SQLite scheduler-budget and malformed-URL boundaries.
It preserves every preexisting test and records narrow incoming-only deferrals.
See [the batch-ten audit](docs/BACKEND_RECONCILIATION_BATCH10_20261002.md).

Bounded empty search results now explicitly report incompleteness. AIO directory
glob results distinguish exactly-full output from an omitted eligible match,
including zero-cap and filtered-tail cases. See
[the search follow-up](docs/SEARCH_FOLLOWUP_20261002.md).

The path-mask follow-up stops the shared output matcher at PATH-list colons,
so every mapped entry is masked independently. Existing security tests and I/O
gates are preserved. See [the follow-up record](docs/PATH_MASK_FOLLOWUP_20261002.md).

The r10 reconciliation repairs MCP URI/replacement handling, cross-loop OAuth
locking, MindIE tool-result framing and OpenAPI method IDs, and restores the
pre-merge memory token user filter. The exact read continuation increment is
explicitly deferred, not fixed. See [the r10 record](docs/BACKEND_BATCH9_MERGE_20261001.md).
No final merge or deployment is permitted before the remaining release gates.

The batch-eight follow-up isolates stream-bridge mock capabilities and scopes
enterprise identity assertions to the role block while retaining native-tool
guidance and all four persisted stop-reason integration checks. It changes no
runtime code. See [the follow-up record](docs/BACKEND_BATCH8_FOLLOWUP_20261001.md).

The batch-eight follow-up separates exact read-file page identities, masks
escaping symlink spellings for display without relaxing I/O containment, and
reports missing directory listings explicitly. Existing Windows command tests
and the shared subagent floor of two are retained; unsupported incoming Windows
runner and single-subagent increments are deferred. See
[the batch-eight record](docs/BACKEND_BATCH8_MERGE_20261001.md) for limitations
and the mandatory full-suite/release gates.

The batch-seven follow-up accepts UTF-8 BOM headings and preserves Unicode
separators inside physical JSONL records. Only the ordinary Gateway performs
startup orphan recovery; the dedicated PPT Runner still starts heartbeat and
retains normal shutdown, but cannot reclaim the Gateway's runs on its restart.
AIO token and IAM cookie tests use complete fake initialization/configuration.
Unimplemented repeated-cancellation and lock-generation increments remain
explicitly deferred. See [the batch-seven record](docs/BACKEND_BATCH7_MERGE_20261001.md)
for known limitations, preserved coverage, and deployment gates.

The batch-six merge follow-up hardens DingTalk routing identity and handles
Feishu mentions before recognized commands, preserving ordinary chat text.
Dashboard coverage names all six currently implemented external cards. Build
tests exercise the Python timeout patcher and tracked proxy/CA examples, not
real runtime credentials. Selected incoming-only Discord, event-lock, delegation,
and E2B tests are deferred with their unsupported behavior explicitly recorded.
See [the batch-six record](docs/BACKEND_BATCH6_MERGE_20261001.md); passing these
tests is not final full-suite or release-config validation.

The 2026-09-28 production source alignment on the enterprise 7·16 branch is
recorded in `../release/production-build-alignment-20260928/README.md`.

The isolated 2026-09-30 dmdb merge restores the pre-merge input trust boundary,
thread-state metadata filtering, and deterministic SkillScan fixes. Incomplete
incoming RPM pacing, sandbox path-mask extensions, scheduled-run status filtering,
and SkillScan instance-client analysis are deferred, not advertised as working.
Existing stream-admission and Redis batching optimizations remain unchanged.
See [the reconciliation record](docs/ENTERPRISE_MERGE_20260930.md) for scope and
required regression gates; this record does not authorize deployment.

The frontend follow-up additionally restores the matching pre-merge
scheduled-task API route, including its permission and queue-mutation guards.
The scheduler service, repositories, and migrations remain unchanged. Validate
the restored UI and API together before completing the merge.

The 2026-10-01 full-suite follow-up offloads custom Skill history I/O and fixes
explicit channel binding parsing without changing deployed Chinese branding.
Incomplete ACP deadlines, the absent Skill reload endpoint, and unmatched AIO
test increments are individually deferred; all pre-merge tests are retained.
See [the batch-two record](docs/BACKEND_BATCH2_MERGE_20261001.md). The earlier
full-suite wait is not yet localized, so this merge is not ready for deployment.

The artifact follow-up restores download-only handling of XML/XSL/RDF and all
`+xml` MIME subtypes for ordinary artifacts and Skill archive members. It keeps
the incoming server-owned preview credentials, Runner port forwarding, media
streaming, and PDF snapshot/security checks. Preview tests use explicit fake
configuration rather than loading runtime secrets. See
[the artifact reconciliation record](docs/ARTIFACT_MERGE_20261001.md).

The next full-suite batch validates assistant search pagination (`limit` 1–1000,
non-negative `offset`) while preserving defaults and filtering. Unimplemented
extended Guardrail identity propagation and BoxLite status-marker expectations
are individually deferred by restoring all pre-merge tests, not disabling
authorization or changing Sandbox protocols. See
[the batch-three record](docs/BACKEND_BATCH3_MERGE_20261001.md).

The BoxLite lifecycle follow-up restores its pre-merge boolean result for warm
entry destruction. Previously a normal close returned `None`, causing shared
cleanup to repark a closed resource. All existing tests, AIO lease protections,
and the shared lifecycle mixin remain unchanged. See
[the lifecycle record](docs/BOXLITE_LIFECYCLE_MERGE_20261001.md).

The full-suite wait at Browserless web-fetch success was traced to the real
ReadabiliPy parser starting a first-use `npm install`. Unit tests now isolate
the third-party parser and external DNS while retaining real tool invocation,
ReadabilityExtractor/Article wiring, Markdown conversion and SSRF validation.
Runtime code and parser dependencies are unchanged; extraction integration in
a dependency-prepared environment remains a separate release check. See
[the test isolation record](docs/BROWSERLESS_TEST_ISOLATION_20261001.md).

Batch four restores pre-merge factory durable-context/system-message middleware
and explicit token-budget opt-in, plus Codex invalid-call/placeholder pairing.
Channel tests retain Chinese enterprise branding and native binding/auth checks;
Responses namespace tests retain the schema now stored for argument validation.
Unimplemented incoming channel rollback/lock-generation/business-response checks
and SDK stream increments are individually deferred, not claimed as fixed.
See [the batch-four scope and recovery list](docs/BACKEND_BATCH4_MERGE_20261001.md).

Batch five rejects malformed JSON/OAuth containers in Claude credential files
without exposing their contents, preserving the existing default-file fallback
and all credential tests. Incoming blank/missing tool-call ID recovery has tests
but no implementation; only its new test class is deferred. Existing tool-call
sanitization, pairing and timing instrumentation remain unchanged. See
[the batch-five record](docs/BACKEND_BATCH5_MERGE_20261001.md) for recovery scope.

DeerFlow is a LangGraph-based AI super agent with sandbox execution, persistent memory, and extensible tool integration. The backend enables AI agents to execute code, browse the web, manage files, delegate tasks to subagents, and retain context across conversations - all in isolated, per-thread environments.

---

## Architecture

```
                        ┌──────────────────────────────────────┐
                        │          Nginx (Port 2026)           │
                        │      Unified reverse proxy           │
                        └───────┬──────────────────┬───────────┘
                                │
            /api/langgraph/*    │    /api/* (other)
            rewritten to /api/* │
                                ▼
               ┌────────────────────────────────────────┐
               │        Gateway API (8001)              │
               │        FastAPI REST + agent runtime    │
               │                                        │
               │ Models, MCP, Skills, Memory, Uploads,  │
               │ Artifacts, Threads, Runs, Streaming    │
               │                                        │
               │ ┌────────────────────────────────────┐ │
               │ │ Lead Agent                         │ │
               │ │ Middleware Chain, Tools, Subagents │ │
               │ └────────────────────────────────────┘ │
               └────────────────────────────────────────┘
```

**Request Routing** (via Nginx):

- `/api/langgraph/*` → Gateway LangGraph-compatible API - agent interactions, threads, streaming
- `/api/ppt-master-runtime/*` → isolated PPT Master Agent sidecar (port 8003) - explicit `/ppt-master` runs, reconnect, and cancel
- `/api/*` (other) → Gateway API - models, MCP, skills, memory, artifacts, uploads, thread-local cleanup
- `/` (non-API) → Frontend - Next.js web interface

---

## Core Components

### Lead Agent

The single LangGraph agent (`lead_agent`) is the runtime entry point, created via `make_lead_agent(config)`. It combines:

- **Dynamic model selection** with thinking and vision support
- **Middleware chain** for cross-cutting concerns (9 middlewares)
- **Tool system** with sandbox, MCP, community, and built-in tools
- **Subagent delegation** for parallel task execution
- **System prompt** with skills injection, memory context, and working directory guidance

### Middleware Chain

Middlewares execute in strict order, each handling a specific concern:

| #   | Middleware                  | Purpose                                                                   |
| --- | --------------------------- | ------------------------------------------------------------------------- |
| 1   | **ThreadDataMiddleware**    | Creates per-thread isolated directories (workspace, uploads, outputs)     |
| 2   | **UploadsMiddleware**       | Injects newly uploaded files into conversation context                    |
| 3   | **SandboxMiddleware**       | Acquires sandbox environment for code execution                           |
| 4   | **SummarizationMiddleware** | Reduces context when approaching token limits (optional)                  |
| 5   | **TodoListMiddleware**      | Tracks multi-step tasks in plan mode (optional)                           |
| 6   | **TitleMiddleware**         | Auto-generates conversation titles after first exchange                   |
| 7   | **MemoryMiddleware**        | Queues conversations for async memory extraction                          |
| 8   | **ViewImageMiddleware**     | Injects image data for vision-capable models (conditional)                |
| 9   | **ClarificationMiddleware** | Intercepts clarification requests and interrupts execution (must be last) |

### Sandbox System

Per-thread isolated execution with virtual path translation:

- **Abstract interface**: `execute_command`, `read_file`, `write_file`, `list_dir`
- **Providers**: `LocalSandboxProvider` (filesystem) and `AioSandboxProvider` (Docker, in community/). Async runtime paths use async sandbox lifecycle hooks so startup, readiness polling, and release do not block the event loop. `AioSandboxProvider` validates active-cache and warm-pool containers during acquire/reuse, dropping definitively dead entries so a thread can provision a fresh sandbox after an unexpected container exit while keeping `get()` as an in-memory lookup. Backend health-check failures are treated as unknown, not dead, and a container that cannot be verified during discovery is simply not adopted (acquire falls through to create instead of failing).
- **Virtual paths**: `/mnt/user-data/{workspace,uploads,outputs}` → thread-specific physical directories
- **Skills path**: `/mnt/skills` → `deer-flow/skills/` directory
- **Skills loading**: Recursively discovers nested `SKILL.md` files under `skills/{public,custom}` and preserves nested container paths
- **SkillScan**: Native offline deterministic scanning runs before the LLM skill scanner on installs and agent-managed skill writes; `CRITICAL` findings block and warning findings become LLM context
- **File-write safety**: `str_replace` serializes read-modify-write per `(sandbox.id, path)` so isolated sandboxes keep concurrency even when virtual paths match
- **Tools**: `bash`, `ls`, `glob`, `grep`, `read_file`, `write_file`, `str_replace` (execution parameters such as `command`, `path`, patterns, and content remain required while every display-only `description` is optional; `write_file` overwrites by default and exposes `append` for end-of-file writes; `bash` is disabled by default when using `LocalSandboxProvider`; use `AioSandboxProvider` for isolated shell access)

### Subagent System

Async task delegation with concurrent execution:

- **Built-in agents**: `general-purpose` (full toolset) and `bash` (command specialist, exposed only when shell access is available)
- **Concurrency**: Max 3 subagents per turn, 15-minute timeout
- **Execution**: Background thread pools with status tracking and SSE events
- **Flow**: Agent calls `task()` tool → executor runs subagent in background → polls for completion → returns result

### Memory System

LLM-powered persistent context retention across conversations:

- **Automatic extraction**: Analyzes conversations for user context, facts, and preferences
- **Structured storage**: User context (work, personal, top-of-mind), history, and confidence-scored facts
- **Debounced updates**: Batches updates to minimize LLM calls (configurable wait time)
- **System prompt injection**: Top facts + context injected into agent prompts
- **Run-level memory identity**: `GET /api/threads/{thread_id}/runs/{run_id}/events?event_types=context:memory` returns the SHA-256 identity of the effective hidden memory block without copying memory text into the event store
- **Storage**: JSON file with mtime-based cache invalidation

### Tool Ecosystem

| Category      | Tools                                                                                                                               |
| ------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| **Sandbox**   | `bash`, `ls`, `read_file`, `write_file`, `str_replace`                                                                              |
| **Built-in**  | `present_files`, `ask_clarification`, `view_image`, `task` (subagent)                                                               |
| **Community** | Tavily (web search), Jina AI (web fetch), Crawl4AI (web fetch), Firecrawl (scraping), fastCRW (scraping), DuckDuckGo (image search) |
| **MCP**       | Any Model Context Protocol server (stdio, SSE, HTTP transports)                                                                     |
| **Skills**    | Domain-specific workflows injected via system prompt                                                                                |

Explicit Skill workflows persist their selected authority, active project, Gate
owner, and bounded tool receipts across runs. A failed owner receipt is bound to
the current project mutation version: after one durable repair, the same semantic
owner must run again before another repair or an old validation-report read. This
keeps diagnostics fresh without reproducing a Skill's workflow or report schema
inside DeerFlow.
Direct artifact-from-reference dependencies declared by loaded Skill authority
are checkpointed, and the reference must have a real current-run read receipt
before the artifact can be written. After a repair, DeerFlow may replay only an
exact, local, project-scoped read-only validator; side-effecting owners are never
automatically replayed.
A failed owner may nevertheless run a separate Skill-owned read-only Python
preflight against the selected Skill workspace when the official recovery stage
requires it before an atomic project repair. Mutating flags remain rejected and
that diagnostic never clears the original owner gate.
One selected top-level route stays locked until a new explicit Skill activation.
The checkpoint retains up to 24 supporting stage authorities separately from
the route and project contracts. Selected-Skill Python owner commands execute
without a terminal display-only `head`/`tail` pipe so the owner's real exit code
cannot be hidden by the truncator; unrelated pipelines remain unchanged.
Legacy main-Agent `ppt-master` threads retain the adapter behavior described
below during migration. New explicit `/ppt-master` tasks use the isolated
runner documented in `../docs/ppt-master-isolated-runner.md`; do not extend the
legacy adapter with new workflow rules.

The isolated runner replays the original request and current upstream authority
requirements on each resumable Agent turn. It keeps official validators and
exporters as exact commands so display-only pipes or shell fallbacks cannot hide
their status. A final answer is accepted only after the upstream delivery checker
and an independent OOXML inspection match every explicitly requested capability.
If they do not, the same Agent session repairs the same project with the concrete
failure for at most three attempts. Only new or changed accepted PPTX files are
published, and their paired previews are regenerated when stale.
The transport graph also derives a bounded title from the original Slash
request and writes it to `ThreadState.title`; it does not attach the lead
`TitleMiddleware` or invoke a second model solely for naming the conversation.
That short initializer is state-only; LangGraph-injected node parameters must
use the exact `config` / `runtime` names, and the compiled-graph invocation test
guards this contract before release.

The legacy `ppt-master` adapter additionally closes its official Executor authority set
before SVG authoring and blocks entry while any `ai`/`web`/`slice` resource is
still `Pending` or `Failed`. A partial Design Spec edit requires one complete
read before the refreshed capability/resource facts are trusted. This behavior
is scoped to the bundled presentation Skill and does not alter other Skills.
For a confirmed Deck/Layout, the same pre-Executor boundary requires the
current template Strategist references and reconciles the handoff, Design Spec,
and lock. Named prototypes cannot be downgraded to flat/style reuse. Structured
SVG file mutations must preserve the selected prototype's Master/Layout root,
inherited layout/master atoms and referenced definitions, and slot identity;
shell-written pages are checked again after execution.
Template completion and Strategist Stage 1 are also blocked until a single
selected workspace's complete portable `templates`/`images`/`icons` file map is
present and byte-identical in the project. Multi-workspace fusion remains owned
by the Skill because it has explicit segment-conflict semantics.
Read-only recovery diagnostics may inspect the active project or selected Skill
root. Sequence retries are scoped to the current owner receipt and project
mutation boundary, so real durable progress cannot inherit stale retry counts.
The generic one-repair freshness boundary remains in force, except that the
`ppt-master` first-page/final SVG gate permits one consolidated project-scoped
read/write repair batch from the checker's complete issue report. It still
rejects stale validation-report reads and stage advancement, and only the exact
checker retry can clear the gate.
For the bundled `ppt-master`, a successful Stage 1/2/3 recommendation write
while its Confirm UI is active is also a terminally valid user wait. A model
response that only announces the next confirmation step is replaced with a
resumable page handoff, not the generic unfinished-plan error. Other Skills and
ordinary post-tool turns keep the existing one-retry guard.
Authority reload receipts are scoped to each run/failure/compaction recovery
boundary, so a required second read of the same document is recorded instead of
being dropped as a duplicate and looping at the reload barrier.
An active `ppt-master` presentation-delivery model turn has a 600-second total
wall-clock ceiling. Stream keepalives or hidden/empty chunks cannot make one
turn unbounded; timeout exposes the durable stage/resume pointer and leaves
other Skills untouched.
`ppt-master` authority receipts are bound to the current run, so repeated
same-run summarization cannot force the complete Executor reference closure on
every model/tool iteration. A new run or owning Skill-script failure still
requires exact re-reads; other Skills retain summary-fingerprint invalidation.
Bounded confirmation-page waits are checkpointed as `waiting-for-user`, not as
failed build steps. The host preserves any owner-written receipt across the
documented page-to-chat fallback and blocks an unsolicited reset after timeout.
If an attempted install reopens the managed-receipt Gate, a later explicit human
chat confirmation clears it only while the checkpoint still records both the
page timeout and permission to switch surfaces. Restarting the Confirm UI daemon
preserves that boundary; older checkpoints recover it from durable owner receipts
until a successful template wait or explicit reset consumes it.

### Gateway API

FastAPI application providing REST endpoints for frontend integration:

| Route                                        | Purpose                                                                                                                                               |
| -------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /api/models`                            | List available LLM models                                                                                                                             |
| `GET/PUT /api/mcp/config`                    | Manage MCP server configurations                                                                                                                      |
| `POST /api/mcp/cache/reset`                  | Reset cached MCP tools so they reload on next use                                                                                                     |
| `GET/PUT /api/skills`                        | List and manage skills                                                                                                                                |
| `POST /api/skills/install`                   | Install skill from `.skill` archive                                                                                                                   |
| `GET /api/memory`                            | Retrieve memory data                                                                                                                                  |
| `POST /api/memory/reload`                    | Force memory reload                                                                                                                                   |
| `GET /api/memory/config`                     | Memory configuration                                                                                                                                  |
| `GET /api/memory/status`                     | Combined config + data                                                                                                                                |
| `GET /api/threads/{id}/runs/{run_id}/events` | Debug/audit events for one run; filter `event_types=context:memory` for effective memory identity                                                     |
| `POST /api/threads/{id}/uploads`             | Upload files (auto-converts PDF/PPT/Excel/Word to Markdown, rejects directory paths, auto-renames duplicate filenames in one request)                 |
| `GET /api/threads/{id}/uploads/list`         | List uploaded files                                                                                                                                   |
| `DELETE /api/threads/{id}`                   | Delete DeerFlow-managed local thread data after LangGraph thread deletion; unexpected failures are logged server-side and return a generic 500 detail |
| `GET /api/threads/{id}/artifacts/{path}`     | Serve generated artifacts                                                                                                                             |
| `GET /api/threads/{id}/artifact-previews/pdf/{path}` | Render a validated generated output PDF as a scriptless server-rasterized page gallery; uploads and unsafe PDFs are rejected                         |

Personal Digital Employee CRUD supports file or SQL persistence through
`agent_storage.backend`. Async Gateway routes offload both backend selection and
the store operation so configuration, filesystem, and synchronous SQL work do
not block unrelated requests on the event loop.

### IM Channels

The IM bridge supports Feishu, Slack, and Telegram. Slack and Telegram still use the final `runs.wait()` response path, while Feishu now streams through `runs.stream(["messages-tuple", "values"])`, serializes rapid same-thread turns inside the channel manager, and updates a single in-thread card per source message in place.

For Feishu card updates, DeerFlow stores the running card's `message_id` per inbound message and patches that same card until the run finishes, preserving the existing `OK` / `DONE` reaction flow. When a follow-up arrives inside an existing Feishu topic while another turn is still running, the later message now waits on the mapped DeerFlow `thread_id`, receives a queued/running card on that exact source message, and keeps a compact source-message blockquote in subsequent patches so rapid consecutive questions remain distinguishable.

---

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- API keys for your chosen LLM provider

### Installation

```bash
cd deer-flow

# Copy configuration files
cp config.example.yaml config.yaml

# Install backend dependencies
cd backend
make install
```

### Configuration

Edit `config.yaml` in the project root:

```yaml
models:
  - name: gpt-4o
    display_name: GPT-4o
    use: langchain_openai:ChatOpenAI
    model: gpt-4o
    api_key: $OPENAI_API_KEY
    supports_thinking: false
    supports_vision: true

  - name: gpt-5-responses
    display_name: GPT-5 (Responses API)
    use: langchain_openai:ChatOpenAI
    model: gpt-5
    api_key: $OPENAI_API_KEY
    use_responses_api: true
    output_version: responses/v1
    supports_vision: true
```

Set your API keys:

```bash
export OPENAI_API_KEY="your-api-key-here"
```

### Running

**Full Application** (from project root):

```bash
make dev  # Starts Gateway + Frontend + Nginx
```

Access at: http://localhost:2026

**Backend Only** (from backend directory):

```bash
# Gateway API + embedded agent runtime
make dev
```

Direct access: Gateway at http://localhost:8001

**Terminal Workbench (TUI)** — a terminal-native UI over the embedded harness,
no services required:

```bash
uv pip install 'deerflow-harness[tui]'   # optional 'textual' dependency
deerflow                                 # launch the TUI
deerflow --print "summarize this repo"   # headless one-shot
```

Sessions opened in the TUI appear in the Web UI sidebar (it writes the shared
`threads_meta` store under the local default user). See [docs/TUI.md](docs/TUI.md).

---

## Project Structure

```
backend/
├── packages/harness/           # deerflow-harness package (import: deerflow.*)
│   └── deerflow/
│       ├── agents/             # Agent system
│       │   ├── lead_agent/     # Main agent (factory, prompts)
│       │   ├── middlewares/    # Middleware components
│       │   ├── memory/         # Memory extraction & storage
│       │   └── thread_state.py # ThreadState schema
│       ├── sandbox/            # Sandbox execution
│       │   ├── local/          # Local filesystem provider
│       │   ├── sandbox.py      # Abstract interface
│       │   ├── tools.py        # bash, ls, read/write/str_replace
│       │   └── middleware.py   # Sandbox lifecycle
│       ├── subagents/          # Subagent delegation
│       │   ├── builtins/       # general-purpose, bash agents
│       │   ├── executor.py     # Background execution engine
│       │   └── registry.py     # Agent registry
│       ├── tools/builtins/     # Built-in tools
│       ├── mcp/                # MCP protocol integration
│       ├── models/             # Model factory
│       ├── skills/             # Skill discovery & loading
│       ├── config/             # Configuration system
│       ├── runtime/            # Embedded run execution (RunManager, StreamBridge)
│       ├── persistence/        # Checkpointer/store engines & schema migrations
│       ├── guardrails/         # Pre-tool-call authorization providers
│       ├── tracing/            # Tracer factory & trace metadata
│       ├── uploads/            # Uploads manager
│       ├── tui/                # Terminal UI (`deerflow` console script)
│       ├── community/          # Community tools & providers
│       ├── reflection/         # Dynamic module loading
│       └── utils/              # Utilities
├── app/                        # FastAPI Gateway + IM channels (import: app.*)
│   ├── gateway/                # Gateway API
│   │   ├── app.py              # Application setup
│   │   └── routers/            # Route modules
│   └── channels/               # IM channel integrations
├── docs/                       # Documentation
├── tests/                      # Test suite
├── langgraph.json              # LangGraph graph registry for tooling/Studio compatibility
├── pyproject.toml              # Python dependencies
├── Makefile                    # Development commands
└── Dockerfile                  # Container build
```

`langgraph.json` is not the default service entrypoint. The scripts and Docker
deployments run the Gateway embedded runtime; the file is kept for LangGraph
tooling, Studio, or direct LangGraph Server compatibility.

---

## Configuration

### Main Configuration (`config.yaml`)

Place in project root. Config values starting with `$` resolve as environment variables.

Key sections:

- `models` - LLM configurations with class paths, API keys, thinking/vision flags
- `tools` - Tool definitions with module paths and groups
- `tool_groups` - Logical tool groupings
- `sandbox` - Execution environment provider
- `skills` - Skills directory paths
- `title` - Auto-title generation settings
- `summarization` - Context summarization settings
- `subagents` - Subagent system (enabled/disabled)
- `memory` - Memory system settings (enabled, storage, debounce, facts limits)

Provider note:

- `models[*].use` references provider classes by module path (for example `langchain_openai:ChatOpenAI`).
- If a provider module is missing, DeerFlow now returns an actionable error with install guidance (for example `uv add langchain-google-genai`).

### Extensions Configuration (`extensions_config.json`)

MCP servers and skill states in a single file:

```json
{
  "mcpServers": {
    "github": {
      "enabled": true,
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_TOKEN": "$GITHUB_TOKEN" }
    },
    "secure-http": {
      "enabled": true,
      "type": "http",
      "url": "https://api.example.com/mcp",
      "oauth": {
        "enabled": true,
        "token_url": "https://auth.example.com/oauth/token",
        "grant_type": "client_credentials",
        "client_id": "$MCP_OAUTH_CLIENT_ID",
        "client_secret": "$MCP_OAUTH_CLIENT_SECRET"
      }
    },
    "postgres": {
      "enabled": false,
      "type": "stdio",
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-postgres",
        "postgresql://localhost/mydb"
      ],
      "description": "PostgreSQL database access",
      "routing": {
        "mode": "prefer",
        "priority": 50,
        "keywords": ["orders", "users", "SQL", "database", "table"]
      },
      "tools": {
        "query": {
          "routing": {
            "priority": 100,
            "keywords": ["query database", "orders table", "metrics"]
          }
        }
      }
    }
  },
  "skills": {
    "pdf-processing": { "enabled": true }
  }
}
```

`routing` adds soft MCP preference hints to the agent prompt. It helps the
model prefer a configured MCP tool for matching requests without forbidding
other tools. When `tool_search.enabled=true` defers MCP schemas, matching
routing metadata can auto-promote up to `tool_search.auto_promote_top_k`
deferred schemas before the model call.

### Environment Variables

- `DEER_FLOW_CONFIG_PATH` - Override config.yaml location
- `DEER_FLOW_EXTENSIONS_CONFIG_PATH` - Override extensions_config.json location
- Model API keys: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, etc.
- Tool API keys: `TAVILY_API_KEY`, `GITHUB_TOKEN`, etc.

### LangSmith Tracing

DeerFlow has built-in [LangSmith](https://smith.langchain.com) integration for observability. When enabled, all LLM calls, agent runs, tool executions, and middleware processing are traced and visible in the LangSmith dashboard.

**Setup:**

1. Sign up at [smith.langchain.com](https://smith.langchain.com) and create a project.
2. Add the following to your `.env` file in the project root:

```bash
LANGSMITH_TRACING=true
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_API_KEY=lsv2_pt_xxxxxxxxxxxxxxxx
LANGSMITH_PROJECT=xxx
```

**Legacy variables:** The `LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT`, and `LANGCHAIN_ENDPOINT` variables are also supported for backward compatibility. `LANGSMITH_*` variables take precedence when both are set.

### Langfuse Tracing

DeerFlow also supports [Langfuse](https://langfuse.com) observability for LangChain-compatible runs.

Add the following to your `.env` file:

```bash
LANGFUSE_TRACING=true
LANGFUSE_PUBLIC_KEY=pk-lf-xxxxxxxxxxxxxxxx
LANGFUSE_SECRET_KEY=sk-lf-xxxxxxxxxxxxxxxx
LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

If you are using a self-hosted Langfuse deployment, set `LANGFUSE_BASE_URL` to your Langfuse host.

### Dual Provider Behavior

If both LangSmith and Langfuse are enabled, DeerFlow initializes and attaches both callbacks so the same run data is reported to both systems.

If a provider is explicitly enabled but required credentials are missing, or the provider callback cannot be initialized, DeerFlow raises an error when tracing is initialized during model creation instead of silently disabling tracing.

**Docker:** In `docker-compose.yaml`, tracing is disabled by default (`LANGSMITH_TRACING=false`). Set `LANGSMITH_TRACING=true` and/or `LANGFUSE_TRACING=true` in your `.env`, together with the required credentials, to enable tracing in containerized deployments.

---

## Development

### Commands

```bash
make install    # Install dependencies
make dev        # Run Gateway API + embedded agent runtime (port 8001)
make gateway    # Run Gateway API without reload (port 8001)
make lint       # Run linter (ruff)
make format     # Format code (ruff)
make detect-blocking-io  # Inventory blocking IO that may block the backend event loop
make migrate-rev MSG="..."  # Autogenerate a new alembic revision against the live ORM models
```

### Schema Migrations

DeerFlow's application tables (`runs`, `threads_meta`, `feedback`, `users`,
`run_events`, and the `channel_*` tables) are owned by alembic. The Gateway
runs `alembic upgrade head` automatically on startup via
`bootstrap_schema(engine, backend=...)`, so operators do not run `alembic`
manually in production. Bootstrap is concurrency-safe (Postgres advisory lock
across processes; per-engine `asyncio.Lock` inside one SQLite process) and
idempotent against pre-existing schemas (empty / legacy / versioned).

When you add or change an ORM model, ship the change as a new revision under
`packages/harness/deerflow/persistence/migrations/versions/`:

```bash
make migrate-rev MSG="add foo column to runs"
```

The target invokes `scripts/_autogen_revision.py`, which builds a fresh temp
SQLite at `head` and diffs the live models against it — so a clean checkout
does not need a pre-existing `./data/deerflow.db`. Review the generated file
and switch raw `op.add_column` / `op.drop_column` calls to the idempotent
helpers in `migrations/_helpers.py` before committing. There is no
`make migrate` / `make migrate-stamp` target on purpose — Gateway startup is
the only execution path, which keeps operational mistakes off the table. See
`backend/CLAUDE.md` (Schema Migrations) for the full design.

### Code Style

- **Linter/Formatter**: `ruff`
- **Line length**: 240 characters
- **Python**: 3.12+ with type hints
- **Quotes**: Double quotes
- **Indentation**: 4 spaces

### Testing

```bash
uv run pytest
```

`make detect-blocking-io` statically scans backend business code for blocking
IO that may run on the backend event loop and is not test-coverage-bound. It
prints a concise summary for human review and writes complete JSON findings to
`.deer-flow/blocking-io-findings.json` at the repository root (regardless of
whether the target is invoked from the repo root or from `backend/`). JSON
findings include both broad IO category and review-oriented fields such as
`priority`, `location`, `blocking_call`, `event_loop_exposure`, `reason`, and
`code`. `priority` is a deterministic review ordering from the operation type,
not proof of a bug. Bare-name same-file calls are resolved by function name,
so duplicate helper names in one file can conservatively over-report async
reachability.

---

## Technology Stack

- **LangGraph** (1.0.6+) - Agent framework and multi-agent orchestration
- **LangChain** (1.2.3+) - LLM abstractions and tool system
- **FastAPI** (0.115.0+) - Gateway REST API
- **langchain-mcp-adapters** - Model Context Protocol support
- **agent-sandbox** - Sandboxed code execution
- **markitdown** - Multi-format document conversion
- **tavily-python** / **firecrawl-py** - Web search and scraping

---

## Documentation

- [Configuration Guide](docs/CONFIGURATION.md)
- [Architecture Details](docs/ARCHITECTURE.md)
- [API Reference](docs/API.md)
- [File Upload](docs/FILE_UPLOAD.md)
- [Path Examples](docs/PATH_EXAMPLES.md)
- [Context Summarization](docs/summarization.md)
- [Plan Mode](docs/plan_mode_usage.md)
- [Setup Guide](docs/SETUP.md)

---

## License

See the [LICENSE](../LICENSE) file in the project root.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines.


## Production Work channel and private title streaming (2026-10-10)

See ../docs/production-work-title-source-sync-20261010.md for the pinned release, operator domain/NO_PROXY settings, cooperative shared-file locking/CAS, private title streaming semantics, and regression commands. Runtime credentials and PVC configuration remain outside Git.

## 2026-10-11: catalogue and homepage scope (no new database schema)

Retain six additional contest employees and four existing-name updates, with
the updated e-AnDa link. The contest catalogue contains 18 entries; preexisting
fixed employee routes are unchanged. Keep the homepage's two existing Haineng
usage counters and add today's users and today's started tasks. These new
counters use the existing dashboard's Asia/Shanghai day, minute boundary,
run/thread-creation data or configured rollups; tasks include all run statuses,
not only successful completions. Require login and retain the bounded queries,
60-second cache, and unavailable-value placeholders.

Keep Contact Us: sh_tyznt@cnooc.com.cn, service hours 8:30-17:30, the supplied
Haineng Work feedback-group QR image, feedback email link and matching styles.
Do not change the QR pixels. Retain both language definitions and scoped tests.

Remove the newly merged square profile/order/withdrawal/settings feature and
its digital_employee_square persistence and 0020 migration. Existing baseline
agent/gallery permissions and publish flows remain unchanged. Persistence and
migrations must match baseline 917c8bb2; retain the earlier Work/title fixes.
No database upgrade, downgrade, table deletion, push or deployment is performed
by source candidate preparation. This candidate supersedes catalogue-only tree
345ad067e6df94dbf4b3e1f8d7c5e79d71509e5b, which removed too much homepage code.
Previous broader-tree test results are historical, not acceptance of this tree.

## Local UAT Work calendar consent (2026-10-21)

Production hardening and administrator/UI credential precedence are documented in
[WORK_PRODUCTION.md](docs/WORK_PRODUCTION.md). The production profile adds the
Gateway HTTPS callback and shared encrypted SQL state (migration 0022, after the
existing PPT 0020 and OA 0021 revisions), remains
opt-in, and retains the local UAT profile below. It has not been deployed to production.
See [the release checklist](docs/WORK_RELEASE_CHECKLIST.md) for source/deployment/ITC
responsibilities. The optional owner filter gates only Work document/calendar
capabilities, not channel binding or ordinary chat; leave it explicitly empty for
a full production rollout. Shared-database Runner images need the Work migration
and ORM registration too; the narrow production overlay preserves its dedicated Agent.

The 2026-10-26 PR is based on 0716 commit `59f7c538`. The Runner overlay now uses
the accepted 2026-10-23 Runner 55, not historical R3. The original production
source lock/snapshots remain unchanged; `work-source-candidate-20261026.json`
records exact undeployed Gateway deltas. Provenance checks distinguish candidate
verification from evidence of deployment.

The default-off local UAT integration requests `calendar:calendar:readonly` and
`calendar:calendar` together on first calendar use, matching the actual Work
permission catalogue rather than fine-grained names in newer API templates.
It checks both returned scopes on the original user's token and reuses complete
consent; document requests continue to request only `docx:document`. ITC must
enable and publish the corresponding user-identity permissions on the UAT app.
Older write-only tokens require a new OAuth flow, not channel rebinding. Calendar
writes still require an explicit sender-bound preview confirmation; no deletion,
invitations or room bookings are enabled, despite the broad provider write scope.
See [WORK_DOCUMENTS_UAT.md](docs/WORK_DOCUMENTS_UAT.md).

Local UAT calendar requests now use a semantic intent followed by server-side
target resolution, rather than asking the model for a complete executable event.
Query by date/time/original title, choose explicitly from numbered ambiguous
results, and preview changes to title, description or times. Title-only changes
preserve times/description and send only the changed field. Relative shifts move
both boundaries; a missing duration/end-time policy prompts a question. Candidate
facts and up to four clarification turns expire after ten minutes and are isolated
by the complete authenticated principal. Editing remains limited to the user's
own non-recurring timed events without other attendees; all-day events are readable
but not editable. Deletion remains disabled. UAT memory mode clears personal
OAuth grants/previews on restart; production and UAT SQL mode retain unexpired
shared state with the same key/configuration. Persisted channel bindings are not cleared.

Read-only calendar intents accept absent, empty or `null` changes. They reject
actual mutation fields; create/update still require a non-null changes object.
Validation diagnostics record only controlled field names/category/attempt, never
raw model output, user values or credentials.
