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

In the `k8s/local-pvc` stack, Nginx also serves WeKnora at
`/platform/knowledge-bases`. Keep that proxy on the `weknora-frontend` Kubernetes
service DNS name; never pin a ClusterIP, because recreating the cluster changes it.
The five built-in external-agent cards use same-origin server launch routes
under `/api/cnooc/external-agents/<agent>/launch`. The route reads the validated
HttpOnly `cnooc.platform_token` cookie and redirects only to the five canonical
HTTPS paths on `datalake.cnooc`, adding the current Token at redirect time.
Keep the strict allowlist, `no-store`/`no-referrer` response headers, and
server-only cookie access. The shared Nginx entry must route this exact API
prefix to the Frontend before its general `/api/` Gateway catch-all. Never
hardcode a Token, expose it to frontend
JavaScript, or restore the legacy `/agent-tools/*` reverse proxies.

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

Enterprise Kubernetes storage note:
- Keep `accessModes: [ReadWriteOnce]` in the PostgreSQL StatefulSet claim-template patch under `k8s/enterprise/storage-patches.yaml`. The patch replaces the matched claim-template item's `spec`; omitting `accessModes` renders an invalid StatefulSet.
- The enterprise WeKnora database intentionally uses the locally imported ARM64 PG16/pg_search image declared in `k8s/enterprise/kustomization.yaml`. Keep its `imagePullPolicy: Never`, `shared_preload_libraries=pg_search`, and nested `PGDATA`; the upstream ParadeDB PG17 image triggers `SIGILL` on the audited datax CPUs.
- Keep the enterprise Frontend's `WEKNORA_FRONTEND_URL` set to the same-origin path `/platform/knowledge-bases`; a loopback or node-pinned browser URL breaks SSO or becomes invalid when the public endpoint changes.
- The patched WeKnora UI must fetch `/api/weknora/sso-session` through a same-origin relative URL in both `config.js` and the `index.html` fallback. These ConfigMap files are mounted with `subPath`, so restart `weknora-frontend` after changing them.
- The enterprise WeKnora App trusts outbound HTTPS through the runtime-created `deer-flow-enterprise-ca` Secret. Keep the projected `/etc/deerflow-ca/ca-bundle.crt` mount and CA environment variables in `k8s/enterprise/runtime-patches.yaml`; create the Secret before apply and restart the Deployment after CA rotation so long-lived HTTP clients reload trust state.
- Apply `k8s/enterprise/weknora-mineru-defaults.sql` after creating a new WeKnora database. Its trigger routes missing PDF/PPT/PPTX rules to the in-cluster adapter for knowledge bases created through the native WeKnora UI while preserving every explicit file-type parser choice.
- Apply `k8s/weknora/enterprise-model-feature-defaults.sql` after the built-in model rows exist. It makes internal `DeepSeek-V4-Flash` the enterprise KnowledgeQA default, normalizes existing knowledge bases and agents to the dedicated embedding/rerank/VLM/ASR built-ins, and installs insert/model-write guards so later objects start ready without per-user setup. Users can still change object-level settings after creation.
- The enterprise Gateway uses the runtime proxy for external model providers, but its `NO_PROXY` and `no_proxy` values must retain the bare Kubernetes Service names (especially `weknora-app`) as well as `.svc` and `.cluster.local`. Otherwise Gateway-to-WeKnora calls are sent through the enterprise proxy and surface as HTTP 302/502 failures. Keep proxy URLs and credentials in the runtime-created `deer-flow-env` Secret; do not commit them.
- The audited enterprise TLS proxy presents a CA-validated legacy RSA certificate that OpenSSL 3 rejects at its default security level. Keep `gateway-openssl-compat.yaml` scoped to the Gateway Pod through `OPENSSL_CONF`, and retain `WEKNORA_BAILIAN_TLS_COMPAT=true` so only the authenticated Bailian embedding/ASR adapter clients construct an explicit `DEFAULT:@SECLEVEL=1` SSL context. Chain and hostname verification remain enabled. Never replace this with `verify=false`/`--insecure` or a host/cluster-wide downgrade, and remove it after the proxy certificate is modernized.
- Keep the internal model host `model.ai.cnooc` in both Gateway proxy-bypass variables. Its streamed chat endpoints are direct-only in the audited enterprise network; sending them through the outbound proxy causes connection resets.
- Keep the exact internal model hostname `model.ai.cnooc` and the authenticated in-cluster `gateway` relay Service in the WeKnora App's comma-separated `SSRF_WHITELIST_EXTRA` as well as both proxy-bypass variables. WeKnora correctly blocks private addresses by default; this narrow deployment allowlist permits only the approved internal endpoints without opening the surrounding private network or disabling SSRF validation.
- Route additional enterprise embedding/rerank providers through authenticated Gateway adapters when practical, so WeKnora continues to call the stable `gateway` Service while Gateway owns upstream credentials, CA trust, proxy routing, timeouts, and audit-safe error mapping. If a provider must be called directly from WeKnora, add only its reviewed exact hostname to `SSRF_WHITELIST_EXTRA`, `NO_PROXY`, and `no_proxy`; never pre-authorize a broad RFC1918 CIDR for anticipated providers.
- Keep the CNOOC data-lake OAuth endpoint in both Gateway proxy-bypass variables by hostname and audited service IP (`datalake.cnooc` and `10.77.78.223`). The SSO validation endpoint is direct-only in the enterprise network; sending it through the outbound proxy times out before the platform Token can be validated.
- The WeKnora built-in Bailian ASR model points to the internal Gateway adapter because WeKnora 0.7.0 and `qwen3-asr-flash` use different request shapes. Keep the bare `gateway` Service name in the WeKnora App proxy-bypass variables; the adapter reuses the Bailian LLM key and never persists uploaded audio.
- The WeKnora built-in `text-embedding-v4` model also points to `http://gateway:8001/api/weknora/bailian/v1`. Gateway validates `EMBEDDING_API_KEY` and relays `/embeddings` through its scoped enterprise proxy/CA/OpenSSL compatibility path without persisting document text; do not restore a direct DashScope URL in the WeKnora database.
- Enterprise PDF/PPT/PPTX parsing runs through `docker/mineru_adapter/`. It converts presentations to PDF, keeps fully searchable PDFs on the bundled WeKnora builtin parser, and sends scanned or mixed PDFs to the internal MinerU API. The PDF detector uses text length plus image-area coverage and replaces inline Base64 images in MinerU markdown before embedding. Keep the upstream URL and Bearer token only in the runtime `deer-flow-mineru` Secret; other file types retain WeKnora's native parser rules.

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
