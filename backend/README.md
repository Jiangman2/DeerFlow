# DeerFlow Backend

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

PostgreSQL-compatible deployments may set database.postgres_maintenance_database (default postgres) to an existing maintenance database such as template1. It is used only for automatic application database creation. Kingbase V009R001C010B0004 deployment candidates and acceptance instructions are in ../k8s/local-pvc-kingbase/README.md; real database certification is pending.
