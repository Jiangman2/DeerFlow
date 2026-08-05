# AGENTS.md

This file provides guidance to AI coding agents (Claude Code, Codex, and others) when working with code in this repository. It is the source of truth; the sibling `CLAUDE.md` imports it via `@AGENTS.md`.

It is the **monorepo orientation layer**: it maps the whole repo and points to the
module guides that own the depth. For anything inside a module, read that module's
guide rather than expecting full detail here:

- **[backend/AGENTS.md](backend/AGENTS.md)** — backend depth: harness/app split, agent &
  middleware chain, sandbox, MCP, skills, memory, IM channels, persistence/migrations,
  config system, test layout.
- **[frontend/AGENTS.md](frontend/AGENTS.md)** — frontend depth: Next.js App Router layout,
  thread/streaming data flow, code style, commands.

## What is DeerFlow

DeerFlow is a LangGraph-based AI super-agent system with a full-stack architecture. The
backend runs a "super agent" with sandboxed execution, persistent memory, subagent
delegation, and extensible tools (built-in, MCP, community), all per-thread isolated. The
frontend is a Next.js chat UI. External IM platforms (Feishu, Slack, Telegram, Discord,
DingTalk) bridge into the same agent through the Gateway.

## Service Topology

A single `make dev` / Docker stack runs four cooperating services:

| Service         | Port   | Role                                                                 |
| --------------- | ------ | ------------------------------------------------------------------- |
| **Nginx**       | `2026` | Unified reverse-proxy entry point — open this in the browser        |
| **Gateway API** | `8001` | FastAPI REST API + embedded LangGraph-compatible agent runtime      |
| **Frontend**    | `3000` | Next.js web interface                                               |
| **Provisioner** | `8002` | Optional — only when sandbox is configured for provisioner/K8s mode |

Nginx is the single public entry: it serves the frontend and proxies `/api/langgraph/*`
to the Gateway's LangGraph runtime, rewriting it to Gateway's native `/api/*` routes; all
other `/api/*` go straight to the Gateway REST routers. See
[backend/AGENTS.md](backend/AGENTS.md) for the runtime and router detail.
The Next.js-owned `/api/runtime-config`, `/api/cnooc/external-agents/*`, and
`/api/shop-cart/*` routes are exceptions and must be proxied to Frontend before the
Gateway `/api/*` catch-all.

Gateway file security is the enforcement boundary for browser uploads and
artifact delivery. Reject active/server-executable upload extensions before
write, validate staged PDFs before atomic replacement, force original uploaded
documents and suspicious generated PDFs to hardened download responses, and
keep inert generated PDF previews behind response CSP plus a scriptless,
opaque-origin iframe sandbox. The browser-facing PowerPoint preview route
renders validated companion PDFs into a static HTML gallery of server-side PNG
pages so enterprise browsers do not depend on their native PDF plug-in; keep
PDFium work serialized and off the event loop. LibreOffice's direct first-page destination
`OpenAction` is inert only when it matches the strict page-reference array;
indirect or executable actions remain download-only. Do not rely on filename
MIME guessing or frontend validation alone.
After artifact read authorization succeeds, resolve the storage bucket from the
trusted `threads_meta.user_id` owner record rather than the current viewer. This
keeps owner and administrator read-only views on the same thread files while
preventing a browser from selecting an arbitrary user bucket.
Conversation PDF/DOC/DOCX/PPT/PPTX uploads reuse the configured WeKnora/MinerU
adapter to write a same-stem Markdown companion without creating
knowledge-base records. Searchable and scanned PDFs retain their
adapter-specific routing, Word uses its native-plus-visual path, and
presentations use its presentation conversion path. Presented PPT/PPTX outputs
separately rely on the active sandbox's LibreOffice to generate a missing
`.preview.pdf`; keep both integrations outside the `ppt-master` skill package
and read the original sandbox-visible workspace source before Gateway publishes
the normalized outputs copy. A deck is not published until that paired preview
has been generated and persisted, so clicking every successfully delivered
PPT/PPTX has a real browser preview. Hosted sandbox loopback URLs must never be
shown to browser users, and Chinese tasks publish a Chinese topic filename with
a date/time suffix instead of exposing the internal pinyin project slug.
PPT generation completion is hard-gated: a failed subprocess must not disable
the general Bash transport, `present_files` may satisfy an active presentation
workflow only with a real `.ppt`/`.pptx`, and SVG/specification intermediates
must never be accepted as a completed deck. Premature model conclusions while
those gates remain incomplete are recovery-only messages: hide them before run
events reach the browser and retry again whenever a recognized presentation
tool produced progress; only repeated no-progress conclusions may become a
visible workflow failure.

In the `k8s/local-pvc` stack, Nginx also serves WeKnora at
`/platform/knowledge-bases`. Keep that proxy on the `weknora-frontend` Kubernetes
service DNS name; never pin a ClusterIP, because recreating the cluster changes it.
The DeerFlow knowledge page embeds that same-origin native workspace. Its SSO
response assigns either the full `system_admin` presentation or the simplified
`personal_workspace_owner` presentation (New Chat, Knowledge Bases, Shared
Spaces, and Agents). Keep WeKnora configured to create the first personal workspace while
disabling ordinary self-service creation of additional tenant workspaces;
native RBAC, not UI hiding, remains the authorization boundary. The embedded
page has no extra DeerFlow header. Entering it collapses DeerFlow's sidebar and
expands WeKnora's sidebar once for both ordinary users and administrators; the
native WeKnora toggle remains available afterwards so chat history stays
reachable. Ordinary users retain the native Agents workflow;
Publish Integrations and system settings remain hidden and the embedded
user menu also hides logout. Their New Knowledge Base
and existing knowledge-base Settings dialogs expose only Basic Information,
while preserving all native knowledge-base type and indexing choices on that
page; deployment defaults supply the hidden configuration. System
administrators retain the expandable complete navigation and full editor.
When `TAVILY_API_KEY` is present, Gateway SSO also idempotently provisions a
native default `DeerFlow Tavily` provider in every workspace the user owns or
administers. The key must remain a runtime secret and must never be exposed in
the browser or committed to Kubernetes manifests.
The five built-in external-agent cards use same-origin server launch routes
under `/api/cnooc/external-agents/<agent>/launch`. The route reads the validated
HttpOnly `cnooc.platform_token` cookie and redirects only to the five canonical
HTTPS paths on `datalake.cnooc`, adding the current Token at redirect time.
Keep the strict allowlist, `no-store`/`no-referrer` response headers, and
server-only cookie access. The shared Nginx entry must route this exact API
prefix to the Frontend before its general `/api/` Gateway catch-all. Never
hardcode a Token, expose it to frontend
JavaScript, or restore the legacy `/agent-tools/*` reverse proxies.
External click telemetry from the Haineng and collaboration platforms enters
through `POST /api/agent_click`. Keep `EXTERNAL_AGENT_CLICK_INGEST_KEY` in the
runtime Secret and require it as a Bearer token or
`X-External-Agent-Click-Key`; never expose the key in browser JavaScript. The
Gateway normalizes the legacy five agent names and stores events in PostgreSQL
audit logs. Dashboard totals preserve Haineng and collaboration as independent
sources, exclude `通用智能体平台`, use the Asia/Shanghai calendar day, and show a
fixed latest-30-natural-day trend independent of the dashboard's main range.

## Repository Map

```
deer-flow/
├── Makefile                        # Root orchestration: drives the full stack (dev/start/stop, docker, setup)
├── config.example.yaml             # Template → copy to config.yaml (gitignored) at repo root
├── extensions_config.example.json  # Template → copy to extensions_config.json (gitignored): MCP servers + skills
├── backend/                        # Python backend — see backend/AGENTS.md
│   ├── Makefile                    # Per-module backend commands (dev, gateway, test, lint, migrate-rev)
│   ├── packages/harness/           # deerflow-harness package (import: deerflow.*) — agent framework
│   └── app/                        # FastAPI Gateway + IM channels (import: app.*)
├── frontend/                       # Next.js frontend (pnpm) — see frontend/AGENTS.md
├── docker/                         # docker-compose files, nginx config, provisioner
├── skills/                         # Agent skills: public/ (committed), custom/ (gitignored)
├── contracts/                      # Cross-component JSON contracts (e.g. subagent status, skill review)
├── scripts/                        # Root orchestration scripts invoked by the Makefile (check, configure, doctor, support_bundle, serve, nginx, docker, deploy, setup_wizard)
├── tests/                          # Root-level tests (currently tests/skills/ — public skill tests)
└── docs/                           # Cross-cutting docs, plans, and design notes
```

Runtime config lives at the **repo root**: copy `config.example.yaml` → `config.yaml`
(main app config) and `extensions_config.example.json` → `extensions_config.json` (MCP
servers + skills). Both real files are gitignored and may be edited at runtime via the
Gateway API. Config schema and resolution order are documented in
[backend/AGENTS.md](backend/AGENTS.md).

Skill quality review note:
- `skills/public/skill-reviewer/` is the built-in read-only skill quality reviewer.
  It uses the harness-layer `review_skill_package` tool and contracts in
  `contracts/skill_review/`. Model-visible review data is compact and
  tag-neutralized; full raw payloads stay in tool artifacts. See
  [backend/AGENTS.md](backend/AGENTS.md) for the non-activation, SkillScan, and
  `skill-creator` ownership boundaries.
- Downloadable `.skill` deliverables must be ZIP packages containing `SKILL.md`;
  `present_files` validates the archive before publishing it.

Durable-memory boundary note:
- Long-term memory is descriptive background, not task authority. One-turn
  operational directives about network access, tools, clarification, templates,
  or output formats remain thread-local unless the user explicitly marks them
  as future/default/always/cross-task preferences.

Platform-confidentiality boundary note:
- Conversational runs are not an operations or database console. Shared runtime
  middleware refuses requests for server details, environment variables and
  credentials, platform databases, cluster/deployment state, internal
  APIs/networks, logs, system prompts, source paths, and account tables before
  model or tool execution. Sandbox auditing independently blocks common shell,
  filesystem, Kubernetes/container, and platform-database access paths, while
  bash output masks request-scoped and platform runtime secrets. Dynamically
  created Kubernetes sandbox Pods must keep
  `automountServiceAccountToken: false`; conversational tasks never need
  cluster API credentials.
- This boundary must not block public product-usage help, WeKnora retrieval, or
  analysis of user-provided files under `/mnt/user-data` (including Excel, CSV,
  SQLite, and DuckDB). Never weaken it by adding platform database credentials
  to a skill or by treating platform storage as user-uploaded data.

Academic research skill note:
- `skills/public/academic-research/` is the single platform-visible compatibility
  entry for the vendored academic research package.
- The package itself lives at `skills/third_party/academic-research-skills/`, outside
  the recursively scanned public/custom/legacy categories. Keep the adapter runtime
  paths under `/mnt/skills/third_party/academic-research-skills/`, upstream license,
  and package manifest aligned; its component `SKILL.md` files must not be exposed as
  separate DeerFlow public skills.

Scheduled-task note:
- The scheduled-task MVP adds a workspace page at `/workspace/scheduled-tasks` plus a background scheduler service gated by `config.yaml -> scheduler.enabled`.
- Scheduled background runs are intentionally non-interactive: they execute through the normal run lifecycle, but the lead-agent toolset excludes `ask_clarification` when `context.non_interactive=true`. The key is honored only for internally-authenticated callers (the scheduler launch path); client-supplied `context.non_interactive` is dropped.
- Scheduled tasks persist an allowlisted run context (model, mode, reasoning, subagent, and WeKnora scope) and pass it through the normal Gateway run path. The creation UI prefers the enterprise `deepseek-v4-flash` model when available, lets users choose another configured model, and does not inherit later chat-page model changes. Never persist caller identity, internal execution flags, runtime-only credentials, or arbitrary client context on the task row.
- Scheduled runs use `scheduler.recursion_limit` as their LangGraph super-step budget. The Gateway clamps it to the global `max_recursion_limit`; keep loop detection enabled and diagnose repeated tool-call traces rather than using an unbounded value.

Enterprise Kubernetes storage note:
- Keep `accessModes: [ReadWriteOnce]` in the PostgreSQL StatefulSet claim-template patch under `k8s/enterprise/storage-patches.yaml`. The patch replaces the matched claim-template item's `spec`; omitting `accessModes` renders an invalid StatefulSet.
- The enterprise WeKnora database intentionally uses the locally imported ARM64 PG16/pg_search image declared in `k8s/enterprise/kustomization.yaml`. Keep its `imagePullPolicy: Never`, `shared_preload_libraries=pg_search`, and nested `PGDATA`; the upstream ParadeDB PG17 image triggers `SIGILL` on the audited datax CPUs.
- WeKnora knowledge graph support is enabled only through the DeerFlow-owned
  Kubernetes Neo4j StatefulSet, runtime Secret, and documented environment
  variables. Do not patch or fork native WeKnora frontend/backend source for
  graph enablement or UI compatibility. The enterprise local-PV layout runs one
  Neo4j Community instance; use a managed service or licensed cluster for HA.
- Keep the enterprise Frontend's `WEKNORA_FRONTEND_URL` set to the same-origin path `/platform/knowledge-bases`; a loopback or node-pinned browser URL breaks SSO or becomes invalid when the public endpoint changes.
- The patched WeKnora UI must fetch `/api/weknora/sso-session` through a same-origin relative URL in both `config.js` and the `index.html` fallback. These ConfigMap files are mounted with `subPath`, so restart `weknora-frontend` after changing them.
- Keep the WeKnora UI on the official v0.7.0 multi-platform manifest digest declared in both the base and enterprise Kustomize bundles. That build understands the App's `resource://` and request-local image handles and uses authenticated, knowledge-base-scoped file retrieval; the earlier UI digest renders retrieved knowledge images as failed previews. DeerFlow chat separately rewrites those handles to its tenant-aware `/api/weknora/knowledge-bases/{id}/files` proxy; never expose WeKnora bearer tokens or direct storage paths to the browser.
- The enterprise Gateway, WeKnora App, and MinerU Adapter trust outbound HTTPS through the runtime-created `deer-flow-enterprise-ca` Secret. Keep the projected `/etc/deerflow-ca/ca-bundle.crt` mount and CA environment variables in their enterprise manifests; create the Secret before apply and restart the affected Deployments after CA rotation so long-lived HTTP clients reload trust state.
- The production Gateway image bundles the hash-verified `cl100k_base`
  tiktoken cache and sets `TIKTOKEN_CACHE_DIR`; keep this cache in release
  source packages so Gateway startup and memory token budgeting never depend
  on downloading BPE data through the enterprise proxy.
- Apply `k8s/enterprise/weknora-mineru-defaults.sql` after creating a new WeKnora database. Its trigger routes missing PDF/PPT/PPTX rules to the in-cluster adapter for knowledge bases created through the native WeKnora UI while preserving every explicit file-type parser choice.
- Keep `k8s/weknora/enterprise-model-feature-defaults-job.yaml` and its versioned Job name aligned with `enterprise-model-feature-defaults.sql`. The Job waits for the built-in model rows, applies the SQL once, and remains completed so routine re-applies do not overwrite later user choices. Bump the Job name only when a revised initializer must run. It makes internal `DeepSeek-V4-Flash` the enterprise KnowledgeQA default, switches empty and future knowledge bases to the dedicated internal embedding model, normalizes database-backed agent overrides to the internal rerank plus enterprise VLM/ASR built-ins, and installs insert/model-write guards so later objects start ready without per-user setup. Populated knowledge bases must be re-parsed successfully before their embedding model is switched and old external embedding rows are hidden. Keep the mounted `k8s/weknora/builtin_agents.yaml` aligned with those defaults so runtime-generated built-in agents are also ready in every existing and future personal workspace.
- The enterprise Gateway uses the runtime proxy for external model providers, but its `NO_PROXY` and `no_proxy` values must retain the bare Kubernetes Service names (especially `weknora-app`) as well as `.svc` and `.cluster.local`. Otherwise Gateway-to-WeKnora calls are sent through the enterprise proxy and surface as HTTP 302/502 failures. Keep proxy URLs and credentials in the runtime-created `deer-flow-env` Secret; do not commit them.
- Provisioner-created enterprise Sandbox Pods must receive both cases of the
  runtime proxy variables from `deer-flow-env` and mount the current
  `deer-flow-enterprise-ca` Secret at `/etc/deerflow-ca`. Mount the enterprise
  OpenSSL compatibility ConfigMap only inside generated Sandbox Pods so generic
  sandbox HTTPS clients can traverse the audited legacy proxy without a
  host/cluster-wide downgrade. Keep
  `DASHSCOPE_TLS_COMPAT=true` scoped to the image-generation DashScope client:
  it lowers only that client's OpenSSL RSA security level while preserving CA
  chain and hostname verification. Image generation is Bailian/DashScope-only
  by default and must not restore Gemini credentials or automatic Gemini
  fallback in generated Sandbox Pods. The audited enterprise network permits
  direct HTTPS to GitHub; keep those exact hosts/provider suffixes in both
  Sandbox `NO_PROXY` variants. Authenticated DashScope model API and Tavily
  search POSTs are reset on the audited direct route even when unauthenticated
  root GETs succeed, so `dashscope.aliyuncs.com`, Aliyun result storage, and
  `api.tavily.com` must remain on the verified enterprise proxy path. This is
  a narrow route policy, not a blanket external-network bypass. Do not restore empty
  proxy overrides in `config.yaml`, rely only on the Sandbox image's build-time
  CA snapshot, or replace either path with `verify=false`.
- Every provisioned enterprise Sandbox uses the release-pinned PPT-capable
  `SANDBOX_IMAGE`. Keep that image preloaded with the complete dependency set
  declared by the vendored upstream `ppt-master`, together with its documented
  undeclared runtime requirements (including Playwright/Chromium, LibreOffice,
  Pandoc, Cairo, FFmpeg, and CJK fonts). A change to
  `skills/public/ppt-master/requirements.txt` therefore requires rebuilding,
  distributing, importing, and smoke-testing the Sandbox image on every worker;
  updating only the Skills PVC is insufficient. Never install these dependencies
  on demand during a conversational run. Existing Sandbox Pods retain their old
  image and must be replaced before release acceptance tests.
- Long presentation workflows may legitimately invoke `bash` far more often
  than ordinary tools. Keep the enterprise runtime's per-tool loop-detection
  override bounded (currently warn at 120 and stop at 240) while retaining the
  global 30/50 limits for all other tools and the identical-call loop guard.
  The enterprise 1M-token chat model compacts at 85%, retains 40% of recent
  context, and caps summarizer input at 393216 tokens; do not disable
  summarization or remove output headroom to accommodate a single workflow.
- The shared Skills PVC sync must normalize `a+rX` only on the platform-owned
  `public` and `third_party` trees after archive extraction. Source archives can
  preserve `0640 root:root`, which Gateway can read but the sandbox file API
  cannot. Keep generated Sandbox mounts read-only and do not broaden permissions
  on user-owned `custom` Skills.
- Gateway's effective `extensions_config.json` must be the writable runtime copy
  under the existing user-data PVC. Keep the ConfigMap-mounted file as a
  read-only release seed; the init container adds missing shipped MCP/Skill
  defaults while preserving persisted administrator choices. Point
  `DEER_FLOW_EXTENSIONS_CONFIG_PATH` at the runtime copy so global Skill toggles
  and MCP settings survive Gateway restarts and rolling updates.
- DashScope video generation must use the video skill's persisted asynchronous
  `--submit-only` / `--status-only` workflow. The provider task can take many
  minutes, while the AIO Sandbox Nginx command channel may terminate a
  synchronous `/v1/bash/exec` request after roughly 60 seconds. Keep every
  status check in a separate short bash call and preserve the hidden task-state
  file so a transport timeout cannot cause a second billable submission. Do not
  treat a larger Nginx timeout or a foreground polling loop as the reliability
  mechanism. The enterprise video skill is DashScope-only and provider-opaque:
  agents must not enumerate environment variables, inspect credentials or SDKs,
  probe provider endpoints/models, construct direct HTTP requests, download
  substitute stock footage, or fall back to Gemini/MiniMax. Its asynchronous
  entry point ignores stale provider-selection variables, and CLI invocation
  without `--submit-only` or `--status-only` is rejected.
- The audited enterprise TLS proxy presents a CA-validated legacy RSA certificate that OpenSSL 3 rejects at its default security level. Keep `gateway-openssl-compat.yaml` scoped to the Gateway Pod through `OPENSSL_CONF`, retain `WEKNORA_BAILIAN_TLS_COMPAT=true` for the authenticated Bailian embedding/ASR adapter clients, and retain `TAVILY_TLS_COMPAT=true` for DeerFlow's Tavily-only verified HTTPS session. These clients construct explicit `DEFAULT:@SECLEVEL=1` SSL contexts while CA-chain and hostname verification remain enabled. Never replace this with `verify=false`/`--insecure` or a host/cluster-wide downgrade, and remove the compatibility flags after the proxy certificate is modernized.
- Keep the internal model host `model.ai.cnooc` in both Gateway proxy-bypass variables. Its streamed chat endpoints are direct-only in the audited enterprise network; sending them through the outbound proxy causes connection resets.
- Configure the internal DeepSeek chat endpoint with
  `deerflow.models.robust_openai:RobustChatOpenAI`. The enterprise gateway can
  return application errors as HTTP 200 envelopes with `choices=null`; this
  adapter preserves bounded `code`/`msg` diagnostics so the LLM error
  middleware can distinguish transient failures from authentication, quota,
  and context-limit errors. Null-choices failures receive at most one retry.
- Enterprise Qwen3 Embedding and Reranker requests pass through authenticated Gateway relays. Keep the upstream `CNOOC_RETRIEVAL_API_KEY` separate from the independently generated `WEKNORA_INTERNAL_MODEL_RELAY_KEY`, never expose either key to browsers, and keep the upstream client direct with `trust_env=false`. The Qwen3 embedding relay requests 1024-dimensional Matryoshka output; if the approved endpoint returns its native 4096 dimensions, Gateway truncates the prefix to 1024 and L2-normalizes it before returning the OpenAI-compatible response. Changing this dimension after documents are indexed requires a complete re-parse of every affected knowledge base.
- Keep the exact internal model hostname `model.ai.cnooc` and the authenticated in-cluster `gateway` relay Service in the WeKnora App's comma-separated `SSRF_WHITELIST_EXTRA` as well as both proxy-bypass variables. WeKnora correctly blocks private addresses by default; this narrow deployment allowlist permits only the approved internal endpoints without opening the surrounding private network or disabling SSRF validation.
- Route additional enterprise embedding/rerank providers through authenticated Gateway adapters when practical, so WeKnora continues to call the stable `gateway` Service while Gateway owns upstream credentials, CA trust, proxy routing, timeouts, and audit-safe error mapping. If a provider must be called directly from WeKnora, add only its reviewed exact hostname to `SSRF_WHITELIST_EXTRA`, `NO_PROXY`, and `no_proxy`; never pre-authorize a broad RFC1918 CIDR for anticipated providers.
- Keep the CNOOC data-lake OAuth endpoint in both Gateway proxy-bypass variables by hostname and audited service IP (`datalake.cnooc` and `10.77.78.223`). The SSO validation endpoint is direct-only in the enterprise network; sending it through the outbound proxy times out before the platform Token can be validated.
- The three AppForge-backed DataLake QA tools run only in Gateway and call the
  approved internal AppForge service directly. Keep `APPFORGE_API_KEY` and any
  workflow identity inputs, including the three explicit App IDs, in
  `deer-flow-env`, keep `trust_env=false`, and keep
  the exact AppForge address in both Gateway proxy-bypass variables. Never
  expose those values as model tool arguments or return raw upstream errors.
- AppForge knowledge results may contain image URLs served from the reviewed
  internal image-preview address. Retain that exact address in Gateway and
  Provisioner `NO_PROXY`/`no_proxy` so newly created Sandbox Pods download the
  original image over the direct internal route. Do not replace this narrow
  entry with a broad private-network CIDR.
- The WeKnora built-in Bailian ASR model points to the internal Gateway adapter because WeKnora 0.7.0 and `qwen3-asr-flash` use different request shapes. Keep the bare `gateway` Service name in the WeKnora App proxy-bypass variables; the adapter reuses the Bailian LLM key and never persists uploaded audio.
- The WeKnora built-in `text-embedding-v4` model also points to `http://gateway:8001/api/weknora/bailian/v1`. Gateway validates `EMBEDDING_API_KEY` and relays `/embeddings` through its scoped enterprise proxy/CA/OpenSSL compatibility path without persisting document text; do not restore a direct DashScope URL in the WeKnora database.
- Enterprise PDF/DOC/DOCX/PPT/PPTX parsing runs through `docker/mineru_adapter/`. It keeps searchable PDF/Word text on the bundled WeKnora parsers, merges native Word text/images with a temporary PDF visual pass, adds captioned full-page evidence for searchable pages containing images/vector figures, converts presentations to PDF, and sends scanned or mixed documents to the internal MinerU API. The PDF detector uses text length plus image-area coverage. If PDFium rejects a browser-readable document during routing inspection, the adapter first attempts the independent builtin parser and otherwise continues to MinerU; source-page rendering failures must not discard already recovered text. MinerU inline images and all visual evidence are extracted into WeKnora's persistent `images` contract with Markdown references. The adapter intentionally serializes every PDFium/DocReader parse in one process because those native libraries are not concurrency-safe; its threaded HTTP server keeps `/health` responsive and reports active/waiting parser counts while uploads queue. Do not bypass that coordinator or add in-process parser concurrency. Keep the upstream URL and Bearer token only in the runtime `deer-flow-mineru` Secret; documents indexed by an older parser release must be re-parsed before their missing images can be retrieved.
- Enterprise conversation uploads set `DEERFLOW_UPLOAD_DOCUMENT_FALLBACK_ENABLED=true`, so an unavailable or unsuccessful Adapter call falls back once to DeerFlow's local `convert_file_to_markdown()` path. Keep the Adapter as the primary parser; the fallback runs in Gateway and provides text recovery rather than the Adapter's complete OCR and visual-evidence contract. MarkItDown does not understand legacy binary `.doc`, so that one format may use the task sandbox's LibreOffice to create a request-scoped temporary DOCX before it re-enters `convert_file_to_markdown()`; never retain the temporary office file or add LibreOffice to Gateway. A user-visible processing warning is emitted only if both paths fail.
- The audited three-node enterprise layout runs three MinerU Adapter replicas with required hostname anti-affinity, one independent process per node. Each process remains internally serialized through `ParseCoordinator`. Keep the single WeKnora App replica at `WEKNORA_ASYNQ_CORE_CONCURRENCY=2` plus `WEKNORA_ASYNQ_SHARED_CONCURRENCY=1`, so at most three parser calls are active and later uploads stay in Redis/Asynq. Scaling WeKnora App replicas or either worker pool multiplies that limit and requires a matching parser-capacity review. Because anti-affinity leaves no fourth node, keep the adapter RollingUpdate at `maxSurge: 0`, `maxUnavailable: 1`, and retain its `minAvailable: 2` PodDisruptionBudget.

## Commands: Root vs. Module

**Root `make` targets drive the whole stack** (run from the repo root):

```bash
make setup       # Interactive setup wizard (recommended for new users)
make doctor      # Check configuration and system requirements
make support-bundle  # Generate redacted troubleshooting summary, AI issue draft, and optional zip
make config      # Generate local config files from the examples
make check       # Check that required tools are installed
make install     # Install all dependencies (frontend + backend + pre-commit hooks)
make dev         # Start all services with hot-reload (Gateway + Frontend + Nginx)
make start       # Start all services in production mode (local, optimized)
make stop        # Stop all running services
make up / down   # Build/stop the production Docker stack (browser at localhost:2026)
make docker-start / docker-stop / docker-logs   # Docker development environment
```

Run `make help` for the full list.

**Per-module commands drive a single module** (run inside that module):

```bash
# Backend (see backend/AGENTS.md for the full set)
cd backend && make dev        # Gateway API with reload (port 8001)
cd backend && make test       # Backend test suite
cd backend && make lint       # ruff check
cd backend && make format     # ruff format

# Frontend (see frontend/AGENTS.md for the full set)
cd frontend && pnpm dev       # Dev server with Turbopack (port 3000)
cd frontend && pnpm check     # Lint + type check (run before committing)
cd frontend && pnpm test      # Unit tests
```

Rule of thumb: **root `make` = the full application**; **`backend/Makefile` and `frontend/`
(`pnpm`) = per-module work.**

## Where to Go Next

- Backend work → **[backend/AGENTS.md](backend/AGENTS.md)**
- Frontend work → **[frontend/AGENTS.md](frontend/AGENTS.md)**
- Setup & install → **[Install.md](Install.md)**, **[CONTRIBUTING.md](CONTRIBUTING.md)**
- Project overview & usage → **[README.md](README.md)** (translations: `README_zh.md`,
  `README_ja.md`, `README_fr.md`, `README_ru.md`)
- Security policy → **[SECURITY.md](SECURITY.md)**
- Changes → **[CHANGELOG.md](CHANGELOG.md)**
- Cutting a release → **[RELEASING.md](RELEASING.md)**

## Cross-Cutting Conventions

These apply repo-wide; module guides own the module-specific detail.

- **Documentation update policy** — keep docs in sync with code: update `README.md` for
  user-facing changes and the relevant `AGENTS.md` for development/architecture changes in
  the same change set.
- **Test-driven development** — features and bug fixes ship with tests. Backend tests live
  in `backend/tests/` (TDD is mandatory there; see [backend/AGENTS.md](backend/AGENTS.md));
  frontend tests live in `frontend/tests/`.
- **Format before pushing** — run `make format` (backend) / `pnpm check` (frontend). Backend
  CI enforces `ruff format --check`, so formatting must be clean before a push.
