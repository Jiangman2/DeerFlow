# AGENTS.md

DeepSeek-V4.1-Flash uses the opt-in enterprise model profile and existing
`RobustChatOpenAI`. Its FCV-41 endpoint requires `/v1/chat/completions` and the
top-level `enable_thinking` boolean via `extra_body`, not `thinking.type` or
reasoning effort. Keep both thinking overrides explicit and selector capability
metadata credential-free. Preserve the existing model list order/default and
use a separate server Secret. Profile/SDK-wire tests run without network;
provider availability and vision still need enterprise-network acceptance.
Provider preparation reports Gateway readiness before refusing. Claim the once
marker only after read-only preflight; recovery of an older consumed marker
requires an exact hash-bound report proving no provider or Secret operation.
Never clear an uncertain attempt or relax the rollout gate to make it pass.
The separate explicit first-flash-503 recovery accepts only a pinned HTTP error
with one synthetic request, zero completed stages and no Secret write; it is
not a generic retry for timeout, tool execution or uncertain outcomes.
An explicitly requested synthetic-tool-503 recovery separately binds the exact
three completed test stages and failed fourth request, requires zero business
tools and no possible Secret write, and reruns the bounded five-call canary.
For historical DeepSeek 503 diagnosis, use the exact hash-bound receipt
collector rather than another provider request. Old receipts contain neither
upstream request IDs nor response bodies/headers; do not invent them, attribute
503 to a particular service, or confuse report mtime with request time. Current
Pod addresses are only current metadata, not proof of historical NAT routing.

Employee featured-store reads use the field-qualified r6 source. On Linux,
force pathname metadata refresh with statx before the bounded precommit open,
then revalidate file identity, permissions and read stability. Do not turn an
uncertain rename/fsync into a write retry, reseed on GET, or relax directory
permissions. The exact qualified source is pinned in the enterprise source lock;
local tests do not replace live shared-storage or rollout acceptance. The Git
copy is formatted to repository style; its AST must match the hash-pinned field
source. Do not confuse that semantic equivalence with identical source bytes.

## Combined Gateway release: OA / DNS / XLSX (2026-10-16)

The additional runtime change is the exact lazy XLSX conversion shim from
`b0ec3261069d8eed2a65707451990ccdc74179d4`. Install it immediately before
MarkItDown only for `.xlsx`; ignore only `defaultColWidthPt`, retain other
errors, never rewrite original workbook bytes, and keep installation locked
and idempotent. Do not restore the superseded startup/sitecustomize patch.
Gateway and DocReader tests must restore the constructor independently.
The release tests additionally check all 12,037 synthetic rows, two sheets,
uppercase extensions, concurrent installation and malformed workbook rejection.
Synthetic acceptance does not substitute for the original incident workbook.
No frontend shop-cart change, dependency upgrade or deployment is bundled.

## OA internal streaming and browser DNS guard (2026-10-16)

OA policy-flow explicitly requests `values` + `messages` from the shared run
launcher; do not change ordinary scheduler defaults or the OA submit/poll/result
contract. Verify the SDK request's `stream: true` through the real LangGraph
messages handler and RobustChatOpenAI, including fragmented tool arguments and
usage aggregation. Do not replace the guarded adapter with a direct SDK call.
This does not guarantee that an upstream proxy will never return 504.

All async browser navigation checks (tools, live WebSocket input/seed and
Playwright request routing) use the shared bounded off-loop URL guard. Preserve
SSRF policy, per-request checks and explicit trusted-CDP behavior. The guard
pool is process-wide and cross-event-loop safe: eight workers, at most 64
outstanding checks including queued work, and a five-second caller deadline
including queue time. Cancellation/timeout cannot kill libc DNS;
shield queued Futures from caller cancellation and release admission only when
work is actually consumed by a worker (or removed during executor shutdown).
Skip abandoned queued checks before initiating DNS. Otherwise cancelled queue
entries can accumulate despite a semaphore. Carry ContextVars into workers, fail closed without exposing
URLs on exception, and never cache a DNS allow decision to bypass later checks.
Regression anchors: `tests/test_oa_model_streaming.py` and
`tests/test_browser_async_guard.py`. Tests must use fake DNS/HTTP transports.

## Model-output recovery guardrails (2026-10-14)

The lead-agent thinking-style prompt carries soft efficiency guidance: reuse
evidence, stop no-progress repetition, and report concrete blockers without
fabricating results or bypassing clarification/Skill gates. It must not claim
that models can precisely count their hidden reasoning tokens or characters.
For this recovery package the operator explicitly requests related tests only:
use the fixed file list in the package runner, not the full backend suite.

Preserve the terminal boundary across middleware: an AIMessage marked
`deerflow_terminal_stop` must not be re-engaged by Todo, artifact-delivery,
terminal-response or Skill workflow recovery. Length-truncated responses must
never dispatch partial tool calls or consume a fresh automatic continuation
budget. Keep genuine short plan-only and empty post-tool retries bounded.
Future-tense words embedded in documents/code/quotations are not completion
state. Do not remove the existing Skill-owned confirmation boundaries.

`models/output_safety.py` is a per-invocation, bounded-memory guard used by
`RobustChatOpenAI` before streaming callbacks/yields. Guard exceptions are
non-retriable and contain no user/model content. Close the upstream stream on
failure/cancellation. The local reasoning limit counts characters, NOT tokens;
never claim that local cancellation guarantees upstream billing cancellation.
Tool argument JSON is not visible prose and must not enter repetition detection.
Malformed arguments receive local syntax/position diagnostics; never trim away
trailing fields, repair executable arguments, or expose their values in logs.
See `docs/model-output-recovery.md` and the regression tests
`test_model_output_incidents.py` / `test_model_output_safety.py`.

The 2026-10-04 production Gateway reconciliation is documented in `../docs/production-gateway-reconciliation-20261004.md`. Preserve metadata-only owner routing and Gateway authentication/CSRF enforcement. Persist partial history only for explicit client interrupts, never lease takeover, shutdown, rollback or model failure; do not write unfinished text to model checkpoints. The regression tests use repository sources, not release archives or fake harness modules.

Batch-fourteen reconciliation is recorded in
`docs/BACKEND_RECONCILIATION_BATCH14_20261002.md`. TUI quit must interrupt an
active stream before exit and registry/submission must reuse the existing
reserved-Slash set without reserving context tasks. Keep unsupported TUI
compaction controls out of Skill activation. Contentless messages yield empty
text, not literal None. WeChat must isolate failed batch items, log their IDs,
propagate cancellation and persist its cursor only after the entire batch was
attempted. Keep auth, ownership, decryption and offloaded persistence unchanged.
Diff counters distinguish true file headers from header-like hunk content using
declared ranges. No additional test deferrals are made in this batch.

Batch-thirteen reconciliation is recorded in
`docs/BACKEND_RECONCILIATION_BATCH13_20261002.md`. Preserve the pre-merge
thread/run internal owner gates and every call site; never equate an internal
token with cross-user permission. Usage aggregations must propagate the resolved
scope into SQL and memory stores before reducing counters, including active
runs. Retain positive cursor/limit constraints and existing upper bounds. Hidden
confirmation-answer replay and the unwired typed-preview renderer remain
explicitly deferred, while all pre-merge tests/assertions and standalone
synopsis helper tests remain. Do not change the WeKnora inline JSON/image budget
or generic raw head/tail runtime to make those missing integrations appear done.

Batch-twelve reconciliation is recorded in
`docs/BACKEND_RECONCILIATION_BATCH12_20261002.md`. Subagent execution owns and
closes its async stream on exhaustion, cancellation and error; a secondary
cleanup failure must not replace the primary exception or a first terminal
status. Treat missing message content as empty only in subagent diagnostics.
Keep the existing constructor, task-tool identity forwarding and guardrail
pipeline unchanged: the incoming is_internal/authz_attributes expansion and
unsupported Tavily extensions remain deferred. SSO tests must supply explicit
non-secret config fixtures without copying live config, and retain IAM while
hiding generic OIDC providers in SSO-only mode. Telegram binding tests assert
the exact existing localized reply and preserve every identity/security check.

The shared Slash contract follow-up is recorded in
`docs/SLASH_CONTRACT_FOLLOWUP_20261002.md`. Keep the fixture aligned with the
unchanged backend/frontend seven-name reserved sets. A context-specific Skill
parser exception has no matching implementation and remains deferred; do not
alter runtime parsers or weaken contract assertions to hide drift. Preserve the
existing composer interception of /compact and /context compact, its API,
abort/thread/in-flight guards and all pre-merge tests. The batch-eleven contract
review is now resolved at the source/fixture level, pending server regressions.

Batch-eleven reconciliation is documented in
`docs/BACKEND_RECONCILIATION_BATCH11_20261002.md`. ZIP extraction must enforce
its 4096-member cap independently of optional scanning and reject colon names
on every platform. Native archive preflight reports those names as CRITICAL.
Stop skill discovery at the first package SKILL.md while preserving recursive
namespace discovery. Format mixed YAML keys as strings without accepting them.
Keep the production skill activation/secret/workflow chain unchanged: owner-token
and reload endpoint increments remain deferred. Tests use their own Python 3
interpreter, not an ambiguous host PATH alias. The shared Slash contract drift
requires its separate exact-file review before acceptance; this package does
not reconcile or verify that contract.

Batch-ten reconciliation is documented in
`docs/BACKEND_RECONCILIATION_BATCH10_20261002.md`: preserve binary SkillScan
bytes/exclusive materialization, positive Run cursors, null config handling,
owned stream-child cancellation and SQLite global-budget transaction ordering.
Keep atomic takeover fault injection aligned with the actual CAS. Do not
silently enable moderation tracing or unbounded memory-only Run retention;
their incoming-only tests remain explicitly deferred, not claimed as fixed.

Search observation limits are recorded in `docs/SEARCH_FOLLOWUP_20261002.md`.
Never present an empty truncated provider result as a completed no-match scan.
For AIO directory globbing, check caps after all eligibility filters and before
append; only another eligible result proves truncation. Preserve SDK transport,
search roots, disabled-skill filtering, path masking and all authorization gates.

The shared output-mask tail stops at colons; keep every PATH-list root as an
independent match without changing root escaping, separator-mode asymmetry or
I/O containment. The legacy pattern tests allow only this explicit tail delta.
See `docs/PATH_MASK_FOLLOWUP_20261002.md`. Do not delete the colon-list security
regression or restore unmatched scanner APIs to accommodate its assertions.

The r10 merge reconciliation is recorded in `docs/BACKEND_BATCH9_MERGE_20261001.md`.
Preserve the pre-merge memory token user filter, artifact authorization and
separate visible HTTP-method registrations. OAuth locks must work across caller
loops and release on cancellation without blocking event loops. Treat tool XML
payloads and URI/path replacements as untrusted data. Exact read continuation
markers are deferred; do not claim that generic range hints guarantee gap-free
walks. No preexisting tests may be removed to conceal a regression.

The enterprise 7·16 production source alignment and its rebuild limits are
recorded in `../release/production-build-alignment-20260928/README.md`.
The isolated PPT Agent Host uses the vendored v6.6.0 Skill build input, while
the shared Skills volume has its own separate `ppt-master` tree.
The current dedicated Runner's R3 source and base-image lock are under
`../docker/ppt-master-runner/`; its `agent.py` must not replace the ordinary
Gateway's copy.

The isolated merge's first full-suite reconciliation is documented in
`docs/BACKEND_BATCH2_MERGE_20261001.md`. Preserve the pre-merge ACP and AIO
contracts while deferring their unmatched new test increments. Skill history
must authorize on the event loop before offloading all storage I/O with owner
ContextVars preserved. Binding requires explicit `/connect`; stripping leading
mentions must not change the shared control-command classifier's authority.

Artifact reconciliation is recorded in `docs/ARTIFACT_MERGE_20261001.md`.
Both ordinary files and Skill archive members must use the shared active-MIME
predicate for HTML, XML, XSL, and every `+xml` subtype, including case/parameter
normalization. Preserve server-owned AIO credentials, direct preview transport,
Runner port forwarding, owner authorization, and the PDF snapshot protections;
unit tests must supply fake preview configuration, not copy runtime secrets.

`docs/BACKEND_BATCH3_MERGE_20261001.md` records the next isolated-merge batch.
Assistant search uses Pydantic bounds for pagination. Do not enable extended
Guardrail identity propagation by adding dataclass fields alone: middleware,
adapter, authenticated provenance, and both sync/async enforcement must agree.
Retain the pre-merge adapter and BoxLite contracts until their full increments
are implemented and reviewed. Previously repaired Gateway context scrubbing
and artifact active-content defenses must remain intact.

`docs/BOXLITE_LIFECYCLE_MERGE_20261001.md` restores the BoxLite warm-pool
destruction callback's boolean contract. Successful close must return `True`;
an escaping close exception returns `False` so shared lifecycle code retains
the resource for retry. Do not change the shared mixin to treat `None` as
success: that would weaken AIO's execution-lease deferral. Keep original
warm-pool and BoxLite tests and validate both providers before deployment.

For Browserless unit tests, mock the third-party parser entry point and public
DNS resolution explicitly; do not let a success test invoke first-use npm
installation or require live internet. Keep the real URL safety validation and
off-event-loop extraction/Article conversion chain exercised, and retain
private-address rejection tests unchanged. This test-only isolation does not
prove readiness of the actual ReadabiliPy Node dependencies in release images;
record and verify that independently. See
`docs/BROWSERLESS_TEST_ISOLATION_20261001.md`.

`docs/BACKEND_BATCH4_MERGE_20261001.md` records restored pre-merge factory and
Codex invariants. Factory-built agents must project durable context and coalesce
system messages before summarization; an explicit token-budget feature opt-in
must enable its config. Codex must serialize named invalid calls with IDs when
the dangling-call middleware supplies a paired placeholder result. Retain
Responses schema validation and Chinese enterprise channel branding. Deferred
incoming channel/SDK increments require complete runtime work and reinstatement
of their recorded tests; this reconciliation does not advertise them as fixed.

`docs/BACKEND_BATCH5_MERGE_20261001.md` records two type guards for malformed
Claude credential containers. Never log raw credential values or JSON bodies;
non-object containers are unavailable credentials and retain existing fallback
ordering. Keep every credential test. The incoming-only malformed tool-call ID
recovery class remains deferred until its causal pairing implementation is
complete; preserve all preexisting dangling-call tests and runtime/timing hooks.

`docs/BACKEND_BATCH6_MERGE_20261001.md` records the next full-suite reconciliation.
Reject DingTalk messages without their P2P sender/group conversation routing key
before binding or publishing. Feishu strips leading mentions only for recognized
control commands; ordinary chat mentions and the shared classifier stay intact.
Build tests exercise the tracked Python Nginx timeout patcher and verify the
documented proxy/CA template without reading gitignored runtime configuration.
Actual release config validation remains a separate mandatory deployment gate.
Incoming-only lock-handoff, Discord restart/flush, earlier-run delegation closure,
and E2B status-marker increments remain deferred, not advertised as repaired.

`docs/BACKEND_BATCH7_MERGE_20261001.md` records BOM/JSONL framing fixes and the
PPT Runner startup-recovery ownership boundary. Only ordinary Gateway startup
may reclaim orphan runs and mark their retained streams/threads as error.
Retain lease heartbeat and shutdown on both profiles. Test AIO instances made
with `__new__` must initialize `_default_env`; secrets stay in structured env,
never command text. IAM callback tests supply fake registered redirect URIs.
The unused incoming memory-shutdown helper and unmatched repeated-cancellation
tests are deferred; legacy shutdown and file-I/O behavior remain unchanged.
Do not describe cancellation draining or JSONL/DB lock handoff as fixed.

`docs/BACKEND_BATCH8_MERGE_20261001.md` records exact read-file loop identities
and display-only symlink masking. Never use the lexical output mapping to
authorize a read/write; resolved target containment remains mandatory.
Preserve same-range loop hard stops and terminal stop ownership. The shared
subagent floor remains two and Windows command execution remains subprocess.run;
the incoming single-subagent and Windows bounded-runner increments are deferred.
All preexisting platform tests remain, with no broad skips or module removals.

`docs/BACKEND_BATCH8_FOLLOWUP_20261001.md` records fixture-only repairs after
the 264-pass focused run. Stop-reason integration bridge mocks must expose real
boolean/integer batching capabilities; do not change production batcher or
worker behavior to accept fabricated mock values. Enterprise identity checks
target the role block, preserving native read_file tool guidance and the
no-inferred-branding guard. Retain all four real-guard persisted-stop assertions.

This file provides guidance to AI coding agents (Claude Code, Codex, and others) when working with code in this repository. It is the source of truth; the sibling `CLAUDE.md` imports it via `@AGENTS.md`.

## Project Overview

DeerFlow is a LangGraph-based AI super agent system with a full-stack architecture. The backend provides a "super agent" with sandbox execution, persistent memory, subagent delegation, and extensible tool integration - all operating in per-thread isolated environments.

**Architecture**:

- **Gateway API** (port 8001): REST API plus embedded LangGraph-compatible agent runtime
- **PPT Master Bridge** (port 8003): transport/checkpoint sidecar for the external Sandbox Agent
- **Frontend** (port 3000): Next.js web interface
- **Nginx** (port 2026): Unified reverse proxy entry point
- **Provisioner** (port 8002, optional in Docker dev): Started only when sandbox is configured for provisioner/Kubernetes mode

**Runtime**:

- `make dev`, Docker dev, and production all run the agent runtime in Gateway via `RunManager` + `run_agent()` + `StreamBridge` (`packages/harness/deerflow/runtime/`). Nginx exposes that runtime at `/api/langgraph/*` and rewrites it to Gateway's native `/api/*` routers. Every maintained Nginx config keeps that route at a bounded 20 MiB request-body limit with request buffering disabled, so long JSON prompts stream to Gateway without temporary-file failures.
- Kubernetes starts `ppt-master-runner` from its independently pinned image as a
  separate Deployment and ClusterIP Service. It must not share the ordinary
  Gateway Pod failure domain. Nginx rewrites `/api/ppt-master-runtime/*` to that
  Service on port 8003, while Gateway uses the same stable service DNS name.
  A bounded init gate requires three consecutive PostgreSQL connection samples;
  transient cluster-DNS or database startup failures therefore block only the
  Runner Pod and never remove the ordinary Gateway endpoint.
  The Runner must ship the entire Alembic chain already present in the shared
  database. Gateway-only migration ConfigMap overlays are not evidence that
  its bare image contains those revisions.
  Metadata-only migration validators must accept both `ast.Assign` and
  `ast.AnnAssign`, since the deployed revisions use typed declarations; validate
  their literal values without importing or executing a migration module.
  The Runner skips global startup
  orphan reconciliation: its empty local registry and a peer run's NULL lease
  cannot establish that the ordinary Gateway died. This narrow startup guard
  is not a substitute for ownership heartbeats in multi-instance Gateway
  deployments, and intentionally does not reclaim stale PPT rows on restart.
  `agents/ppt_master_runner/` compiles one `StateGraph` relay node and must not
  call `create_agent`, create a chat model, or install Agent middleware. It
  acquires the thread Sandbox and talks to the authenticated
  `ppt-master-agent-host` container on that Pod's ClusterIP Service port 8788.
  The host container uses the PPT-capable Sandbox image, runs the real Codex
  Agent CLI, installs the exact vendored v4.5.0 Skill into its normal Skill
  directory, persists the Codex session/events on user-data, and returns final
  output paths. Each turn replays the original Slash request and requires fresh
  reads of the applicable upstream authorities before mutation.
  The Provisioner must initialize `/mnt/user-data/{workspace,uploads,outputs}`
  before either Sandbox container starts. The Agent host must also revalidate
  and restore those writable roots immediately before every fresh or repair
  Agent process launch, rebuilding its writable Skill snapshot and Codex home
  when the workspace was recreated. A missing workspace is a recoverable
  storage-directory invariant, not a reason to manufacture a replacement
  LangGraph thread or surface downstream state-polling 404s.
  The runner must wait separately for the Agent host `/health` endpoint after
  Sandbox acquisition; readiness of the command API on port 8080 does not prove
  readiness of the host sidecar on port 8788. Bounded transient DNS/connect/read
  failures are retried during startup and task polling, while a missed deadline
  remains a visible host-startup failure. Because Gateway and the runner are
  separate processes with separate warm-pool maps, the runner must also hold a
  shared filesystem execution lease for the whole Agent turn. Every AIO Sandbox
  destruction path requires a non-blocking exclusive lease and defers deletion
  without dropping tracking when another process is executing. Open the lease
  read-write without truncation: Linux `LOCK_SH` rejects a write-only
  descriptor, while destruction still uses `LOCK_EX` through the same helper.
  Exact owning
  validator/export commands may not use terminal truncation pipelines or shell
  fallbacks that hide their exit status. Completion runs the upstream delivery
  checker plus an independent OOXML capability reconciliation against the
  explicit user request. A mismatch resumes the same Agent session and project
  for at most three concrete repair attempts; only a new or changed accepted
  PPTX and a fresh paired preview are returned. Public command/file/message receipts are mapped to the existing
  durable `task_*` stream. Normalize every streamed Agent message to a plain
  message dictionary before publishing it: the shared durable step projector
  must never receive a live LangChain `BaseMessage`. Because this transport
  graph intentionally does not attach the lead `TitleMiddleware`, it derives a
  bounded deterministic title from the original Slash request (preferring an
  explicit `《topic》`) and writes the normal `ThreadState.title` field so the
  run worker can sync `threads_meta`. Keep that title initializer as a short
  state-only graph node, or use LangGraph's exact injected parameter names
  `config` and `runtime`; underscore-prefixed variants are not injected and
  abort the thread before its first checkpoint. Its regression test must invoke
  the compiled graph rather than checking graph topology alone. Missing Agent credentials or a provider without the
  required Responses protocol is a deployment error, never a fallback to the
  lead Agent. Provider TOML is configuration, not authentication: the selected
  provider's declared `env_key` must be non-empty. Local Kubernetes uses
  Model Studio `qwen3.7-max` through its native Responses endpoint and the
  existing `DASHSCOPE_API_KEY`. Enterprise Kubernetes gives the Agent host only
  `PPT_MASTER_RESPONSES_RELAY_KEY`; Gateway retains `DeepSeek_V4_API_KEY` and
  translates Codex Responses turns to the internal DeepSeek streamed Chat
  Completions contract at `/api/codex/deepseek/v1/responses`. Keep that exact
  endpoint self-authenticated, CSRF-exempt only by exact path, direct to
  `model.ai.cnooc` with `trust_env=false`, and fully CA/hostname verified.
  Codex CLI may group function or custom tools inside Responses `namespace`
  containers. The enterprise relay must flatten every child to a deterministic,
  collision-safe Chat Completions function name, preserve that mapping across
  replayed tool history, and restore the original child name, kind, and namespace
  on response items. Never drop the namespace or its children to accommodate an
  upstream endpoint that supports only ordinary functions. Enterprise Codex
  configuration disables provider-hosted `web_search`; the relay also omits the
  known `web_search` and `web_search_preview` definitions defensively because
  the internal Chat Completions endpoint cannot execute Responses-hosted tools.
  Ordinary function/custom tools and namespace children remain available, and
  unknown future hosted tool types still fail closed instead of being silently
  discarded.
- Scheduled-task executions must reuse that same Gateway run lifecycle. The scheduler may decide _when_ work runs, but it must dispatch through the existing run path rather than introducing a parallel execution stack. Scheduled and manual occurrences share one global concurrency budget; durable queue admission, per-task active-run uniqueness, leases, and atomic terminal bookkeeping remain repository-owned so optional multi-instance scheduling is race-safe. PostgreSQL serializes launch-budget claims with an advisory transaction lock; SQLite must reserve its database-wide writer with `BEGIN IMMEDIATE` before counting and promoting a queued occurrence, or claims for distinct rows can exceed that shared budget.
- The enterprise single-replica Gateway reserves 2 CPU / 4Gi and is limited to
  4 CPU / 16Gi. Preserve this production saturation baseline in
  `k8s/enterprise/runtime-patches.yaml`; a scheduler or parser hotfix that
  deep-copies the live Pod template must prove those resources remain unchanged.
- Group OA policy-flow work is a durable orchestration layer over that same run
  lifecycle, not a second Agent runtime. The public machine API is limited to
  `/api/v1/integrations/oa/policy-flow/jobs`; it authenticates a scoped Bearer
  key, pins owner/agent and optional callback from server environment, stores idempotency,
  leases, retries, and callback state in `oa_policy_flow_jobs`, and launches via
  `launch_scheduled_thread_run`. Completion requires exactly one valid UTF-8
  JSON artifact from `present_files`. Polling-only mode sets
  `OA_POLICY_FLOW_CALLBACK_ENABLED=false`, marks terminal jobs' callback state
  `disabled`, and never schedules callback transport. When enabled, keep callback
  targets operator-owned, `trust_env=false`, redirect-disabled, and TLS verified. Never allow an OA
  request to select a user, custom agent, callback URL, filesystem path, or
  internal thread/run API. See `docs/oa-policy-flow-integration.md`.
- Run persistence records the effective model resolved by the agent factory even when callers omit `context.model_name`, so new default-model runs remain attributable in admin usage reports.

**Project Structure**:

```
deer-flow/
├── Makefile                    # Root commands (check, install, dev, stop)
├── config.yaml                 # Main application configuration
├── extensions_config.json      # MCP servers and skills configuration
├── backend/                    # Backend application (this directory)
│   ├── Makefile               # Backend-only commands (dev, gateway, lint)
│   ├── langgraph.json         # LangGraph Studio graph configuration
│   ├── packages/
│   │   └── harness/           # deerflow-harness package (import: deerflow.*)
│   │       ├── pyproject.toml
│   │       └── deerflow/
│   │           ├── agents/            # LangGraph agent system
│   │           │   ├── lead_agent/    # Main agent (factory + system prompt)
│   │           │   ├── middlewares/   # middleware components (see Middleware Chain section)
│   │           │   ├── memory/        # Memory extraction, queue, prompts
│   │           │   └── thread_state.py # ThreadState schema
│   │           ├── sandbox/           # Sandbox execution system
│   │           │   ├── local/         # Local filesystem provider
│   │           │   ├── sandbox.py     # Abstract Sandbox interface
│   │           │   ├── tools.py       # bash, ls, read/write/str_replace
│   │           │   └── middleware.py  # Sandbox lifecycle management
│   │           ├── subagents/         # Subagent delegation system
│   │           │   ├── builtins/      # general-purpose, bash agents
│   │           │   ├── executor.py    # Background execution engine
│   │           │   └── registry.py    # Agent registry
│   │           ├── tools/builtins/    # Built-in tools (present_files, ask_clarification, view_image, review_skill_package)
│   │           ├── mcp/               # MCP integration (tools, cache, client)
│   │           ├── models/            # Model factory with thinking/vision support
│   │           ├── skills/            # Skills discovery, loading, parsing
│   │           ├── config/            # Configuration system (app, model, sandbox, tool, etc.)
│   │           ├── community/         # Community tools (search/fetch/scrape, image search, AIO sandbox)
│   │           ├── reflection/        # Dynamic module loading (resolve_variable, resolve_class)
│   │           ├── utils/             # Utilities (network, readability)
│   │           └── client.py          # Embedded Python client (DeerFlowClient)
│   ├── app/                   # Application layer (import: app.*)
│   │   ├── gateway/           # FastAPI Gateway API
│   │   │   ├── app.py         # FastAPI application
│   │   │   └── routers/       # FastAPI route modules (models, mcp, memory, skills, uploads, threads, artifacts, agents, suggestions, channels, announcements)
│   │   └── channels/          # IM platform integrations
│   ├── tests/                 # Test suite
│   └── docs/                  # Documentation
├── frontend/                   # Next.js frontend application
└── skills/                     # Agent skills directory
    ├── public/                # Public skills (committed)
    └── custom/                # Custom skills (gitignored)
```

## Important Development Guidelines

### Documentation Update Policy

**CRITICAL: Always update README.md and AGENTS.md after every code change**

When making code changes, you MUST update the relevant documentation:

- Update `README.md` for user-facing changes (features, setup, usage instructions)
- Update `AGENTS.md` for development changes (architecture, commands, workflows, internal systems). `CLAUDE.md` imports it via `@AGENTS.md`, so editing `AGENTS.md` updates both.
- Keep documentation synchronized with the codebase at all times
- Ensure accuracy and timeliness of all documentation

## Replay golden test

`tests/test_replay_golden.py` uses recursion_limit 2000 to match the browser lead-agent budget; title and suggestion nodes exceed the former 50-step test ceiling. Regenerate `write_read_file.ultra.events.json` only after a normal replay completion with no replay misses.

## Commands

**Root directory** (for full application):

```bash
make check      # Check system requirements
make install    # Install all dependencies (frontend + backend)
make dev        # Start all services (Gateway + Frontend + Nginx), with config.yaml preflight
make start      # Start production services locally
make stop       # Stop all services
```

**Backend directory** (for backend development only):

```bash
make install            # Install backend dependencies
make dev                # Run Gateway API with reload (port 8001)
make gateway            # Run Gateway API only (port 8001)
make test               # Run all backend tests
make test-blocking-io   # Run strict Blockbuster runtime gate on tests/blocking_io/
make lint               # Lint with ruff
make format             # Format code with ruff
make migrate-rev MSG="..."  # Autogenerate a new alembic revision (see Schema Migrations section)
```

The `detect-blocking-io` target parses `app/`, `packages/harness/deerflow/`,
and `scripts/` with AST. By default it reports only blocking IO candidates that
are inside async code, reachable from async code in the same file, or reachable
from sync-only `AgentMiddleware` before/after hooks that LangGraph can execute
on the async graph path. It prints a concise summary and writes complete JSON
findings to `.deer-flow/blocking-io-findings.json` at the repository root
(both `make detect-blocking-io` from the repo root and `cd backend && make
detect-blocking-io` resolve to the same repo-root path). JSON findings include
`priority`, `location`, `blocking_call`, `event_loop_exposure`, `reason`, and
`code` for model-assisted or manual review. `priority` is a deterministic
review ordering from operation type, not proof of a bug. Bare-name same-file
calls are resolved by function name, so duplicate helper names in one file can
conservatively over-report async reachability. It is intentionally
informational and is not run from CI in this round.

For a diff-scoped view of the same findings, `scripts/scan_changed_blocking_io.py`
(repo root) reports findings on the added lines of `git diff <base>...HEAD`
plus findings new versus the merge base (so a new async caller exposing an
untouched sync helper in the same file is still reported) — used by the
`blocking-io-guard` skill (`.agent/skills/blocking-io-guard/`) as the
deterministic scope step before routing each candidate to a fix and/or a
`tests/blocking_io/` runtime anchor.

Regression tests related to Docker/provisioner behavior:

- `tests/test_docker_sandbox_mode_detection.py` (mode detection from `config.yaml`)
- `tests/test_provisioner_kubeconfig.py` (kubeconfig file/directory handling)
- `tests/test_provisioner_request_threading.py` (keeps provisioner sandbox CRUD
  endpoints as sync FastAPI handlers so synchronous K8s client calls run in the
  Starlette worker pool instead of on the ASGI event loop)
- `tests/test_mineru_adapter.py` (locks the enterprise PDF API protocol, the
  searchable/scanned routing decision, multilingual figure capture, searchable
  visual-page evidence, content-based legacy OLE2 Word/PowerPoint detection,
  native Word plus rendered-visual merging, and the
  WeKnora-compatible response envelope used by the PDF/DOC/DOCX/PPT/PPTX
  adapter)

The WeKnora Gateway upload proxy reads `WEKNORA_PRESENTATION_PARSER_ENGINE`
(`markitdown` by default) and `WEKNORA_MINERU_ENDPOINT`. The enterprise overlay
sets them to `mineru` and the in-cluster adapter Service. With
`WEKNORA_MINERU_DOCUMENT_VISUAL_FALLBACK=true`, PDF/DOC/DOCX/PPT/PPTX uploads
use that adapter. Searchable PDF/Word text stays on the bundled native parsers;
Word's native text/images are merged with a temporary PDF visual pass, and
searchable pages containing images, vector graphics, or multilingual figure
captions receive captioned full-page visual evidence. Scanned/mixed documents
and presentations use MinerU. Other uploads omit parser overrides and preserve
the knowledge base defaults.
PDF routing remains fault-isolated: PDFium inspection failure first tries the
independent builtin parser and then MinerU. Visual publication is strict once a
document is known to require page evidence; a required source-page rendering
failure rejects the incomplete parse instead of publishing text-only success.
The approved upstream is MinerU-2605. `EnterpriseMinerUClient` accepts its
service root or a legacy full `/file_parse` or `/tasks` URL. Enterprise runs use
the provider-supported asynchronous Bearer-authenticated contract: submit one
multipart `POST /tasks`, poll `/tasks/{task_id}`, and retrieve
`/tasks/{task_id}/result` with Markdown/images enabled and one bounded
1800-second end-to-end timeout. Production must keep
`MINERU_REQUEST_MODE=async`; the Adapter's WeKnora-facing `/file_parse` request
remains synchronous from the caller's perspective while the upstream work is
polled internally. Retry transient business code `20006`, short-lived status or
result HTTP 404, and transient status/result connection or read-timeout failures
only against the same task ID. Never repeat `POST /tasks`, fall back to a second
synchronous submission after task acceptance, or trust a provider-returned
cross-origin status/result URL. The synchronous 20-page batch
settings remain available only as an explicit emergency rollback path.
Word native text/tables/images remain the text-quality authority, but publication
requires the complete PDF visual pass; conversion, MinerU, or required
source-page rendering failure must reject the incomplete document. Normalize the JSON `results` document into
WeKnora Markdown plus persistent images, keep legacy `pages` parsing only for
cutover compatibility, treat HTTP-200 `success=false` envelopes as failures,
and keep `trust_env=false` on the direct internal route.
Requests does not merge
`REQUESTS_CA_BUNDLE` from the environment when `trust_env` is disabled, so the
client must explicitly assign the reviewed mounted CA bundle (preferring
`MINERU_CA_BUNDLE`, then `REQUESTS_CA_BUNDLE`, then `SSL_CERT_FILE`) to the
session's `verify` setting. A configured missing CA path is a startup error;
never replace this binding with `verify=false` or disable hostname validation.
The adapter uses a threaded HTTP server but serializes the complete
PDFium/DocReader parse pipeline through `ParseCoordinator`. This is required
because the native parser stack can corrupt its allocator state when two
documents enter it concurrently. `/health` remains responsive during a long
parse and reports `parser.active` plus `parser.waiting`; do not bypass the
coordinator or increase in-process parser concurrency.
The enterprise three-node overlay obtains safe parallelism from six adapter
worker Pods, evenly spread two per node, rather than from threads inside one
process. A separate bounded dispatcher routes only to free worker Pod IPs and
prioritizes interactive conversation uploads over batch work that has not yet
started. Keep the single WeKnora App replica at four core Asynq workers plus two
shared workers, so no more than six document requests leave its durable Redis
queue at once. MinerU-2605's 20-request upstream capacity is reserve headroom
and does not override this local native-parser safety boundary.
Any WeKnora replica or worker-pool increase must be paired with a reviewed
adapter-capacity change and a 3/10/30-document acceptance test.
The pinned WeKnora DocReader image receives narrow source-mounted parser
compatibility through `sitecustomize`: XLSX compatibility installs immediately,
while the webpage parser uses a one-shot post-import hook because the image adds
its application package path after Python startup. The hook may observe only
`docreader.parser.web_parser`, must delegate its normal loader, and must remove
itself as soon as the static-HTML/WebKit compatibility patch is installed.
When `WEKNORA_MINERU_AUTO_CONFIGURE` is enabled, the SSO bridge also merges that
adapter endpoint into the parser settings of spaces the current user owns or
administers; it never stores the upstream enterprise API URL or Key in WeKnora.

The same SSO bridge maintains seven platform built-in WeKnora models: three
chat models (`qwen-plus`, Bailian `deepseek-v4-pro`, and CNOOC Haineng
`DeepSeek-V4-Flash`), plus embedding, rerank, vision, and speech recognition.
When the CNOOC Haineng credential is present, internal
`DeepSeek-V4-Flash` is the default KnowledgeQA model; Qwen and Bailian
DeepSeek remain selectable alternatives. Without that credential, Qwen remains
the safe default. `WEKNORA_PREFER_CNOOC_DEEPSEEK=false` explicitly opts out.
The Bailian models reuse `LLM_API_KEY` unless a type-specific key overrides it.
The internal DeepSeek entry uses `DeepSeek_V4_API_KEY`, is omitted when that key
is absent, and requires `model.ai.cnooc` in proxy-bypass variables. WeKnora
0.7.0's ASR client posts OpenAI multipart audio, so the speech model uses the
internal `http://gateway:8001/api/weknora/bailian/v1` base URL. Gateway validates
the shared key, translates the request to Bailian `qwen3-asr-flash`
input-audio chat format, and does not persist audio bytes.
The built-in `text-embedding-v4` model uses the same internal base URL.
Gateway validates `EMBEDDING_API_KEY` and relays the OpenAI-compatible
`/embeddings` JSON body through its enterprise proxy/CA/OpenSSL compatibility
path without persisting document text. This keeps WeKnora away from the
interception proxy's alternating certificate profiles.
When `WEKNORA_BAILIAN_TLS_COMPAT=true`, only the authenticated Bailian
embedding and ASR adapter clients build an explicit
`DEFAULT:@SECLEVEL=1` SSL context for the audited enterprise proxy's legacy RSA
certificate. CA-chain and hostname verification remain enabled.
When `TAVILY_TLS_COMPAT=true`, the DeerFlow Tavily integration similarly mounts
a verified `DEFAULT:@SECLEVEL=1` SSL context only on its own HTTPS session. It
loads the configured enterprise CA bundle and must not be replaced with
`verify=false`, `--insecure`, or a process-wide verification bypass.

Direct production builds of `backend/Dockerfile` default `UV_EXTRAS` to
`postgres` because the Kubernetes Gateway manifests use PostgreSQL for the
database, checkpointer, and store. Callers can still replace or extend that
comma-separated build argument; Docker Compose continues to pass its explicit
environment-derived value.

CNOOC SSO stores the validated upstream platform Token in the HttpOnly
`cnooc.platform_token` cookie and stores only its SHA-256 digest in the nullable,
unique `users.cnooc_platform_token_hash` binding. Token login must commit that
binding before issuing either login cookie. The frontend's same-origin server
launch routes read the cookie only for four allowlisted data-lake external
agents and add the current Token to the final redirect URL. The **专业文档解析专家**
card is an explicit direct-link exception and opens
`https://cnoocclaw.cnooc.com.cn/zywdjxzj/`, whose destination owns its data-lake
session handling. Before any launch, file-list, or download route consumes the
Token, it calls the authenticated
`GET /api/v1/data-lake/auth/session` endpoint, which compares the Cookie digest
with the current user's binding in constant time. A mismatch clears the stale
Cookie; temporary validation failure remains fail-closed without deleting a
possibly valid Cookie. After the existing appCode-refresh retry, an HTTP 200
token-exchange response with a non-success business code maps to
`403 data_lake_permission_denied`; transport, invalid-response, and upstream 5xx
failures must not use that permission code. Those responses must remain
`no-store` and `no-referrer`; the Token must never be hardcoded, returned in a
response body, read by frontend JavaScript, or placed in browser-readable
storage. Logout clears this cookie together with the DeerFlow `access_token`.
The composer can acquire the same cookie through authenticated
`POST /api/v1/data-lake/auth/token`. Gateway persists a SHA-256 challenge digest
bound to the current user for 60 seconds, then calls the Data Lake appCode and
token APIs, validates an exchanged Token through the CNOOC identity endpoint,
requires that stable identity to match the challenge-bound user, and commits
the same user Token digest before setting the Cookie. A `cnooc` user must match
the trusted platform `userid` exactly. An `iam` user must already have a
non-empty IAM `oauth_id`, and both the current and trusted platform identities
must have the same canonical `@cnooc.com.cn` email. Local users, other OAuth
providers, missing IAM identities, non-corporate emails, and mismatches remain
fail-closed. The appCode
request must send `timeStamp` as the first eight digits of the Unix-seconds
string; the platform rejects the complete ten-digit epoch. The provider
maps an exchanged Token rejected by that identity endpoint to the scoped
`401 data_lake_login_required` response; the frontend handles this business 401
without invoking its generic DeerFlow-login redirect. The provider
calls the public, exact-path and CSRF-exempt
`POST /api/v1/data-lake/auth/callback`; that route enforces a 16 KiB strict
schema with a published OpenAPI request body, atomically consumes the
challenge, supports only identical idempotent retries, and returns the bound
user profile with `no-store`/`no-referrer` headers. The shared Nginx entry
rate-limits the exact callback path per client IP and returns the documented
JSON 429 response before excess traffic reaches SQL. Keep callback challenges
in shared SQL persistence so multi-worker
issuance and callbacks cannot diverge. Never persist the raw challenge or
platform token.
When `DEER_FLOW_SSO_ONLY=true`, the Gateway is the authoritative enforcement
boundary for enterprise-only authentication: local login, registration,
first-admin initialization, password changes, and generic OIDC authorization
and callbacks return `403 local_auth_disabled`. Provider discovery returns an
empty list, while `/api/v1/auth/cnooc/login` remains available. Keep this check
server-side rather than relying on the Frontend to hide forms.
Every successful CNOOC SSO login also refreshes the nullable DeerFlow user
profile fields sourced from the validated response (`ryxm`, `bmId`, `bmdm`,
and `bmmc`). Admin dashboard responses expose only the display name and
organization name alongside the existing login account; credentials and the
platform Token remain excluded.

CNOOC IAM is a separate lane from DataLake platform-token SSO. Browser login
uses IAM's authorization-code flow and may bind only a prior BIM-synchronized
`oauth_provider="iam"` account; it must not auto-provision a user. BIM account
synchronization reaches `/api/v1/iam/account-sync` through a Base64
SM4-ECB-PKCS7 envelope, validates its independent remote credentials and SM3
signature, and returns the same encrypted format. It is therefore exempt from
browser session/CSRF checks but must never log encrypted bodies, plaintext
credentials, signatures, or SM4 keys. Never place an IAM access token in the
`cnooc.platform_token` cookie: DataLake compatibility/exchange is a distinct
integration. See `docs/iam-integration.md`.

In provisioner/Kubernetes mode, `sandbox.environment` is injected through the
AIO sandbox's structured per-command `bash.exec` environment. The remote backend
must not silently discard these values or persist credentials in Pod manifests;
request-scoped secrets override configured values for the command that uses them.
The sandbox image must support `/v1/bash/exec` (all-in-one-sandbox 1.9.3+).
Set `sandbox.sandbox_api_key` for shared AIO deployments. The Gateway SDK and
readiness polling send it as `Authorization: Bearer`; Provisioner-created Pods
receive the matching `SANDBOX_API_KEY` through a Secret, and their exec probes
read that environment value without embedding it in the Pod specification.

Sandbox capacity is reconciled when the Gateway starts: provisioner-managed
Pods left by a previous Gateway process are adopted as warm entries and the
oldest entries are immediately removed until `sandbox.replicas` is satisfied.
Creation also removes every excess warm entry needed to reserve one slot; active
Sandboxes remain a soft-cap exception and are reclaimed by `idle_timeout`.

Blocking-IO runtime gate (`tests/blocking_io/`):

- Wraps every item under `tests/blocking_io/` with a strict Blockbuster
  context scoped to `app.*` and `deerflow.*` (see
  `tests/support/detectors/blocking_io_runtime.py`). Any sync blocking IO
  call whose stack passes through DeerFlow business code while running on
  the asyncio event loop raises `BlockingError` and fails the test.
- Regression anchors live there: `test_skills_load.py` (locks the
  `asyncio.to_thread` offload around `LocalSkillStorage.load_skills`, fix
  for #1917); `test_sqlite_lifespan.py` (locks the offload around
  SQLite path resolution plus `ensure_sqlite_parent_dir`, fix for #1912);
  `test_jsonl_run_event_store.py` (locks `JsonlRunEventStore`'s async
  API offloading its file IO via `asyncio.to_thread`);
  `test_uploads_middleware.py` (locks `UploadsMiddleware.abefore_agent`
  offloading the uploads-directory scan off the event loop); and
  `test_uploads_router.py` (locks Gateway upload/list/delete endpoints
  offloading upload directory creation, staged writes, chmod/cleanup,
  directory scans/deletes, and remote sandbox sync off the event loop).
- `test_gate_smoke.py` is a meta-test asserting the gate actually catches
  unoffloaded blocking IO and that the `@pytest.mark.allow_blocking_io`
  opt-out works.
- Coverage boundary: the gate only sees code that test execution actually
  touches. Static AST coverage is a separate concern (out of scope for
  this PR).
- CI: runs on every PR via `.github/workflows/backend-blocking-io-tests.yml`,
  hard-fail.

Boundary check (harness → app import firewall):

- `tests/test_harness_boundary.py` — ensures `packages/harness/deerflow/` never imports from `app.*`

CI runs these regression tests for every pull request via [.github/workflows/backend-unit-tests.yml](../.github/workflows/backend-unit-tests.yml).

## Architecture

### Harness / App Split

The backend is split into two layers with a strict dependency direction:

- **Harness** (`packages/harness/deerflow/`): Publishable agent framework package (`deerflow-harness`). Import prefix: `deerflow.*`. Contains agent orchestration, tools, sandbox, models, MCP, skills, config — everything needed to build and run agents.
- **App** (`app/`): Unpublished application code. Import prefix: `app.*`. Contains the FastAPI Gateway API and IM channel integrations (Feishu, Slack, Telegram, DingTalk).

**Dependency rule**: App imports deerflow, but deerflow never imports app. This boundary is enforced by `tests/test_harness_boundary.py` which runs in CI.

**Import conventions**:

```python
# Harness internal
from deerflow.agents import make_lead_agent
from deerflow.models import create_chat_model

# App internal
from app.gateway.app import app
from app.channels.service import start_channel_service

# App → Harness (allowed)
from deerflow.config import get_app_config

# Harness → App (FORBIDDEN — enforced by test_harness_boundary.py)
# from app.gateway.routers.uploads import ...  # ← will fail CI
```

Package import hygiene: the `deerflow.agents` and `deerflow.subagents` package
roots expose heavyweight graph/executor entrypoints lazily. Internal modules
that only need lightweight types, config, or registries should import the
concrete submodule instead of adding eager package-root imports that pull in the
tool graph or subagent executor during state/schema imports.

### Agent System

**Lead Agent** (`packages/harness/deerflow/agents/lead_agent/agent.py`):

- Entry point: `make_lead_agent(config: RunnableConfig)` registered in `langgraph.json`
- Dynamic model selection via `create_chat_model()` with thinking/vision support
- Tools loaded via `get_available_tools()` - combines sandbox, built-in, MCP, community, and subagent tools
- System prompt generated by `apply_prompt_template()` with skills, memory, and subagent instructions

**ThreadState** (`packages/harness/deerflow/agents/thread_state.py`):

- Extends `AgentState` with: `sandbox`, `thread_data`, `title`, `artifacts`, `todos`, `uploaded_files`, `viewed_images`, `goal`, `promoted`, `delegations`, `skill_context`, `skill_workflow`, `summary_text`
- Uses custom reducers: `merge_artifacts` (deduplicate), `merge_viewed_images` (merge/clear), `merge_goal` (preserve the active goal across ordinary state updates unless the goal writer replaces it), `merge_promoted` (catalog-hash-scoped deferred tool promotions), `merge_delegations` (append task delegation entries, same id latest wins, terminal status never downgraded, capped to the most recent entries), `merge_skill_context` (dedupe active-skill references by path, keep the most recently read entries; entries store a name/path/description reference, not the SKILL.md body), and `merge_skill_workflow` (replace on a new Slash activation; merge bounded tool receipts and checkpoint fields for the current activation). `summary_text` is a LastValue channel updated by summarization and projected into model requests as durable context data instead of being stored as a `messages` item.

**Runtime Configuration** (via `config.configurable`):

- `thinking_enabled` - Enable model's extended thinking
- `model_name` - Select specific LLM model
- `is_plan_mode` - Enable TodoList middleware
- `subagent_enabled` - Enable task delegation tool
- `max_concurrent_subagents` - Per-response `task` call concurrency limit (clamped by `SubagentLimitMiddleware`)
- `max_total_subagents` - Optional per-run total delegation cap override (falls back to `subagents.max_total_per_run`, clamped to 1-50)
  Gateway and `DeerFlowClient.stream()` always provide the runtime `run_id`; custom
  graph integrations must do the same. If it is absent, enforcement deliberately
  counts the thread's full delegation ledger (fail-restrictive) and emits a warning.

### Middleware Chain

Lead-agent middlewares are assembled in strict order across three functions: the shared base in `packages/harness/deerflow/agents/middlewares/tool_error_handling_middleware.py` (`_build_runtime_middlewares`, exposed via `build_lead_runtime_middlewares`), then the lead-only middlewares appended in `packages/harness/deerflow/agents/lead_agent/agent.py` (`build_middlewares`). Items marked _(optional)_ are appended only when their config/runtime condition holds, so the live chain length varies.

**Shared runtime base** (`build_lead_runtime_middlewares`; subagents reuse most of this via `build_subagent_runtime_middlewares`):

1. **InputSanitizationMiddleware** - First, so it is the outermost `wrap_model_call` wrapper; every inner middleware (including LLM retries) sees sanitized messages. `additional_kwargs.original_user_content` is server-owned provenance: Gateway strips caller-supplied values for non-internal run requests, trusted IM calls may carry the string they captured before adding transport/file context, and the middleware replaces any non-string value before wrapping. Uploads and sanitization retain first-writer-wins only for validated strings.
   1a. **PlatformConfidentialityMiddleware** - Runs immediately inside input sanitization as an advisory-only disclosure policy. It never keyword-classifies or pre-emptively refuses a request; platform diagnostics, deployment guidance, logs, configuration, network checks, code work, and user-provided data all reach the model. The policy narrowly prohibits disclosure of actual system prompts or credential values and assistance bypassing access controls. Concrete enforcement belongs to `SandboxAuditMiddleware`, sandbox environment filtering, bash-output masking, and observable/persisted-context redaction.
2. **ToolOutputBudgetMiddleware** - Caps tool output size (per app config) before it re-enters the model context
3. **ToolResultSanitizationMiddleware** - Neutralizes framework/injection tags (e.g. `<system-reminder>`) and boundary markers in _remote-content_ tool results (`web_fetch`/`web_search`/`image_search`/`web_capture`) so attacker-controlled fetched pages cannot forge trusted framework context. Mirrors `InputSanitizationMiddleware`'s user-input guardrail for the other untrusted-content entry point; sits inner of `ToolOutputBudgetMiddleware` (neutralizes the raw output, then the budget truncates). Local tool output (bash/read_file) is left untouched. Scope is a name-based allowlist, so MCP remote-content tools registered under other names (e.g. `fetch_url`) are not yet covered — a metadata-tagging follow-up is tracked in the middleware source
4. **ThreadDataMiddleware** - Creates per-thread directories under the user's isolation scope (`backend/.deer-flow/users/{user_id}/threads/{thread_id}/user-data/{workspace,uploads,outputs}`); resolves `user_id` via `get_effective_user_id()` (falls back to `"default"` in no-auth mode)
5. **UploadsMiddleware** - Tracks and injects newly uploaded files into conversation (lead agent only). Same-stem Markdown outlines and fallback previews use `utf-8-sig`, so an optional leading BOM cannot hide the first heading or occupy a preview line; physical line numbers, embedded U+FEFF characters, and original bytes remain unchanged.
6. **SandboxMiddleware** - Acquires sandbox, stores `sandbox_id` in state
7. **DanglingToolCallMiddleware** - Injects placeholder ToolMessages for AIMessage tool_calls that lack responses (e.g., user interruption), preserving raw provider tool-call payloads in `additional_kwargs["tool_calls"]`; malformed tool-call names and arguments are sanitized in the model-bound request so strict OpenAI-compatible providers do not reject the next request. A newly returned `invalid_tool_calls` response is automatically routed back to the model once with the synthetic parser error, then becomes a visible error fallback if the retry is also malformed.
8. **LLMErrorHandlingMiddleware** - Normalizes provider/model invocation failures into recoverable assistant-facing errors before later stages run
9. **GuardrailMiddleware** - _(optional, if `guardrails.enabled`)_ Pre-tool-call authorization via pluggable `GuardrailProvider`; returns an error ToolMessage on deny. Providers: built-in `AllowlistProvider` (zero deps), OAP policy providers (e.g. `aport-agent-guardrails`), or custom. See [docs/GUARDRAILS.md](docs/GUARDRAILS.md)
10. **SandboxAuditMiddleware** - Audits sandboxed shell/file operations before tool execution and blocks exact destructive operations, cluster-control commands, protected secret paths, secret environment reads, and internal platform-service targeting. It intentionally allows harmless sandbox diagnostics (`hostname`, `uname`, CPU/OS inspection), read-only `/dev/tcp` connectivity probes, ordinary user-data database tools, runtime library/model variables, and generated document/presentation commands up to 512 KiB. Do not restore broad command-name, path-prefix, environment-keyword, or tool-name bans.
11. **ReadBeforeWriteMiddleware** - _(optional, if `read_before_write.enabled`, default on)_ Outermost write gate (issue #3857): `read_file` stamps a content hash onto its ToolMessage; `write_file` (append/overwrite-existing) and `str_replace` are blocked unless the newest mark for that path matches the file's current hash. Sits outside ToolProgressMiddleware and ToolErrorHandlingMiddleware so a blocked write returns immediately without consuming a ToolProgress slot. Blocked results call `normalize_tool_result` directly to stamp `deerflow_tool_meta` (`recoverable_by_model=True`) before returning, keeping the result well-formed for any outer consumer. Marks live on messages, so summarization dropping the read result invalidates the gate automatically; writes never refresh marks, forcing a re-read between consecutive edits. Gate check + tool execution are serialized per (thread, path) so same-turn parallel writes cannot reuse one stale mark; on sandboxes whose `read_file` reports failures as `"Error: ..."` strings instead of raising (AIO/E2B), uninspectable targets fail open (creation proceeds, no mark stamped)
12. **ToolProgressMiddleware** - _(optional, if `tool_progress.enabled`)_ State-machine-based stagnation guard (RFC #3177). Outer wrapper around ToolErrorHandlingMiddleware so its `wrap_tool_call` receives results already stamped with `deerflow_tool_meta`. Tracks per-(thread, tool) consecutive "no-new-info" calls across three error categories: (a) `recoverable_by_model=True` (no_results, not_found, permission, Jaccard-duplicate success): ACTIVE → WARNED (terminal — hint re-injected on each subsequent problem); (b) `recoverable_by_model=False, action≠stop` (rate_limited, transient): ACTIVE → WARNED → BLOCKED after `warn_escalation_count` more problems; (c) `recoverable_by_model=False, action=stop` (auth, config, internal): immediately BLOCKED on first occurrence. **Division of labor with LoopDetectionMiddleware:** ToolProgressMiddleware is a result-quality guard — fires after tool execution and blocks specific tools that stop producing new information; LoopDetectionMiddleware is a call-pattern guard — fires after the model responds and hard-stops the whole turn when the model repeatedly issues identical tool_calls. Both can inject HumanMessage hints in the same model call without conflict; neither reads the other's internal state.
    Normal Bash command returns are a narrow ToolProgress exception: a subprocess
    configuration or build error is recoverable because it does not mean the shared
    Bash transport is unavailable. Infrastructure exceptions remain authoritative
    and may still stop Bash.
13. **ToolErrorHandlingMiddleware** - Receives `AppConfig`, converts tool exceptions into error `ToolMessage`s so the run can continue instead of aborting, stamps every result with `deerflow_tool_meta` (status / error_type / recoverable_by_model / recommended_next_action / source) via `tool_result_meta.normalize_tool_result`, stamps structured metadata for task exception wrappers, and stamps skill-read metadata for downstream durable-context capture. Task tool result text is generated from the same status/result/error inputs as the structured metadata so callers do not hand-write a second protocol string.

Authorization identity plumbing is independent of whether authorization enforcement is enabled. Gateway removes client-supplied `is_internal` / `authz_attributes` / `channel_user_id`, derives `is_internal` only from the server-owned `request.state.auth_source`, and accepts `channel_user_id` only from an internally authenticated IM caller's top-level `body.context`; free-form `body.config` can never supply it. `build_principal_from_context` is the shared Principal builder for assembly-time authorization and `GuardrailAuthorizationAdapter`; it applies `default_role`, strict-boolean internal provenance, and copy-on-read `authz_attributes`. Task delegation carries `is_internal` plus copied attributes through `SubagentExecutor`, while `GuardrailMiddleware` maps the same runtime fields into `GuardrailRequest`. Phase 1A provides this trusted identity chain only; automatic Layer 1 filtering and Layer 2 authorization middleware wiring remain separate enforcement work.

Before changing a later authorization phase, read the [authorization RFC](../docs/plans/2026-07-10-pluggable-authorization-rfc.md) and its [implementation notes](../docs/plans/2026-07-10-pluggable-authorization-implementation-notes.md). The notes are the cumulative handoff record for merged PR behavior, reviewer feedback, trust-boundary decisions, deferred scope, and required regression coverage.

**Lead-only middlewares** (`build_middlewares`, appended after the base):

14. **DynamicContextMiddleware** - Injects the current date (and optionally memory) as a `<system-reminder>` into the first HumanMessage, keeping the base system prompt fully static for prefix-cache reuse
15. **WeKnoraScopeMiddleware** - Injects the selected `weknora_knowledge_base_ids` as hidden current-turn guidance requiring the complete permission-scoped `weknora_*` family to use that exact scope. It documents the read/search/ask/agent split, requires agent discovery before invocation, and never auto-approves WeKnora pending actions
16. **SkillActivationMiddleware** - Detects strict `/skill-name task` syntax on the latest real user message, resolves only enabled and runtime-allowed skills, records one `middleware:skill_activation` audit event, and checkpoints the selected Skill identity, content hash, original task, and deterministic completion requirements. The complete `SKILL.md` is injected on the activation call; later tool-loop calls receive a bounded continuation contract. A later user `继续`/`确认`/resume reply restores the same enabled Skill from the checkpoint, rebinds its normal declared secrets, and reinjects both the original task and current Skill body even though the reply does not repeat the Slash command. This middleware adapts blocking chat fallback instructions to DeerFlow's structured `ask_clarification`; it does not recreate a Skill's schemas, validators, stage machine, or reference-order gates in DeerFlow code.
16a. **Deferred activation-scoped tool policy** - The September 30 test-environment merge does not include the incoming, incompletely integrated permission middleware. Keep `SkillActivationMiddleware` followed by `SkillWorkflowMiddleware` and the durable-context chain as in the verified production image. Do not claim per-activation schema/execution enforcement until its context contract, shared-token plumbing, redaction, and end-to-end tests are implemented together. See `../docs/enterprise-merge-20260930-deferred-skill-policy.md`.
17. **SkillWorkflowMiddleware** - Observes explicit-Skill tool calls/results and stores bounded, tool-backed receipts in `ThreadState.skill_workflow`: selected top-level workflow/profile, the latest supporting authority documents, writable project root, current command or script stage, confirmation surface, gate result, exact failed-command resume pointer, requirements parsed from the Skill's own design contract, and delivered artifacts. It does not infer or invent Skill stages. This main-Agent middleware is retained for ordinary Skills and legacy presentation threads only; new explicit `/ppt-master` tasks bypass the complete lead middleware chain through the isolated runner, so no new PPT workflow semantics belong here. On a new run it blocks non-read actions until the selected route and latest stage documents have been re-read, so a compact path reminder cannot silently replace their full contract. A legacy `ppt-master` workflow without a persisted route serializes the package's declared bootstrap as a successful `attribution_guard.py` receipt, `routing.md`, and exactly one selected top-level route. If a model batches a valid current prerequisite with a redundant integrity call or later read, the middleware preserves only the current prerequisite and lets the normal tool node create its real receipt instead of discarding the complete batch and consuming sequence retries. Default Generate also reloads its durable `design_spec.md` and `spec_lock.md`. Managed confirmation receipts named by the loaded authority cannot be fabricated through generic file/shell writes; the Skill's user/server command remains their owner. Successful page template selection is checkpointed independently of the bounded receipt tail. While that page owner remains active and its wait has not failed, a plain-text chat confirmation request is also rejected as a confirmation-surface switch. Failed owning Skill receipts may run active-project repairs, but those repairs do not clear the gate; the same semantic owner operation must succeed, and Confirm UI wait/reset/daemon/template-completion modes are distinct operations. A presentation workflow is complete only after its real deck passes delivery acceptance and `present_files` succeeds. The acceptance adapter reconciles confirmed Deck/Layout handoffs with the official `template_reuse_scope` contract: `style` stays flat, while `layout`/`mirror` require structured mode, adherence, complete prototype/Master/Layout/page mappings, and matching package counts. Failed delivery acceptance permits only active-project repair plus another delivery attempt, and only successful `present_files` clears it. A model message that claims completion while still calling inspection/repair tools keeps those tools but has its unverified completion prose removed from history. Unchanged sequence violations stop after three attempts with the exact gate/resume pointer. One unsupported explicit completion claim is retried; a repeated no-progress completion claim becomes a visible bounded failure.
    A forbidden generic confirmation-receipt write remains blocked. If the official owner wait or template-completion command subsequently fails on a real project precondition, that command becomes the durable failed owner so its documented project repair and same-owner retry can proceed without weakening the managed-artifact boundary.
    For one selected template workspace, template completion, Strategist contract authoring, and SVG execution require the complete portable `templates`/`images`/`icons` mapping. Each template-copy command is postflight-checked; only a complete successful copy becomes a durable project mutation receipt. A live page confirmation owner cannot be shut down or silently replaced with chat unless its bounded wait actually times out or the user explicitly requests the surface switch.
18. **DurableContextMiddleware** - Captures `task` delegations into `ThreadState.delegations`, loaded skill-file references into `ThreadState.skill_context`, and projects the checkpointed `skill_workflow` before summarization can erase the corresponding tool history. On a new user turn, an earlier-run `in_progress` delegation with a run id and no matching `ToolMessage` is marked `cancelled`; resumed runs, same-run entries, entries without run ids, and entries with any structured or legacy reply are preserved. Static authority rules are injected as a `SystemMessage`; untrusted field values (`summary_text`, delegation results, skill descriptions, original Skill task, paths, commands, and receipts) are injected separately as a hidden `HumanMessage` data block. `build_subagent_runtime_middlewares` also attaches this middleware immediately before subagent summarization so a compacted `summary_text` is projected ahead of a preserved assistant/tool tail instead of leaving strict providers with an assistant-first request.
19. **SummarizationMiddleware** - _(optional, if enabled)_ Context reduction when approaching token limits
20. **TodoListMiddleware** - _(optional, if `is_plan_mode`)_ Task tracking with the `write_todos` tool
21. **TokenUsageMiddleware** - _(optional, if `token_usage.enabled`)_ Records token usage metrics; subagent usage is merged back into the dispatching AIMessage by message position
22. **TitleMiddleware** - Auto-generates the thread title after the first complete exchange and normalizes structured message content before prompting the title model. If a first-turn run is interrupted before this middleware can write a title, `runtime/runs/worker.py` keeps the run in a finalizing state, persists a local fallback title from the latest checkpoint or original run input, and then syncs it to `threads_meta.display_name`. Replacement runs admitted by `multitask_strategy="interrupt"` / `"rollback"` wait for older same-thread finalization before entering the graph; the interrupted run only skips the fallback title write once a later run has started and may have advanced the checkpoint.
23. **MemoryMiddleware** - Queues conversations for async memory update (filters to user + final AI responses)
24. **ViewImageMiddleware** - _(optional, if the model supports vision)_ Injects base64 image data before the LLM call
25. **McpRoutingMiddleware** - _(optional, if `tool_search.enabled` and PR1 MCP routing metadata produce a routing index)_ Auto-promotes matching deferred MCP tool schemas before the model call by writing a minimal `promoted` state update. It matches only the latest real `HumanMessage`, uses the global `tool_search.auto_promote_top_k` limit (default 3, clamped to 1..5), never executes tools, and must be installed before `DeferredToolFilterMiddleware`
26. **DeferredToolFilterMiddleware** - _(optional, if `tool_search.enabled`)_ Hides deferred (MCP) tool schemas from the bound model until `tool_search` or `McpRoutingMiddleware` promotes them (reads per-thread promotions from `ThreadState.promoted`, hash-scoped)
27. **SystemMessageCoalescingMiddleware** - Merges every SystemMessage into a single leading SystemMessage per request; provider-agnostic fix for strict backends (vLLM/SGLang/Qwen/Anthropic) that reject non-leading system messages. Touches the per-request payload only (checkpoint state unchanged); on midnight crossings only the latest `dynamic_context_reminder` SystemMessage survives
28. **SubagentLimitMiddleware** - _(optional, if `subagent_enabled`)_ Truncates excess `task` tool calls to enforce both the per-response concurrency limit (`max_concurrent_subagents`, clamped to 1-4) and the per-run total delegation cap (`max_total_subagents` runtime override or `subagents.max_total_per_run`, default 6, clamped to 1-50). The total cap counts current-run entries in the durable delegation ledger (entries are tagged with `run_id` when captured), so repeated planning checkpoints in one run cannot keep launching legal-sized batches indefinitely, while later user turns in the same thread get a fresh run budget. If the cap is exhausted, the middleware strips remaining `task` calls, forces `finish_reason="stop"`, and appends a visible limit note so the run can synthesize existing results instead of ending with an empty tool-call response.
29. **LoopDetectionMiddleware** - _(optional, if `loop_detection.enabled`)_ Detects repeated tool-call loops; hard-stop clears both structured `tool_calls` and raw provider tool-call metadata before forcing a final text answer; stamps `loop_capped` via `consume_stop_reason` (#3875 Phase 2), symmetric to `TokenBudgetMiddleware`. Repetition accounting is run-scoped even though one middleware instance serves many turns in the same thread. A successful `write_file`/`str_replace` that demonstrably changes durable content resets the exact-call and per-tool repetition windows, so the required `read_file -> edit -> read_file -> edit` version-gated workflow is not mistaken for a loop; an unchanged full-file write does not reset them. `read_file` repetition keys use the exact normalized line window—an omitted `end_line` is open-ended—so adjacent continuation pages are progress while the same page still trips the 3/5 identical-call guard. Keep bounded 120/240 frequency overrides for both `bash` and `read_file`: long artifact workflows legitimately cross the global 30/50 frequency threshold, while the independent identical-call hash guard still catches an actual no-progress same-call loop at 3/5.
30. **TokenBudgetMiddleware** - _(optional, if `token_budget.enabled`)_ Enforces per-run token limits
31. **Custom middlewares** - _(optional)_ Any `custom_middlewares` passed to `build_middlewares` are injected here, before the artifact-delivery/terminal-response/safety/clarification tail
32. **ArtifactDeliveryMiddleware** - Rejects final responses that reference `/mnt/user-data/outputs/...` or the corresponding `/api/threads/.../artifacts/mnt/user-data/outputs/...` URL unless every referenced file exists in the current thread outputs directory and is present in `ThreadState.artifacts` after a successful `present_files` call. It removes the invalid answer and retries once with hidden instructions requiring real shell execution, file validation, and publication; a repeated invalid claim becomes a visible error fallback with no fabricated link. The run worker applies the same validator when an in-flight provider response is interrupted before normal middleware/checkpoint finalization, then journals a same-ID fallback so paginated history cannot resurrect the uncommitted link.
33. **TerminalResponseMiddleware** - When a provider returns an empty terminal `AIMessage` after tool execution, injects a hidden recovery prompt and retries the model once; a second empty response is replaced in checkpoint state by a visible error fallback marked for the run worker, so the run finishes as an error instead of a silent success. Tool receipts are scoped to the worker-supplied pre-run message-ID boundary, so compacted historical tools cannot make a plan-only current response eligible for automatic retry. The scoped `ppt-master` exception recognizes a successful `recommendations.stage1/2/3.json` write while its Confirm UI is active as a durable user-wait boundary and returns a resumable page handoff instead of applying the generic unfinished-plan failure.
34. **WeKnoraCitationMiddleware** - Canonicalizes WeKnora 0.7 native `<kb>` tags and repairs model-truncated bare citation labels only from unique, same-origin references in the current user turn. The subagent runtime installs the same middleware before returning delegated knowledge results; the lead may recover only already-canonical `/platform/knowledge-bases/...` links from the current `task` result. Ambiguous same-named documents, historical references, arbitrary links, and code examples are never rewritten.
35. **SafetyFinishReasonMiddleware** - _(optional, if `safety_finish_reason.enabled`)_ Suppresses tool execution when the provider safety-terminated the response (e.g. `finish_reason=content_filter`); registered after terminal-response/custom middlewares so LangChain's reverse-order `after_model` dispatch runs it first
36. **ClarificationMiddleware** - Intercepts `ask_clarification` tool calls, writes a readable `ToolMessage.content` fallback plus structured `ToolMessage.artifact.human_input` request payload, and interrupts via `Command(goto=END)` (must be last). Because this middleware can short-circuit tool execution before LangChain emits `on_tool_end`, `RunJournal` performs a root-run final reconciliation for allowlisted clarification `ToolMessage`s whose `tool_call_id` was produced by the current run, so human-input request cards remain recoverable from `run_events` after checkpoint compaction. Human Input Card replies are submitted as `hide_from_ui` `HumanMessage`s with `additional_kwargs.human_input_response`; `RunJournal` persists only allowlisted hidden response sources (currently `ask_clarification`) as `llm.human.input`, which preserves answered-card state after compaction without exposing generic internal hidden context.

**Generic execution/convergence behavior:** explicit Slash Skill runs carry a
checkpointed Skill identity, original request, project root, selected authority,
tool receipts, current gate, and exact resume pointer across context compaction and
later user continuation runs. Skill packages continue to own phase ordering,
schemas, commands, and validators; DeerFlow preserves their evidence instead of
reimplementing those contracts. Generated project files remain under writable
thread user-data paths, never the read-only Skill mount. Infer a project root only
from a successful owning-tool receipt or a successful `design_spec.md` /
`spec_lock.md` receipt; an arbitrary research, image, export, or preview path under
`projects/` is not root authority. Persist those two planning documents as resume
authorities only after they exist, because Default Generate creates them after
template selection and Strategist confirmation. Read-only diagnostics remain
available while a persisted authority reload is pending. `LoopDetectionMiddleware`
still provides a bounded call-pattern safety limit and marks hard-stop messages as
terminal so an earlier workflow middleware cannot auto-resume them; the terminal tail retries
one visible future-tense plan after tool execution once. These behaviors are generic
and must not be keyed to a particular skill package. A failed Skill script gate is
cleared by a successful semantic retry of the same owning script for the same
project and explicit stage; comments, exports, and harmless shell formatting do
not strand a recovered gate, while another project or stage cannot clear it.
Resume-document retention keeps the selected top-level route, up to 24 recent
non-contract stage documents, and both durable project contracts; complete
conditional branches survive compaction and later stage reads cannot evict
`design_spec.md` or `spec_lock.md`. The selected top-level route is exclusive
until a new explicit Skill activation, so a competing top-level workflow read
is rejected rather than being misclassified as another stage. A failed
owning Skill script clears the current run's authority-load receipt and persists
its bounded redacted detail, so the next repair must first reload those documents.
When summarization changes `summary_text`, its fingerprint invalidates the same
receipt even if the run ID did not change, closing the in-run compaction gap. An
authority reload remains inside the failed gate and never overwrites its owner,
detail, or resume pointer with a temporary reload gate.
Synthetic reload tool-call ids include the run, current compacted-context
fingerprint, and failed-owner boundary. Re-reading the same path after an
in-run failure or compaction must therefore produce a new collectible receipt
instead of colliding with an earlier reload and repeating forever.
When authority prose explicitly declares that a named artifact is authored,
written, or created from a `${SKILL_DIR}/...` reference, checkpoint that direct
dependency and require a real read receipt for the reference in the current run
before `write_file` or `str_replace` can mutate the artifact. This remains
generic and authority-declared: never infer dependencies from filenames or
copy the referenced schema into middleware code.
Persist a failed gate's receipt id, tool, semantic action, stage, detail, and
resume pointer as one owner bundle. On legacy checkpoints, rebuild that bundle
from the matching failed evidence before accepting any later receipt. Successful
or failed auxiliary reads, project-scoped repair writes, user `continue` turns,
and clarification waits may add evidence or durable mutation progress but must
not replace the owner stage/detail or clear the gate. Only a successful semantic
owner retry (or the accepted delivery retry for `present_files`) may do that.
Bound long failed receipt details with both a head and a tail; retaining only the
front loses validator blocker summaries that are commonly printed after warnings.
For a Python owner command under the selected Skill root, remove only a terminal
display-only `head`/`tail` pipeline before execution. Observable Bash output is
already bounded; the truncator must not mask the owner's exit status or terminate
it with SIGPIPE. Leave ordinary and non-terminal processing pipelines unchanged.
The `ppt-master` host adapter closes the official Executor reference set before
SVG authoring from persisted Design Spec/lock facts: shared core authorities are
always required, and image, chart/native-data, structured, web-image, notes,
animation, audio, and locked mode/style authorities are conditional. Block
Executor entry while an `ai`/`web`/`slice` row remains `Pending` or `Failed`, and
require a complete Design Spec read after a partial replacement. Keep this logic
scoped to the vendored presentation Skill; it must not affect another Skill.
Sandbox execution parameters (`command`, paths, patterns, content, and replacement
strings) remain required, but every model-facing `description` is optional display
metadata and must never become a workflow failure boundary.
One unchanged semantic owner retry remains available for a transient failure. If
it fails again before a successful `write_file`/`str_replace` mutation inside the
active project, keep the already-reloaded authority valid and block another owner
retry until a durable project repair occurs. This prevents validator -> reload ->
unchanged validator cycles without hard-coding any Skill's stage graph.
The failed receipt and any report it emitted are versioned by the mutation receipt
that existed when the owner ran. Once a successful active-project mutation changes
that version, the generic host path renders the prior diagnostic as stale and
permits only the same semantic owner retry; additional repairs, reads of the old
report, and stage advancement wait for the fresh owner receipt. The
`ppt-master` first-page/final SVG gate may instead consume the checker's complete
issue batch in one consolidated project-scoped SVG read/write repair pass. It
must still reject reads of the stale validation report and all stage advancement,
and the exact checker retry remains the only way to clear the gate. Both paths
compare receipt identities and must not encode the Skill's report schema.
Each active `ppt-master` presentation-delivery model turn also has a 600-second
total wall-clock ceiling. Unlike provider read/chunk-gap timeouts, keepalive,
empty, and hidden-reasoning chunks cannot extend it. Timeout ends only that
turn with the persisted stage and exact resume pointer; project files and
non-`ppt-master` Skills remain unchanged.
`ppt-master` authority receipts are current-run scoped rather than summary-hash
scoped. Its long Executor phase may compact on nearly every model/tool
iteration, so same-run summary rewrites must not trigger the complete reference
closure again. A new run or owning Skill-script failure still invalidates the
receipt and requires exact re-reads. Other Skills keep the generic
summary-fingerprint invalidation behavior.
When Confirm UI has selected a Deck/Layout, the adapter must also close the
official template Strategist authority before Design Spec/lock authoring and
reconcile those artifacts before Executor entry. If Template Application names
prototype SVGs, `style`/flat reuse is contradictory. `layout`/`mirror` pages must
retain the mapped prototype's Master/Layout root attributes, inherited
layout/master atoms and referenced SVG definitions, and slot identity/bounds;
inspect normal file mutations before execution and shell-authored SVGs after
execution. This is a template handoff consistency check, not a second template
schema or a replacement for the Skill's own validators.
Before `--complete-template-phase` or any Stage 1+ Confirm UI wait, a single
selected workspace must have every portable file under `templates`, `images`,
and `icons` installed byte-for-byte in the project. This closes the owner
server's intentionally shallow `design_spec.md` existence check. Do not apply
the exact-source comparison to multi-workspace fusion, whose overrides remain
owned by the Skill's segment-resolution contract.
For a single checkpointed local Python validation command under the selected
Skill root and active project, the host may deterministically replace a
stale-report diversion with that exact command. The replay matcher fails closed
for compound shell, image generation, export, publication, network submission,
and every other side-effecting owner action.
Read-only failed-gate diagnostics may inspect both the active project and the
selected Skill's read-only root; official scripts can legitimately direct a
repair toward their bundled resource libraries. Sequence-guard retry accounting
includes the current owner receipt and project-mutation boundary, so durable
progress starts a fresh bounded audit while unchanged invalid decisions still
terminate after three attempts.
A recovery stage may also invoke a different Skill-owned, non-mutating Python
validator against those two roots before its atomic repair. Allow only a single
validator invocation with simple environment assignments and no write/fix/output
flags; its success is diagnostic progress and never clears the failed owning
receipt.
A documented Confirm UI `--wait-only` timeout is a bounded external-user wait,
not a failed validator receipt. Persist it as `waiting-for-user`, leave the
owner server available, and allow the authority-defined chat fallback without
consuming failed-gate sequence retries. Preserve an owner-authored receipt found
after that timeout and reject an unsolicited template-phase reset; only an
explicit user request to reselect may discard it. A short chat confirmation may
refer to the latest visible template question even when transparent tool output
or a bounded host blocker intervened after that question.
When a premature install reopens the managed-artifact Gate after the timeout,
an explicit later HumanMessage may clear it only while both the checkpointed
timeout and surface-switch permission remain true. Model-authored confirmation
text alone never clears the boundary. A successful Confirm UI daemon restart
does not consume either flag. Legacy checkpoints whose daemon restart cleared
them recover the boundary from the latest durable template-wait receipt, unless
a later successful template wait or explicit template-phase reset superseded it.

### Configuration System

**Main Configuration** (`config.yaml`):

Setup: Copy `config.example.yaml` to `config.yaml` in the **project root** directory.

**Config Versioning**: `config.example.yaml` has a `config_version` field. On startup, `AppConfig.from_file()` compares user version vs example version and emits a warning if outdated. Missing `config_version` = version 0. Run `make config-upgrade` to auto-merge missing fields. When changing the config schema, bump `config_version` in `config.example.yaml`.

**Config Caching**: `get_app_config()` caches the parsed config, but automatically reloads it when the resolved config path or file content signature changes. The signature includes file metadata and a content digest, so Gateway and LangGraph reads stay aligned with `config.yaml` edits even on object-store or network mounts where mtime can remain stale.

**Config Hot-Reload Boundary**: Gateway dependencies route through `get_app_config()` on every request, so per-run fields like `models[*].max_tokens`, `summarization.*`, `title.*`, `memory.*`, `subagents.*`, `tools[*]`, and the agent system prompt pick up `config.yaml` edits on the next message. `AppConfig` is intentionally **not** cached on `app.state` — `lifespan()` keeps a local `startup_config` variable for one-shot bootstrap work and passes it to `langgraph_runtime(app, startup_config)`.

Infrastructure fields are **restart-required**. The authoritative list lives in `packages/harness/deerflow/config/reload_boundary.py::STARTUP_ONLY_FIELDS` and is mirrored by the standardised `"startup-only:"` prefix on the corresponding `Field(description=...)` in `AppConfig`, so IDE hover on those fields surfaces the reason inline (no need to context-switch into this table). Currently registered: `database`, `checkpointer`, `run_events`, `stream_bridge`, `sandbox`, `log_level`, `logging`, `channels`, `channel_connections`, `scheduler`, `run_ownership`. Adding a new restart-required field requires updating the registry; drift is pinned by `tests/test_reload_boundary.py`.

**Persistence backend resolution**: the unified `database` section selects the
Gateway's LangGraph checkpointer, LangGraph Store, and DeerFlow SQL repositories.
The deprecated `checkpointer` section remains backward compatible and, when
present, overrides `database` for the LangGraph checkpointer and Store only;
application repositories continue to use `database`.

**PostgreSQL checkpointer capacity**: the process-wide asynchronous
checkpointer pool is independent of the SQLAlchemy application pool. It keeps
the historical `min_size=4` / `max_size=4` unless
`DEERFLOW_CHECKPOINTER_POSTGRES_POOL_MIN_SIZE` and
`DEERFLOW_CHECKPOINTER_POSTGRES_POOL_MAX_SIZE` are set before startup. Values
must be positive and `max >= min`; log the resolved values without logging the
DSN. For the enterprise 100-concurrent-run profile, start at `8/32` only after
checking PostgreSQL connection headroom, then use graph idle-wait timing events
and database connection statistics to validate the change.

Configuration priority:

1. Explicit `config_path` argument
2. `DEER_FLOW_CONFIG_PATH` environment variable
3. `config.yaml` in current directory (backend/)
4. `config.yaml` in parent directory (project root - **recommended location**)

Config values starting with `$` are resolved as environment variables (e.g., `$OPENAI_API_KEY`).
`ModelConfig` also declares `use_responses_api` and `output_version` so OpenAI `/v1/responses` can be enabled explicitly while still using `langchain_openai:ChatOpenAI`.

**DashScope Qwen3.7 Gateway model**: `qwen3.7-plus` is configured as an
OpenAI-compatible Chat Completions model with
`base_url: https://dashscope.aliyuncs.com/compatible-mode/v1` and
`api_key: $DASHSCOPE_API_KEY`; do not copy the separate PPT Agent's native
Responses provider settings into this entry. Use
`deerflow.models.robust_openai:RobustChatOpenAI` with
`when_thinking_enabled.extra_body.enable_thinking: true` and
`preserve_thinking: true`, then explicitly disable with
`when_thinking_disabled.extra_body.enable_thinking: false`. The robust adapter
retains and replays `reasoning_content` for tool-call continuations and keeps
standard LLM timing diagnostics.

**Extensions Configuration** (`extensions_config.json`):

MCP servers and skills are configured together in `extensions_config.json` in project root:

Docker development mounts the project directory at `/app/project` and points
`DEER_FLOW_CONFIG_PATH` / `DEER_FLOW_EXTENSIONS_CONFIG_PATH` into that directory.
Keep mutable config files behind a directory bind mount: single-file bind mounts
can become stale or inaccessible when a host editor replaces a file on save.

Configuration priority:

1. Explicit `config_path` argument
2. `DEER_FLOW_EXTENSIONS_CONFIG_PATH` environment variable
3. `extensions_config.json` in current directory (backend/)
4. `extensions_config.json` in parent directory (project root - **recommended location**)

### Gateway API (`app/gateway/`)

Browser voice input is an authenticated same-origin WebSocket at
`/api/voice-input/stream`. The browser sends only raw 16 kHz mono PCM16 frames;
Gateway obtains `QWEN_ASR_API_KEY` from the runtime Secret, connects directly to
the configured enterprise Qwen3-ASR WebSocket with proxy inheritance disabled,
and returns only sanitized `ready`/`partial`/`final`/`error` events. Keep origin
validation, authentication, frame/session/concurrency bounds, and Nginx Upgrade
forwarding intact. Never expose the upstream key or raw upstream diagnostics to
the browser, and do not fall back to browser-vendor speech-recognition services.

Workspace announcements are SQL-backed by `deerflow.persistence.announcement`. Signed-in users read the currently published, in-window item from `GET /api/announcements/active`; admin-only `/api/admin/announcements` endpoints create, edit, publish, and archive items. Publishing rejects overlapping active windows so the workspace has one unambiguous announcement.

FastAPI application on port 8001 with health check at `GET /health`. Set `GATEWAY_ENABLE_DOCS=false` to disable `/docs`, `/redoc`, and `/openapi.json` in production (default: enabled).

CORS is same-origin by default when requests enter through nginx on port 2026. Split-origin or port-forwarded browser clients must opt in with `GATEWAY_CORS_ORIGINS` (comma-separated exact origins); Gateway `CORSMiddleware` and `CSRFMiddleware` both read that variable so browser CORS and auth-origin checks stay aligned.
The authenticated thread-scoped Live Preview proxy is the one browser mutation
bridge that cannot use the Frontend's double-submit helper because it serves an
upstream standalone application. Its `/api/threads/<id>/live-preview/5050/`
mutations require both an exactly matching `Origin` and
`Sec-Fetch-Site: same-origin`; every other non-auth Gateway mutation continues
to require the CSRF cookie/header pair. Do not broaden this exception to other
ports or paths, and do not exempt Live Preview mutations without the Fetch
Metadata plus Origin checks.
The proxy reaches the AIO Sandbox command service at its normal service URL and
sets the fixed trusted `x-aio-proxy-port: 5050` header itself. Never accept that
port-selector header from the browser or restore the synthetic Host-subdomain
route; Confirm UI intentionally binds to the Sandbox Pod loopback interface.
When the active `ppt-master` authority names Confirm UI managed receipts, the
workflow host also treats the Skill's own `scripts/docs/confirm_ui.md` as a
required owner contract for the current run. It must be re-read before any
other action after a run boundary and before model-authored stage payloads are
written. Keep the schema in the Skill document/server; the host stores only the
document dependency and must not synthesize or repair recommendation JSON.
Before a non-template `confirm_ui/server.py --wait-only` call enters its long
user wait, `SkillWorkflowMiddleware` performs a bounded probe against that
already-running owner service's `/api/session` and `/api/recommendations`
endpoints. An already-persisted requested receipt passes immediately; an owner
HTTP 409 becomes a failed tool receipt with the exact bounded owner error so the
model corrects the recommendation and retries instead of hanging behind a page
that cannot render. Service-unavailable probes defer to the official wait-only
recovery behavior. Keep this as owner-service delegation: never duplicate the
Stage 1/2/3 field schema in DeerFlow middleware or weaken the Confirm UI's own
validation.
After a successful Confirm UI daemon launch, checkpoint the exact loopback URL
as a host fact and inject a hidden handoff reminder until the owner closes. The
model's next visible progress message must include that URL as Markdown; the
frontend rewrites it to the authenticated thread proxy. Keep the official
default wait intact and recognize timestamped owner `[ERROR]` lines as failed
receipts even if a shell pipeline masks the process status. Runtime dependency
installation is not a recovery path for the bundled `ppt-master`: block it and
require a Sandbox image rebuild when an import probe genuinely fails.
When the owner's default wait budget itself expires, record a neutral
`waiting-for-user` boundary rather than an owning-script failure. The next model
turn rechecks the managed receipt once and either retains it or follows the
Skill's documented chat fallback; it must not reset a persisted receipt merely
because the long poll timed out.

**Routers**:

| Router                                                 | Endpoints                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| ------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Models** (`/api/models`)                             | `GET /` - list models; `GET /{name}` - model details                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| **Features** (`/api/features`)                         | `GET /` - report config-gated feature availability (currently `agents_api.enabled`) for frontend UI gating                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| **Console** (`/api/console`)                           | Read-only cross-thread observability for the current user (the data layer for an operations dashboard or external monitoring): `GET /stats` - headline counters (runs/threads/agents/tokens/cost); `GET /runs` - paginated run history joined with thread titles (per-run cost); `GET /usage` - zero-filled daily token series + per-model breakdown with spend. Queries `runs`/`threads_meta` directly as a reporting layer (no new `RunStore` methods); requires a SQL database backend — returns 503 on `database.backend: memory`. Real-cost estimation reads optional `models[*].pricing` (`currency`, `input_per_million`, `output_per_million`, `input_cache_hit_per_million`; `ModelConfig` is `extra="allow"`, so no schema change) and prices each run from its `token_usage_by_model` input/output split. Pricing is **cache-aware**: `RunJournal` accumulates prompt-cache hits from `usage_metadata.input_token_details.cache_read` into a sparse `cache_read_tokens` bucket key (also threaded through `SubagentTokenCollector` → `record_external_llm_usage_records`), and cache-hit input tokens are billed at `input_cache_hit_per_million` (omitted → billed at the miss price, a conservative upper bound). Legacy rows fall back to run-level totals at `model_name`; unpriced models yield `cost: null` and cost fields are null when no pricing is configured                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| **Admin Dashboard** (`/api/admin/dashboard`)           | Admin-only business reporting from SQL-backed users, threads, runs, feedback, audit clicks, scheduled tasks, and structured run events. Success/error rates and duration metrics use terminal runs only; active runs remain visible in run totals. Activity uses `Asia/Shanghai` and adaptively groups arbitrary selected ranges into start-anchored daily, seven-day, 30-day, or 365-day buckets. The headline conversation count and activity bars both use append-only `thread_creations` records inside the selected window; separate fields expose the all-time creation ledger and current `threads_meta` count. Deleting a thread therefore changes only the current count, never historical creation totals. `GET /users` pages every registered account and aggregates only runs joined to its current `threads_meta`; `GET /users/{user_id}/sessions` pages every current thread with run status counts and chat links, excluding runs whose thread was deleted. `GET /failures` pages all historical failures, includes the owner's SSO display name/account, and supports case-insensitive `q` filtering by either field; detailed diagnostics stay on the existing read-only observability endpoint. The authorization decorator permits administrators to pass cross-user ownership checks for read actions only; create, write, cancel, and delete remain owner-scoped. Dashboard summary and observability rankings accept the same arbitrary custom date range. Knowledge-base usage is read directly and read-only from WeKnora users, knowledge bases, documents, sessions, messages, organizations, organization tenant members, knowledge-base shares, and join requests; soft-deleted rows remain in selected-range history and `weknora_search`/`weknora_ask` counts are not used as a proxy. DeerFlow SSO display names override WeKnora username fallbacks by normalized email without changing native WeKnora data. Per-message knowledge scope is preferred for per-KB conversation attribution and aggregate conversation/question totals; when WeKnora 0.7.0 leaves those message fields empty, only those unscoped messages fall back to the session's current knowledge-base scope. The per-user knowledge ranking has no SQL row limit, excludes accounts with no inventory/activity, and is paginated by the frontend. Scheduled-task creation writes a best-effort append-only `scheduled_task.create` audit so later task deletion does not erase history; the dashboard combines it with current task inventory and run rows. File, sandbox, PPT, image, and artifact usage continues to be classified from actual structured tool calls (legacy tool rows remain supported), never from `run.end` prompt/history payloads or assistant prose. Artifact instances deduplicate by `(thread_id, path)`, and aggregate queries must not use silent row limits. |
| **MCP** (`/api/mcp`)                                   | `GET /config` - get config; `PUT /config` - update config (saves to extensions_config.json)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| **Skills** (`/api/skills`)                             | `GET /` - list skills; `GET /{name}` - details; `PUT /{name}` - update enabled; `POST /install` - install from .skill archive (accepts standard optional frontmatter like `version`, `author`, `compatibility`); `POST /reload` - admin-only process-local prompt-cache invalidation after trusted external filesystem changes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| **Memory** (`/api/memory`)                             | `GET /` - memory data; `POST /reload` - force reload; `GET /config` - config; `GET /status` - config + data                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| **Uploads** (`/api/threads/{id}/uploads`)              | `POST /` - upload files (auto-converts PDF/PPT/Excel/Word); browser-active/server-executable extensions are rejected before write, and staged PDFs require a valid signature with no active actions, embedded executable content, or uninspectable encryption; `GET /list` - list; `DELETE /{filename}` - delete                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| **Threads** (`/api/threads/{id}`)                      | `DELETE /` - remove DeerFlow-managed local thread data after LangGraph thread deletion; `POST /branches` - create a new main-thread branch from a completed assistant turn checkpoint. Workspace files are not checkpointed, so the branch only best-effort copies the current workspace when branching from the **latest** turn (`workspace_clone_mode="current_thread_best_effort"`); branching from an older/historical turn skips the copy (`workspace_clone_mode="skipped_historical_turn"`) so the branch never inherits files that only exist in a later timeline; `GET /goal`, `PUT /goal`, `DELETE /goal` - read, set, and clear the active thread goal; `POST /compact` - manually summarize older active context into `summary_text` and retain the recent message window, blocked while a run is in flight; unexpected failures are logged server-side and return a generic 500 detail                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| **Artifacts** (`/api/threads/{id}/artifacts`)          | `GET/HEAD /{path}` - serve or probe artifacts; after access authorization, resolve the storage bucket from the authoritative `threads_meta.user_id` record so owner and administrator read-only views reach the same file. Active content types and passive document files from `uploads/` are forced to `application/octet-stream` attachments with `nosniff`, CSP sandbox, `no-store`, and `noopen` headers (safe raster image/media/passive-text uploads remain inline). `?download=true` still forces download. Inert generated PDFs remain inline with CSP sandbox headers; PDFs with active or uninspectable structures are forced to download. `GET /api/threads/{id}/artifact-previews/pdf/{path}` accepts validated PDFs only from the thread outputs directory, including ordinary generated PDFs and PowerPoint companions, and serves a scriptless static page gallery whose images are serialized PDFium PNG renders. Original uploads and unsafe PDFs remain excluded, avoiding native browser PDF plug-in incompatibility without relaxing iframe isolation.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| **Suggestions** (`/api/suggestions`)                   | `GET /config` - returns global suggestions config boolean; `POST /threads/{id}/suggestions` - generate follow-up questions; rich list/block model content is normalized and inline reasoning (`<think>...</think>`, including unclosed/truncated blocks from reasoning models like MiniMax-M3) is stripped before JSON parsing                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| **Input Polish** (`/api/input-polish`)                 | `POST /` - rewrite a composer draft before it is sent. This is a short authenticated `runs:create` LLM request using `input_polish` config; it does not create a LangGraph run, persist a message, or modify thread state. Shares the non-graph one-shot LLM path (`deerflow.utils.oneshot_llm.run_oneshot_llm`) with the suggestions route so model build + Langfuse metadata + invoke stay in one place; validates the same stripped view of the draft it sends to the model, and preserves literal `<think>` substrings in the rewrite (`strip_think_blocks(truncate_unclosed=False)`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| **Thread Runs** (`/api/threads/{id}/runs`)             | `POST /` - create background run; `POST /stream` - create + SSE stream; `POST /wait` - create + block; `POST /regenerate/prepare` - prepare clean input + checkpoint metadata for regenerating the latest assistant answer; `GET /` - list runs; `GET /{rid}` - run details; `POST /{rid}/cancel` - cancel; `GET /{rid}/join` - join SSE; `GET /{rid}/messages` - paginated per-run messages `{data, has_more}`; `GET /{rid}/events` - full event stream; `GET /{rid}/workspace-changes` - workspace/output file change summary and optional diffs; `GET /../messages` - legacy thread message array; `GET /../messages/page` - backward thread-global `seq` history page with middleware/successful-regenerate filtering and page-run-scoped feedback enrichment; `GET /../token-usage` - aggregate tokens                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| **Feedback** (`/api/threads/{id}/runs/{rid}/feedback`) | `PUT /` - upsert feedback; `DELETE /` - delete user feedback; `POST /` - create feedback; `GET /` - list feedback; `GET /stats` - aggregate stats; `DELETE /{fid}` - delete specific                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| **Runs** (`/api/runs`)                                 | `POST /stream` - stateless run + SSE; `POST /wait` - stateless run + block; `GET /{rid}/messages` - paginated messages by run_id `{data, has_more}` (cursor: `after_seq`/`before_seq`); `GET /{rid}/feedback` - list feedback by run_id                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| **GitHub Webhooks** (`/api/webhooks/github`)           | `POST /` - receive GitHub App / repo webhook deliveries. Verifies `X-Hub-Signature-256` against `GITHUB_WEBHOOK_SECRET`; exempt from auth + CSRF because authenticity is enforced by HMAC. The route is fail-closed: mounted only when `GITHUB_WEBHOOK_SECRET` is set, or when explicit dev opt-in `DEER_FLOW_ALLOW_UNVERIFIED_GITHUB_WEBHOOKS=1` is set. Recognized events include `ping`, `issues`, `issue_comment`, `pull_request`, `pull_request_review`, and `pull_request_review_comment`; unknown events return 200 with `handled=false`. Fan-out runtime failures return 503 so GitHub retries; permanent/non-retryable conditions such as `channels.github.enabled: false`, unknown events, malformed payloads, or unavailable channel service return 200 with a skipped/handled response.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| **GitHub Event-Driven Agents**                         | Custom agents can declare a `github:` block in their `config.yaml` to bind to repos and event triggers. Webhook fan-out publishes one `InboundMessage` per matching binding to the channel bus; `GitHubChannel` routes those messages through `ChannelManager`. The response `dispatch` summarizes matched/fired/skipped agents.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |

**Workspace change review**: `packages/harness/deerflow/workspace_changes/`
captures a pre-run and post-run snapshot of the thread-owned `workspace` and
`outputs` directories. `runtime/runs/worker.py` performs the filesystem scan via
`asyncio.to_thread` and writes a `workspace_changes` event with category
`workspace` when changes exist. Uploads are intentionally excluded. Text diffs
are size-limited; binary, large, and sensitive-looking paths are persisted as
metadata only.

**RunManager / RunStore contract**:

- `RunManager.get()` is async; direct callers must `await` it.
- Gateway shutdown remembers the first cancellation request and continues draining `RunManager.shutdown()` through repeated cancellation before the checkpointer and database engines close. Drain failure remains best-effort without cancellation; after cancellation it is logged and the original cancellation is propagated only at the safe terminal boundary.
- Regenerate preparation treats a protocol-valid hidden `human_input_response` as confirmed user replay input and preserves its hidden/correlation metadata. Summaries, malformed responses, goal continuations, and every other hidden/control human remain excluded.
- The history batch helpers `list_successful_regenerate_sources()` and `get_many_by_thread()` default to `user_id=AUTO`: they resolve the request user and fail closed when no user context exists. Migration/admin callers that intentionally need an unscoped read must pass `user_id=None` explicitly.
- When a persistent `RunStore` is configured, `get()` and `list_by_thread()` hydrate historical runs from the store. In-memory records win for the same `run_id` so task, abort, and stream-control state stays attached to active local runs.
- `cancel()` returns a :class:`~deerflow.runtime.CancelOutcome` enum: `cancelled` (local cancel), `taken_over` (non-owning worker claimed the run because the owner's lease expired — marks it as `error`), `lease_valid_elsewhere` (owner's lease is still alive — caller should return 409 + `Retry-After`), `not_active_locally` (heartbeat disabled, preserving the old 409 path), `not_cancellable` (terminal state), or `unknown` (not found in memory or store). `create_or_reject(..., multitask_strategy="interrupt"|"rollback")` persists interrupted status through `RunStore.update_status()`, matching normal `set_status()` transitions.
- Store-only hydrated runs are readable history. In multi-worker mode with heartbeat enabled, cancel on a store-only run can take over (mark `error`) when the owner's lease has expired past the grace window; otherwise it fails with 409 + `Retry-After`. In single-worker mode (heartbeat off), store-only runs still return 409.
- `POST /wait` (both thread-scoped and `/api/runs/wait`) drains the stream bridge via `wait_for_run_completion()` instead of bare `await record.task`, so it honours the run's `on_disconnect` setting and cancels the background run on real client disconnect rather than returning a stale checkpoint (issue #3265).
- Redis `StreamBridge` keys use a rolling retained-buffer TTL (`stream_bridge.stream_ttl_seconds`, refreshed on `publish()` / `publish_end()`) as a leak safety net, not as a run timeout. Startup orphan recovery publishes `END_SENTINEL` and schedules stream cleanup for recovered runs; malformed `Last-Event-ID` reconnect values live-tail new Redis events rather than replaying the retained buffer. Do not broaden this into a shared-database multi-pod reaper without adding worker ownership/liveness first.
- Thread-scoped run creation accepts `checkpoint` / `checkpoint_id`; Gateway validates the checkpoint belongs to the request thread before writing `checkpoint_id` / `checkpoint_ns` into `config.configurable` for LangGraph branching.
- Thread-scoped Gateway runs evaluate an active `ThreadState.goal` after the visible turn completes. `runtime/goal.py` asks a non-thinking evaluator model to judge only visible conversation evidence and return a typed blocker; the evaluator model is created once per run and reused across hidden continuation checks. The evaluator runs after the graph root's tracing scope has already closed, so `create_goal_evaluator_model`/`evaluate_goal_completion` attach their own model-level tracing callbacks (`attach_tracing=True`) and inject Langfuse trace metadata (`thread_id`/`user_id`/`deerflow_trace_id`) directly onto the `ainvoke` call — the same standalone-caller pattern as `oneshot_llm.run_oneshot_llm` and `MemoryUpdater` (see Tracing System below). Satisfied goals are cleared; every non-satisfied evaluation — continuable or stand-down — is persisted with `last_evaluation` (the blocker, reason, and evidence summary; outcomes that stop the loop additionally record a `stand_down_reason` for observability), but only `goal_not_met_yet` evaluations are streamed as hidden `HumanMessage` continuations, and only when a durable assistant end-of-turn checkpoint exists, the run has not been aborted, the thread did not change during evaluation, and the no-progress breaker has not fired. The continuation cap is 8 — a hard maximum in the `0`–`8` range; callers requesting more are clamped (`set_goal`/TUI) or rejected with 422 (`PUT /goal`). The no-progress breaker keys on the latest visible assistant evidence (not the evaluator's free-text reason, which an LLM rewords every turn), so two consecutive continuations that add no new visible assistant output stop the loop after 2 attempts. Model-response cleanup helpers such as think-block stripping and code-fence stripping live in `deerflow.utils.llm_text` so `runtime/goal.py` and Gateway suggestion parsing share the same JSON-prep behavior.

Proxied through nginx: `/api/langgraph/*` → Gateway LangGraph-compatible runtime, all other `/api/*` → Gateway REST APIs.

**Platform media generation:** `deerflow.community.media_generation` exposes
Gateway-local `generate_image` and `generate_video` tools only when their
operator-owned endpoint environment variables are configured. Both clients use
`trust_env=false`, reject redirects, cap response sizes, and write atomically to
the current thread outputs. The image response must contain exactly one
format-matching `b64_json` payload. Video submission stores an idempotent
thread-scoped job record, resumes by job ID, accepts only current-thread local
reference images or SSRF-screened public URLs, and downloads only from the API
host or an explicit hostname allowlist. Unknown or lost jobs must fail visibly
instead of being silently resubmitted. Media tools publish their own artifacts;
their Skills must not use Bash/provider SDKs or call `present_files` for the same
result. Artifact routes stream inline audio/video via `FileResponse` instead of
reading large files into Gateway memory.

### Sandbox System (`packages/harness/deerflow/sandbox/`)

**Interface**: Abstract `Sandbox` with `execute_command(command, env=None)`, `read_file`, `write_file`, `list_dir`. The optional `env` injects per-call environment variables (request-scoped secrets — see Request-Scoped Secrets below); `LocalSandbox` merges it via `subprocess.run(env=...)` and `AioSandbox` routes env-bearing commands through the `bash.exec(env=...)` API on a fresh session.
**AIO long-command transport**: the PPT-capable AIO image patches both upstream locations in `/opt/gem/nginx-server-active.conf`; otherwise the outer authentication proxy returns its stock 60-second 504 while the inner command process continues. Keep the outer read/send timeout above `AioSandbox`'s bounded command hard timeout and preserve the per-command client limit as the actual stop boundary.
**Provider Pattern**: `SandboxProvider` with `acquire`, `acquire_async`, `get`, `release` lifecycle. Async agent/tool paths call async sandbox lifecycle hooks so Docker sandbox creation, discovery, cross-process locking, readiness polling, and release stay off the event loop.
**Environment policy** (`sandbox/env_policy.py`): `execute_command` no longer inherits the full `os.environ`. `build_sandbox_env()` scrubs secret-looking names (`*KEY*`/`*SECRET*`/`*TOKEN*`/`*PASS*`/`*CREDENTIAL*`) from the inherited environment before layering injected request secrets on top, so platform credentials (e.g. `OPENAI_API_KEY`) never leak into skill subprocesses. Benign vars (`PATH`, `HOME`, `LANG`, `VIRTUAL_ENV`, ...) are preserved.
**Implementations**:

- `LocalSandboxProvider` - Local filesystem execution. `acquire(thread_id)` returns a per-thread `LocalSandbox` (id `local:{thread_id}`) whose `path_mappings` resolve `/mnt/user-data/{workspace,uploads,outputs}` and `/mnt/acp-workspace` to that thread's host directories, so the public `Sandbox` API honours the `/mnt/user-data` contract uniformly with AIO. `acquire()` / `acquire(None)` keeps the legacy generic singleton (id `local`) for callers without a thread context. Per-thread sandboxes are held in an LRU cache (default 256 entries) guarded by a `threading.Lock`. Legacy global-custom mounts are gated by the same user-scoped skill discovery rule used for prompt/list visibility; providers must not infer visibility from raw directory presence alone.
- `AioSandboxProvider` (`packages/harness/deerflow/community/`) - Docker-based isolation. Keep the `agent-sandbox` SDK floor at `0.0.30` or newer so rebuilt environments cannot resolve an older client that lacks the current AIO API contract. Active-cache and warm-pool entries are checked with the backend during acquire/reuse; definitively dead containers are dropped from all in-process maps so the thread can discover or create a fresh sandbox instead of reusing a stale client. Backend health-check failures are treated as unknown, not dead; local discovery likewise treats an unverifiable container as not adoptable and falls through to create rather than failing acquire. `get()` remains an in-memory lookup for event-loop-safe tool paths. Legacy global-custom mounts follow the same shared visibility helper as local and remote providers. Because the AIO shell APIs expose text but not a reliable structured process status, `AioSandbox.execute_command()` appends and strips a private exit-code marker; non-zero commands return an explicit `Error:` plus active-skill/`--help` recovery guidance so tool progress cannot count a failed CLI call as success. Provisioner/Kubernetes deployments may set `sandbox.shared_thread_data_mounts: true` only when Gateway and every sandbox mount the same thread-data volume at `/mnt/user-data`; uploads are then written once through the Gateway instead of cold-starting a sandbox and copying the bytes over its API. The local single-node PVC stack satisfies this with `ReadWriteOnce`; multi-node deployments require storage that provides `ReadWriteMany` (or an equivalent shared filesystem) before enabling the flag.
- In the enterprise K8s profile, Provisioner projects provider credentials and
  both cases of the proxy variables from `deer-flow-env` into each generated
  Sandbox Pod, and mounts the current `deer-flow-enterprise-ca` Secret. The
  enterprise OpenSSL compatibility ConfigMap is mounted only into generated
  Sandbox Pods so generic HTTPS clients inherit `DEFAULT:@SECLEVEL=1` without a
  node-wide or cluster-wide downgrade. The image-generation skill's DashScope
  client additionally uses
  `DASHSCOPE_TLS_COMPAT=true` to create a provider-scoped
  `DEFAULT:@SECLEVEL=1` SSL context; the context still validates the mounted CA
  chain and hostname for both API calls and generated-image downloads. Keep
  these settings runtime-injected so CA rotation and proxy credential changes
  do not require rebuilding the large Sandbox image. Never disable CA or
  hostname verification.
- **Provisioner-generated Pod health/resources**: `docker/provisioner/app.py` reads startup/readiness/liveness timing from `SANDBOX_*_PROBE_*` environment variables, the inner AIO HTTP timeout from `SANDBOX_HEALTHCHECK_TIMEOUT_SECONDS`, and CPU/memory requests/limits from `SANDBOX_{CPU,MEMORY}_{REQUEST,LIMIT}`. Keep the startup window at least five minutes for cold AIO images, and keep the kubelet exec timeout longer than the inner HTTP timeout. Liveness must tolerate roughly one minute of transient unresponsiveness for browser/PPT workloads; readiness may remain faster because it does not restart the container. The local/enterprise K8S overlays use a 5-second inner timeout, 10-second probe timeout, 10-minute startup window, and 1 CPU/1Gi requests with 2 CPU/4Gi limits; tune those values only with measured node capacity. Stable per-thread Sandbox names are intentional: on Pod-create `409`, reuse only a non-terminating Pod; wait up to `SANDBOX_POD_DELETE_WAIT_SECONDS` (default 30) for a terminating predecessor to disappear, then recreate. Never return a Service URL without a usable Pod.
- `BoxliteProvider` (`packages/harness/deerflow/community/boxlite/`) - BoxLite micro-VM isolation. The `boxlite` runtime is optional (`deerflow-harness[boxlite]`) and lazy-imported only when this provider is selected. The provider owns one private asyncio event loop on a daemon thread because BoxLite handles are loop-affine; sync `Sandbox` calls marshal onto that loop with `run_coroutine_threadsafe`.
  Boxes are named deterministically from `user_id:thread_id`, released into an in-process warm pool after each agent turn, and reclaimed only by the same user/thread. Warm-pool health checks use a short explicit timeout and forward that timeout through both BoxLite `exec(timeout=...)` and the private-loop `.result(timeout)` bridge so a hung VM cannot pin the per-thread acquire lock indefinitely.
  `sandbox.replicas` caps active + warm VMs per gateway process; if capacity is exhausted, only warm-pool VMs are evicted. `sandbox.idle_timeout` stops idle warm VMs after the configured seconds. `reset()` is intentionally a lightweight registry clear for `reset_sandbox_provider()` and does not close boxes, stop the idle reaper, or close the private loop; full teardown remains `shutdown()`.

**Shared warm-pool lifecycle:** community sandbox providers that keep released sandboxes alive for fast reuse share `deerflow.community.warm_pool_lifecycle.WarmPoolLifecycleMixin`. The mixin owns the common `DEFAULT_IDLE_TIMEOUT=600`, `IDLE_CHECK_INTERVAL=60`, `DEFAULT_REPLICAS=3`, idle-checker loop, warm-pool expiry, oldest-warm eviction, replica counting, and soft-cap logging. Providers remain responsible for their own active registries, creation/discovery, health checks, and destroy hook (`_destroy_warm_entry`): AIO destroys `SandboxInfo` through its backend; Boxlite closes loop-affine `BoxliteBox` handles. AIO keeps active-idle cleanup outside the mixin and delegates only warm-pool expiry to the shared helper.

**Virtual Path System**:

- Agent sees: `/mnt/user-data/{workspace,uploads,outputs}`, `/mnt/skills`
- Physical: `backend/.deer-flow/users/{user_id}/threads/{thread_id}/user-data/...`, `deer-flow/skills/`
- Translation: `LocalSandboxProvider` builds per-thread `PathMapping`s for the user-data prefixes at acquire time; `tools.py` keeps `replace_virtual_path()` / `replace_virtual_paths_in_command()` as a defense-in-depth layer (and for path validation). AIO has the directories volume-mounted at the same virtual paths inside its container, so both implementations accept `/mnt/user-data/...` natively.
- Detection: `is_local_sandbox()` accepts both `sandbox_id == "local"` (legacy / no-thread) and `sandbox_id.startswith("local:")` (per-thread)

**Sandbox Tools** (in `packages/harness/deerflow/sandbox/tools.py`):

- `bash` - Execute commands with path translation and error handling. For `LocalSandbox` (host bash), POSIX output is captured through bounded pipe-drain threads and stdin is `/dev/null`, so a backgrounded long-lived process (`server &`) returns immediately instead of blocking the turn on an inherited pipe, while unredirected background output is drained without growing anonymous temp files. Commands that read stdin get immediate EOF. The command runs in its own process group with a wall-clock timeout (`sandbox.bash_command_timeout`, default 600s); on timeout the whole group is killed and the agent gets a notice telling it to background long-lived processes. The bash tool description itself also instructs the model to background long-lived processes (e.g. servers) up front so it doesn't waste the turn waiting on a foreground server. See `LocalSandbox.execute_command` / `_run_posix_command` and `bash_tool`'s docstring.
- `ls` - Directory listing (tree format, max 2 levels)
- `read_file` - Read file contents with optional line range
- `write_file` - Write/append to files, creates directories; overwrites by default and exposes the `append` argument in the model-facing schema for end-of-file writes; subject to the read-before-write gate when `read_before_write.enabled` (see Middleware Chain)
- `str_replace` - Substring replacement (single or all occurrences); same-path serialization is scoped to `(sandbox.id, path)` so isolated sandboxes do not contend on identical virtual paths inside one process; subject to the read-before-write gate when `read_before_write.enabled` (see Middleware Chain)

### Subagent System (`packages/harness/deerflow/subagents/`)

**Built-in Agents**: `general-purpose` (all tools except `task`) and `bash` (command specialist)
**Execution**: Dual thread pool - `_scheduler_pool` (3 workers) + `_execution_pool` (3 workers)
**Concurrency and total delegation cap**: `MAX_CONCURRENT_SUBAGENTS = 3` is enforced by `SubagentLimitMiddleware` (truncates excess tool calls in `after_model`; runtime `max_concurrent_subagents` is clamped to 1-4). The same middleware also enforces `subagents.max_total_per_run` (default 6, config schema 1-50, runtime override `max_total_subagents` clamped to the same range) against current-run entries in the durable delegation ledger, so a long lead-agent run cannot bypass concurrency limits by launching repeated legal-sized batches at each planning checkpoint, but historical delegations from previous runs in the same thread do not consume the new run's budget. The lead-agent prompt uses the same clamped values, so model-visible limits match enforcement. Gateway `run_agent()` and embedded `DeerFlowClient.stream()` both provide a per-invocation `run_id` in runtime context; `DeerFlowClient.stream()` also tags its input `HumanMessage` with that same id so durable-context capture can identify the current request boundary. Gateway resume paths may not append a new `HumanMessage`, so the worker also exposes the pre-run checkpoint's message ids in runtime context; durable-context capture uses that as the current-run boundary and never re-tags older task calls as the resumed run. When no delegation slots remain, task calls are stripped, provider raw tool-call metadata is synced, `finish_reason` is forced to `stop`, and a visible "subagent delegation limit" note is appended so the agent can synthesize already-collected results. Default subagent timeout `subagents.timeout_seconds=1800` (30 min) and built-in `general-purpose` `max_turns=150` (raised from 100/15-min so deep-research subtasks stop hitting `GraphRecursionError` out of the box)
**Flow**: `task()` tool → `SubagentExecutor` → background thread → poll 5s → SSE events → result. `task_started` carries the resolved effective model name. The per-subagent `SubagentTokenCollector` publishes a cumulative usage snapshot to the shared `SubagentResult` after every completed LLM response; the next `task_running` event carries that snapshot, so collapsed workspace cards can update without re-accounting parent-run totals. Terminal ToolMessage metadata (`subagent_model_name`, `subagent_token_usage`) and the persisted `subagent.end` event retain the model/usage after reload; absent provider usage stays absent rather than being estimated as zero.
**Events**: `task_started`, `task_running`, `task_completed`/`task_failed`/`task_timed_out`
**Handled LLM failures**: `LLMErrorHandlingMiddleware` deliberately converts provider/model exceptions into an `AIMessage` so the graph can end cleanly, stamping `additional_kwargs.deerflow_error_fallback=true` plus error metadata. Clean graph termination does not imply subagent success: `SubagentExecutor` inspects the last assistant message at terminalization and maps a marked fallback to `SubagentStatus.FAILED`, which then emits `task_failed` and the existing structured `subagent_error`. Only the marker is authoritative — error-looking assistant prose without it remains a normal completed result, so neither the executor nor frontend parses display text as a status protocol.
**Guardrail caps & `stop_reason` (#3875 Phase 2)**: three independent axes can end a subagent run early, and all now surface _why_ through one additive field rather than a new status enum. **Turn axis**: `recursion_limit` on the subagent `run_config` equals `max_turns`, so exhausting the turn budget raises `GraphRecursionError` from `agent.astream`; `executor.py::_aexecute` catches it specifically (before the generic `except Exception`). **Token axis**: `TokenBudgetMiddleware` is attached per-agent via `build_subagent_runtime_middlewares` from `subagents.token_budget` (default `max_tokens` **coupled to `summarization.enabled`** — 1,000,000 when subagent summarization is on, 2,000,000 when off, warn at 0.7, hard-stop at 1.0; a user-set budget always wins regardless of the switch — #3875 Phase 3; a backstop against a subagent that burns tokens on trivial work). It does _not_ raise: at the hard-stop threshold it strips the in-flight turn's tool calls, forces `finish_reason="stop"`, and lets the run complete naturally with a final answer. **Loop axis**: `LoopDetectionMiddleware` (attached at the same point) catches repeated identical tool-call sets — or one tool _type_ called many times with varying args — and its hard-stop likewise strips `tool_calls` and forces a final answer without raising, recording `loop_capped`. Each guard exposes its cap on a per-`run_id` `consume_stop_reason(run_id)` accessor; `_aexecute` collects **every** middleware with that method (duck-typed via `hasattr`, so the executor has no import coupling to the guard classes) and surfaces the first non-`None` reason — adding a future guard needs no executor change. **Surfacing**: whichever axis fired, `_aexecute` stamps a normal status plus an additive reason — `completed` + `stop_reason=token_capped|turn_capped|loop_capped` when a usable final answer (or partial recovered from the last streamed chunk via `_extract_final_result` → `utils/messages.py::message_content_to_text`, returning a `"No response Generated"` sentinel when no text survived) was produced; `failed` + `stop_reason=turn_capped` when nothing usable survived. `SubagentResult.stop_reason` flows through `task_tool.py::_task_result_command` → `format_subagent_result_message` (renders `Task Succeeded (capped: ...)` / `Task failed (capped: ...)`) and `make_subagent_additional_kwargs`, which stamps the additive `subagent_stop_reason` key alongside the normal `subagent_status`. **Why additive, not an enum**: a new status value would break v1 consumers; an optional field is ignored by older frontends and ledger readers, so the cross-language contract (`contracts/subagent_status_contract.json` v2 + `subagents/status_contract.py` + `frontend/.../subtask-result.ts`, pinned by `test_status_values_match_contract` / `test_stop_reason_values_match_contract`) stays backward-compatible. The durable delegation ledger captures `stop_reason` onto the entry and renders model-facing guidance ("hit a guardrail cap with a partial result; reuse it, retry tighter, or raise the per-agent budget (`max_turns` / `token_budget`)") so the lead reuses a capped completion knowingly instead of mistaking it for a clean one. (Phase 1 shipped this surfacing as a `MAX_TURNS_REACHED` status enum in #3949; Phase 2 replaced that enum with the additive `stop_reason` field per the agreed design — the `max_turns_reached` status value and `SubagentStatus.MAX_TURNS_REACHED` are gone.)
**Context compaction (#3875 Phase 3, #4039)**: subagents inherit `DeerFlowSummarizationMiddleware` via `build_subagent_runtime_middlewares`, gated on the **same** `summarization.enabled` switch the lead reads (one config covers both chains; trigger/keep/model/prompt come from the shared `summarization` config so they cannot drift). The subagent builder attaches `DurableContextMiddleware` immediately before summarization, using the same skills path/read-tool settings as the lead chain. Compaction stores the generated summary in `ThreadState.summary_text` rather than as a `messages` item; the durable-context wrapper therefore projects it into the next model request as guarded hidden human data. This is required when a message-count keep policy preserves only an assistant tool-call plus its tool results: without the injected summary the next request begins with assistant/tool history and strict OpenAI-compatible providers can reject it. Because `DurableContextMiddleware` inserts a second `SystemMessage(authority_contract)` after the subagent's leading system prompt, the builder also appends `SystemMessageCoalescingMiddleware` innermost (mirroring the lead chain, appended after the optional summarization middleware so it is unconditionally last) to merge every `SystemMessage` into one leading `system_message` — otherwise the durable fix would trade #4039's assistant-first HTTP 400 for a duplicate-system 400 on the same strict backends (#4040). The factory is called with `skip_memory_flush=True` on the subagent path: the lead's `memory_flush_hook` (attached when `memory.enabled`) flushes pre-compaction messages into durable memory keyed by `thread_id`, and subagents share the parent's `thread_id`, so without skipping the hook a subagent's internal turns would pollute the **parent** thread's durable memory. Placement differs from the lead chain (lead appends summarization _before_ the guard trio; subagent appends it _after_) — benign because the middleware implements only `before_model` (compaction) with no `after_model`/`consume_stop_reason`, so it cannot disturb the Phase 2 guard-cap stop-reason channel. Compaction rewrites the messages channel via `RemoveMessage(id=REMOVE_ALL_MESSAGES)`, which shrinks `len(messages)` below the step-capture cursor mid-run; `capture_new_step_messages` (see Step capture below) resets the cursor to the new tail on contraction so steps appended after the compaction point are not silently dropped.
**Step capture & persistence (#3779)**: `executor.py` captures both assistant turns (`AIMessage`) **and** tool outputs (`ToolMessage`) via `subagents/step_events.py::capture_new_step_messages`, which walks the _newly-appended tail_ of each `stream_mode="values"` chunk (not just `messages[-1]`) so a multi-tool-call turn — where LangGraph's `ToolNode` appends several `ToolMessage`s in one super-step — keeps every tool output instead of dropping all but the last. `runtime/runs/worker.py::_SubagentEventBuffer` additionally persists these `task_*` custom events to the `RunEventStore` as `subagent.start`/`subagent.step`/`subagent.end` (`category="subagent"`, `task_id` in `metadata`). It **batches** writes via `put_batch` (flushing on a terminal `subagent.end`, at `FLUSH_THRESHOLD` events, and in the worker's `finally`) rather than one `put()` per step, since `put()` is a documented low-frequency path (per-thread advisory lock per call) and a deep subagent (`max_turns=150`) emits hundreds of steps on the hot stream loop. `build_subagent_step` caps both the per-step `text` and each tool call's serialized `args` at `SUBAGENT_STEP_MAX_CHARS` (flagged `truncated` / `args_truncated`) so a large `write_file`/`bash` payload can't produce an unbounded row. The dedicated category keeps them out of `list_messages` (the thread feed) while `list_events` returns them for the frontend's fetch-on-expand backfill. `list_events` accepts `task_id` (filters on `metadata["task_id"]` — SQL-side in `DbRunEventStore` via `event_metadata["task_id"].as_string()`, in-memory in the JSONL/memory stores) plus an `after_seq` forward cursor, so the card pages through one subagent's steps without the run-wide `limit` truncating the tail (no schema migration: the filter rides the existing run-scoped index). `step_events.py` is a pure, unit-tested layer (`build_subagent_step` / `subagent_run_event`). **History contraction (#3875 Phase 3)**: `capture_new_step_messages` assumes append-only growth, but `DeerFlowSummarizationMiddleware` rewrites the messages channel via `RemoveMessage(id=REMOVE_ALL_MESSAGES)`, shrinking `len(messages)` below the cursor mid-run. On contraction (`total < processed_count`) the cursor resets to the new tail; `capture_step_message`'s id/content dedup prevents re-emitting pre-compaction steps, so steps appended after the compaction point are still captured instead of being dropped until `total` overtakes the stale cursor.
**Deferred MCP tools** (if `tool_search.enabled`): `SubagentExecutor._build_initial_state` assembles deferral after policy filtering via the shared `assemble_deferred_tools` (fail-closed), appends the `tool_search` tool, injects the `<available-deferred-tools>` section into the subagent's `SystemMessage`, and threads the setup to `_create_agent`, which attaches `McpRoutingMiddleware` (when PR1 routing metadata matches deferred tools) before `DeferredToolFilterMiddleware` through `build_subagent_runtime_middlewares(...)`. Subagents thus withhold full MCP schemas until promotion, same as the lead agent; each task run gets a fresh `ThreadState` so promotion is isolated per run
**Checkpointer isolation**: Subagent graphs are compiled with `checkpointer=False` to avoid inheriting the parent run's checkpointer, since subagents are one-shot and never resume.
**Checkpoint lineage / stream isolation**: `_aexecute` deliberately omits checkpoint-coordinate keys (`thread_id`, `checkpoint_ns`, `checkpoint_id`, `checkpoint_map`) from the child `RunnableConfig`. LangGraph must inherit those coordinates from the copied parent ContextVar so the delegated graph retains a non-root subgraph namespace; explicitly re-supplying even the same parent `thread_id` starts a new root lineage on LangGraph 1.2.6+ and can route child AI/tool frames into the parent `messages` stream. DeerFlow business components still receive the parent `thread_id` through `runtime.context`, which is the preferred lookup path for sandbox, middleware, and attribution code. Regression coverage in `tests/test_subagent_executor.py::TestSubagentCheckpointLineage` keeps the invocation-contract assertion active on every supported version and version-gates the production-shaped parent-stream test to LangGraph 1.2.6+, where the leak exists.

### Tool System (`packages/harness/deerflow/tools/`)

`get_available_tools(groups, include_mcp, model_name, subagent_enabled)` assembles:

1. **Config-defined tools** - Resolved from `config.yaml` via `resolve_variable()`
2. **MCP tools** - From enabled MCP servers (lazy initialized, cached with resolved-path + content-signature invalidation)
3. **Built-in tools**:
   - `present_files` - Make final files visible to the user. Files already under `/mnt/user-data/outputs` are published directly; files under the current thread's `/mnt/user-data/workspace` are copied into the persistent outputs bucket first. Remote-sandbox files are downloaded before publication, while paths outside the current thread remain rejected. Presenting a generated Office document (DOC/DOCX, XLS/XLSX, ODT/ODS/ODP, or PPT/PPTX) also publishes an existing same-stem `.preview.pdf`; when it is absent and an active sandbox is available, the tool generates it with sandbox LibreOffice from the original sandbox-visible source path and an isolated temporary user profile before publishing the normalized document. Office publication fails closed when the paired PDF cannot be persisted, preventing a successful file card that opens an empty preview panel. Original uploads are not promoted through this generated-artifact preview contract.
   - `ask_clarification` - Request clarification (intercepted by ClarificationMiddleware, which preserves text fallback and adds `artifact.human_input` for Web UI Human Input Cards)
   - `view_image` - Read image as base64 (added only if model supports vision)
   - `setup_agent` - Bootstrap-only: persist a brand-new custom agent's `SOUL.md` and `config.yaml`. Bound only when `is_bootstrap=True`.
   - `update_agent` - Custom-agent-only: persist self-updates to the current agent's `SOUL.md` / `config.yaml` from inside a normal chat (partial update + atomic write). Bound when `agent_name` is set and `is_bootstrap=False`.
   - Model-facing lead and subagent prompts share `agents/language_policy.py`.
     Simplified Chinese is the default for user-visible answers, titles,
     descriptions, summaries, and reports unless the current user request
     explicitly selects another language. Technical identifiers remain in
     their required original form. The bootstrap skill applies the same rule
     to both `SOUL.md` and the persisted one-line agent description.
4. **Subagent tool** (if enabled):
   - `task` - Delegate to subagent (description, prompt, subagent_type)

Scheduled-task runtime note:

- The September 30 reconciliation also restores the pre-merge
  `app/gateway/routers/scheduled_tasks.py`: interval/custom-agent validation,
  cron preview, runs:create checks, ThreadId validation, active-occurrence
  mutation guards, and atomic queued-run cancellation on pause/delete. Its
  scheduler service, persistence, and migrations were already retained; do not
  replace them or deploy schema changes as part of this narrow restoration.

- Scheduled background runs set `context.non_interactive=true` and therefore exclude `ask_clarification` from the lead-agent tool list. The lead-agent system prompt also tells unattended runs to stop safely when required inputs/approvals are missing and to avoid unapproved destructive side effects. This keeps scheduler-triggered runs from stalling or guessing through a human confirmation mid-execution. `non_interactive` is an internal-only context key: it is merged from `body.context` only when the request authenticated as the process-internal user (the scheduler path), never from arbitrary HTTP/IM clients. `GET /api/scheduled-tasks/capabilities` reports the current process poller's actual `scheduler_enabled` state plus these non-interactive semantics so the frontend does not imply that saved tasks will auto-run while the poller is stopped.
- The scheduled-task row stores only the allowlisted user-configurable run context needed to reproduce the chosen model, mode, reasoning, subagent, and WeKnora scope. The scheduler passes that context into `launch_scheduled_thread_run`, which filters it again before forcing the internal `non_interactive` and authenticated owner fields. Do not persist or forward runtime-only secrets, client-supplied identity, or internal flags.
- `once`, `cron`, and minimum-60-second `interval` schedules share the same occurrence projection. Overlaps enqueue instead of being silently skipped; queue timeout, occurrence ordering, and one active occurrence per task are persistence invariants. Multi-instance mode must fail closed unless PostgreSQL, database run events, and run-ownership heartbeats are configured.
- The incoming scheduled-task run-history status filter is deferred in the dmdb 2026-09-30 merge: its enum and SQL/API predicate are absent. Preserve the existing ownership and pagination behavior; review a future filter as one complete backend/frontend contract.

JSONL development event stores are framed only by physical LF delimiters. Do not use Python `splitlines()` to read records: Unicode NEL/line/paragraph separators are valid inside JSON string values and must round-trip without becoming false record boundaries. Production PostgreSQL event storage is unaffected by this local-store rule.

**Community tools** (`packages/harness/deerflow/community/`): optional integrations, each in its own subpackage and wired through `config.yaml`. Documented examples:

- `tavily/` - Web search (5 results default) and web fetch (4KB limit)
- `jina_ai/` - Web fetch via Jina reader API with readability extraction
- `firecrawl/` - Web scraping via Firecrawl API
- `image_search/` - Image search via DuckDuckGo
- `aio_sandbox/` - Docker-based isolation (`AioSandboxProvider`)

Additional providers also live here (`boxlite`, `brave`, `browserless`, `crawl4ai`, `ddg_search`, `e2b_sandbox`, `exa`, `fastcrw`, `groundroute`, `infoquest`, `searxng`, `serper`); see each subpackage for specifics.

The active CNOOC DataLake question-answering tools are `datalake_smart_qa`
and `datalake_data_qa`. Smart QA retains the approved internal Retrieve API.
Data QA calls a new AppForge application (`app-v16p2syh`); it does not compose
or fall back to the retired Metrics/Detail applications. Use its dedicated
`APPFORGE_DATA_QA_API_KEY`, `SquareWorkflow_` conversation prefix and exactly
empty `hytoken`/`hyaccount` inputs, never inherited identities. Both services
use `trust_env=false`; only `query` is model-visible. Keep the new App's actual
SSE protocol and business capabilities subject to field acceptance. Reject
retired registrations during config validation; pending historical old calls
receive only a local migration notice. Switch a versioned ConfigMap, dedicated
Secret reference and compatible image together; never modify the mounted old
configuration first. Reference a single
Retrieve store from `config.yaml` as a list containing
`$DATALAKE_RETRIEVE_DOCUMENT_STORE_ID`; comma-separated
`DATALAKE_RETRIEVE_DOCUMENT_STORE_IDS` is also supported. Keep runtime values in
the Secret, sanitize provider errors, and retain bounded valid JSON so the
generic tool-output budget does not replace useful structured evidence with a
file-only preview.

`DataLakeSourceMiddleware` checkpoints the run's source restriction before
execution and after model selection. Keep deterministic explicit-source/table
recognition conservative and accompany it with model guidance; do not claim
complete natural-language classification. Disabled access returns the button
hint before model invocation for recognized questions. DataLake selection,
including failed/empty calls, locks out external tools and arbitrary execution
for that run. Enforce both schema filtering and tool execution, propagate the
restriction through task/subagents, and merge parallel updates without losing
the restriction. This never grants data access or rewrites historical messages.

Retrieve image references from the reviewed DataLake preview origin are
percent-encoded and rewritten to the authenticated same-origin Gateway route
`/api/datalake/image-preview`. That route is a strict read-only proxy for the
single approved HTTP host, port, and path prefix: keep redirects disabled,
`trust_env=false`, image MIME allowlisting, the 32 MiB cap, traversal rejection,
and response hardening. The model embeds the proxy URL directly and must not
download the source through Sandbox or publish a duplicate artifact.

The web run context may carry the strict JSON boolean `datalake_enabled` only
under `body.context`; Gateway keeps it runtime-only and rejects low-level
`config.context`/`config.configurable` occurrences or non-boolean values. Before
dispatch, Gateway intersects explicit `true` with the authenticated user's
HttpOnly platform-token cookie matching their persisted token hash (the same
proof used by the Data Lake session endpoint). A client flag, owner attribution,
or internal service credential alone never grants access. Missing opt-in or
session proof denies access, including legacy and non-web runs. Never copy raw
platform tokens into run context or checkpoints. A disabled run filters both
DataLake QA tools before deferred assembly. The
`task` tool and `SubagentExecutor` propagate the effective flag to child runtime
contexts and apply the same filter defensively. The DataLake boundaries still
return a bounded `datalake_disabled` result unless the effective flag is exactly
`true`, including stale direct references and absent/malformed context, before
configuration, credentials, or network access.

`datalake_md_search` is an optional, distinct AppForge master-data workflow.
It is not enabled in the current enterprise environment or this migration. It is limited
to companies, branches/operating organizations, fields, blocks, wells, and
their registered ownership/list/count relationships; it must not be used for
metrics, production, reports, document QA, arbitrary SQL, or inferred complex
relationships. Its `APPFORGE_MD_SEARCH_API_KEY` is Gateway Secret-only. It is
intentionally absent from `DATALAKE_TOOL_NAMES`, so the Data Lake toggle does
not hide it or block its direct invocation.

**ACP agent tools**:

- `invoke_acp_agent` - Invokes external ACP-compatible agents from `config.yaml`
- ACP launchers must be real ACP adapters. The standard `codex` CLI is not ACP-compatible by itself; configure a wrapper such as `npx -y @zed-industries/codex-acp` or an installed `codex-acp` binary
- Missing ACP executables now return an actionable error message instead of a raw `[Errno 2]`
- Each ACP agent uses a per-thread workspace at `{base_dir}/users/{user_id}/threads/{thread_id}/acp-workspace/`. The workspace is accessible to the lead agent via the virtual path `/mnt/acp-workspace/` (read-only). In docker sandbox mode, the directory is volume-mounted into the container at `/mnt/acp-workspace` (read-only); in local sandbox mode, path translation is handled by `tools.py`

### MCP System (`packages/harness/deerflow/mcp/`)

- Uses `langchain-mcp-adapters` `MultiServerMCPClient` for multi-server management
- **Lazy initialization**: Tools loaded on first use via `get_cached_mcp_tools()`
- **Cache invalidation**: Detects extensions-config changes by comparing the resolved config path and a `(mtime, size, sha256)` content signature against the values recorded at initialization (mirrors `config/app_config.py::get_app_config()`), not a strict mtime `>` comparison. This catches same-second edits, mtime that stays put or moves backward (`git checkout`, `cp -p` / backup restore, `tar` / `rsync`, object-store / network mounts), and a switch to a different config file with an equal-or-older mtime
- **Transports**: stdio (command-based), SSE, HTTP
- **OAuth (HTTP/SSE)**: Supports token endpoint flows (`client_credentials`, `refresh_token`) with automatic token refresh + Authorization header injection
- **Routing hints**: `extensions_config.json -> mcpServers.<server>.routing` and
  `tools.<original_tool_name>.routing` are soft preference metadata. The effective
  routing is resolved while `mcp/tools.py::get_mcp_tools()` still has both
  `source_name` and the original MCP tool name, then stored on `tool.metadata`
  under `deerflow_mcp_routing`. Prompt rendering uses
  `tools/builtins/tool_search.py::get_mcp_routing_hints_prompt_section`, which
  references `tool_search` when a hinted MCP tool is currently deferred; do not
  add a parallel routing middleware for PR1-style preference hints.
- **Stdio file outputs**: Persistent stdio sessions are scoped by `user_id:thread_id`. For stdio transports only, DeerFlow pins the subprocess default `cwd` to the thread workspace and `TMPDIR`/`TMP`/`TEMP` to `workspace/.mcp/tmp/`, unless the operator explicitly configured `cwd` or temp env values. SSE/HTTP transports skip this filesystem prep entirely.
- **Stdio path translation**: MCP-returned local file references are not copied. If a `ResourceLink` or conservative free-text path resolves to an existing file inside the thread's mounted user-data tree, it is translated deterministically to `/mnt/user-data/...`; paths outside that tree remain unchanged.
- **Runtime updates**: Gateway API saves to extensions_config.json; the Gateway-embedded runtime detects changes via the resolved-path + content-signature check above, so multi-worker / stale-mtime deployments still pick up an added/removed MCP server without a restart (the `PUT /api/mcp/config` reset only clears the cache in its own worker)

### Skills System (`packages/harness/deerflow/skills/`)

- **Location**: `deer-flow/skills/{public,custom}/`
- **Format**: Directory with `SKILL.md` (YAML frontmatter: name, description, license, allowed-tools, required-secrets)
- **Loading**: `load_skills()` recursively scans namespace directories under `skills/{public,custom}`, but stops descending once it finds a `SKILL.md`; that directory is a package boundary, so no nested `SKILL.md` is registered as a runtime skill. SkillScan has a deliberately narrower packaging rule: known eval fixtures are permitted as support data, while other nested `SKILL.md` files are reported as package defects. It parses runtime metadata and reads enabled state from extensions_config.json.
- **Progressive-read budget**: `read_file` applies `sandbox.skill_file_output_max_chars` and `sandbox.skill_reference_output_max_chars` (both default 150,000) to canonical entry files and supporting resources under `skills.container_path`; ordinary files keep `read_file_output_max_chars`. This prevents long workflows and references from silently losing mandatory later phases, schemas, or examples while avoiding a global context-budget increase.
- **Vendored runtime packages**: Third-party packages containing nested `SKILL.md` files must live outside the scanned `public`, `custom`, and `legacy` category roots. For example, `skills/third_party/academic-research-skills/` is runtime content reached through the single public `academic-research` adapter; its internal components are not independently registered platform skills.
- **External reload**: `POST /api/skills/reload` is an admin-only, process-local invalidation hook for trusted MinIO/NFS/CSI writes. `SkillStorage` instances do not cache a catalog — `load_skills()` scans on every call — so the route clears all `(app_config, user_id)` entries and the rendered prompt-section LRU, then waits up to the shared refresh timeout for the existing off-loop single-flight refresh. Each invalidation receives a generation-bound result handle; a successful scan atomically replaces the global enabled-skills cache, while a loader-level failure propagates to the HTTP waiter and preserves the last-known-good global cache. Per-user/config scans capture the refresh version and cannot repopulate shared caches if invalidation occurs while they are loading. A timed-out HTTP wait fails generically while the daemon refresh worker continues. Subsequent runs rescan after a successful reload; active runs keep their existing snapshot. Each Uvicorn worker/Kubernetes Pod must be targeted separately. Direct mount writes bypass install/edit validation, SkillScan, and history, so mounted roots are an operator-controlled trust boundary.
- **Tool policy**: Preserve the production construction-time lead-agent `allowed-tools` filtering for custom/bootstrap agents that declare a finite skill allowlist. Merely discoverable default-agent skills do not narrow the global toolset. Slash activation and durable `skill_context` continue to provide instructions and workflow context, but this baseline does not enforce the deferred activation-scoped model-schema/execution policy. Subagents retain their existing static filtering; do not change existing authorization, secret binding, or SkillScan boundaries as part of this deferral.
- **Injection (legacy / default)**: Enabled skills are listed in the agent system prompt with full metadata and container paths (`<available_skills>` block). Controlled by `skills.deferred_discovery: false` (default).
- **Deferred discovery** (`skills.deferred_discovery: true`): Skills are listed by name only in a compact `<skill_index>` block, keeping the system prompt prefix-cache friendly. The agent calls the `describe_skill` tool at runtime to fetch full metadata for skills it wants to use, then loads the SKILL.md via `read_file`. Two new modules support this path:
  - `skills/catalog.py` — `SkillCatalog` (immutable, searchable; query forms: `select:a,b`, `+prefix`, free-text regex); `select:` returns all requested skills without a result cap; other modes cap at `MAX_RESULTS=5`.
  - `skills/describe.py` — `build_describe_skill_tool(catalog)` builds the `describe_skill` tool as a closure; `build_skill_search_setup(skills, enabled, ...)` produces a `SkillSearchSetup(describe_skill_tool, skill_names)` that is wired into both the LangGraph agent factory (`agent.py`) and the embedded client (`client.py`).
- **Slash activation**: `/skill-name task` loads that enabled skill's complete `SKILL.md` on the first model call. Ordinary skills then use a compact validated continuation contract; strict serial workflows with hard-stop user-confirmation gates keep the complete body visible across the tool loop, matching the effective legacy read-result behavior at the cost of higher cached input-token usage. The per-run cache is keyed to the activating user message, redacted from traces/persistence, and cannot leak into a later non-slash message. The resolver rejects leading whitespace, missing separators, reserved channel commands (`/new`, `/help`, `/bootstrap`, `/status`, `/models`, `/memory`, `/goal`), disabled skills, and skills outside a custom agent's whitelist. Large skill/reference workflows should run on a context window sized for the combined instructions, artifacts, and tool history.
- **Installation**: `POST /api/skills/install` extracts .skill ZIP archive to custom/ directory
- **SkillScan**: `packages/harness/deerflow/skills/skillscan/` is the native deterministic scanner for `.skill` archives and agent-managed skill writes. It runs offline before the LLM scanner, emits structured findings (`rule_id`, `severity`, `file`, `line`, `message`, `remediation`, redacted `evidence` — category/analyzer are encoded in the `rule_id` prefix), blocks `CRITICAL`, and passes warning findings into `scan_skill_content()`. `skills/package_files.py` is the shared definition of code paths and executable magic for the installer and scanner. Installer path checks remain intact. NUL/non-UTF-8 code raises `package-undecodable-script` and is still analyzed with replacement decoding; executable binaries skip noisy string-table text rules after their blocking binary finding. The dmdb 2026-09-30 merge restores these existing protections and defers incomplete instance-client/scope/work-budget analysis and scanner-side ADS preflight increments. `scan_archive_preflight()` / `scan_skill_dir()` are pure sync functions (dispatch off the event loop); `enforce_static_scan()` applies the blocking policy and the `skill_scan.enabled` kill switch. Do not add Semgrep/OpenGrep or YAML rule-engine dependencies to the core path; Phase 1 rule specs live in Python constants next to their analyzers in `skillscan/orchestrator.py`. See `docs/ENTERPRISE_MERGE_20260930.md`; preserve all pre-merge regression definitions when reconciling incomplete incoming tests.
- **Agent-managed creation**: `skill_evolution.enabled` registers the
  `skill_manage` tool used by the public `skill-creator`. Writes resolve the
  runtime user and persist below
  `.deer-flow/users/<user_id>/skills/custom/`; refreshing the user prompt cache
  makes the created skill visible in later chats without a Gateway restart.
  Every create/edit/supporting-file write remains behind SkillScan and
  fail-closed LLM moderation. The moderation parser uses
  `utils.llm_text.extract_response_text` so Responses API `text` and
  `output_text` blocks are accepted while reasoning and tool blocks are ignored.
- **Skill Review Core**: `packages/harness/deerflow/skills/review/` provides read-only package snapshots, deterministic facts, resource/eval analysis, report rendering, and the CLI (`python -m deerflow.skills.review.cli`). It reuses the shared frontmatter helper and SkillScan; it must not import `app.*`, execute target scripts, install dependencies, or call networks. JSON contracts live in `contracts/skill_review/`. Snapshots retain binary content as base64 and materialize every non-symlink file byte-for-byte for SkillScan; only eval-fixture `SKILL.md` samples are excluded. Exclusive creation and required content bytes make duplicate/case-folded paths and incomplete untruncated snapshots fail closed. The `review_skill_package` built-in tool labels results with `review_subject_entry` and never `skill_context_entry`, so reviewing a target does not activate it, bind its secrets, execute scripts, or apply its `allowed-tools`. Its model-visible `ToolMessage.content` is a compact JSON payload with untrusted control tags neutralized; the full raw review payload, including Markdown renders, stays in `ToolMessage.artifact`. CI should run the CLI with `--fail-on error --fail-on-incomplete` so blocker/error findings and truncated/not-assessed packages fail the gate. The public `skills/public/skill-reviewer` skill owns semantic readiness review and suggestions only; mutation and runtime experiments remain owned by `skill-creator`.
- **Default-agent tool policy**: Skills that are merely discoverable by the default agent do not statically narrow its tool set. Lead-agent `allowed-tools` is applied at construction only when a custom/bootstrap agent declares a finite skill allowlist; otherwise one narrow public helper could remove `bash`, `write_file`, and `present_files` from unrelated tasks. Activation-scoped enforcement remains deferred as described above.

#### Request-Scoped Secrets (`required-secrets`)

Lets a caller pass per-request, short-lived end-user credentials (e.g. an ERP token) to a skill's sandbox scripts without the value entering the prompt, tool arguments, the executed command string, or traces (issue #3861).

- **Declare**: a skill lists the secrets it needs in `SKILL.md` frontmatter — `required-secrets:` as a string list or `{name, optional}` mappings. `name` is both the lookup key and the env var name exposed to scripts. Parsed by `skills/parser.py::parse_required_secrets` into `Skill.required_secrets` (`SecretRequirement`); malformed entries are dropped with a warning.
- **Carry**: the caller sends values out-of-band in the run request's `context.secrets` mapping (never a message). `runtime/secret_context.py` owns the contract (`SECRETS_CONTEXT_KEY`, `extract_request_secrets`). The existing `context` passthrough carries it to `runtime.context` without mirroring into `configurable`. `build_run_config` still sets `configurable.thread_id` on the context path — the checkpointer requires it.
- **Bind (point A+)**: `SkillActivationMiddleware._resolve_secret_bindings` recomputes the injection set (`runtime.context[__active_skill_secrets]`) on every model call from two unioned sources, then REPLACES the key. (1) _Slash_: the run's most recent `/skill` activation, persisted as a source on the run context (only the activated skill's **canonical container path**, never its declared secrets) so the whole tool loop after the activation call keeps the binding; a new activation replaces it. Slash reads the genuine user text via `get_original_user_content_text`; `InputSanitizationMiddleware` preserves it (`ORIGINAL_USER_CONTENT_KEY`), so activation fires even after sanitization. (2) _In-context_ (autonomous invocation): skills the model actually loaded in this thread — `ThreadState.skill_context` entries. **Both sources resolve the live registry skill by normalized container path on every call** (`_resolve_registry_skill`) and bind only that skill's own declared secrets — enabled + allowlist checked for both; the `secrets-autonomous: false` opt-out (malformed values fail closed to `false`) additionally gates the in-context path but exempts explicit slash. Resolving by registry — not by trusting the source's stored data — is what makes a caller-forged `__slash_skill_secret_source` harmless (`runtime.context` is caller-mergeable; the gateway also strips caller `__`-keys in `build_run_config`), #3938. Authorization is three-gated regardless of activation style: skill **enabled** by the operator × values **supplied per-request** by the caller (`context.secrets`) × names **declared** in frontmatter (∩ semantics). Because the set is recomputed per call, a skill evicted from `skill_context` (capacity) or a caller that stops supplying a value loses injection on the next call. The injected value always comes from the caller's request, never the host environment (scrubbed first — see below), so a declared name that also exists in the host env is safe: the caller's value wins and the host value is dropped (the #3861 per-user-key-overrides-shared-key case). Missing required secrets are logged once per binding change, not injected; binding changes are recorded as a `middleware:skill_secrets` journal event (skill and secret names only, never values).
- **Inject**: `bash_tool` reads the injection set and passes it as `execute_command(env=...)`. Scope is the activation turn/run only — a run without `/skill` activation injects nothing.
- **AIO image requirement**: on `AioSandbox` the env path uses the `bash.exec` API (`POST /v1/bash/exec`), which upstream all-in-one-sandbox only ships since `1.9.3` — older images (including a `latest` tag frozen on the `1.0.0.x` line) 404 the whole `/v1/bash/*` namespace. `AioSandbox` detects the 404, remembers the capability gap on the instance, and fails fast with an actionable upgrade error instead of letting the model retry raw 404s; there is deliberately **no** fallback through the legacy shell path because none keeps the secret values out of the command string (#3921). Regression tests: `tests/test_aio_sandbox.py::TestBashExecUnsupportedFailFast`.
- **Inherited-env scrub**: `execute_command` no longer leaks the Gateway's `os.environ` to skill subprocesses — `env_policy.build_sandbox_env` drops secret-looking names (`*KEY*`/`*SECRET*`/`*TOKEN*`/`*PASS*`/`*CREDENTIAL*`/`*DSN*` + a connection-string denylist like `DATABASE_URL`/`REDIS_URL`/`GH_PAT`, plus no-flag credential sources like `MYSQL_PWD`/`REDISCLI_AUTH`/`PGPASSFILE`/`PGSERVICEFILE`) so platform credentials never reach a skill; a skill that needs one must declare it.
- **Leak surfaces sealed** (verified by a real-gateway e2e run — secret reaches the sandbox but none of these): prompt (value never in a message), trace (`tracing/metadata.py` never copies `context`), checkpoint (secrets live on `runtime.context`, not graph state), audit (journal records names only), stdout (`tools.py::mask_secret_values` redacts injected values from bash output), and **run-record persistence + run API** (`services.py::start_run` stores `redact_config_secrets(body.config)` so `runs.kwargs_json` and `RunResponse.kwargs` never carry the secret).
- **Scope / non-goals**: no persistence/vaulting — values are request-scoped and never stored server-side, so long-lived use means the caller re-supplies `context.secrets` on each request while the skill stays in `skill_context`; subagents do not inherit the injection set; the MCP per-user-credential gap (#3322) is a sibling, not covered here. Tests: `tests/test_skill_request_scoped_secrets.py`.

### Model Factory (`packages/harness/deerflow/models/factory.py`)

- `create_chat_model(name, thinking_enabled)` instantiates LLM from config via reflection
- Supports `thinking_enabled` flag with per-model `when_thinking_enabled` overrides
- Supports vLLM-style thinking toggles via `when_thinking_enabled.extra_body.chat_template_kwargs.enable_thinking` for Qwen reasoning models, while normalizing legacy `thinking` configs for backward compatibility
- Supports `supports_vision` flag for image understanding models
- Config values starting with `$` resolved as environment variables
- Missing provider modules surface actionable install hints from reflection resolvers (for example `uv add langchain-google-genai`)

### vLLM Provider (`packages/harness/deerflow/models/vllm_provider.py`)

- `VllmChatModel` subclasses `langchain_openai:ChatOpenAI` for vLLM 0.19.0 OpenAI-compatible endpoints
- Preserves vLLM's non-standard assistant `reasoning` field on full responses, streaming deltas, and follow-up tool-call turns
- Designed for configs that enable thinking through `extra_body.chat_template_kwargs.enable_thinking` on vLLM 0.19.0 Qwen reasoning models, while accepting the older `thinking` alias

### IM Channels System (`app/channels/`)

Bridges external messaging platforms (Feishu, Slack, Telegram, Discord, DingTalk, GitHub) to the DeerFlow agent via Gateway's LangGraph-compatible API.

**Architecture**: Channels communicate with Gateway through the `langgraph-sdk` HTTP client (same as the frontend), ensuring threads are created and managed server-side. The internal SDK client injects process-local internal auth plus a matching CSRF cookie/header pair so Gateway accepts state-changing thread/run requests from channel workers without relying on browser session cookies.

**Components**:

- `message_bus.py` - Async pub/sub hub (`InboundMessage` → queue → dispatcher; `OutboundMessage` → callbacks → channels)
- `store.py` - JSON-file persistence mapping `channel_name:chat_id[:topic_id]` → `thread_id` (keys are `channel:chat` for root conversations and `channel:chat:topic` for threaded conversations)
- `manager.py` - Core dispatcher: creates threads via `client.threads.create()`, routes commands including `/goal` (setting a goal persists it through Gateway and then routes the objective as a chat turn), keeps Slack/Discord on `client.runs.wait()`, uses `client.runs.stream(["messages-tuple", "values"])` for Feishu/Telegram incremental outbound updates, serializes same-thread Feishu turns in-manager when the channel's `ChannelRunPolicy.serialize_thread_runs=True` so rapid follow-ups queue instead of tripping the runtime busy reply, and switches to `client.runs.create()` (fire-and-forget, returns once the run is `pending`) for channels whose `ChannelRunPolicy.fire_and_forget=True` so long autonomous runs do not hit the SDK default 300s `httpx.ReadTimeout`
- `base.py` - Abstract `Channel` base class (start/stop/send lifecycle)
- `service.py` - Manages lifecycle of all configured channels from `config.yaml`. A failed or cancelled startup drains `ChannelService.stop()` before propagating and clears the singleton only after successful rollback, so no half-started channel workers survive Gateway startup cancellation.
- `slack.py` / `feishu.py` / `telegram.py` / `discord.py` / `dingtalk.py` - Platform-specific implementations (`feishu.py` tracks the running card `message_id` in memory and patches the same card in place; `telegram.py` registers the "Working on it..." placeholder as the stream target and edits it in place via `editMessageText`; `dingtalk.py` optionally uses AI Card streaming for in-place updates when `card_template_id` is configured)
- `github.py` - Webhook-driven GitHub channel. Inbound messages come from `POST /api/webhooks/github`; outbound is log-only because GitHub agents post explicitly with `gh` from their sandbox when they choose to comment or create a PR
- `app/gateway/routers/channel_connections.py` - Browser-facing user connection and disconnect APIs
- `deerflow.persistence.channel_connections` - SQL-backed user-owned connection, optional credential, connect state, and conversation store

**Message Flow**:

1. External platform -> Channel impl -> `MessageBus.publish_inbound()`
   - For GitHub, the webhook router verifies the delivery then calls `fanout_event(bus, ...)`; matching agent bindings publish one `InboundMessage` each instead of a long-polling channel worker.
2. `ChannelManager._dispatch_loop()` consumes from queue
3. For user-owned channel connections, incoming messages carry `connection_id`, `owner_user_id`, and `workspace_id`; `owner_user_id` becomes the DeerFlow run `user_id`, while the raw platform user id remains `channel_user_id`. The Gateway accepts `channel_user_id` only from an internally authenticated channel caller's top-level `body.context`, clears it from both free-form `body.config` sections, and writes it into runtime context only (never `configurable`, which is checkpointed). `bash_tool` exposes it to sandbox commands as the fixed env var `DEERFLOW_CHANNEL_USER_ID` — via a shell-quoted command-string prefix, NOT the `execute_command(env=...)` channel, which is reserved for request-scoped secrets and would switch `AioSandbox` onto the `bash.exec` path (image >= 1.9.3, fresh session per call). Per-call injection keeps group-chat identity correct (one thread/sandbox, many senders) **without depending on the AIO shell's session semantics**: every IM-channel command carries an explicit `export VAR=<id>; ` (valid id) or `unset VAR; ` (empty / non-str / over the 256-char cap). The AIO no-env path reuses a persistent shell session (the reason for the class lock, #1433), so a bare command could otherwise resolve a stale id an earlier sender exported; the `unset` closes the window the length/type guard would open (a dropped id would inherit the previous sender's value). Non-IM runs (no `channel_user_id` in context) are left untouched. Not injected on the Windows local sandbox (its PowerShell/cmd.exe fallback has no `export`/`unset`). Propagates across `task` delegation: `task_tool` captures the dispatching turn's id and the subagent executor forwards it into the subagent's runtime context, same as the guardrail attribution fields. The runtime-context value is authorization-grade at the Gateway/guardrail boundary, but the exported shell variable remains informational because any bash command can overwrite its own environment; skills must not treat the shell variable itself as authenticated identity. Tests: `tests/test_gateway_services.py`, `tests/test_channel_user_id_env.py`
4. For chat: look up/create thread through Gateway's LangGraph-compatible API
5. Feishu/Telegram chat: `runs.stream()` → accumulate AI text → publish multiple outbound updates (`is_final=False`) → publish final outbound (`is_final=True`)
6. Slack/Discord chat: `runs.wait()` → extract final response → publish outbound
   6b. GitHub chat (`ChannelRunPolicy.fire_and_forget=True`): `runs.create()` returns once the run is `pending`; the manager does not wait for the final state and does not publish an outbound. The agent posts its own reply mid-run via `gh` from the sandbox. `ConflictError` on a busy thread still trips the standard `THREAD_BUSY_MESSAGE` path (log-only on GitHub).
7. Feishu channel sends one running reply card up front, then patches the same card for each outbound update (card JSON sets `config.update_multi=true` for Feishu's patch API requirement). Messages already sent inside an existing Feishu topic carry a compact source-message preview in that card, and queued same-thread follow-ups patch their own source message's card from queued → running → final without falling back to the generic busy reply.
8. Telegram streaming: the "Working on it..." placeholder message is registered as the stream target; non-final updates `editMessageText` it in place (channel-side throttle: 1s in private chats, 3s in groups due to Telegram's 20 msg/min group cap; 4096-char truncation; rate-limited updates dropped); the final update performs the last edit and splits >4096 texts into follow-up messages
9. DingTalk AI Card mode (when `card_template_id` configured): `runs.stream()` → create card with initial text → stream updates via `PUT /v1.0/card/streaming` → finalize on `is_final=True`. Falls back to `sampleMarkdown` if card creation or streaming fails
10. For commands (`/new`, `/status`, `/models`, `/memory`, `/goal`, `/help`): handle locally or query Gateway API
11. Outbound → channel callbacks → platform reply
    - GitHub is the exception: the channel logs the final assistant message and does **not** auto-post it to GitHub. Agents use the sandbox `gh` CLI (`gh issue comment`, `gh pr comment`, `gh pr create`, etc.) for intentional writeback, so silence is cheap when several agents fan out on the same event.

**Owner-scoped file storage**: inbound files, uploads, and output artifacts are staged under the DeerFlow owner's bucket so they land where the agent run reads/writes (`users/{user_id}/threads/{thread_id}/user-data/{uploads,outputs}`). `ChannelManager._handle_chat` resolves the storage owner once via `_channel_storage_user_id(msg)` (sanitized owner id, falling back to `safe(msg.user_id)` for unbound auth-enabled channels — mirroring `_resolve_run_params`'s run identity; `None` only when no identity is available) and threads it as the `user_id=` kwarg through the file pipeline:

- `Channel.receive_file(msg, thread_id, user_id=...)` — owner-bound channels persist downloaded files under the owner's bucket instead of the default bucket
- `_ingest_inbound_files(...)` and the underlying `ensure_uploads_dir` / `get_uploads_dir` — owner-scoped via the same kwarg
- `_resolve_attachments` / `_prepare_artifact_delivery` — resolve output artifacts from the bound owner's bucket
  The cached value is reused for both the blocking (`runs.wait`) and streaming (`_handle_streaming_chat`) paths, so uploads and artifact delivery always target the same bucket even if a channel returns a rewritten `InboundMessage` from `receive_file`. The bucket id matches the memory bucket resolved by `_resolve_memory_user_id` (both normalize through `make_safe_user_id`).

**Configuration** (`config.yaml` -> `channels`):

- `langgraph_url` - LangGraph-compatible Gateway API base URL (default: `http://localhost:8001/api`)
- `gateway_url` - Gateway API URL for auxiliary commands (default: `http://localhost:8001`)
- In Docker Compose, IM channels run inside the `gateway` container, so `localhost` points back to that container. Use `http://gateway:8001/api` for `langgraph_url` and `http://gateway:8001` for `gateway_url`, or set `DEER_FLOW_CHANNELS_LANGGRAPH_URL` / `DEER_FLOW_CHANNELS_GATEWAY_URL`.
- Per-channel configs: `feishu` (app_id, app_secret), `slack` (bot_token, app_token), `telegram` (bot_token), `dingtalk` (client_id, client_secret, optional `card_template_id` for AI Card streaming), `github` (operator kill-switch `enabled`, plus `default_mention_login` for mention-required GitHub triggers)

**User-owned channel connections** (`config.yaml` -> `channel_connections`):

- Disabled by default. It is a user-binding layer on top of the existing `channels.*` runtime config, not a replacement for provider bot credentials.
- No public IP, OAuth callback URL, or provider webhook route is required by the current implementation.
- Telegram uses a deep-link `/start <code>` flow over the existing long-polling worker. Slack, Discord, Feishu/Lark, DingTalk, WeChat, and WeCom use `/connect <code>` over their existing outbound channel workers.
- Frontend APIs: `GET /api/channels/providers`, `GET /api/channels/connections`, `POST /api/channels/{provider}/connect`, and `DELETE /api/channels/connections/{connection_id}`.
- Browser APIs remain protected by normal Gateway auth/CSRF. Provider messages arrive through the already-configured channel workers.
- Provider-level `connection_status` reflects the user's newest connection row. With no binding it is `not_connected`, except in auth-disabled local mode where a configured running channel reports `connected` because all channel messages already route to the default user.
- Slack replies use the configured operator bot token from `channels.slack` unless per-connection credentials are present; unreadable or corrupt stored credentials are treated as unavailable.
- Telegram, Slack, Discord, Feishu/Lark, DingTalk, WeChat, and WeCom workers resolve incoming platform identities to connection records before reaching `ChannelManager`.
- **Connect-code ordering vs `allowed_users`**: inbound workers consume a valid `/connect <code>` (or Telegram `/start <code>`) **before** applying the `allowed_users` filter, so a newly allowlisted-but-unbound user can bootstrap their first bind via the browser flow. Consequence: `allowed_users` is **not** a bind-time defense — any sender who possesses a valid code can consume it (not only allowlisted users). The bind security model rests on the code's confidentiality: `secrets.token_urlsafe(16)`, 600 s TTL, one-time `consume_oauth_state`, and codes surfaced only in the initiating browser (never echoed to chat). `allowed_users` still gates ordinary (non-bind) messages.
- **Single-active-owner transfer semantics**: an external identity is keyed by `(provider, external_account_id, workspace_id)`. The latest successful bind wins — `upsert_connection` revokes other owners' active rows for the same identity (ownership transfer). This invariant is enforced at the DB layer by the partial unique index `uq_channel_connection_active_identity` (`WHERE status != 'revoked'`), so concurrent connects from different owners cannot both end `connected`; the losing writer retries against the now-visible state. `find_connection_by_external_identity` therefore resolves deterministically.
- See `backend/docs/IM_CHANNEL_CONNECTIONS.md` for provider setup, operational notes, and the architecture diagrams (connect-code flow, single-active-owner transfer, sync vs streaming dispatch, owner-scoped file storage pipeline).

**GitHub event-driven agents** (webhook-driven IM channel):

- Custom agents declare a `github:` block in their `config.yaml` to bind to repos and event triggers; the webhook route is fail-closed by default (mounted only when `GITHUB_WEBHOOK_SECRET` is set) and exempt from auth/CSRF because authenticity is enforced by HMAC.
- Outbound is **log-only** by design: each agent posts its own reply mid-run via the `gh` CLI from its sandbox, so the manager uses `fire_and_forget=True` and `runs.create()` returns once pending.
- See [backend/docs/GITHUB_AGENTS.md](docs/GITHUB_AGENTS.md) for the architecture diagrams: webhook → fan-out → `InboundMessage` dispatch, `preferred_thread_id = UUID5(repo, number, agent_name)` thread determinism, mention-handle precedence chain, GH token lifecycle via `GH_TOKEN`/`GITHUB_TOKEN` per-call `extra_env`, and the narrow `ConflictError` (HTTP 409) thread-create race recovery.

### Memory System (`packages/harness/deerflow/agents/memory/`)

**Components**:

- `updater.py` - LLM-based memory updates with fact extraction, whitespace-normalized fact deduplication (trims leading/trailing whitespace before comparing), and atomic file I/O
- `queue.py` - Debounced update queue (per-thread deduplication, configurable wait time); captures `user_id` at enqueue time so it survives the `threading.Timer` boundary
- `prompt.py` - Prompt templates for memory updates
- `storage.py` - File-based storage with per-user isolation; cache keyed by `(user_id, agent_name)` tuple
- `tools.py` - Tool-driven memory mode (`memory_search`, `memory_add`, `memory_update`, `memory_delete`) using the same storage/update primitives

**Per-User Isolation**:

- Memory is stored per-user at `{base_dir}/users/{user_id}/memory.json`
- Per-agent per-user memory at `{base_dir}/users/{user_id}/agents/{agent_name}/memory.json`
- Custom agent definitions (`SOUL.md` + `config.yaml`) are also per-user at `{base_dir}/users/{user_id}/agents/{agent_name}/`. The legacy shared layout `{base_dir}/agents/{agent_name}/` remains read-only fallback for unmigrated installations
- Middleware mode captures `user_id` via `get_effective_user_id()` at enqueue time; tool mode resolves `user_id` and `agent_name` from `ToolRuntime.context` via `resolve_runtime_user_id(runtime)` so tool calls stay scoped to the authenticated user and active custom agent
- The `/api/memory*` endpoints resolve the owner through `_resolve_memory_user_id(request)`: trusted internal callers (IM channel workers carrying the `X-DeerFlow-Owner-User-Id` header, e.g. a bound `/memory` command) act for the connection owner; browser/API callers fall back to `get_effective_user_id()`. The header is only honored after `AuthMiddleware` validated the internal token, mirroring `get_trusted_internal_owner_user_id` used by the threads router
- In no-auth mode, `user_id` defaults to `"default"` (constant `DEFAULT_USER_ID`)
- Absolute `storage_path` in config opts out of per-user isolation
- **Migration**: Run `PYTHONPATH=. python scripts/migrate_user_isolation.py` to move legacy `memory.json`, `threads/`, and `agents/` into per-user layout. Supports `--dry-run` (preview changes) and `--user-id USER_ID` (assign unowned legacy data to a user, defaults to `default`).

**Data Structure** (stored in `{base_dir}/users/{user_id}/memory.json`):

- **User Context**: `workContext`, `personalContext`, `topOfMind` (1-3 sentence summaries)
- **History**: `recentMonths`, `earlierContext`, `longTermBackground`
- **Facts**: Discrete facts with `id`, `content`, `category` (preference/knowledge/context/behavior/goal), `confidence` (0-1), `createdAt`, `source`

**Workflow**:

- `memory.mode: middleware` (default) keeps the passive path: `MemoryMiddleware` filters messages (user inputs + final AI responses), captures `user_id` via `get_effective_user_id()`, queues conversation with the captured `user_id`, and the debounced background thread invokes the LLM to extract context updates and facts using the stored `user_id`.
- `memory.mode: tool` skips `MemoryMiddleware` and registers `memory_search`, `memory_add`, `memory_update`, and `memory_delete` on the agent. The model decides when to search, add, update, or delete facts; this is opt-in/experimental and should not be described as better than middleware mode without eval evidence.
- Both modes share `FileMemoryStorage`, per-user/per-agent isolation, prompt injection, manual CRUD primitives, and the updater backend.
- Middleware mode queue debounces (30s default), batches updates, deduplicates per-thread, applies updates atomically (temp file + rename) with cache invalidation, and skips duplicate fact content before append.
- Persisted memory follows the conversation language policy and defaults to
  Simplified Chinese. The updater may use `factsToRewrite` only to normalize an
  existing fact's language in place; it must preserve the fact ID, confidence,
  provenance, timestamps, names, numbers, URLs, versions, technical terms, and
  semantic meaning. Existing summaries in another language are rewritten on
  the next eligible memory update.
- Durable-memory writes enforce a task boundary independently of the extraction model: resource identifiers, paths, filenames, tool failures, and one-off deliverable constraints (page count, outline, template, brand/VI, colors, logo, watermark, layout, chart/matrix, export settings) are rejected at apply time. A deliverable rule is durable only when its text explicitly declares future/default/always/cross-task intent. Stable role, organization, domain, and technology context remain eligible; assistant-authored claims alone must not create user facts.
- Staleness pass (same LLM invocation as the regular updater, no extra API call): when `staleness_review_enabled` is `true` and at least `staleness_min_candidates` aged facts exist, `_select_stale_candidates` selects facts older than their individual review window (`expected_valid_days`, or the global `staleness_age_days` fallback) that are not in `staleness_protected_categories` (default: `correction`), surfaces them in the prompt with a `valid:Nd` annotation, and the LLM judges each as KEEP, REMOVE, or EXTEND. REMOVE entries go in `staleFactsToRemove`; EXTEND entries go in `staleFactsToExtend` with an `extend_by_days` value, which sets the fact's `expected_valid_days` to `min(days_since_created + extend_by_days, staleness_max_extension_days)`. The LLM assigns `expected_valid_days` when creating a fact; it is clamped at write time to `staleness_age_days × staleness_max_lifetime_multiplier` (creation cap). `_apply_updates` enforces the guardrail unconditionally at apply time: it intersects both the removal and extension sets with `_select_stale_candidates` output before applying the per-cycle cap (`staleness_max_removals_per_cycle`), so protected and non-aged facts can never be targeted regardless of model behavior or the feature flag setting. Facts the LLM proposed for removal are excluded from extension even if the per-cycle cap prevented their actual deletion that cycle. Extensions use an absolute ceiling (`staleness_max_extension_days`) rather than the creation multiplier so a deliberate review decision can advance the window beyond the initial cap while preventing `timedelta` overflow from a malformed `extend_by_days`.
- Consolidation pass (same LLM invocation as the regular updater, no extra API call): when `consolidation_enabled` is `true` and at least one category holds `consolidation_min_facts` or more facts, `_select_consolidation_candidates` identifies fragmented categories and surfaces at most `consolidation_max_groups_per_cycle` of them (largest first) in the prompt. The LLM decides which groups to merge and proposes a synthesised fact per group. `_apply_updates` enforces guardrails: source IDs must exist and must not overlap across groups, group size is capped at `consolidation_max_sources`, the merged fact's confidence cannot exceed the source maximum, and facts below `fact_confidence_threshold` are not written.
- Next interaction injects selected facts + context into `<memory>` tags in the system prompt when `injection_enabled` is true.

**Run-level memory identity**:

- Every Gateway run with an effective hidden memory block hashes the exact `HumanMessage.content`, including the `<memory>` wrapper, and records one `context:memory` event through its run-scoped `RunJournal`. Later runs and checkpoint-based branches reuse the frozen message without reloading memory; goal continuations are deduplicated to one event per run.
- A first-run block is trusted only when it comes from `DynamicContextMiddleware`'s current update. A reused block must have existed in the checkpoint before the run, and the Gateway strips dynamic-context markers from untrusted input so a caller cannot forge the identity event by reusing a known message ID.
- The production consumer is the existing debug/audit endpoint `GET /api/threads/{thread_id}/runs/{run_id}/events?event_types=context:memory`. Event content has exactly one field, `content_sha256`, which operators use to compare the effective memory identity across runs. The full memory text stays in checkpoint state and is not duplicated into `run_events`.

**Token counting** (`packages/harness/deerflow/agents/memory/prompt.py`):

- `_count_tokens` budgets the injection. In default `tiktoken` mode, the encoding is loaded lazily and cached.
- Failed tiktoken loads are cached with a timestamp. During the fixed cooldown (`_TIKTOKEN_RETRY_COOLDOWN_S`, 600s), callers fall back to char estimation immediately instead of re-triggering the blocking BPE download; after the cooldown, transient outages can self-heal without a restart.
- In-flight loads are cached as a LOADING sentinel so concurrent callers fall back instead of spawning more blocking threads.
- Set `memory.token_counting: char` to skip tiktoken entirely and use the network-free CJK-aware char estimate.
- The production Gateway image sets `TIKTOKEN_CACHE_DIR` and bundles the
  hash-verified `cl100k_base` cache object under
  `backend/assets/tiktoken-cache/`. Keep its URL-derived cache filename and
  SHA-256 aligned with the tiktoken version pinned by `backend/uv.lock`; this
  prevents runtime BPE downloads through restricted enterprise proxies.

Focused regression coverage for the updater lives in `backend/tests/test_memory_updater.py`.

DeerMem-private configuration belongs under `memory.backend_config`. In
particular, use `backend_config.model.model` for the memory-update model,
`backend_config.debounce_seconds` for queue delay, and keep storage/fact/token
settings in that same nested object. A model-only value matching a configured
DeerFlow model name is resolved as a host model alias so its credentials, base
URL, TLS, proxy, and tracing settings remain authoritative. Legacy top-level
DeerMem keys are normalized before Pydantic validation for compatibility, but
new deployment manifests and ConfigMaps must use the nested form.

**Configuration** (`config.yaml` → `memory`):

- `enabled` / `injection_enabled` - Master switches
- `mode` - Operation mode: `middleware` (default passive background extraction) or `tool` (experimental model-driven memory tools). Modes are mutually exclusive.
- `storage_path` - Path to memory.json (absolute path opts out of per-user isolation)
- `debounce_seconds` - Wait time before processing (default: 30)
- `shutdown_flush_timeout_seconds` - Host-shared hard budget (seconds) to drain the memory backend's pending-update buffer on Gateway graceful shutdown (default: 30; 1–300). Each pending item does one LLM call, so large IM batches may need more. The Gateway lifespan resolves configuration and the backend off the event loop, calls `MemoryManager.shutdown_flush(timeout)` after channels/scheduler stop, and drains that entire owned operation before propagating lifespan cancellation. The backend short-circuits on an idle buffer, so the host calls it unconditionally (no pending/processing gate). Must fit inside the pod's K8s `terminationGracePeriodSeconds` (gateway Helm chart sets this; default 45s), including resolution and surrounding shutdown work, or K8s SIGKILLs the drain mid-flight.
- `model_name` - LLM for updates (null = default model)
- `max_facts` / `fact_confidence_threshold` - Fact storage limits (100 / 0.7)
- `max_injection_tokens` - Token limit for prompt injection (2000)
- `token_counting` - Token counting strategy for the injection budget: `tiktoken` (default, accurate but may download BPE data from a public endpoint on first use — can block for a long time in network-restricted environments, see issues #3402/#3429) or `char` (network-free CJK-aware char estimate, never touches tiktoken)
- `staleness_review_enabled` - Enable proactive staleness pruning of aged facts (default: `true`; only triggers when aged candidates exist)
- `staleness_age_days` - Age in days before a fact becomes a staleness candidate (default: 90; range: 30–365)
- `staleness_min_candidates` - Minimum aged candidates required to trigger a review cycle (default: 3; range: 1–50)
- `staleness_max_removals_per_cycle` - Maximum facts removed in a single cycle; lowest-confidence entries are kept when the LLM requests more (default: 10; range: 1–50)
- `staleness_protected_categories` - Fact categories that are never pruned by staleness review (default: `["correction"]`)
- `staleness_max_lifetime_multiplier` - Creation-time cap multiplier for a fact's LLM-assigned `expected_valid_days`: stored value is clamped to `staleness_age_days × multiplier` so the model cannot defer first review indefinitely (default: 20.0; range: 1.0–100.0). Default 20.0 (90 × 20 = 1800 d ≈ 5 years) is generous enough to support the very-stable prompt tier without needing multiple review cycles to escape the cap.
- `staleness_max_extension_days` - Absolute upper bound (in days) on `expected_valid_days` after a lifetime extension (`staleFactsToExtend`). Applied at write time as `min(days_since + extend_by, staleness_max_extension_days)`. Uses an absolute ceiling rather than the multiplier because extensions are deliberate review decisions; prevents `timedelta` overflow and LLM misfire from permanently deferring a fact (default: 3650 = 10 years; range: 90–36500).
- `consolidation_enabled` - Enable memory consolidation (default: `true`; no extra API call — runs in the same LLM invocation as the normal memory update)
- `consolidation_min_facts` - Minimum facts in a category to trigger consolidation review (default: 8; range: 3–30)
- `consolidation_max_groups_per_cycle` - Maximum categories the LLM can merge in one cycle (default: 3; range: 1–10; also controls the LLM's prompt instruction)
- `consolidation_max_sources` - Maximum source facts per merge group; prevents over-merging (default: 8; range: 2–20)

### Reflection System (`packages/harness/deerflow/reflection/`)

- `resolve_variable(path)` - Import module and return variable (e.g., `module.path:variable_name`)
- `resolve_class(path, base_class)` - Import and validate class against base class

### Schema Migrations (`packages/harness/deerflow/persistence/migrations/`)

DeerFlow's application tables (`runs`, `threads_meta`, `feedback`, `users`, `run_events`, plus the four `channel_*` tables) are owned by alembic via a **hybrid bootstrap** strategy. LangGraph's checkpointer tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`) live in the same database but are owned by LangGraph and excluded from alembic's view via `migrations/_env_filters.py::include_object`.

**Convention**: every ORM model change (new column, new table, new index) MUST ship as an alembic revision under `migrations/versions/`. The Gateway runs `alembic upgrade head` automatically on startup; users do not run `alembic` manually in production.

**Hybrid bootstrap** (`persistence/bootstrap.py::bootstrap_schema`, invoked from `persistence/engine.py::init_engine`):

| DB state                                       | Action                                                                                         |
| ---------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| empty (no DeerFlow tables)                     | `create_all` + `alembic stamp head`                                                            |
| legacy (DeerFlow tables, no `alembic_version`) | `create_all` (baseline tables only, backfill) + `alembic stamp 0001_baseline` + `upgrade head` |
| versioned (`alembic_version` row exists)       | `alembic upgrade head`                                                                         |

The legacy branch handles pre-alembic databases that already have at least one DeerFlow-owned table. `create_all` runs first because stamping at `0001_baseline` makes alembic skip the baseline's own `create_table` DDL on the subsequent upgrade — so any baseline table introduced into `Base.metadata` after the user's DB was first provisioned (e.g. the `channel_*` tables from PR #1930 for users upgrading across multiple releases) would otherwise never be created, and the first request hitting that table would 500 with `no such table`. The backfill is **restricted to `_BASELINE_TABLE_NAMES`** so it does not also create tables that future revisions introduce — those revisions' own `op.create_table` would otherwise fail with `relation already exists`. A guard test pins `_BASELINE_TABLE_NAMES` against `0001_baseline.upgrade()`'s actual output, so editing 0001 to add or remove a table forces a matching update to the constant. Column-level shape (pre-#3658 vs post-#3658 vs manual-ALTER for `token_usage_by_model`) is answered by each `versions/*.py` revision via the idempotent helpers in `migrations/_helpers.py` (`safe_add_column` / `safe_drop_column`) which no-op when the change is already present and `logger.warning` on shape drift. **Adding a new ORM column / table only requires a new revision file — no edit to `bootstrap.py` is needed** _unless_ the new revision adds a new baseline table (rare; only happens when a new model is part of the baseline rather than introduced by its own revision).

The empty-DB path keeps using `create_all` because `Base.metadata` is the only authoritative schema source — `create_all` renders both SQLite (JSON, type affinity) and Postgres (JSONB, partial indexes) correctly without anyone having to keep a hand-written baseline in lockstep. `0001_baseline.upgrade()` is therefore almost never executed in practice; it exists as a stamp target + chain root.

**Concurrency safety**: Postgres uses `pg_advisory_lock` to serialise concurrent Gateway instances. SQLite uses a per-engine `asyncio.Lock` for same-process startup and is best-effort across processes via SQLite's file-level write lock + `PRAGMA busy_timeout`; multi-instance deployments should use Postgres. Column revisions in `versions/` additionally use idempotent helpers (`_helpers.py::safe_add_column`, `safe_drop_column`) so repeated post-baseline changes and retries are no-ops when the change is already present.

**Authoring a new revision**:

```bash
cd backend && make migrate-rev MSG="add foo column to runs"
```

This invokes `alembic revision --autogenerate` against the live ORM models. Review the generated file under `migrations/versions/` and switch raw `op.add_column` / `op.drop_column` calls to the idempotent helpers from `_helpers.py` before committing. There is no `make migrate` / `make migrate-stamp` target on purpose — the only execution path is Gateway startup, which keeps operational mistakes off the table.

**Where things live**:

- `migrations/env.py` — alembic env, delegates filter to `_env_filters.py`, sets `render_as_batch=True` for SQLite ALTER support
- `migrations/_env_filters.py::include_object` — drops LangGraph checkpointer tables from alembic's view
- `migrations/_helpers.py` — `safe_add_column` / `safe_drop_column`
- `migrations/versions/0001_baseline.py` — chain root, matches the schema `create_all` produces from `Base.metadata`
- `migrations/versions/0002_runs_token_usage.py` — fixes issue #3682
- `persistence/bootstrap.py` — `bootstrap_schema(engine, backend=...)`, the three-branch decision + locking
- Tests: `tests/test_persistence_bootstrap.py` (branches), `tests/test_persistence_bootstrap_concurrency.py` (concurrency), `tests/test_persistence_bootstrap_regression.py` (issue #3682), `tests/test_persistence_migrations_env.py` (filter), `tests/blocking_io/test_persistence_bootstrap.py` (asyncio.to_thread anchor)

### Terminal Workbench / TUI (`packages/harness/deerflow/tui/`)

A terminal-native UI over the embedded harness, exposed as the `deerflow` console script (`[project.scripts]` in `packages/harness/pyproject.toml`). It is a UI shell over `DeerFlowClient` and does **not** fork agent behavior. `textual` is an optional dependency (`deerflow-harness[tui]`; also in the backend dev group); the console script degrades to headless help when it is absent. Full guide: [docs/TUI.md](docs/TUI.md).

**Module layout** (all layers except `app.py` are pure / Textual-free and unit-tested directly):

- `cli.py` — `plan_launch()` (pure launch-mode decision) + headless `--print` / `--json` + `main()` entry point. TTY → TUI, else headless help. Uses an **absolute** `from deerflow.tui.app import run_tui` so the `app.py` module name doesn't trip `test_harness_boundary.py` (which records relative import module names verbatim).
- `view_state.py` — `ViewState` + `reduce(state, action)`, the testable heart. Rows: user / assistant / tool / system. Title captured from `values` events.
- `runtime.py` — `translate(StreamEvent) -> [Action]` (pure) + `stream_actions()` which brackets a run with `RunStarted`/`RunEnded` and turns model errors into an `AssistantError` row.
- `message_format.py` / `command_registry.py` / `input_history.py` / `render.py` / `theme.py` — pure helpers (tool summaries, slash registry + `resolve()`, ↑/↓ history, Rich renderers).
- `app.py` — Textual `App`. Runs `DeerFlowClient.stream()` (sync) on a worker thread and marshals actions to the UI thread via `call_from_thread`. Slash palette with `/goal` management + model/thread modal pickers; priority key bindings gated by `check_action` so they never steal keys from overlays or the composer.
- `session.py` / `persistence.py` — builds the client + checkpointer and the `ThreadMetaWriter`.

**Web UI visibility**: the Web UI lists threads from the `threads_meta` SQL table (user-scoped), not the checkpointer. `persistence.py` writes a `threads_meta` row under the default user (`"default"`) into the same DB the Gateway reads — via the harness-only `deerflow.persistence.engine.init_engine_from_config()` — so TUI sessions appear in the Web UI sidebar **without** running the Gateway. Best-effort: a no-op on the `memory` backend. All DB work runs on one long-lived background event loop (a SQLAlchemy async engine is bound to its creating loop).

**Tests**: `tests/test_tui_*.py` — pure layers via plain pytest, the app/palette/overlays via Textual's pilot harness with a fake in-process session, and `test_tui_persistence.py` for the `threads_meta` round-trip.

### Request Trace Context (`packages/harness/deerflow/trace_context.py`)

Request trace correlation is controlled by `logging.enhance.enabled` at **both** entry points, gated through the shared helper `deerflow.config.app_config.is_trace_correlation_enabled` so the Gateway and embedded paths cannot drift:

- **Gateway HTTP**: `app.gateway.trace_middleware.TraceMiddleware` binds one request-level trace id per HTTP request, inheriting inbound `X-Trace-Id` when present or generating a new id otherwise. The middleware writes the final value to every HTTP response at `http.response.start`, which covers SSE / streaming responses without consuming the body.
- **Embedded / TUI / CLI**: `DeerFlowClient.stream()` mints (or inherits) a request-level trace id per turn only when the flag is on. When it is off, no fresh id is minted — a caller that explicitly wraps `stream()` in `request_trace_context(...)` still opts in, because the downstream `get_current_trace_id()` read propagates that value into Langfuse metadata regardless of the flag. Because `stream()` is a sync generator (which shares the caller's context), the id binding is set/reset around each `next()` step rather than around `yield from`: this keeps LangGraph node execution and its log records inside the binding, while returning control to the caller with the ContextVar restored — avoids cross-request leak between yields and `ValueError: <Token> was created in a different Context` on GC-driven close of an abandoned generator (regression pinned by `tests/test_client_langfuse_metadata.py::test_stream_does_not_leak_trace_id_to_caller_context_between_yields` and `::test_stream_abandoned_generator_close_does_not_raise_cross_context`).

The same ContextVar value is injected into enhanced log records as `trace_id` and into Langfuse metadata as `deerflow_trace_id`.

`logging` is registered as a **restart-required** field
(`STARTUP_ONLY_FIELDS["logging"]`): `configure_logging()` installs the trace-context
filter and enhanced formatter on root handlers only during app.py lifespan startup,
and `TraceMiddleware` captures `logging.enhance.enabled` once when the FastAPI app
is constructed (via `resolve_trace_enabled(get_app_config())` in `create_app()`,
itself a thin alias for `is_trace_correlation_enabled`). This keeps the response
`X-Trace-Id` header, log `trace_id` fields, and Langfuse `deerflow_trace_id`
coherent — a runtime `config.yaml` edit to `logging.enhance.*` needs a Gateway
restart to take effect. The `deerflow_trace_id` chain inherits this guarantee
transitively because every injection point ultimately reads the same
`trace_context` ContextVar that the middleware alone populates. `DeerFlowClient`
reads its own `self._app_config` snapshot (captured at `__init__`) through the
same helper for the embedded gate.

`deerflow_trace_id` is a DeerFlow correlation metadata key, not Langfuse's native
trace id and not a DeerFlow `run_id`. Keep the existing subagent `trace_id` field
separate: that short id is still only for subagent execution logs/status.

### Tracing System (`packages/harness/deerflow/tracing/`)

LangSmith and Langfuse are both supported. The wiring lives in two layers:

- `factory.py::build_tracing_callbacks()` — returns the LangChain `CallbackHandler` list for the providers currently enabled via env vars (`LANGSMITH_TRACING`, `LANGFUSE_TRACING`, etc.). The handlers are attached at the **graph invocation root** for in-graph runs (`make_lead_agent` and `DeerFlowClient.stream` both append them to `config["callbacks"]` before invoking the graph) so a single run produces one trace with all node / LLM / tool calls as child spans. Standalone callers — anything that invokes a model outside such a graph (e.g. `MemoryUpdater`) — keep `create_chat_model`'s default `attach_tracing=True`, which falls back to model-level callback attachment.
- `metadata.py::build_langfuse_trace_metadata()` — builds the Langfuse-reserved trace attributes for `RunnableConfig.metadata`. The Langfuse v4 `langchain.CallbackHandler` lifts these onto the root trace (see its `_parse_langfuse_trace_attributes`), but only when it sees `on_chain_start(parent_run_id=None)` — which is why the callbacks have to live at the graph root, not the model.

**Trace-attribute injection points**: both `runtime/runs/worker.py::run_agent` (gateway path) and `client.py::DeerFlowClient.stream` (embedded path) merge the metadata into `config["metadata"]` right before constructing the graph. `subagents/executor.py::_aexecute` does the same for every subagent run so subagent traces group under the parent thread's session card (carrying the parent `thread_id` → `langfuse_session_id`, the user_id captured at `task_tool` → `langfuse_user_id`, and a `subagent:<normalized-name>` trace name). Caller-supplied keys win via `setdefault`, so an external `session_id` override is preserved. Field mapping:

| Langfuse field        | Source                                                                                                                                                                                                                                                                                                                                              |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `langfuse_session_id` | LangGraph `thread_id`                                                                                                                                                                                                                                                                                                                               |
| `langfuse_user_id`    | `get_effective_user_id()` (`default` in no-auth); for subagents, captured from `runtime.context` at `task_tool` time via `resolve_runtime_user_id()`                                                                                                                                                                                                |
| `langfuse_trace_name` | `RunRecord.assistant_id` / client `agent_name` (defaults to `lead-agent`); for subagents, `subagent:<name>` (lowercased, `_` → `-`)                                                                                                                                                                                                                 |
| `langfuse_tags`       | `env:<DEER_FLOW_ENV>` + `model:<model_name>`                                                                                                                                                                                                                                                                                                        |
| `deerflow_trace_id`   | Current request/entry trace id from `deerflow.trace_context`; matches `X-Trace-Id` for enhanced Gateway HTTP requests. Gated by `logging.enhance.enabled` in both gateway and embedded paths via `is_trace_correlation_enabled` — off by default; embedded callers can still opt in per-turn by wrapping `stream()` in `request_trace_context(...)` |

Returns `{}` when Langfuse is not in the enabled providers — LangSmith-only deployments are unaffected. Set `DEER_FLOW_ENV` (or `ENVIRONMENT`) to tag traces by deployment environment. Tests live in `tests/test_tracing_factory.py`, `tests/test_tracing_metadata.py`, `tests/test_worker_langfuse_metadata.py`, `tests/test_client_langfuse_metadata.py`, and `tests/test_subagent_executor.py::TestSubagentTracingWiring`.

**Monocle telemetry** is a third provider, structurally unlike LangSmith/Langfuse. It is **not** a LangChain callback: `tracing/monocle.py::setup_monocle_tracing_if_enabled()` calls `monocle_apptrace.setup_monocle_telemetry()` once, which installs a **process-global OTel `TracerProvider`**, patches span serialization, and auto-instruments the openai/langchain/langgraph clients. Because that is a one-time, process-global side effect (not a per-run callback), it is initialized from the **Gateway lifespan** (`app/gateway/app.py`) — never from `build_tracing_callbacks()` — and it is **off by default**. The setup call was deliberately moved out of `agents/__init__.py`, so `import deerflow.agents` must never start tracing (pinned by `tests/test_monocle_tracing.py::test_no_import_time_setup`). The Gateway lifespan is the **sole call site** (pinned by `test_gateway_lifespan_initializes_monocle`), so unlike LangSmith/Langfuse — which attach at the graph roots and cover every path — the embedded `DeerFlowClient` and the TUI are not instrumented; embedded users who want Monocle traces call `setup_monocle_tracing_if_enabled()` themselves before running the agent.

Unlike the Langfuse metadata above, DeerFlow injects **no** per-run fields into Monocle traces — the only attribute it sets is `workflow_name="deer-flow"`; every span attribute (`span.type`, `entity.*`, token usage, span inputs/outputs, `scope.agentic.session`) is produced by Monocle's own metamodel and auto-instrumentation, so there is no DeerFlow trace-attribute layer to maintain here.

Config is env-driven like the others — `MonocleTracingConfig`, built in `get_tracing_config()` and gated by `is_monocle_tracing_enabled()`. `MONOCLE_TRACING` enables it; `MONOCLE_EXPORTERS` selects exporters (default `file` → trace JSON in `.monocle/`; also `console`, `okahu`, `s3`, `blob`, `gcs`, where `okahu` requires `OKAHU_API_KEY`). `setup_monocle_tracing_if_enabled()` stays a thin wrapper on purpose: `monocle_apptrace` already guards duplicate setup (`instrumentor.py::check_duplicate_setup`) and never force-overrides an existing global provider, so the wrapper only gates on config. Coexistence with Langfuse (v4, also OTel-based) is **verified**: whichever library initializes second reuses the existing global `TracerProvider` and attaches its own span processor, so neither side loses spans (pinned by `test_coexists_with_langfuse`). Both processors see all spans, so Monocle's exporters also capture Langfuse's spans when both are enabled. (LangSmith is a plain callback and coexists trivially.) Tests: `tests/test_monocle_tracing.py`.

### Config Schema

**`config.yaml`** key sections:

- `models[]` - LLM configs with `use` class path, `supports_thinking`, `supports_vision`, provider-specific fields
- Enterprise OpenAI-compatible endpoints that encode application failures as
  HTTP 200 responses with `choices=null` should use
  `deerflow.models.robust_openai:RobustChatOpenAI`. It raises a typed,
  bounded-diagnostic exception containing the provider's `code`, `msg`, and
  `success` fields. `LLMErrorHandlingMiddleware` retries an unknown/transient
  null-choices response once, while recognized authentication, quota, and
  context-limit errors remain non-retriable. Keep streaming enabled for the
  audited enterprise endpoint: its safety gateway returns business code
  `20009` for non-streaming tool-call completions but accepts the equivalent
  streamed protocol. The adapter normalizes JSON-encoded streamed chunks before
  LangChain conversion. DeepSeek-V4-Flash's separate
  `delta.reasoning_content` is retained in `additional_kwargs` and replayed
  for subsequent tool-call requests; pair
  `when_thinking_enabled.extra_body.enable_thinking: true` with the explicit
  false counterpart under `when_thinking_disabled`. It may quarantine an
  isolated malformed scalar frame
  only when a later normal chunk contains a terminal `finish_reason`; otherwise
  the incomplete stream remains a typed `MALFORMED_RESPONSE` failure. That exact
  transport defect may use up to three fresh request attempts, bounded by the
  operator retry cap. Never log the raw scalar, turn it into assistant content,
  or recover tool calls embedded in a provider error message. Providers that
  repeat cumulative usage on each streamed chunk must set
  `cumulative_stream_usage: true`; the adapter tracks snapshots independently
  by completion id, publishes only deltas, and clears terminal or idle state so
  LangChain cannot multiply prompt/cache usage by the chunk count. Keep the
  option disabled for providers that already emit per-chunk usage.
  Historical aggregates written before that guard cannot recover the missing
  final provider snapshot exactly. Use
  `backend/scripts/correct_cumulative_token_usage.py` for PostgreSQL incident
  repair: audit is read-only, apply quarantines only impossible per-call
  contributions after writing an atomic backup, rollback restores the exact
  original run aggregates, and `run_events` always remains immutable. Never
  replace this with an unbacked bulk reset or an arbitrary estimated divisor.
- `logging.enhance` - Optional request trace correlation (`enabled`, `format`) for Gateway `X-Trace-Id`, log `trace_id`, and Langfuse `deerflow_trace_id`
- `DEERFLOW_RUN_TIMING_LOGS=true` - Temporary, content-free performance diagnostics. Emits `RUN_TIMING` records for run start/end, complete worker streams, per-thread checkpoint-lock waits, in-lock graph streaming, content-free graph-task lifecycle, DeerFlow-owned `after_agent`/`aafter_agent` spans, Sandbox acquire/create/ready, tool start/end, active-goal evaluation/checkpoint reads, hidden middleware retry/injection markers, hidden summary/title calls, memory queue handoff, and post-graph finalization. `agent_stream_*` brackets the whole worker turn; its nested `checkpoint_lock_wait_*` and `agent_graph_stream_*` distinguish lock contention from time actually inside `agent.astream()`. `agent_graph_stream_tail.elapsed_ms` measures the silent interval after the final published graph update and before graph exit. When all observed graph tasks have ended but the next `agent.astream()` item still has not arrived after 30 seconds, `agent_graph_stream_idle_wait_watchdog` records only the number, bounded coroutine implementation labels, and bounded per-label task counts; `agent_graph_stream_idle_wait_end` gives the eventual wait outcome. The positive `DEERFLOW_RUN_TIMING_IDLE_WAIT_SECONDS` override changes that diagnostic threshold without cancelling the graph. Browser bridge publication logs only error/cancelled calls and successful calls slower than one second (`agent_stream_bridge_publish_slow`); `DEERFLOW_RUN_TIMING_SLOW_BRIDGE_PUBLISH_MS` is its positive override. Do not add a publish log per token: it would alter load-test behavior. While enabled, Gateway privately consumes LangGraph `tasks` mode and emits `graph_task_start` / `graph_task_end` pairs with a fixed node name and local task index only; task state, inputs, outputs, and task ids are never forwarded to the browser or log. `middleware_after_agent_*` uses a fixed DeerFlow middleware name and outcome only, exposing cleanup or Sandbox-release delay inside a graph `after_agent` node without recording hook state. `stream_kind=initial` denotes the user turn and `stream_kind=goal` a goal continuation. Todo, artifact-delivery, terminal-response, and Skill workflow markers record only a retry/injection decision, not content. Finalization separately brackets journal flush, run-completion persistence, title sync, duration persistence, thread-status update, and the optional completion hook; an optional pair appears only when its dependency and code path run. PostgreSQL checkpointers additionally aggregate checkpoint API calls, connection-pool borrow waits, checked-out connection holds, and awaited SQL execution at `run_end`; each group reports the peak same-Run in-flight operation count, and slow records identify the in-flight count present when they began. This is not an exact LangGraph predecessor-chain queue length. `DEERFLOW_CHECKPOINTER_DB_SESSION_LABELS=true` additionally labels each borrowed PostgreSQL checkpointer session with only `dfcp:<run-id>` so a bounded `pg_stat_activity` sample can separate active database work from upstream application waiting; it is disabled by default and adds one diagnostic SQL command per borrow. Never log SQL, parameters, checkpoint state, user/thread data, or a connection identity. It is independent of `DEERFLOW_LLM_TIMING_LOGS`, which adds the model request/first-chunk/end records. Non-stream goal-evaluator requests use `mode=async_nonstream`; their `first_chunk_ms` is response-completion latency because the provider exposes no chunks. Each model request's three records share an `llm_call_id`; model attribution first uses the current task context and falls back to reserved inheritable LangChain metadata (`deerflow_timing_thread_id` / `deerflow_timing_run_id`) so callback child tasks retain the parent DeerFlow IDs. `run_end` separates true execution time/count of built-in Sandbox API tools (`sandbox_tool_execution_total_ms` / `sandbox_tool_count`: `bash`, `ls`, `glob`, `grep`, `read_file`, `write_file`, `str_replace`) from Gateway/external tools (`non_sandbox_tool_*`). Any acquisition nested inside a first Sandbox tool is removed from the execution total and reported as `sandbox_tool_acquire_overlap_ms`; individual `tool_end` records retain every tool's full duration. Neither path records tool arguments, command text, or paths. Enable both only for a bounded investigation and remove them afterwards. See `docs/run-timing-logs.md`.
- vLLM reasoning models should use `deerflow.models.vllm_provider:VllmChatModel`; for Qwen-style parsers prefer `when_thinking_enabled.extra_body.chat_template_kwargs.enable_thinking`, and DeerFlow will also normalize the older `thinking` alias
- `tools[]` - Tool configs with `use` variable path and `group`
- `tool_groups[]` - Logical groupings for tools
- `sandbox.use` - Sandbox provider class path
- `skills.path` / `skills.container_path` - Host and container paths to skills directory
- `skills.deferred_discovery` - When `true`, replaces the full-metadata `<available_skills>` prompt block with a compact `<skill_index>` (names only) and registers the `describe_skill` tool so the agent fetches metadata on demand. Defaults to `false` (legacy full-metadata injection)
- `title` - Auto-title generation (enabled, max_words, max_chars, model_name; null model_name uses the current default LLM, while an explicit model name pins title generation to that model; both use `prompt_template` and fall back locally on failure)
- `summarization` - Context summarization (enabled, trigger conditions, keep policy)
- `subagents.enabled` - Master switch for subagent delegation
- Built-in `general-purpose` and `bash` subagents load no Skills by default.
  This prevents lead-agent-only workflow instructions (especially `task`
  delegation) from leaking into non-recursive child contexts. To use a
  compatible Skill in a child, configure it explicitly under
  `subagents.agents.<name>.skills` (or the custom agent's `skills` list).
- `memory` - Memory system (enabled, storage_path, debounce_seconds, shutdown_flush_timeout_seconds, model_name, max_facts, fact_confidence_threshold, injection_enabled, max_injection_tokens, staleness_review_enabled, staleness_age_days, staleness_min_candidates, staleness_max_removals_per_cycle, staleness_protected_categories, staleness_max_lifetime_multiplier, staleness_max_extension_days)

**`extensions_config.json`**:

- `mcpServers` - Map of server name → config (enabled, type, command, args, env, url, headers, oauth, description, `routing`, `tools`, `tool_call_timeout`). `routing.mode="prefer"` emits `<mcp_routing_hints>` prompt guidance; if `tool_search` defers the hinted tool, `McpRoutingMiddleware` can also auto-promote matching deferred schemas before the model call. It does not hard-disable other tools.
- `tool_search.auto_promote_top_k` - Global MCP routing auto-promote breadth. Default `3`, clamped to `1..5`; applies only when `tool_search.enabled=true` and only to deferred MCP tools with `routing.mode="prefer"` and non-empty keywords. For lead agents the deferred catalog is built from the full configured MCP set; auto-promotion never grants authority because an active skill's runtime policy still filters model-visible schemas, `tool_search` results, and execution.
- `skills` - Map of skill name → state (enabled)

Both can be modified at runtime via Gateway API endpoints or `DeerFlowClient` methods.

### Embedded Client (`packages/harness/deerflow/client.py`)

`DeerFlowClient` provides direct in-process access to all DeerFlow capabilities without HTTP services. All return types align with the Gateway API response schemas, so consumer code works identically in HTTP and embedded modes.

**Architecture**: Imports the same `deerflow` modules that Gateway API uses. Shares the same config files and data directories. No FastAPI dependency.

**Agent Conversation**:

- `chat(message, thread_id)` — synchronous, accumulates streaming deltas per message-id and returns the final AI text
- `stream(message, thread_id)` — subscribes to LangGraph `stream_mode=["values", "messages", "custom"]` and yields `StreamEvent`:
  - `"values"` — full state snapshot (title, messages, artifacts); AI text already delivered via `messages` mode is **not** re-synthesized here to avoid duplicate deliveries
  - `"messages-tuple"` — per-chunk update: for AI text this is a **delta** (concat per `id` to rebuild the full message); tool calls and tool results are emitted once each
  - `"custom"` — forwarded from `StreamWriter`
  - `"end"` — stream finished (carries cumulative `usage` counted once per message id)
- Agent created lazily via `create_agent()` + `build_middlewares()`, same as `make_lead_agent`
- Supports `checkpointer` parameter for state persistence across turns
- `reset_agent()` forces agent recreation (e.g. after memory or skill changes)
- See [docs/STREAMING.md](docs/STREAMING.md) for the full design: why Gateway and DeerFlowClient are parallel paths, LangGraph's `stream_mode` semantics, the per-id dedup invariants, and regression testing strategy

**Gateway Equivalent Methods** (replaces Gateway API):

| Category  | Methods                                                                                               | Return format                                                       |
| --------- | ----------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| Models    | `list_models()`, `get_model(name)`                                                                    | `{"models": [...]}`, `{name, display_name, ...}`                    |
| MCP       | `get_mcp_config()`, `update_mcp_config(servers)`                                                      | `{"mcp_servers": {...}}`                                            |
| Skills    | `list_skills()`, `get_skill(name)`, `update_skill(name, enabled)`, `install_skill(path)`              | `{"skills": [...]}`                                                 |
| Goals     | `get_goal(thread_id)`, `set_goal(thread_id, objective, max_continuations=8)`, `clear_goal(thread_id)` | `{"goal": {...}}` or `{"goal": None}`                               |
| Memory    | `get_memory()`, `reload_memory()`, `get_memory_config()`, `get_memory_status()`                       | dict                                                                |
| Uploads   | `upload_files(thread_id, files)`, `list_uploads(thread_id)`, `delete_upload(thread_id, filename)`     | `{"success": true, "files": [...]}`, `{"files": [...], "count": N}` |
| Artifacts | `get_artifact(thread_id, path)` → `(bytes, mime_type)`                                                | tuple                                                               |

**Key difference from Gateway**: Upload accepts local `Path` objects instead of HTTP `UploadFile`, rejects directory paths before copying, and reuses a single worker when document conversion must run inside an active event loop. Artifact returns `(bytes, mime_type)` instead of HTTP Response. The new Gateway-only thread cleanup route deletes `.deer-flow/threads/{thread_id}` after LangGraph thread deletion; there is no matching `DeerFlowClient` method yet. `update_mcp_config()` and `update_skill()` automatically invalidate the cached agent.

**Tests**: `tests/test_client.py` (77 unit tests including `TestGatewayConformance`), `tests/test_client_live.py` (live integration tests, requires config.yaml)

**Gateway Conformance Tests** (`TestGatewayConformance`): Validate that every dict-returning client method conforms to the corresponding Gateway Pydantic response model. Each test parses the client output through the Gateway model — if Gateway adds a required field that the client doesn't provide, Pydantic raises `ValidationError` and CI catches the drift. Covers: `ModelsListResponse`, `ModelResponse`, `SkillsListResponse`, `SkillResponse`, `SkillInstallResponse`, `McpConfigResponse`, `UploadResponse`, `MemoryConfigResponse`, `MemoryStatusResponse`.

## Development Workflow

### Test-Driven Development (TDD) — MANDATORY

**Every new feature or bug fix MUST be accompanied by unit tests. No exceptions.**

- Write tests in `backend/tests/` following the existing naming convention `test_<feature>.py`
- Run the full suite before and after your change: `make test`
- Tests must pass before a feature is considered complete
- For lightweight config/utility modules, prefer pure unit tests with no external dependencies
- If a module causes circular import issues in tests, add a `sys.modules` mock in `tests/conftest.py` (see existing example for `deerflow.subagents.executor`)

```bash
# Run all tests
make test

# Run a specific test file
PYTHONPATH=. uv run pytest tests/test_<feature>.py -v
```

### Running the Full Application

From the **project root** directory:

```bash
make dev
```

This starts all services and makes the application available at `http://localhost:2026`.

**All startup modes:**

|          | **Local Foreground**                         | **Local Daemon**                                             | **Docker Dev**                                      | **Docker Prod**                     |
| -------- | -------------------------------------------- | ------------------------------------------------------------ | --------------------------------------------------- | ----------------------------------- |
| **Dev**  | `./scripts/serve.sh --dev`<br/>`make dev`    | `./scripts/serve.sh --dev --daemon`<br/>`make dev-daemon`    | `./scripts/docker.sh start`<br/>`make docker-start` | —                                   |
| **Prod** | `./scripts/serve.sh --prod`<br/>`make start` | `./scripts/serve.sh --prod --daemon`<br/>`make start-daemon` | —                                                   | `./scripts/deploy.sh`<br/>`make up` |

| Action      | Local                                       | Docker Dev                                        | Docker Prod                                |
| ----------- | ------------------------------------------- | ------------------------------------------------- | ------------------------------------------ |
| **Stop**    | `./scripts/serve.sh --stop`<br/>`make stop` | `./scripts/docker.sh stop`<br/>`make docker-stop` | `./scripts/deploy.sh down`<br/>`make down` |
| **Restart** | `./scripts/serve.sh --restart [flags]`      | `./scripts/docker.sh restart`                     | —                                          |

**Nginx routing**:

- `/api/langgraph/*` → Gateway embedded runtime (8001), rewritten to `/api/*`
- `/api/*` (other) → Gateway API (8001)
- `/` (non-API) → Frontend (3000)

### Running Backend Services Separately

From the **backend** directory:

```bash
# Gateway API
make gateway
```

Direct access (without nginx):

- Gateway: `http://localhost:8001`

### Frontend Configuration

The frontend uses environment variables to connect to backend services:

- `NEXT_PUBLIC_LANGGRAPH_BASE_URL` - Defaults to `/api/langgraph` (through nginx)
- `NEXT_PUBLIC_BACKEND_BASE_URL` - Defaults to empty string (through nginx)

When using `make dev` from root, the frontend automatically connects through nginx.

## Key Features

### File Upload

Multi-file upload with automatic document conversion:

- Endpoint: `POST /api/threads/{thread_id}/uploads`
- Supports: PDF, PPT, Excel, Word documents; automatic conversion uses
  `markitdown` when enabled, while PDF/DOC/DOCX/PPT/PPTX can use the enterprise
  parser bridge independently
- Rejects directory inputs before copying so uploads stay all-or-nothing
- Reuses one conversion worker per request when called from an active event loop
- Files stored in thread-isolated directories under the resolving user's bucket (`users/{user_id}/threads/{thread_id}/user-data/uploads`). For IM channels the owner is threaded explicitly via the `user_id=` kwarg (see IM Channels → Owner-scoped file storage); HTTP/embedded callers resolve it from `get_effective_user_id()`
- Duplicate filenames in a single upload request are auto-renamed with `_N` suffixes so later files do not truncate earlier files
- Gateway HTTP uploads stage bytes as `.upload-*.part` files and atomically replace the destination only after size validation. These staging files are hidden from upload listings, agent upload context, and sandbox listing/search tools, and swept on Gateway startup if a hard crash leaves one behind.
- Before the atomic replacement, the Gateway rejects browser-active/server-executable extensions and validates PDF identity plus active-content indicators. This check runs on the staged file so a rejected replacement cannot destroy an existing upload.
- Direct artifact access never renders original uploaded PDFs or active web content in the authenticated origin. Generated inert PDFs keep inline preview with CSP; this includes LibreOffice's tightly validated direct first-page destination `OpenAction` but excludes indirect or executable actions. Suspicious PDFs and explicit downloads use hardened attachment responses.
- When `WEKNORA_MINERU_ENDPOINT` (or `DEERFLOW_UPLOAD_DOCUMENT_PARSER_ENDPOINT`) is available and `DEERFLOW_UPLOAD_DOCUMENT_PARSER_ENABLED` is not false, PDF/DOC/DOCX/PPT/PPTX conversation uploads call the in-cluster `/file_parse` adapter and atomically write a same-stem Markdown companion. This reuses WeKnora's searchable/scanned PDF routing, Word native-plus-visual path, enterprise OCR, and presentation conversion without creating a knowledge-base record. Word native text/tables/images remain part of the merged result, but its LibreOffice visual PDF, required MinerU enhancement, and page evidence are strict; incomplete visual processing is an adapter failure, as it is for PPT/PPTX. The enterprise manifest explicitly enables this bridge and sets `DEERFLOW_UPLOAD_DOCUMENT_PARSE_TIMEOUT_SECONDS=1860`, leaving 60 seconds beyond the Adapter's bounded 1800-second upstream wait for response transfer and atomic persistence; the local Docker/PVC overlay keeps the bridge disabled because it cannot reach the enterprise MinerU service, and other development deployments retain the shorter code default unless they opt in explicitly. The legacy `DEERFLOW_UPLOAD_PDF_*` variables remain accepted. If `uploads.auto_convert_documents` or the explicit enterprise `DEERFLOW_UPLOAD_DOCUMENT_FALLBACK_ENABLED` override is true, adapter failure falls back to `convert_file_to_markdown()` with a visible `processing_warning`; that text-only fallback must never be represented as complete visual parsing. Because MarkItDown does not support legacy OLE `.doc`, that format alone may use the task sandbox's LibreOffice to produce a temporary DOCX and then re-enter `convert_file_to_markdown()`; the request-unique sandbox and Gateway temporary files are removed afterwards. A `processing_warning` is returned only if the adapter path did not complete visually. The original document is retained in every case, and the client and agent must not silently claim visual parsing succeeded.
- `/ppt-master` submissions with an attached PPT/PPTX send the validated multipart purpose `presentation_template`. Gateway skips MinerU and local Markdown conversion only for those presentation files, while retaining all upload extension, staged-write, ownership, and size checks. Unmarked PPT/PPTX uploads continue through the configured enterprise parser.
- Gateway HTTP upload/list/delete handlers offload filesystem work through `deerflow.utils.file_io.run_file_io`, a dedicated ContextVar-preserving file IO executor. Non-mounted sandbox uploads acquire sandboxes with `SandboxProvider.acquire_async()` and offload `read_bytes()` plus `sandbox.update_file()` together.
- Agent receives uploaded file list via `UploadsMiddleware`

See [docs/FILE_UPLOAD.md](docs/FILE_UPLOAD.md) for details.

### Plan Mode

TodoList middleware for complex multi-step tasks:

- Controlled via runtime config: `config.configurable.is_plan_mode = True`
- Provides `write_todos` tool for task tracking
- One task in_progress at a time, real-time updates

See [docs/plan_mode_usage.md](docs/plan_mode_usage.md) for details.

### Context Summarization

Automatic conversation summarization when approaching token limits:

- Configured in `config.yaml` under `summarization` key
- Trigger types: tokens, messages, or fraction of max input
- Keeps recent messages while summarizing older ones
- Manual compaction uses `POST /api/threads/{id}/compact`, reuses the same
  `DeerFlowSummarizationMiddleware`, writes a new checkpoint with updated
  `messages` and `summary_text`, and bumps only those channel versions.
  The route shares the per-thread serialization gate used by `/goal` writes
  and run admission so compaction cannot race with goal updates or runs that
  read/write checkpoints.

See [docs/summarization.md](docs/summarization.md) for details.

### WeKnora identity bridge

`app/gateway/weknora_identity.py` provisions or reuses a WeKnora account with
the DeerFlow user's email and a deterministic service-managed credential. Each
successful SSO login mirrors DeerFlow `system_role=admin` to WeKnora's
`is_system_admin` and `can_access_all_tenants` flags; regular users are
explicitly kept non-admin. Keep this role sync covered by
`tests/test_weknora_default_models.py` when changing the bridge. Proxy requests
reuse the linked session for `WEKNORA_SESSION_CACHE_TTL_S` seconds (default 900)
so list and upload endpoints do not repeat registration/login on every call. For
scoped chat runs, Gateway forwards the selected knowledge-base IDs, derives a
per-user token server-side, and the run worker binds that token only for the
duration of the configured permission-scoped `weknora_*` tool execution.
The native knowledge picker also supplies a consumer-tenant routing hint for
each selected KB. Gateway accepts that hint only when it belongs to the
authenticated user's WeKnora memberships, publishes a server-owned
KB-to-tenant map in the run context, and every direct WeKnora GET, JSON POST,
session, and SSE tool request sends the resulting `X-Tenant-ID` beside the
per-user bearer token. Never use the knowledge-base owner/source tenant for a
shared-resource call, and never treat a browser tenant hint as authorization;
WeKnora remains the final per-user RBAC boundary.
The browser scope may contain complete knowledge bases, individual knowledge
documents, or tags grouped under their parent knowledge base. Normalize it once
from `weknora_knowledge_base_ids`, `weknora_knowledge_ids`,
`weknora_tag_scopes`, and `weknora_scope_items`, then bind it in a run-local
context variable beside the bearer token. Tool arguments may narrow a non-browser
fallback scope but must never widen an explicit browser-selected run scope.
File/tag scope rows without their parent knowledge-base identity are invalid.
The public DeerFlow surface contains ten wrappers: knowledge-base metadata,
document listing/detail/chunks/full parsed read, search, RAG ask, agent listing,
and agent invocation. Wrapper names are DeerFlow-owned but the underlying
WeKnora endpoints are official. Agent invocation uses native `agent-chat`, omits
raw system prompts, keeps internal tool execution behind WeKnora authorization,
and returns approval-required events without approving them.
WeKnora image handles are never exposed to a current DeerFlow chat as raw
`resource://` URLs. The tools rewrite them to the knowledge-base-scoped
`/api/weknora/knowledge-bases/{id}/files` route; Gateway resolves the caller's
consumer tenant, fetches the image with the caller's linked WeKnora session,
and returns only `image/*` content. The unscoped `/api/weknora/files` route is
limited to rendering older messages that still contain raw stable handles.
`WeKnoraCitationMiddleware` also restores images selected by the current
WeKnora answer if a final model rewrite drops them. Search results expose a
ready-to-copy `resource_images[].markdown` for both document-embedded images
and standalone image-file `image_info` metadata; when the current search turn
contains exactly one unique authenticated image, the middleware restores that
image as well. It never guesses between multiple candidate images.
When a `weknora_search`, `weknora_ask`, or `weknora_invoke_agent` result exceeds the tool-output budget,
the complete JSON is still written to the thread's `.tool-results` artifact, but
`ToolOutputBudgetMiddleware` must keep a valid compact JSON envelope inline
with every ranked hit's source metadata, bounded text excerpt, citations, and
`resource_images` (plus `answer_images` for ask). Do not replace it with the generic head/tail preview:
invalid or image-free previews make the lead agent misreport a populated
knowledge base as empty.
When `TAVILY_API_KEY` is present, the same bridge creates an idempotent native
WeKnora Tavily provider named `DeerFlow Tavily` in every workspace the user owns
or administers and makes it the default search provider. Set
`WEKNORA_TAVILY_AUTO_CONFIGURE=false` to opt out. Never return the key to the
browser or persist it in manifests; WeKnora's provider API owns encrypted
credential storage.
The SSO response also carries `ui_policy`, derived only from the authenticated
DeerFlow `system_role`. Administrators receive `system_admin` with the full
native UI; regular users receive `personal_workspace_owner` with New Chat,
Knowledge Bases, Shared Spaces, and Agents navigation. This is a presentation contract,
not an authorization substitute. The deployment-level WeKnora
`WEKNORA_TENANT_SELF_SERVICE_CREATION_ENABLED=false` setting is what prevents
ordinary users from creating additional tenant workspaces, while native
WeKnora RBAC protects tenant and shared-space data.
In the enterprise overlay, WeKnora's Qwen3 Embedding and Reranker definitions
target authenticated Gateway relay routes rather than the private upstream
services directly. Keep `CNOOC_RETRIEVAL_API_KEY` server-side in Gateway and
use an independently generated `WEKNORA_INTERNAL_MODEL_RELAY_KEY` for the
in-cluster hop. The relay bypasses environment proxies with `trust_env=false`.
Qwen3 embeddings are fixed to 1024 Matryoshka dimensions; when the approved
endpoint returns 4096 values, the relay takes the 1024-value prefix and
L2-normalizes it. Do not change that dimension without rebuilding every
affected knowledge-base index. The approved retrieval credential admits 200
concurrent upstream requests. Keep `WEKNORA_INTERNAL_EMBEDDING_CONCURRENCY=200`
as both the default and hard Gateway ceiling while retaining WeKnora's 2+1
document worker limits. Gateway retries transient business code `10020` with bounded backoff
to absorb brief shared-capacity races while rerank requests retain their
independent path.
The workspace knowledge-base adapter refreshes `/auth/me` memberships before
listing, queries each membership with `X-Tenant-ID`, classifies ownership using
WeKnora's `creator_id`, and keeps an `access_tenant_id` distinct from the
resource's owner/source tenant. Preserve that distinction on document list and
mutation routes; organization-shared resources often require the consumer tenant
for authorization even though embeddings belong to the source tenant.
Document-list requests normally receive that consumer tenant from the frontend.
When it is missing, the Gateway fallback must resolve the knowledge-base ID only
across the current user's live memberships (including shared-resource listings)
and send the matching consumer tenant as `X-Tenant-ID`; never trust the WeKnora
JWT's current tenant as proof that it owns the selected knowledge base.
Lead and delegated agents expose those tools only when the current run contains
non-empty `weknora_knowledge_base_ids`. Without an explicit selection,
`WeKnoraScopeMiddleware` also prevents stale durable-memory references from being
treated as an active KB scope or `/mnt/user-data/knowledge_bases` file source.
The browser scope adapter mirrors WeKnora New Chat's Knowledge Base / Tag / File
mention selector. Its list-envelope normalizer must accept both flat `data: []`
and official nested `data: {data: [], total: ...}` responses, especially for
tags. Global file lookup proxies the official `/knowledge/search` contract and
must preserve each result's parent knowledge base and tags. Whole-KB, exact-file,
and per-KB tag selections remain distinct in run context; model-supplied tool
arguments may narrow output but must never replace or widen that server-bound
run scope. Normalize at most 20 explicit knowledge-scope selections per run so
browser state, middleware guidance, and downstream WeKnora calls remain
bounded; this does not replace the independent upload limits.

Runs with an explicit WeKnora scope skip the durable DeerMem queue entirely: retrieved
enterprise-document facts belong to that task and remain in its checkpoint/history.
Other DeerMem updates still reject task-local operational resources (KB/run/thread IDs,
container paths, filenames, tool errors, one-off artifact progress, and one-deliverable
templates/branding/layout requirements). Durable memory is reserved for cross-thread
user profile, explicitly durable preferences, and reusable facts.

### Vision Support

For models with `supports_vision: true`:

- `ViewImageMiddleware` processes images in conversation
- `view_image_tool` added to agent's toolset
- Images automatically converted to base64 and injected into state

## Code Style

- Uses `ruff` for linting and formatting
- Line length: 240 characters
- Python 3.12+ with type hints
- Double quotes, space indentation

## Documentation

See `docs/` directory for detailed documentation:

- [CONFIGURATION.md](docs/CONFIGURATION.md) - Configuration options
- [ARCHITECTURE.md](docs/ARCHITECTURE.md) - Architecture details
- [API.md](docs/API.md) - API reference
- [SETUP.md](docs/SETUP.md) - Setup guide
- [FILE_UPLOAD.md](docs/FILE_UPLOAD.md) - File upload feature
- [PATH_EXAMPLES.md](docs/PATH_EXAMPLES.md) - Path types and usage
- [summarization.md](docs/summarization.md) - Context summarization
- [plan_mode_usage.md](docs/plan_mode_usage.md) - Plan mode with TodoList
- [run-timing-logs.md](../docs/run-timing-logs.md) - Content-free run-stage performance diagnostics


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

## 2026-10-13: portal/GLM candidate boundaries

Hot-employee flags live only in the operator-configured shared JSON store
`DEERFLOW_EMPLOYEE_FEATURED_PATH`; this feature adds no SQL tables or revisions.
Do not reuse the channel configuration. Explicit release initialization freezes
all currently public identities as hot, exactly once. New public employees
default hot, while private drafts do not. Curation keys use source/name/publisher
so the same publisher's updates and republication preserve admin removal.
Publication-specific tokens separately reject stale browser edits. Gateway
GET/startup must never initialize or recreate the file. Mutations require admin
authorization, identity plus version CAS, bounded cross-process locks, fresh
reads and fsynced atomic replacement. Each mutation and its bounded change record
are one JSON transaction; the latest 256 changes are retained, not an unlimited
audit log. Fail closed on missing/corrupt/unsafe storage and lock timeout. Linux
flock/rename must be verified on the actual shared filesystem across nodes before
release; Windows locks only qualify local development. Resolve creator display
names in batches from trusted user records, never email or guessed identities.
Operator maintenance is available only through `employee_directory_maintenance`.
Never expose restore through the public API. Verify backup content against the
explicit hash, compare the whole-document revision under lock, save a private
exclusive preimage, and restore as a new transaction with increasing entry
versions. A raw file copy can reauthorize stale admin edits and is not rollback.
An unrelated PPT migration still needs its own qualification in every DB client
image. Never downgrade production as a code rollback.

`EnterpriseGLMChat` implements only the supplied GLM-5.3-Flash Chat Completions
contract: fixed thinking, omitted/high/max effort, one base64 image before text,
reasoning replay and bounded async wall time. Keep `thinking_always_on` and
`supports_vision` out of provider payloads and credentials out of model metadata.
The server Secret/config addition and real provider acceptance remain release
gates; no default model, OCR service or isolated PPT provider is changed here.
Reject Responses protocol overrides and immediately close the upstream iterator
when GLM streaming is closed/cancelled. SDK-wire tests must stay offline. The
explicit `enterprise_glm_probe --run` requires enterprise-network execution and a
server-side environment key; at most four synthetic calls, no business tools,
automatic retry, raw model output or credentials in receipts. Keep TLS enabled.
The supplied GLM-5.3-Flash document section 6.2 explicitly forbids disabling
thinking. Do not apply GLM-5.2 mode settings to FCGLM-001. The speculative
switchable profile and diagnostic were withdrawn before deployment; preserve
the fixed-thinking profile, single-image guard and the same model in tool turns.
GLM streaming additionally requires a terminal choice followed by raw `[DONE]`.
Keep the observer response-local, before the pinned OpenAI 2.32 decoder output
is consumed; never patch shared SDK clients or change other model streams.
Qualify private SDK seam changes with raw-byte sync/async and structured-output
tests. Missing termination is a non-retriable terminal safety error, not a
completed tool call. Preserve cancellation, close responses, retain final usage,
and distinguish transport completion from task completion. The isolated field
canary source-binds existing modules and never updates the running service.
The October 21 GLM field canary passed all four stages on Gateway generation
381 / SDK 2.32.0. Its release builder appends only those four exact source files
to the observed `58a50175cfae...` ARM64 manifest; preserve all base layers and
runtime settings. Preload-only receipts are not rollout acceptance. Recheck node
identities and the live Gateway specification before any later image-only patch.
The separate GLM activation binds all five actual preloads and the saved field
canary, uses an operator-attested task-drain window, and tests the complete
current specification before its one image replacement. Persist once/intent
before submission and mark uncertain outcomes honestly. Post-rollout acceptance
checks all 70 worker identities, exact source hashes and offline SDK termination
semantics plus existing directory/model reads; it never repeats provider calls.
An interrupted observer is not permission to reissue the patch or clear once.

Browser `private_origins` are operator-owned exact scheme/host/port plus audited
private address ranges. Explicit `private_origin_suffixes` additionally permit a
base DNS domain and its dot-boundary subdomains at the configured scheme/port;
they still require audited CIDRs and independent network enforcement. The
explicit enterprise internal namespace `https://cnooc` is the only supported
single-label suffix exception; never generalize it to public TLDs or enable it
by default. Its dot-boundary children include `datalake.cnooc`, independently
from the separate `https://cnooc.com.cn` policy. Exact
origins override suffix rules; the most-specific suffix wins without merging
parent ranges. Never populate them from a model or browser request,
guess production ranges, allow metadata/loopback targets, or disable TLS checks.
Application DNS checking does not prevent Chromium DNS rebinding or prove safe
redirect/worker egress; independently validate deployment network controls and
the actual failing URL before enabling a private exception. An empty diagnostic
result is not proof of DNS, proxy, certificate or authorization health.

Enterprise direct routing preserves existing NO_PROXY/no_proxy entries in both
Gateway command environments and Provisioner-created Sandbox environments.
Owned Chromium uses the separate operator-only DEERFLOW_BROWSER_DIRECT_SUFFIXES
setting to build explicit dot-boundary proxy bypass arguments. This grants no
private URL permission and changes neither TLS verification nor an existing CDP
browser. Do not assume arbitrary Sandbox Chromium launchers consume NO_PROXY;
verify their actual launcher before declaring browser acceptance. Existing
processes are not reconfigured by an environment patch.

Owned Browser Live sessions provide an explicit manual-login boundary. Exact
operator `manual_login_origins` protect authentication pages (including MFA
pages without password inputs); this setting grants no network access. Visible
password/OTP inputs also latch privacy. Serialize human controls with Agent
operations and suppress Agent snapshots, text, screenshots and mutation until
the user returns control from outside the login surface. Clear destroys the
owned context; never export Cookie/storage state or control an external CDP
context's login. Bind the session to the trusted thread owner. Reconnects carry
an opaque session identity and fail before allocation on a different worker;
that identity is continuity metadata, never authorization. Revalidate platform
authentication and thread ownership during long-lived sockets. These controls
do not provide multi-Pod placement or cross-conversation login persistence;
production intranet rollout still requires verified session routing and network
enforcement. Do not substitute ClientIP affinity for per-thread browser ownership.

Server acceptance packaging is allowlist-only (`deploy/enterprise/package-acceptance.py`).
No keys, private configs or production source snapshots belong in that archive.
Use hidden terminal input and stdin, not argv, for optional live GLM credentials.
Only a new isolated synthetic directory may be mutated by the shared-volume probe;
never initialize the real featured store or change existing directory permissions.
Report incomplete checks and deferred candidate/PPT acceptance explicitly.
Linux channel regression must exercise actual inter-process flock and timeout,
permission failure before publication, and CAS conflicts. Do not mock away flock
to manufacture a Windows pass. After a committed config save, local restart failure
is 503 and must not roll back another writer's shared configuration.
Known active artifact suffixes must remain attachment-only even if the Linux MIME
database is missing or misclassifies them; keep both ordinary and .skill paths covered.

The r3 server probe permits static directory subPath only with equal shared-volume
and subPath identities, followed by successful read-only parent checks on both
nodes before any fixture creation. Never infer directory safety from the name or
repair permissions automatically. Keep single-file/dynamic/readonly/local-volume
rejection and real cross-process lock/CAS tests. GLM diagnostic `length` must fail
acceptance even if an expected marker appears; separately record incomplete and
unattempted stages. Its 1000-token request setting follows the supplied streaming
example, not a guaranteed bound on hidden reasoning; retain total deadlines,
byte/frame caps, no retries and no raw content in exported receipts.

The r4 shared-filesystem diagnostic synchronizes only through bounded kubectl
stdin/stdout control frames. Do not use filesystem existence markers as a
cross-node barrier: their visibility can be delayed independently of the data
transaction under test. A holder acknowledges after acquiring real flock; both
writers acknowledge before either receives an explicit nonce/action/index-bound
GO. EOF or wrong control identity must fail without that write. Remote gated
workers have a 45-second hard lifetime; controller transport waits are bounded.
Keep every completed/failing phase receipt in both logs and the result, including
partial/uncertain outcomes. Never weaken actual shared JSON freshness, lock/CAS,
lost-update or atomic-failure assertions, flush caches or retry to manufacture a
PASS. Shared-only mode forbids GLM and reports its two-node source scope honestly;
it is not an all-replica production audit or rollout authorization.
