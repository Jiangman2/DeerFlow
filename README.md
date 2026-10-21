# 🦌 DeerFlow - 2.0

Enterprise GLM-5.3-Flash stream completion now checks the terminal choice and
raw `[DONE]` marker before accepting a response. Truncated streams stop with a
visible warning and no automatic tool execution or retry. Fixed thinking and
single-image support retain the supplied 5.3 contract. See the
[GLM contract and candidate verification](docs/enterprise-glm-modes.md);
offline qualification is separate from server acceptance and deployment.
The October 21 four-stage field canary and all five node preloads passed;
the separately qualified image-only activation still requires an operator
task-drain window and subsequent 70-worker runtime acceptance.

Enterprise DeepSeek-V4.1-Flash is available as an opt-in
[model profile](deploy/enterprise/deepseek-v4.1-flash.model.yaml). Append it to
the existing server model list and inject `DEEPSEEK_V4_1_FLASH_API_KEY` from a
Gateway Secret. The selector reads `/api/models`; no frontend rebuild is needed.
Flash mode sends `enable_thinking: false`, thinking mode sends `true`; image and
tool inputs use the existing Chat Completions adapter. Live provider acceptance
and a Gateway configuration rollout are required before it appears online.
Preserve the existing default model, GLM, OCR and isolated PPT provider.
See [enterprise preparation and acceptance](docs/enterprise-deepseek-v41.md)
for the hidden-key entry tool and the distinction between preparation and rollout.
Wait for an existing Gateway rollout to finish before this preparation. Failed
read-only preflight reports can be resumed with an explicitly bound recovery
package; retain the original report and once marker.
For an initial provider HTTP 503, an exact report-bound retry can be prepared;
the model remains unavailable in the selector until all live checks pass.
For the October 21 HTTP-503 incident, the preparation guide includes a
credential-free application record and a read-only historical receipt collector;
the existing reports do not identify which upstream component returned 503.

Homepage portal footer: total/today model usage remains supplied by Haineng;
today's users/tasks use the business dashboard's active-user/started-run
definitions for Shanghai midnight through the current minute. The authenticated
`/api/home/statistics` endpoint returns only those two aggregate counts, not
administrator details. Contact shows the general agent (`sh_tyznt@cnooc.com.cn`)
and AI platform (`sh_ai@cnooc.com.cn`) emails; feedback uses the former. The
Haineng Work support group uses the supplied October 12 QR asset.

Enterprise SkillHub requires authentication for skill discovery, labels,
version/file inspection and downloads. Browser access uses the current DeerFlow
session plus SkillHub authorization; API clients use a validated native token.
The proxy strips caller-supplied forwarding authority and framework version
headers on the new SkillHub routes.
The workspace shows a recovery screen while authentication is unavailable.
Deployment and checks: [SkillHub registry authentication](docs/skillhub-registry-auth-remediation.md).

Digital Employee Square governance: signed-in `admin` accounts can edit public
profiles, change global ordering, and withdraw any employee (including bundled
external/contest cards). Ordinary accounts can still publish new employees and
withdraw their own published snapshots, but cannot edit public profiles/order
or withdraw another publisher's employees. Private employee editing is unchanged.
Withdrawals are reversible presentation changes, not deletion of private agents.
Administrators can show withdrawn employees and restore them. Public overrides
are persisted in the application database (`digital_employee_square`, migration
`0020_digital_employee_square`); no role assignment UI or client-side allowlist
is introduced. Assigning `admin` grants existing platform-wide administrator
rights, not only these employee permissions. Original publishers can republish
their withdrawn snapshots, even after admin withdrawal; successful publication
restores visibility and refreshes the private snapshot while preserving admin
presentation/order overrides. Overwriting an online snapshot or another user's
snapshot remains restricted to admins.

## Enterprise portal candidate (2026-10-13, not deployed)

The welcome quick actions remove Surprise and Create. Proofreading and Translation
are direct links beside the original four shortcuts; PPT Assistant is an external
link inside PPT Generation, alongside its unchanged four editable Skill prompts.
More holds the remaining four tools in a compact two-column menu. All seven approved
destinations preserve their URL fragments and open in a new tab. Mobile keeps a
single horizontally scrollable action rail. New chat also offers a user-guide link.
Digital-employee discovery shows administrator-curated hot entries. The one-time
release snapshot marks all then-existing public employees hot; newly published
employees also default to hot as soon as they enter the public catalogue. Private
drafts remain private. Administrator removal survives refresh, restart and the
same publisher republishing the same employee. Show all clears discovery filters. Administrators
manage hot status from each public employee card; profile creator names come from
trusted account display names, with an explicit unknown fallback.

Hot flags use a separate shared persistent JSON file, not a new SQL table. Set
`DEERFLOW_EMPLOYEE_FEATURED_PATH` to an operator-provisioned private directory's
absolute JSON path, identical across Gateway replicas. Before admitting writes,
verify cross-node locking/atomic rename and take a frozen catalogue snapshot with
`python -m app.gateway.employee_directory_seed --initialize --actor RELEASE_ID`.
This explicit one-time step must run with the production catalogue configuration
while publication is paused; repeats preserve administrator choices. Missing or
corrupt state fails visibly, never reseeds from GET or startup. Keep and back up
this directory across releases; the file retains the latest 256 change records.
Do not reuse the Work channel configuration or a container-local path. This does
not remove migration requirements from any separately combined PPT feature.
The GLM-5.3-Flash candidate profile in
`deploy/enterprise/glm-5.3-flash.model.yaml` requires a server-side Secret and real
text/tool/image acceptance; it does not change the current default model. The
browser private-origin policy is opt-in, not a production network diagnosis or a
blanket allowance for private networks. No code change here deploys production.
GLM-5.3-Flash documentation section 6.2 explicitly requires always-on thinking;
GLM-5.2's disabled-thinking option does not apply. The speculative switchable
candidate was withdrawn before deployment. Single-image vision is configured;
GLM-OCR remains a separate service. See `docs/enterprise-glm-modes.md`.

Candidate operations (inside the candidate Backend Python 3.12 environment,
with the verified shared mount; not the host's Python 3.7):

```bash
python -m app.gateway.employee_directory_maintenance status
python -m app.gateway.employee_directory_maintenance backup --output /VERIFIED_PRIVATE_DIRECTORY/hot-before.json
python -m app.gateway.employee_directory_maintenance restore \
  --from-file /VERIFIED_PRIVATE_DIRECTORY/hot-before.json --sha256 VERIFIED_BACKUP_SHA256 \
  --expected-revision CURRENT_REVISION --actor RELEASE_ID \
  --backup-current /VERIFIED_PRIVATE_DIRECTORY/hot-before-restore.json
```

Replace the uppercase placeholders only with audited operator values. Backups
are exclusive private files and never overwrite earlier evidence. Restore checks
the supplied backup hash and live revision under the same lock as administrator
writes, saves a preimage, and creates increasing per-entry versions. It must not
copy old CAS versions back into service. Missing/corrupt live state fails closed
and needs separate operator-led recovery; this command never reseeds it. Keep
private backups out of Git and public artifact delivery.

The opt-in GLM acceptance command is
`python -m app.gateway.enterprise_glm_probe --run` **on the enterprise network**.
It reads `GLM_5_3_FLASH_API_KEY` from the server environment, optionally an approved
`GLM_5_3_FLASH_CA_BUNDLE`, and performs at most four synthetic requests (text,
function call, function-result continuation, one generated red image). It prints
only bounded receipts and provider-reported usage, not credentials, reasoning or
answers, executes no business tool and never automatically retries. Each call
defaults to a 120-second total deadline; reasoning tokens are not capped by
`max_tokens`. TLS and hostname verification stay enabled, redirects are disabled,
and this diagnostic uses direct internal routing, not inherited proxy variables.
Qualify the actual candidate deployment's routing separately; a direct probe is
not proof that a differently configured proxy route works. Local SDK/SSE tests
use mock transports only: this enterprise model is not reachable from the local
development machine. Do not run the supplied raw examples with embedded keys.

Release scope for these portal additions is Frontend plus Gateway, shared hot
configuration provisioning and the additive GLM model/Secret configuration. No
new database migration belongs to these additions. The separately prepared PPT
research/confirmation batch is not yet integrated into this workspace; its
Runner, Agent Host and database requirements remain separate. Before combining
or packaging, refresh production fingerprints, reconcile both deltas against
that baseline, verify the shared filesystem across replicas and run candidate
image/intranet acceptance. The deferred browser investigation must not be
silently marked fixed or enabled by guessing private ranges or weakening TLS.

Server diagnostics are bundled by `deploy/enterprise/package-acceptance.py`.
Read `deploy/enterprise/ACCEPTANCE-RUNBOOK.txt` before running the package on
datax-01. It supports Python 3.7 at the host and uses one-shot Python 3.12 children
in owned Gateway replicas. `--glm` explicitly allows up to four synthetic live
provider calls with a hidden terminal key; `--shared-fixture` creates only a new
private directory on a verified common RWX/NFS mount. Neither option initializes
the real hot store or changes channel credentials, workloads, images or schemas.
Failure to verify the shared parent remains a blocker, never an automatic chmod.
Collection completion is not acceptance or release approval; candidate-image,
authenticated-browser and integrated PPT qualification remain separate gates.
The October 14 r4 storage retest uses bounded stdin/stdout control acknowledgements,
not shared-directory marker visibility, to synchronize lock holders and writers.
Both writers must report ready before either receives GO; disconnects and invalid
control messages cannot authorize a write. Real shared JSON reads, flock, CAS and
atomic-replace checks remain unchanged, with no cache flush or eventual-success
retry. Partial phase receipts are retained on failure. `--shared-only` requires
an explicit safe shared parent, samples two Gateway nodes, and skips GLM and website
probes; it does not claim a fresh all-70 source audit. Existing runtime-directory
permissions are never changed. A passing retest is not candidate-image approval.

The October 14 r3 diagnostic accepts identical static directory `subPath` mounts
only after both replicas pass a read-only directory/permission preflight; single
files, dynamic subpaths, mismatched shared identities and unsafe parents remain
blocked. Its GLM request uses the supplied streaming example's `max_tokens=1000`,
keeps the wall-time/response bounds, and reports `length` as an incomplete output
limit rather than a capability verdict. Later uncalled stages remain explicitly
`NOT_ATTEMPTED`; reasoning is not printed or assumed to fit that token parameter.
The uploaded r2 root-page GET succeeded with TLS enabled, while application policy
denied the private target. This is not authenticated browser acceptance, and no
private-origin exception is enabled automatically by these diagnostics.

Linux regression now checks real channel-store process locking, stale-writer
conflicts, bounded lock timeout and failure-atomic saves. Permission setup failure
must prevent publication. A different boundary applies after a successful save:
a failed local channel restart returns 503 without undoing the committed shared
configuration. Historical tests have been aligned with those existing semantics.
Artifact downloads additionally recognize known active extensions independently
of the OS MIME database, covering minimal Linux images with no XHTML mapping.
Per-workspace output-mask patterns retain their existing boundary semantics but
use CPython's uncached regex compiler, so process-global regex caches do not
retain workspace-specific host paths. Targeted Linux regression passes; the
full suite is not accepted yet: the temporary QA image's framework dependencies
do not match the source lock and a later no-progress run was interrupted. Do not
weaken the exact-version test or use scoped passes as full release acceptance.

The 2026-10-04 multi-Gateway stop-routing and interrupted-history repairs are documented in [the production source reconciliation](docs/production-gateway-reconciliation-20261004.md). Git publication does not deploy or restart services.

The 7·16 enterprise branch's 2026-09-28 production source alignment and its
rebuild limits are documented in
[`release/production-build-alignment-20260928/README.md`](release/production-build-alignment-20260928/README.md).
The captured WeKnora UI 0.8.0 source and two-stage production candidate build
are under [`vendor/weknora-ui-20260913-r5/`](vendor/weknora-ui-20260913-r5/).

English | [中文](./README_zh.md) | [日本語](./README_ja.md) | [Français](./README_fr.md) | [Русский](./README_ru.md)

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](./backend/pyproject.toml)
[![Node.js](https://img.shields.io/badge/Node.js-22%2B-339933?logo=node.js&logoColor=white)](./Makefile)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

<a href="https://trendshift.io/repositories/14699" target="_blank"><img src="https://trendshift.io/api/badge/repositories/14699" alt="bytedance%2Fdeer-flow | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>

> On February 28th, 2026, DeerFlow claimed the 🏆 #1 spot on GitHub Trending following the launch of version 2. Thanks a million to our incredible community — you made this happen! 💪🔥

DeerFlow (**D**eep **E**xploration and **E**fficient **R**esearch **Flow**) is an open-source **super agent harness** that orchestrates **sub-agents**, **memory**, and **sandboxes** to do almost anything — powered by **extensible skills**.

https://github.com/user-attachments/assets/a8bcadc4-e040-4cf2-8fda-dd768b999c18

> [!NOTE]
> **DeerFlow 2.0 is a ground-up rewrite.** It shares no code with v1. If you're looking for the original Deep Research framework, it's maintained on the [`1.x` branch](https://github.com/bytedance/deer-flow/tree/main-1.x) — contributions there are still welcome. Active development has moved to 2.0.

## Official Website

Learn more and see **real demos** on our [**official website**](https://deerflow.tech).

## Sister Projects

<img width="446" height="280" alt="image" align="middle" src="https://github.com/user-attachments/assets/077edef4-d560-41af-bb0d-d0a5f14fcc20" />

- [**LLM Space**](https://github.com/deer-flow/llm-space) - Meet our secret weapon behind DeerFlow — one desktop tool to prototype agent ideas, inspect each harness step, replay failures, and benchmark performance.

## Coding Plan from ByteDance Volcengine

- We strongly recommend using Doubao-Seed-2.0-Code, DeepSeek v3.2 and Kimi 2.5 to run DeerFlow
- [Learn more](https://www.byteplus.com/en/activity/codingplan?utm_campaign=deer_flow&utm_content=deer_flow&utm_medium=devrel&utm_source=OWO&utm_term=deer_flow)
- [中国大陆地区的开发者请点击这里](https://www.volcengine.com/activity/codingplan?utm_campaign=deer_flow&utm_content=deer_flow&utm_medium=devrel&utm_source=OWO&utm_term=deer_flow)

## InfoQuest

DeerFlow has newly integrated the intelligent search and crawling toolset independently developed by BytePlus--[InfoQuest (supports free online experience)](https://docs.byteplus.com/en/docs/InfoQuest/What_is_Info_Quest)

<a href="https://docs.byteplus.com/en/docs/InfoQuest/What_is_Info_Quest" target="_blank">
  <img
    src="https://sf16-sg.tiktokcdn.com/obj/eden-sg/hubseh7bsbps/20251208-160108.png"   alt="InfoQuest_banner"
  />
</a>

---

## Table of Contents

- [🦌 DeerFlow - 2.0](#-deerflow---20)
  - [Official Website](#official-website)
  - [Coding Plan from ByteDance Volcengine](#coding-plan-from-bytedance-volcengine)
  - [InfoQuest](#infoquest)
  - [Table of Contents](#table-of-contents)
  - [One-Line Agent Setup](#one-line-agent-setup)
  - [Quick Start](#quick-start)
    - [Configuration](#configuration)
    - [Running the Application](#running-the-application)
      - [Deployment Sizing](#deployment-sizing)
      - [Option 1: Docker (Recommended)](#option-1-docker-recommended)
      - [Option 2: Local Development](#option-2-local-development)
    - [Advanced](#advanced)
      - [Sandbox Mode](#sandbox-mode)
      - [MCP Server](#mcp-server)
      - [IM Channels](#im-channels)
      - [LangSmith Tracing](#langsmith-tracing)
      - [Langfuse Tracing](#langfuse-tracing)
      - [Monocle Tracing](#monocle-tracing)
      - [Using Multiple Providers](#using-multiple-providers)
  - [From Deep Research to Super Agent Harness](#from-deep-research-to-super-agent-harness)
  - [Core Features](#core-features)
    - [Skills \& Tools](#skills--tools)
      - [Claude Code Integration](#claude-code-integration)
    - [Session Goals](#session-goals)
    - [Manual Context Compaction](#manual-context-compaction)
    - [Sub-Agents](#sub-agents)
    - [Sandbox \& File System](#sandbox--file-system)
    - [Context Engineering](#context-engineering)
    - [Long-Term Memory](#long-term-memory)
  - [Recommended Models](#recommended-models)
  - [Embedded Python Client](#embedded-python-client)
  - [Scheduled Tasks](#scheduled-tasks)
  - [Terminal Workbench (TUI)](#terminal-workbench-tui)
  - [Documentation](#documentation)
  - [⚠️ Security Notice](#️-security-notice)
    - [Improper Deployment May Introduce Security Risks](#improper-deployment-may-introduce-security-risks)
    - [Security Recommendations](#security-recommendations)
  - [Contributing](#contributing)
  - [License](#license)
  - [Acknowledgments](#acknowledgments)
    - [Key Contributors](#key-contributors)
  - [Star History](#star-history)

## One-Line Agent Setup

If you use Claude Code, Codex, Cursor, Windsurf, or another coding agent, you can hand it the setup instructions in one sentence:

```text
Help me clone DeerFlow if needed, then bootstrap it for local development by following https://raw.githubusercontent.com/bytedance/deer-flow/main/Install.md
```

That prompt is intended for coding agents. It tells the agent to clone the repo if needed, choose Docker when available, and stop with the exact next command plus any missing config the user still needs to provide.

## Quick Start

### Configuration

Optional per-model [`request_admission`](backend/docs/CONFIGURATION.md#model-request-admission)
paces calls against provider request-per-minute limits. It is disabled unless
explicitly configured.

1. **Clone the DeerFlow repository**

   ```bash
   git clone https://github.com/bytedance/deer-flow.git
   cd deer-flow
   ```

2. **Run the setup wizard**

   From the project root directory (`deer-flow/`), run:

   ```bash
   make setup
   ```

   This launches an interactive wizard that guides you through choosing an LLM provider, optional web search, and execution/safety preferences such as sandbox mode, bash access, and file-write tools. It generates a minimal `config.yaml` and writes your keys to `.env`. Takes about 2 minutes.

   The wizard also lets you configure an optional web search provider, or skip it for now.

   Run `make doctor` at any time to verify your setup and get actionable fix hints.
   If you are opening a GitHub issue about a local setup or runtime problem, run
   `make support-bundle`. The command prints reporter next steps, writes a
   `*-issue-summary.md` file to paste into the issue, a `*-issue-draft.md` file
   for AI-assisted issue filing, and an optional evidence zip under
   `.deer-flow/support-bundles/`. If an AI assistant files the issue, start from
   the draft and replace every REQUIRED placeholder instead of inventing missing
   facts. Attach the zip only if a maintainer asks for it, or if the summary
   alone is not enough. Maintainers and AI triage tools can start with
   `triage.json`; the bundle includes redacted diagnostics and file manifests
   only, and does not include `.env`, raw conversation messages, or user file
   contents.

   > **Advanced / manual configuration**: If you prefer to edit `config.yaml` directly, run `make config` instead to copy the full template. Optional dependency auto-detection accepts UTF-8 configuration files with or without a byte-order mark (BOM), and installs the Ollama extra when a configured model uses `langchain_ollama`. See `config.example.yaml` for the complete reference including CLI-backed providers (Codex CLI, Claude Code OAuth), OpenRouter, Responses API, subagent runtime caps such as `subagents.max_total_per_run`, and more.

   <details>
   <summary>Manual model configuration examples</summary>

   ```yaml
   models:
     - name: gpt-4o
       display_name: GPT-4o
       use: langchain_openai:ChatOpenAI
       model: gpt-4o
       api_key: $OPENAI_API_KEY

     - name: openrouter-gemini-2.5-flash
       display_name: Gemini 2.5 Flash (OpenRouter)
       use: langchain_openai:ChatOpenAI
       model: google/gemini-2.5-flash-preview
       api_key: $OPENROUTER_API_KEY
       base_url: https://openrouter.ai/api/v1

     - name: gpt-5-responses
       display_name: GPT-5 (Responses API)
       use: langchain_openai:ChatOpenAI
       model: gpt-5
       api_key: $OPENAI_API_KEY
       use_responses_api: true
       output_version: responses/v1

     - name: qwen3.7-plus
       display_name: Qwen3.7-Plus
       use: deerflow.models.robust_openai:RobustChatOpenAI
       model: qwen3.7-plus
       api_key: $DASHSCOPE_API_KEY
       base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
       max_tokens: 65536
       supports_thinking: true
       supports_reasoning_effort: true
       when_thinking_enabled:
         extra_body:
           enable_thinking: true
           preserve_thinking: true
       when_thinking_disabled:
         extra_body:
           enable_thinking: false

     - name: qwen3-32b-vllm
       display_name: Qwen3 32B (vLLM)
       use: deerflow.models.vllm_provider:VllmChatModel
       model: Qwen/Qwen3-32B
       api_key: $VLLM_API_KEY
       base_url: http://localhost:8000/v1
       supports_thinking: true
       when_thinking_enabled:
         extra_body:
           chat_template_kwargs:
             enable_thinking: true
   ```

   OpenRouter and similar OpenAI-compatible gateways should be configured with `langchain_openai:ChatOpenAI` plus `base_url`. If you prefer a provider-specific environment variable name, point `api_key` at that variable explicitly (for example `api_key: $OPENROUTER_API_KEY`).

   Enterprise-compatible gateways that return business failures as HTTP 200
   payloads with `choices: null`, `code`, and `msg` can instead use
   `deerflow.models.robust_openai:RobustChatOpenAI`. DeerFlow then preserves a
   bounded diagnostic, retries unknown transient null responses once, and
   avoids retrying recognized authentication, quota, or context-limit errors.
   For streamed endpoints, isolated malformed scalar SSE frames are discarded
   only when a later standard chunk proves a legal terminal finish; a truncated
   stream remains a typed failure and receives at most three fresh request
   attempts. Raw malformed frame content is never logged or promoted to an
   assistant message or tool call. If that endpoint emits cumulative token
   usage on every streamed chunk, set `cumulative_stream_usage: true` on the
   model so DeerFlow converts each completion's snapshots to deltas before
   LangChain aggregates them. Leave the option disabled for providers that
   already emit per-chunk usage. DeepSeek-V4-Flash endpoints that emit
   `delta.reasoning_content` can use the same adapter; it retains that field
   for the UI and replays it for later tool-call turns. Configure its thinking
   switch explicitly as `when_thinking_enabled.extra_body.enable_thinking:
   true` and `when_thinking_disabled.extra_body.enable_thinking: false`.
   Model Studio Qwen3.7-Plus uses the same OpenAI-compatible Chat Completions
   transport at `https://dashscope.aliyuncs.com/compatible-mode/v1`; it is not
   a `/v1/responses` configuration. Set `preserve_thinking: true` while
   thinking is enabled so the provider can retain its reasoning across a
   tool-call continuation. If its DashScope traffic must use an egress proxy,
   inject `HTTP_PROXY`/`HTTPS_PROXY` into the Gateway container from the runtime
   Secret; do not put `trust_env` in the model configuration, because that is
   not an OpenAI request field. The local-PVC test overlay is
   `k8s/local-pvc/qwen3.7-dashscope-proxy-test-patch.yaml`.

   If an older deployment already persisted impossible cumulative-stream
   totals, use the audited and reversible maintenance procedure in
   [Cumulative stream token-usage repair](docs/token-usage-cumulative-repair.md).
   The tool defaults to a read-only audit and never rewrites `run_events`.

   To route OpenAI models through `/v1/responses`, keep using `langchain_openai:ChatOpenAI` and set `use_responses_api: true` with `output_version: responses/v1`.

   For vLLM 0.19.0, use `deerflow.models.vllm_provider:VllmChatModel`. For Qwen-style reasoning models, DeerFlow toggles reasoning with `extra_body.chat_template_kwargs.enable_thinking` and preserves vLLM's non-standard `reasoning` field across multi-turn tool-call conversations. Legacy `thinking` configs are normalized automatically for backward compatibility. Reasoning models may also require the server to be started with `--reasoning-parser ...`. If your local vLLM deployment accepts any non-empty API key, you can still set `VLLM_API_KEY` to a placeholder value.

   CLI-backed provider examples:

   ```yaml
   models:
     - name: gpt-5.4
       display_name: GPT-5.4 (Codex CLI)
       use: deerflow.models.openai_codex_provider:CodexChatModel
       model: gpt-5.4
       supports_thinking: true
       supports_reasoning_effort: true

     - name: claude-sonnet-4.6
       display_name: Claude Sonnet 4.6 (Claude Code OAuth)
       use: deerflow.models.claude_provider:ClaudeChatModel
       model: claude-sonnet-4-6
       max_tokens: 4096
       supports_thinking: true
   ```

   - Codex CLI reads `~/.codex/auth.json`
   - Claude Code accepts `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_CREDENTIALS_PATH`, or `~/.claude/.credentials.json`
   - ACP agent entries are separate from model providers — if you configure `acp_agents.codex`, point it at a Codex ACP adapter such as `npx -y @zed-industries/codex-acp`
   - On macOS, export Claude Code auth explicitly if needed:

   ```bash
   eval "$(python3 scripts/export_claude_code_oauth.py --print-export)"
   ```

   API keys can also be set manually in `.env` (recommended) or exported in your shell:

   ```bash
   OPENAI_API_KEY=your-openai-api-key
   TAVILY_API_KEY=your-tavily-api-key
   ```

   </details>

### Running the Application

#### Deployment Sizing

Use the table below as a practical starting point when choosing how to run DeerFlow:

| Deployment target                        | Starting point                    | Recommended        | Notes                                                                                                      |
| ---------------------------------------- | --------------------------------- | ------------------ | ---------------------------------------------------------------------------------------------------------- |
| Local evaluation / `make dev`            | 4 vCPU, 8 GB RAM, 20 GB free SSD  | 8 vCPU, 16 GB RAM  | Good for one developer or one light session with hosted model APIs. `2 vCPU / 4 GB` is usually not enough. |
| Docker development / `make docker-start` | 4 vCPU, 8 GB RAM, 25 GB free SSD  | 8 vCPU, 16 GB RAM  | Image builds, bind mounts, and sandbox containers need more headroom than pure local dev.                  |
| Long-running server / `make up`          | 8 vCPU, 16 GB RAM, 40 GB free SSD | 16 vCPU, 32 GB RAM | Preferred for shared use, multi-agent runs, report generation, or heavier sandbox workloads.               |

- These numbers cover DeerFlow itself. If you also host a local LLM, size that service separately.
- Linux plus Docker is the recommended deployment target for a persistent server. macOS and Windows are best treated as development or evaluation environments.
- If CPU or memory usage stays pinned, reduce concurrent runs first, then move to the next sizing tier.

#### Option 1: Docker (Recommended)

For the separate Kubernetes PPT Bridge, keep its Alembic migration chain
compatible with the shared database, including revisions supplied to Gateway
by code overlays. The `ppt-master-runner` runtime profile skips global startup
run recovery so restarting the bridge does not mark a live ordinary Gateway
run with a NULL lease as failed. Stale PPT records are not automatically
reclaimed by that guard; full multi-instance recovery still requires safe run
ownership configuration.

**Development** (hot-reload, source mounts):

```bash
make docker-init    # Pull sandbox image (only once or when image updates)
make docker-start   # Start services (auto-detects sandbox mode from config.yaml)
```

`make docker-start` starts `provisioner` only when `config.yaml` uses provisioner mode (`sandbox.use: deerflow.community.aio_sandbox:AioSandboxProvider` with `provisioner_url`).

In provisioner mode, values declared under `config.yaml -> sandbox.environment`
are forwarded to sandbox commands through the sandbox API. This is the supported
way to make configured provider variables available to PPT, podcast, and music
scripts; use an all-in-one-sandbox image with `/v1/bash/exec` support (1.9.3+).

Image and video generation can instead be exposed as Gateway-local tools by
setting `DEERFLOW_IMAGE_GENERATION_API_URL` and/or
`DEERFLOW_VIDEO_GENERATION_API_URL`. The image endpoint uses the documented
OpenAI-style `b64_json` response. The video endpoint uses a durable asynchronous
job ID, bounded polling, and `/content` MP4 download. Generated files are
validated, written atomically into the current thread's outputs, and published
without a Sandbox shell command. The clients use `trust_env=false`, reject
unsafe reference URLs, and never make an arbitrary download host trusted; add a
known result host explicitly with `DEERFLOW_VIDEO_GENERATION_DOWNLOAD_HOSTS`.
The optional `DEERFLOW_MEDIA_GENERATION_API_KEY` is sent only as a Bearer token
to those operator-configured endpoints.

The remaining bundled provider scripts use `DASHSCOPE_API_KEY` by default for
Qwen-TTS (`qwen3-tts-flash`) and Fun-Music (`fun-music-v1`). The vendored PPT
workflow retains its own image-provider contract. The Fun-Music model requires
separate Model Gallery access in China (Beijing).

Shared or production AIO deployments should also set
`sandbox.sandbox_api_key: $SANDBOX_API_KEY` and inject the same secret into each
Sandbox runtime. DeerFlow authenticates AIO requests and readiness checks with
an `Authorization: Bearer` header.

If a model emits malformed JSON for a tool call, DeerFlow returns the parser
error to the model and retries that call once. A second malformed response is
shown as a visible error instead of silently ending the task or waiting for
unrelated user input.
Remote/AIO sandbox shell commands also preserve their real process exit code.
A non-zero exit is returned to the model as an explicit error with guidance to
inspect the active skill/reference or the command's `--help`, preventing failed
CLI calls from being mistaken for successful progress.

The administrator business dashboard is the consolidated operations view. It
shows database-backed user rankings, model/agent activity, external-agent launch
clicks, tokens, failures, slow or high-token runs, and feedback. Slow and
high-token entries link back to their original chats. Slow runs and high-token
runs use five-row paginated, scrollable lists. The all-history failed-run table
supports account/name filtering and opens a read-only diagnostic view with the
stored error, failed node, tool calls, and events. The former standalone
observability page redirects to the dashboard; incomplete health heuristics are
not presented as live monitoring. Headline data refreshes every 15 seconds.
Success rates and durations use completed runs only, daily buckets use
Asia/Shanghai calendar dates, and feature/artifact counters are derived from
structured tool events rather than assistant prose or serialized prompts.
The headline conversation count and the activity bars both use creation-ledger
records inside the selected time range, so their totals reconcile even if users
later delete conversations. The headline detail keeps the operational values
concise: completed runs, running runs, and conversations that still exist.
Longer ranges automatically use weekly, roughly monthly, or roughly yearly
activity buckets, while summary and run rankings accept the same arbitrary
custom date range. Conversation and active-user bars share one numeric scale;
active users are de-duplicated inside each displayed time bucket, while the
headline active-user value is de-duplicated across the complete selected
range. A paginated registered-user section lists every account and
expands into all currently existing
conversations with their success, failure, active-run, and total-run counts;
runs from deleted conversations are intentionally excluded from that current
session view. Administrators can open these conversations in read-only mode for
operational review, but cannot continue, cancel, modify, or delete another
user's conversation.
For CNOOC SSO accounts, user rankings and registered-user lists show the
validated employee display name together with the login account and
organization name. Legacy or local accounts without those optional profile
fields continue to display their login account.
Enterprise deployments may set the server-only
`DEER_FLOW_UNAUTHENTICATED_HOME_URL` to an HTTPS data-lake portal. DeerFlow's
exact root path always renders the public product landing page. Its Login, New
Chat, and Get Started actions enter the server-side auth route: an authenticated
session continues to the workspace, while an unauthenticated session returns to
that portal. APIs, static assets, and workspace deep links keep their normal
authorization behavior.
Set `DEER_FLOW_SSO_ONLY=true` on both Gateway and Frontend when CNOOC DataLake
SSO is the only approved sign-in path. In this mode every unauthenticated auth
page redirects to `DEER_FLOW_UNAUTHENTICATED_HOME_URL`, while Gateway rejects
local login, registration, first-admin initialization, password changes, and
generic OIDC entry points. The CNOOC token SSO route remains enabled. This is a
server-side policy as well as a UI redirect, so direct API calls cannot restore
local account access.

Optional CNOOC IAM support adds a separate authorization-code sign-in flow and
a BIM-to-Gateway account synchronization API. IAM sign-in only binds an account
already synchronized by BIM; it never silently creates a local browser account.
The BIM API uses an SM4-encrypted, SM3-signed server-to-server envelope and
dedicated runtime secrets, rather than browser cookies or CSRF tokens. See
[`docs/iam-integration.md`](docs/iam-integration.md) for configuration, routes,
and rollout guidance.

The dashboard also reads WeKnora's own database in read-only mode to report
current users, knowledge bases, documents, file formats, knowledge-scoped
conversations, and question turns alongside creation/upload/conversation
history for the selected range. Soft-deleted WeKnora rows remain part of
historical counts rather than being inferred from `weknora_search` or
`weknora_ask` tool calls. Per-user and per-knowledge-base tables expose the same
inventory/history split. Knowledge-base creators, shared-space owners, and
ranked users show the matching DeerFlow SSO display name and login account. The
untruncated per-user ranking is paginated in the
browser and omits accounts with no knowledge inventory or activity. Shared-space
reporting reads native organizations, member workspaces, knowledge-base shares,
shared documents, role composition, and join requests; knowledge-base details
remain a separate full-width table.
Scheduled-task reporting combines current task inventory with execution rows
and an append-only creation audit, so tasks deleted after this audit is
deployed remain in historical creation totals. External-agent usage bars use
an explicit solid color for compatibility with older enterprise browsers.

Docker builds use the upstream `uv` registry by default. If you need faster mirrors in restricted networks, export `UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple` and `NPM_REGISTRY=https://registry.npmmirror.com` before running `make docker-init` or `make docker-start`.

Backend processes automatically pick up `config.yaml` changes on the next config access, so model metadata updates do not require a manual restart during development.

> [!TIP]
> On Linux, if Docker-based commands fail with `permission denied while trying to connect to the Docker daemon socket at unix:///var/run/docker.sock`, add your user to the `docker` group and re-login before retrying. See [CONTRIBUTING.md](CONTRIBUTING.md#linux-docker-daemon-permission-denied) for the full fix.

**Production** (builds images locally, mounts runtime config and data):

```bash
make up     # Build images and start all production services
make down   # Stop and remove containers
```

Access: http://localhost:2026

For persistent deployments, configure `database.backend` as `sqlite` or
`postgres`. The selected backend is shared by the LangGraph checkpointer,
LangGraph Store, and DeerFlow application data. The deprecated `checkpointer`
section, when present, overrides the first two for backward compatibility.

For a PostgreSQL-backed checkpointer, the Gateway process pool defaults to four
connections. Set `DEERFLOW_CHECKPOINTER_POSTGRES_POOL_MIN_SIZE` and
`DEERFLOW_CHECKPOINTER_POSTGRES_POOL_MAX_SIZE` at process start to tune it;
both must be positive and `max` must not be below `min`. For the enterprise
100-concurrent-run load profile, `8` and `32` are a conservative starting
point after confirming PostgreSQL connection headroom. A Gateway rollout is
required for either setting to take effect.

The unified nginx endpoint is same-origin by default and does not emit browser CORS headers. If you run a split-origin or port-forwarded browser client, set `GATEWAY_CORS_ORIGINS` to comma-separated exact origins such as `http://localhost:3000`; the Gateway then applies the CORS allowlist and matching CSRF origin checks.

> [!IMPORTANT]
> The Gateway still owns active run tasks in process, so production defaults to a single Gateway worker (`GATEWAY_WORKERS=1`). The Redis stream bridge (`stream_bridge.type: redis`) shares SSE delivery and `Last-Event-ID` replay across workers, with a rolling retained-buffer TTL (`stream_ttl_seconds`) as a cleanup safety net. Malformed reconnect IDs live-tail new events instead of replaying the retained buffer. It does not make run cancellation, request de-duplication, or IM channel state fully cross-worker by itself; use single-worker Gateway or explicit sticky routing/ownership before raising `GATEWAY_WORKERS`.

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed Docker development guide.

#### Option 2: Local Development

If you prefer running services locally:

Prerequisite: complete the "Configuration" steps above first (`make setup`). `make dev` requires a valid `config.yaml` in the project root. Set `DEER_FLOW_PROJECT_ROOT` to define that root explicitly, or `DEER_FLOW_CONFIG_PATH` to point at a specific config file. Runtime state defaults to `.deer-flow` under the project root and can be moved with `DEER_FLOW_HOME`; skills default to `skills/` under the project root and can be moved with `DEER_FLOW_SKILLS_PATH`. Run `make doctor` to verify your setup before starting.
On Windows, run the local development flow from Git Bash. Native `cmd.exe` and PowerShell shells are not supported for the bash-based service scripts, and WSL is not guaranteed because some scripts rely on Git for Windows utilities such as `cygpath`.

1. **Check prerequisites**:

   ```bash
   make check  # Verifies Node.js 22+, pnpm, uv, nginx
   ```

2. **Install dependencies**:

   ```bash
   make install  # Install backend + frontend dependencies + pre-commit hooks
   ```

3. **(Optional) Pre-pull sandbox image**:

   ```bash
   # Recommended if using Docker/Container-based sandbox
   make setup-sandbox
   ```

   The helper reads the configured sandbox image from UTF-8 `config.yaml`
   with or without a leading BOM and with LF or CRLF line endings.

4. **(Optional) Load sample memory data for local review**:

   ```bash
   python scripts/load_memory_sample.py
   ```

   This copies the sample fixture into the default local runtime memory file so reviewers can immediately test `Settings > Memory`.
   See [backend/docs/MEMORY_SETTINGS_REVIEW.md](backend/docs/MEMORY_SETTINGS_REVIEW.md) for the shortest review flow.

5. **Start services**:

   ```bash
   make dev
   ```

6. **Access**: http://localhost:2026

#### Startup Modes

DeerFlow runs the agent runtime inside the Gateway API. Development mode enables hot-reload; production mode uses a pre-built frontend.

|          | **Local Foreground**                         | **Local Daemon**                                             | **Docker Dev**                                      | **Docker Prod**                     |
| -------- | -------------------------------------------- | ------------------------------------------------------------ | --------------------------------------------------- | ----------------------------------- |
| **Dev**  | `./scripts/serve.sh --dev`<br/>`make dev`    | `./scripts/serve.sh --dev --daemon`<br/>`make dev-daemon`    | `./scripts/docker.sh start`<br/>`make docker-start` | —                                   |
| **Prod** | `./scripts/serve.sh --prod`<br/>`make start` | `./scripts/serve.sh --prod --daemon`<br/>`make start-daemon` | —                                                   | `./scripts/deploy.sh`<br/>`make up` |

| Action      | Local                                       | Docker Dev                                        | Docker Prod                                |
| ----------- | ------------------------------------------- | ------------------------------------------------- | ------------------------------------------ |
| **Stop**    | `./scripts/serve.sh --stop`<br/>`make stop` | `./scripts/docker.sh stop`<br/>`make docker-stop` | `./scripts/deploy.sh down`<br/>`make down` |
| **Restart** | `./scripts/serve.sh --restart [flags]`      | `./scripts/docker.sh restart`                     | —                                          |

Gateway owns `/api/langgraph/*` and translates those public LangGraph-compatible paths to its native `/api/*` routers behind nginx. The maintained proxies accept bounded long JSON prompts on this route and disable request-body buffering, avoiding raw nginx failures caused by temporary-file spooling.

#### Docker Production Deployment

`deploy.sh` supports building and starting separately:

```bash
# One-step (build + start)
deploy.sh

# Two-step (build once, start later)
deploy.sh build              # build all images
deploy.sh start              # start pre-built images

# Stop
deploy.sh down
```

### Advanced

#### Sandbox Mode

DeerFlow supports multiple sandbox execution modes:

- **Local Execution** (runs sandbox code directly on the host machine)
- **Docker Execution** (runs sandbox code in isolated Docker containers)
- **Docker Execution with Kubernetes** (runs sandbox code in Kubernetes pods via provisioner service)

For Docker development, service startup follows `config.yaml` sandbox mode. In Local/Docker modes, `provisioner` is not started.

See the [Sandbox Configuration Guide](backend/docs/CONFIGURATION.md#sandbox) to configure your preferred mode.

#### MCP Server

DeerFlow supports configurable MCP servers and skills to extend its capabilities.
For HTTP/SSE MCP servers, OAuth token flows are supported (`client_credentials`, `refresh_token`).
For stdio MCP servers, per-tool call timeouts can be configured with `tool_call_timeout`.
MCP routing hints can also prefer a specific MCP tool for matching requests without forbidding other tools. When `tool_search` defers MCP schemas, matching routing metadata can auto-promote up to `tool_search.auto_promote_top_k` deferred schemas before the model call.
See the [MCP Server Guide](backend/docs/MCP_SERVER.md) for detailed instructions.

#### IM Channels

DeerFlow supports receiving tasks from messaging apps. Channels auto-start when configured — no public IP required for any of them.

DeerFlow can also expose user-owned IM channel connections in the workspace UI. When `channel_connections` is enabled, logged-in users can bind Telegram, Slack, Discord, Feishu/Lark, DingTalk, WeChat, or WeCom from the sidebar / Settings > Channels. It reuses the existing outbound `channels.*` transports, so no public IP or provider callback URL is required. Incoming IM messages then run under the connected DeerFlow user account. See [IM Channel Connections](backend/docs/IM_CHANNEL_CONNECTIONS.md) for setup and security notes.

If Gateway shutdown or startup cancellation interrupts IM-channel activation, DeerFlow now drains the partial `ChannelService` rollback before propagating the cancellation. This prevents an interrupted startup from leaving orphaned channel workers or a half-initialized global service.

Gateway shutdown also keeps the bounded in-flight RunManager drain alive across repeated cancellation. The checkpointer and database engines therefore cannot close underneath runs that are still writing their terminal state; the first cancellation is propagated after the drain reaches a safe terminal boundary.

Administrators can create, schedule, publish, and archive workspace announcements from Settings > Announcement Management. The active announcement is shown to signed-in users as a prominent animated workspace card; severity, display window, and whether users may dismiss it are configurable.

The settings dialog uses a bounded desktop viewport inset and an independent
inner content frame. This keeps the title, navigation, and content borders
separated on legacy enterprise browsers while retaining full-screen, scrollable
behavior on mobile WebViews. The enterprise workspace hides the Appearance
navigation entry and opens Settings on Account while retaining the underlying
theme provider and existing stored theme preference.

The mobile workspace keeps the same authenticated DeerFlow routes and APIs, but
uses a compact welcome surface, a safe-area-aware bottom composer, single-line
composer controls, horizontally scrollable quick actions, and a touch-sized
navigation drawer. Its top mode pill reuses the current conversation's normal
mode/reasoning settings, visible copy follows the selected locale, and the
mobile welcome uses a circular CNOOC-branded animated assistant with a
reduced-motion fallback. Closing the mobile drawer before opening Settings is
coordinated by a persistent dialog host, so the settings surface cannot be lost
when the Sidebar Sheet unmounts. Desktop layout and permission behavior are
unchanged.

Custom-agent new-chat routes reuse that same CNOOC welcome composition on
desktop and mobile. Their agent icon, name, and description remain a separate
welcome region above the shared composer; custom-agent routing and every native
task/header action are preserved after the first message is sent.

| Channel       | Transport                    | Difficulty |
| ------------- | ---------------------------- | ---------- |
| Telegram      | Bot API (long-polling)       | Easy       |
| Slack         | Socket Mode                  | Moderate   |
| Feishu / Lark | WebSocket                    | Moderate   |
| WeChat        | Tencent iLink (long-polling) | Moderate   |
| WeCom         | WebSocket                    | Moderate   |
| DingTalk      | Stream Push (WebSocket)      | Moderate   |

**Configuration in `config.yaml`:**

```yaml
channels:
  # LangGraph-compatible Gateway API base URL (default: http://localhost:8001/api)
  langgraph_url: http://localhost:8001/api
  # Gateway API URL (default: http://localhost:8001)
  gateway_url: http://localhost:8001

  # Optional: global session defaults for all mobile channels
  session:
    assistant_id: lead_agent # or a custom agent name; custom agents are routed via lead_agent + agent_name
    config:
      recursion_limit: 100
    context:
      thinking_enabled: true
      is_plan_mode: false
      subagent_enabled: false

  feishu:
    enabled: true
    app_id: $FEISHU_APP_ID
    app_secret: $FEISHU_APP_SECRET
    # domain: https://open.feishu.cn       # China (default)
    # domain: https://open.larksuite.com   # International

  wecom:
    enabled: true
    bot_id: $WECOM_BOT_ID
    bot_secret: $WECOM_BOT_SECRET

  slack:
    enabled: true
    bot_token: $SLACK_BOT_TOKEN # xoxb-...
    app_token: $SLACK_APP_TOKEN # xapp-... (Socket Mode)
    allowed_users: [] # empty = allow all

  telegram:
    enabled: true
    bot_token: $TELEGRAM_BOT_TOKEN
    allowed_users: [] # empty = allow all

  wechat:
    enabled: false
    bot_token: $WECHAT_BOT_TOKEN
    ilink_bot_id: $WECHAT_ILINK_BOT_ID
    qrcode_login_enabled: true # optional: allow first-time QR bootstrap when bot_token is absent
    allowed_users: [] # empty = allow all
    polling_timeout: 35
    state_dir: ./.deer-flow/wechat/state
    max_inbound_image_bytes: 20971520
    max_outbound_image_bytes: 20971520
    max_inbound_file_bytes: 52428800
    max_outbound_file_bytes: 52428800

    # Optional: per-channel / per-user session settings
    session:
      assistant_id: mobile-agent # custom agent names are also supported here
      context:
        thinking_enabled: false
      users:
        "123456789":
          assistant_id: vip-agent
          config:
            recursion_limit: 150
          context:
            thinking_enabled: true
            subagent_enabled: true

  dingtalk:
    enabled: true
    client_id: $DINGTALK_CLIENT_ID # Client ID of your DingTalk application
    client_secret: $DINGTALK_CLIENT_SECRET # Client Secret of your DingTalk application
    allowed_users: [] # empty = allow all
    card_template_id: "" # Optional: AI Card template ID for streaming typewriter effect
```

Notes:

- `assistant_id: lead_agent` calls the default LangGraph assistant directly.
- If `assistant_id` is set to a custom agent name, DeerFlow still routes through `lead_agent` and injects that value as `agent_name`, so the custom agent's SOUL/config takes effect for IM channels.
- IM channel workers call Gateway's LangGraph-compatible API internally and automatically attach process-local internal auth plus the CSRF cookie/header pair required for thread and run creation.
- Feishu/Lark now queues rapid follow-up messages per mapped DeerFlow `thread_id` instead of immediately surfacing the generic busy reply, and topic replies keep a per-message card with a compact source-message preview across queued/running/final patches.

Set the corresponding API keys in your `.env` file:

```bash
# Telegram
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ

# Slack
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...

# Feishu / Lark
FEISHU_APP_ID=cli_xxxx
FEISHU_APP_SECRET=your_app_secret

# WeChat iLink
WECHAT_BOT_TOKEN=your_ilink_bot_token
WECHAT_ILINK_BOT_ID=your_ilink_bot_id

# WeCom
WECOM_BOT_ID=your_bot_id
WECOM_BOT_SECRET=your_bot_secret

# DingTalk
DINGTALK_CLIENT_ID=your_client_id
DINGTALK_CLIENT_SECRET=your_client_secret
```

**Telegram Setup**

1. Chat with [@BotFather](https://t.me/BotFather), send `/newbot`, and copy the HTTP API token.
2. Set `TELEGRAM_BOT_TOKEN` in `.env` and enable the channel in `config.yaml`.

**Slack Setup**

1. Create a Slack App at [api.slack.com/apps](https://api.slack.com/apps) → Create New App → From scratch.
2. Under **OAuth & Permissions**, add Bot Token Scopes: `app_mentions:read`, `chat:write`, `im:history`, `im:read`, `im:write`, `files:write`.
3. Enable **Socket Mode** → generate an App-Level Token (`xapp-…`) with `connections:write` scope.
4. Under **Event Subscriptions**, subscribe to bot events: `app_mention`, `message.im`.
5. Set `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` in `.env` and enable the channel in `config.yaml`.

**Feishu / Lark Setup**

1. Create an app on [Feishu Open Platform](https://open.feishu.cn/) → enable **Bot** capability.
2. Add permissions: `im:message`, `im:message.p2p_msg:readonly`, `im:resource`.
3. Under **Events**, subscribe to `im.message.receive_v1` and select **Long Connection** mode.
4. Copy the App ID and App Secret. Set `FEISHU_APP_ID` and `FEISHU_APP_SECRET` in `.env` and enable the channel in `config.yaml`.

**WeChat Setup**

1. Enable the `wechat` channel in `config.yaml`.
2. Either set `WECHAT_BOT_TOKEN` in `.env`, or set `qrcode_login_enabled: true` for first-time QR bootstrap.
3. When `bot_token` is absent and QR bootstrap is enabled, watch backend logs for the QR content returned by iLink and complete the binding flow.
4. After the QR flow succeeds, DeerFlow persists the acquired token under `state_dir` for later restarts.
5. For Docker Compose deployments, keep `state_dir` on a persistent volume so the `get_updates_buf` cursor and saved auth state survive restarts.

**WeCom Setup**

1. Create a bot on the WeCom AI Bot platform and obtain the `bot_id` and `bot_secret`.
2. Enable `channels.wecom` in `config.yaml` and fill in `bot_id` / `bot_secret`.
3. Set `WECOM_BOT_ID` and `WECOM_BOT_SECRET` in `.env`.
4. Make sure backend dependencies include `wecom-aibot-python-sdk`. The channel uses a WebSocket long connection and does not require a public callback URL.
5. The current integration supports inbound text, image, and file messages. Final images/files generated by the agent are also sent back to the WeCom conversation.

**DingTalk Setup**

1. Create a DingTalk application in the [DingTalk Developer Console](https://open.dingtalk.com/) and enable **Robot** capability.
2. Set the message receiving mode to **Stream Mode** in the robot configuration page.
3. Copy the `Client ID` and `Client Secret`, set `DINGTALK_CLIENT_ID` and `DINGTALK_CLIENT_SECRET` in `.env`, and enable the channel in `config.yaml`.
4. _(Optional)_ To enable streaming AI Card replies (typewriter effect), create an **AI Card** template on the [DingTalk Card Platform](https://open.dingtalk.com/document/dingstart/typewriter-effect-streaming-ai-card), then set `card_template_id` in `config.yaml` to the template ID. You also need to apply for the `Card.Streaming.Write` and `Card.Instance.Write` permissions.

When DeerFlow runs in Docker Compose, IM channels execute inside the `gateway` container. In that case, do not point `channels.langgraph_url` or `channels.gateway_url` at `localhost`; use container service names such as `http://gateway:8001/api` and `http://gateway:8001`, or set `DEER_FLOW_CHANNELS_LANGGRAPH_URL` and `DEER_FLOW_CHANNELS_GATEWAY_URL`.

**Commands**

Once a channel is connected, you can interact with DeerFlow directly from the chat:

| Command   | Description              |
| --------- | ------------------------ |
| `/new`    | Start a new conversation |
| `/status` | Show current thread info |
| `/models` | List available models    |
| `/memory` | View memory              |
| `/help`   | Show help                |

> Messages without a command prefix are treated as regular chat — DeerFlow creates a thread and responds conversationally.

#### Request Trace Correlation

Gateway request trace correlation is disabled by default so existing HTTP responses and log formats stay unchanged. To enable it, set:

```yaml
logging:
  enhance:
    enabled: true
    format: text
```

When enabled, every Gateway HTTP response includes `X-Trace-Id`, logs include `trace_id`, and Langfuse traces created by that request include `metadata.deerflow_trace_id` with the same value.

#### Run-stage timing diagnostics

For a bounded performance investigation, set `DEERFLOW_RUN_TIMING_LOGS=true`
and `DEERFLOW_LLM_TIMING_LOGS=true` on Gateway. They add content-free,
run-correlated records for run admission, Sandbox acquisition/creation/readiness,
tool execution, and model request timing. Every model request has a stable
`llm_call_id` across its start/first-chunk/end records; group by that ID within
each run when calculating model latency. The `run_end` record also splits
cumulative tool time into Sandbox API execution
(`sandbox_tool_execution_total_ms` / `sandbox_tool_count`, including shell and
file operations) and Gateway/external tools (`non_sandbox_tool_total_ms` /
`non_sandbox_tool_count`). Any Sandbox acquisition nested in a first tool call
is excluded from the execution total and recorded separately as
`sandbox_tool_acquire_overlap_ms`; neither path records tool arguments or
command text. See
[docs/run-timing-logs.md](docs/run-timing-logs.md); remove the flags after the
test to avoid high-volume logs. For a captured `kubectl logs --timestamps`
file, `python3 scripts/deerflow_timing_report.py gateway-timing.log` produces
one content-free load-test timing row per finished run.
For a suspected silent graph tail, the same flag emits a 30-second idle-wait
watchdog and the eventual `anext()` completion after all observed graph tasks
have ended; it also reports only slow browser-bridge publishes. Tune the
diagnostic-only thresholds with `DEERFLOW_RUN_TIMING_IDLE_WAIT_SECONDS` and
`DEERFLOW_RUN_TIMING_SLOW_BRIDGE_PUBLISH_MS` (both must be positive). Neither
setting times out or cancels the graph.
For PostgreSQL checkpoint diagnostics, the same bounded flag adds per-run
`checkpoint_aput_*`, `checkpoint_aput_writes_*`,
`checkpoint_pool_acquire_*`, `checkpoint_pool_hold_*`, and
`checkpoint_sql_execute_*` totals to `run_end`. They distinguish checkpoint
API time, pool-borrow wait, checked-out connection hold, and SQL round-trip
time without logging SQL, parameters, checkpoint state, or user content. Each
group also exposes `inflight_max`, the peak same-Run concurrent operation count;
it is not an exact LangGraph predecessor-chain queue length. Slow checkpoint
records include their `inflight_at_start`.
Set `DEERFLOW_CHECKPOINTER_TIMING_SLOW_MS` only when needed (default `1000`)
to emit slow checkpoint operations before the Run ends; normal checkpoint
operations remain aggregate-only.
For a temporary PostgreSQL-side correlation view, additionally set
`DEERFLOW_CHECKPOINTER_DB_SESSION_LABELS=true`. Each pooled checkpointer
borrow then labels its database session as `dfcp:<run-id>`; inspect only
`pg_stat_activity` state and wait-event aggregates as documented in
[docs/run-timing-logs.md](docs/run-timing-logs.md). The label is disabled by
default, excludes user/thread/content data, and adds one small SQL call per
borrow.
Non-stream calls (including active-goal evaluation) emit the same LLM triplet
with `mode=async_nonstream`, where `first_chunk_ms` means response-completion
latency. `goal_evaluate_*` and `goal_checkpoint_read_*` isolate active-goal
evaluation and its checkpoint reads. `run_finalize_*` encloses separately timed
journal flush, run-completion persistence, title sync, duration persistence,
thread-status update, and optional completion-hook stages, so post-graph work
does not appear only as unattributed wall time.
`agent_stream_*` brackets the complete worker stream turn; its nested
`checkpoint_lock_wait_*` isolates per-thread checkpoint-lock contention and
`agent_graph_stream_*` measures the actual `agent.astream()` lifetime once the
lock is held. `agent_graph_stream_tail.elapsed_ms` is the silent time from the
last published graph update to graph exit, which exposes a post-answer hang.
With this diagnostic flag on, Gateway privately consumes LangGraph `tasks`
events and emits content-free `graph_task_start` / `graph_task_end` pairs, so
the internal graph node responsible for that wait can be identified without
forwarding task state to the browser.
Every DeerFlow-owned `after_agent` / `aafter_agent` hook also emits a paired
`middleware_after_agent_*` span with only a fixed middleware name and outcome;
this isolates cleanup and Sandbox-release waits inside a graph `after_agent`
node without exposing state or conversation data.
Hidden middleware re-entry markers (`todo_*`,
`artifact_delivery_*`, `terminal_*`, and `skill_workflow_*`) identify retries or
injected reminders. `context_summarize_*`, `title_generation_*`, and
`memory_enqueue_*` additionally expose hidden summary/title calls and the
non-blocking memory queue handoff. Together they separate a real post-response
graph gap from a non-stream final answer without logging conversation content.
The report distinguishes unmatched model-end records with `LLM_ORPHAN#`; they
remain in cumulative model time but cannot inflate the matched-call `COV%`.

#### LangSmith Tracing

DeerFlow has built-in [LangSmith](https://smith.langchain.com) integration for observability. When enabled, all LLM calls, agent runs, and tool executions are traced and visible in the LangSmith dashboard.

Add the following to your `.env` file:

```bash
LANGSMITH_TRACING=true
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_API_KEY=lsv2_pt_xxxxxxxxxxxxxxxx
LANGSMITH_PROJECT=xxx
```

#### Langfuse Tracing

DeerFlow also supports [Langfuse](https://langfuse.com) observability for LangChain-compatible runs.

Add the following to your `.env` file:

```bash
LANGFUSE_TRACING=true
LANGFUSE_PUBLIC_KEY=pk-lf-xxxxxxxxxxxxxxxx
LANGFUSE_SECRET_KEY=sk-lf-xxxxxxxxxxxxxxxx
LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

If you are using a self-hosted Langfuse instance, set `LANGFUSE_BASE_URL` to your deployment URL.

**Trace correlation fields.** Every agent run is annotated with Langfuse's reserved trace attributes so the Sessions and Users pages light up automatically:

- `session_id` = LangGraph `thread_id` — groups every trace of the same conversation
- `user_id` = effective user from `get_effective_user_id()` (falls back to `default` in no-auth mode)
- `trace_name` = assistant id (defaults to `lead-agent`)
- `tags` = `[env:<DEER_FLOW_ENV>, model:<model_name>]` (omitted when not set)
- `metadata.deerflow_trace_id` = DeerFlow request correlation id, matching `X-Trace-Id` when request trace correlation is enabled

These are injected into `RunnableConfig.metadata` at the graph invocation root for both the gateway path (`runtime/runs/worker.py::run_agent`) and the embedded path (`client.py::DeerFlowClient.stream`), so any LangChain-compatible callback can read them. Set `DEER_FLOW_ENV` (or `ENVIRONMENT`) to tag traces by deployment environment.

#### Monocle Tracing

DeerFlow also supports [Monocle](https://github.com/monocle2ai/monocle), an OpenTelemetry-based tracer for agentic applications. It records each run end-to-end: LLM calls, agent steps, and tool and MCP invocations, with their inputs, outputs, timings, and token counts.

Add the following to your `.env` file:

```bash
MONOCLE_TRACING=true
MONOCLE_EXPORTERS=file          # file, console, okahu, s3, blob, gcs (default: file)
OKAHU_API_KEY=okh_xxxxxxxx      # required only for the `okahu` exporter
```

Each run writes one trace file to `.monocle/`; open it in the [Monocle VS Code extension](https://marketplace.visualstudio.com/items?itemName=OkahuAI.monocle-apptrace) to inspect the span timeline and token counts. Connect to [Okahu](https://www.okahu.ai), an agent-observability platform, to analyze traces across runs and run trace-based and agentic evaluations (via the `okahu` exporter).

Traces capture span inputs and outputs verbatim — prompts, tool arguments, and model responses — plus token usage and timings. The `file` exporter keeps them on local disk and never rotates or cleans them up, so prune `.monocle/` periodically; the remote exporters (`okahu`, `s3`, `blob`, `gcs`) send that same data off-box, so enable only destinations you trust. Monocle is initialized once at Gateway startup: a configuration error (unknown exporter, missing `OKAHU_API_KEY`) is logged there and tracing stays off until the Gateway restarts.

#### Using Multiple Providers

LangSmith and Langfuse attach as LangChain callbacks, so you can enable both and DeerFlow reports each run to both. If an enabled provider is missing required credentials or fails to initialize, DeerFlow fails fast and names it. Monocle uses a global OpenTelemetry provider rather than a callback; Langfuse shares that provider, so all three can run together. Because both span processors sit on the same shared provider, Monocle's exporters also see Langfuse's spans when both are enabled.

For Docker deployments, tracing is disabled by default. Set `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` in your `.env` to enable it.

## From Deep Research to Super Agent Harness

DeerFlow started as a Deep Research framework — and the community ran with it. Since launch, developers have pushed it far beyond research: building data pipelines, generating slide decks, spinning up dashboards, automating content workflows. Things we never anticipated.

That told us something important: DeerFlow wasn't just a research tool. It was a **harness** — a runtime that gives agents the infrastructure to actually get work done.

So we rebuilt it from scratch.

DeerFlow 2.0 is no longer a framework you wire together. It's a super agent harness — batteries included, fully extensible. Built on LangGraph and LangChain, it ships with everything an agent needs out of the box: a filesystem, memory, skills, sandbox-aware execution, and the ability to plan and spawn sub-agents for complex, multi-step tasks.

Use it as-is. Or tear it apart and make it yours.

## Core Features

### Skills & Tools

Long skill entry files and their supporting resources use separate
`sandbox.skill_file_output_max_chars` and
`sandbox.skill_reference_output_max_chars` budgets (150,000 characters by
default). This keeps mandatory later workflow phases, schemas, and examples
visible without globally enlarging ordinary file reads.

Enterprise video generation uses the platform-managed DashScope service through
short asynchronous submit/status commands. Provider credentials, model
selection, proxy routing, and CA trust are managed by the platform; the agent
does not discover providers, inspect credentials, or issue direct provider API
probes.

Skills are what make DeerFlow do _almost anything_.

A standard Agent Skill is a structured capability module — a Markdown file that defines a workflow, best practices, and references to supporting resources. DeerFlow ships with built-in skills for research, report generation, slide creation, web pages, image and video generation, and more. But the real power is extensibility: add your own skills, replace the built-in ones, or combine them into compound workflows.

Skills are loaded progressively — only when the task needs them, not all at once. This keeps the context window lean and makes DeerFlow work well even with token-sensitive models.

The bundled academic research package is exposed through one public skill,
`/academic-research`. Its vendored component skills are runtime implementation
details under `skills/third_party/` and do not appear as separate platform skills.

Users can explicitly activate an enabled skill for a single turn by starting the request with `/skill-name`, for example `/data-analysis analyze uploads/foo.csv`. DeerFlow loads that skill's complete `SKILL.md` as hidden context on the first model call, then restores a compact validated continuation contract after each tool call. This retains exact reference paths and mandatory phase gates without multiplying a large skill body by every tool iteration. The skill is read from disk and audited once; the per-run contract is never written to chat history or persisted run configuration. Slash activation respects disabled skills, custom-agent skill whitelists, and existing channel commands such as `/new` and `/help`.

For a Kubernetes PVC deployment, see [Public Skill incremental deployment](docs/public-skill-update-deployment.zh-CN.md) before updating one bundled public Skill. It uses a target-scoped sync Job rather than reapplying the entire environment.

When an activated skill declares a mandatory `read_file` reference or names a
schema skeleton for a generated control file, DeerFlow blocks `write_file` and
`str_replace` until that exact supporting resource has been read successfully.
The same content-driven gate protects SVG authoring and speaker-note files when
the skill declares an `executor-base.md` contract, preventing models from
inventing deep workflow formats or skipping later instructions.
Content-declared full-section contracts are validated before generated Markdown
is written. Skills that declare first-page and final quality stages also receive
runtime ordering gates: page two cannot be authored before the first-page check
succeeds, and SVG export/finalization cannot run after an edit until the final
check succeeds.
Skills that declare a blocking explicit-user confirmation cannot write their
design contract until either the confirmation UI completes or a structured
response arrives for a matching `suggestion`/`risk_confirmation` request. An
earlier missing-information answer and assistant-authored confirmation text are
not accepted as user consent. On DeerFlow's chat fallback, the agent uses the
structured confirmation card rather than plain text or generated quick replies,
so the confirmed response resumes the same activated workflow. The card carries
the complete generated design proposal as its context, allowing the user to
review the values at the point of confirmation.

Long, tool-heavy skills should use a model whose context window can hold the
entry file, required references, generated artifacts, and tool history together.
A 1M-context model avoids premature summarization of large slide or document
workflows. Ordinary slash skills use compact continuation reminders to control
repeated input cost; strict serial workflows with hard-stop confirmation gates
retain their complete instructions across the tool loop so later phases do not
lose their contracts.

The web client gives interactive runs a bounded 2000 LangGraph super-step
budget. Super-steps include middleware and tool nodes, not only LLM calls, so a
complex but progressing Skill can legitimately exceed the old 1000-step value.
Gateway clamping, token budgets, and loop detection remain enabled. For AIO
Sandboxes, the PPT-capable image also aligns the outer authentication proxy's
idle timeout with the bounded command transport; this prevents false 504s on
long image-generation commands while retaining the client hard timeout.

A skill directory is a package boundary: once DeerFlow finds its `SKILL.md`, nested `SKILL.md` files under that package (for example evaluation fixtures) remain supporting data and are not registered as runtime skills. Namespace directories without their own `SKILL.md` can still group nested skills.

Users can explicitly activate an enabled skill for a single turn by starting the request with `/skill-name`, for example `/data-analysis analyze uploads/foo.csv`. DeerFlow loads that skill's `SKILL.md` as hidden current-turn context while leaving the base prompt limited to skill metadata. Slash activation respects disabled skills, custom-agent skill whitelists, and existing channel commands such as `/new` and `/help`.

The September 30 test-environment merge retains the verified production lead-agent behavior: merely discoverable default-agent skills do not statically narrow its tools; custom/bootstrap agents with a finite skill allowlist retain construction-time `allowed-tools` filtering. Slash activation still loads enabled, agent-allowed Skill instructions and preserves their workflow and secret-binding behavior. Activation-scoped model-schema and tool-execution enforcement is deferred because the incoming middleware and tests lack their matching context helpers and execution-chain integration. Do not describe that deferred enforcement as active or as an authorization boundary. See [the merge deferral record](docs/enterprise-merge-20260930-deferred-skill-policy.md).

When you install `.skill` archives through the Gateway, DeerFlow accepts standard optional frontmatter metadata such as `version`, `author`, and `compatibility` instead of rejecting otherwise valid external skills.

If a trusted operator manages the configured skills directory through an external mount such as MinIO, NFS, or CSI, an administrator can call `POST /api/skills/reload` after changing files. This invalidates skill prompt caches for the current Gateway process and waits up to the bounded refresh timeout so subsequent runs rescan the latest files; running tasks are unchanged. A loader-level filesystem failure returns a generic server error and preserves the last successfully loaded process cache rather than publishing an empty catalog. Uvicorn workers and Kubernetes Pods must each be targeted separately. Direct mount writes bypass the validation, SkillScan, and history applied by DeerFlow's install/edit APIs, so only operator-controlled systems should have write access.

Skill installs and agent-managed skill edits run through **SkillScan**, a native deterministic safety scanner before the LLM-based skill scanner. Phase 1 runs offline with no Semgrep/OpenGrep dependency, blocks high-confidence `CRITICAL` findings such as private keys, shell execution, or bulk environment data flowing through a proven outbound network client, and passes warning findings to the LLM scanner for contextual review. Package review is byte-faithful: binary files are scanned, non-UTF-8/NUL-bearing scripts cannot hide behind binary classification, archive names cannot smuggle NTFS alternate data streams, and missing or colliding snapshot content fails closed. Python instance-client checks use a bounded same-scope evidence chain from a proven imported constructor through simple-name aliases to a supported outbound method or context-manager use. Bare canonical-looking names are not inferred as modules; ambiguous flows and unsupported constructs remain documented false negatives rather than producing unsafe guesses. Set `skill_scan.enabled: false` in `config.yaml` to disable only the deterministic analyzers; safe archive extraction and the LLM scanner still run.

Set `skill_evolution.enabled: true` to let the bundled `skill-creator` persist
agent-authored skills through `skill_manage`. Each authenticated user's skills
are stored under that user's `.deer-flow/users/<user_id>/skills/custom/`
directory, become available to that user's later conversations immediately,
and remain subject to SkillScan plus LLM moderation. Moderation accepts both
Chat Completions string responses and Responses API text blocks; reasoning and
tool-call blocks never participate in the security decision.

DeerFlow also ships with **skill-reviewer**, a public skill for read-only skill quality review. It uses the built-in `review_skill_package` tool to inspect installed skills, local packages, archives, or pasted `SKILL.md` content without activating the target skill, binding its secrets, executing its scripts, or installing it. The tool returns a compact, tag-neutralized JSON payload to the model context and keeps the full raw review payload in the tool artifact for programmatic consumers. The deterministic review core reuses DeerFlow parsing and SkillScan facts, emits versioned JSON contracts under `contracts/skill_review/`, and can be run from the backend CLI:

```bash
cd backend
uv run python -m deerflow.skills.review.cli ../skills/public/data-analysis --format text --fail-on error --fail-on-incomplete
```

Tools follow the same philosophy. DeerFlow comes with a core toolset — web search, web fetch, rendered web capture, file operations, bash execution — and supports custom tools via MCP servers and Python functions. Swap anything. Add anything.

Tavily search accepts operator-configured `include_domains` and
`exclude_domains` source filters without exposing them to the model. Tavily
search and fetch also resolve credentials from their own tool entries, with
`TAVILY_API_KEY` as the fallback for either tool.

Enterprise deployments can enable two CNOOC DataLake QA tools:
`datalake_smart_qa` retains the internal Retrieve API and ranked narrative
source chunks. `datalake_data_qa` calls the new AppForge application
`app-v16p2syh` with a dedicated `APPFORGE_DATA_QA_API_KEY`, empty identity
inputs, and a `SquareWorkflow_` conversation ID scoped to user/thread/tool.
It replaces the retired Metrics/Detail registrations without calling their
old applications. Keep `trust_env: false` for these internal direct routes.
Publish a new versioned tool ConfigMap together with the compatible Gateway
image and Secret reference; do not overwrite the live shared ConfigMap.
Paired rollback restores the saved image/configuration references only after
checking that no later Deployment change would be overwritten.
See [Data QA replacement](docs/datalake-data-qa-replacement.md) for outstanding
field acceptance and rollout requirements.
The joint Data QA/GLM preparation tool creates separate immutable configuration
resources and runs two bounded candidate checks before any Gateway cutover.
Successful preparation does not establish business-data correctness or switch
the active model/tool configuration.
If a candidate Job fails before its result is collected, inspect its Job
conditions/events and retain the already prepared resources for verified reuse.
The report-bound r2 resumption reuses those immutable resources and copies the
existing Gateway node toleration into two isolated checks; it does not roll out
Gateway or require another credential entry.
Both field candidate Jobs have now passed. The paired activation controller
checks persisted custom-agent references before submitting one compare-and-swap
patch for image, config and dedicated Secret reference, then accepts every
Gateway replica. It requires a drained task window; candidate acceptance alone
does not mean the production update or business-answer validation is complete.
The first joint activation stopped before its patch: two of 186 persisted
Agent files still reference retired tools. Keep that gate and inspect bounded
reference details before preparing edits; do not blanket-replace tool names or
repeat the rollout while these references remain unresolved.
The detailed inventory identified four tokens in two personal SOUL files. The
pinned r2 continuation backs up both originals, migrates only those references,
and reuses the qualified paired rollout. It requires paused Agent edits as well
as drained tasks; uncertain exec/patch outcomes never trigger automatic restore.

For recognized enterprise DataLake queries, disabled access returns a prompt to
enable the **Use Data Lake** button. Once a run selects DataLake, empty results,
permission failures and upstream errors never authorize fallback to external
search, browsers, MCP services or scripts. The restriction follows delegated
tasks; ordinary public-information questions remain independent.

An optional AppForge workflow, `datalake_md_search`, is available for
exploration-and-production master data only: companies, branches/operating
organizations, oil and gas fields, blocks, wells, and their ownership, lists,
or counts. It must not be used for production, KPIs, trends, reports,
calculations, document QA, arbitrary SQL, or inferred business relationships.
It is not enabled in the current enterprise deployment or this migration.
For a separately approved deployment, add its `config.example.yaml` entry and inject
`APPFORGE_MD_SEARCH_API_KEY` through the Gateway runtime Secret. It is
intentionally outside the user's **Use Data Lake** toggle and outside
`DATALAKE_TOOL_NAMES`; do not reuse the browser's platform Token or expose the
AppForge credential to the model.

When Retrieve evidence includes an image from the reviewed internal preview
service, Gateway converts it to an authenticated same-origin
`/api/datalake/image-preview` URL. The browser can render the original image on
an HTTPS DeerFlow page without learning or directly contacting the HTTP-only
internal origin; all other origins, redirects, non-image responses, traversal
paths, and oversized payloads are rejected.

The chat composer exposes this integration as **Data Lake**. The user's
enabled/disabled choice is stored once in browser settings and applies to every
conversation; missing settings default to disabled. When **Use Data Lake** is
selected and the HttpOnly `cnooc.platform_token` cookie is absent, Gateway
persists a user-bound 60-second challenge, obtains the cached platform appCode,
exchanges the challenge for a token, validates the exchanged Token through the
CNOOC identity endpoint, stores only its unique SHA-256 binding on the current
user, and then writes the raw Token only to the HttpOnly cookie. The appCode
request follows the platform's eight-digit Unix-time prefix contract. Users who
sign in through enterprise IAM can authorize Data Lake only when their trusted
IAM corporate email exactly matches the corporate email verified from the Data
Lake Token; other identity providers and mismatches remain blocked. CNOOC Token
login creates the same binding before issuing either login cookie. Opening the
file list repeats this idempotent authorization check. Every Next.js file-list,
download, and external-agent launch route asks Gateway to compare that Cookie
with the current user's binding before forwarding it; a mismatch clears the old
Cookie and resets the browser Data Lake preference to disabled. If the
shop-cart list service instead reports `code=1` and `msg=invalid_token`, the UI
prompts the user to sign in to Data Lake while preserving the Cookie and enabled
preference. If token exchange is reachable but rejects the account at the
business layer, the UI shows **No Data Lake permission** and remains disabled;
transport and upstream service failures retain the retry-oriented authorization
error. Logging out also resets the preference. The public
provider callback is
`POST /api/v1/data-lake/auth/callback`; it accepts the one-time challenge and
returns the bound user profile without exposing the token to browser
JavaScript. Its OpenAPI operation declares the strict JSON request body, and
the shared Nginx entry returns a JSON 429 response when the per-client limit is
exceeded. Choosing **Do not use Data Lake** hides the file list, closes an
open list dialog, and prevents all three DataLake tools from being assembled or
contacted for each run. Existing attachments remain part of their
conversations, and the choice is carried into normal, regenerated,
custom-agent, and branched runs.

Regenerating an answer after a Human Input clarification now replays the latest
protocol-validated card response instead of falling back to an earlier visible
question. The response stays hidden in the transcript and retains its request
correlation metadata; unrelated hidden or control messages are never replayed.

Gateway-generated follow-up suggestions now normalize both plain-string model output and block/list-style rich content before parsing the JSON array response, so provider-specific content wrappers do not silently drop suggestions.

The Web UI composer can polish draft input before sending. The rewrite runs as a short Gateway LLM request using the `input_polish` model configuration, keeps slash skill prefixes such as `/data-analysis`, and only replaces the local draft after the user clicks the polish button; it does not create a thread run or persist a message.

The Web UI composer supports same-origin enterprise voice dictation. The browser
records microphone audio as 16 kHz mono PCM16 and streams it through the
authenticated Gateway WebSocket at `/api/voice-input/stream`; Gateway owns the
`QWEN_ASR_API_KEY` Secret and relays the audio to the configured Qwen3-ASR
service. Only the bounded transcript contract returns to the composer, where
users can review or edit it before sending. Microphone capture requires a
secure browser context: deploy DeerFlow behind HTTPS, or use an audited
enterprise-browser policy that marks the exact intranet HTTP origin as secure.
The legacy browser `SpeechRecognition` service is not used as a fallback.

Interrupted first-turn runs still persist a fallback conversation title, so stopping a streaming response does not leave the thread as "Untitled" after refresh.

Reopening a chat that still has a pending or running backend run automatically
rejoins that run's live stream, including after the browser-local reconnect
metadata was lost. The composer remains busy during recovery, preventing an
accidental second submission and the resulting active-run HTTP 409 conflict.

In the Web UI, completed assistant turns can be branched into a new main conversation. The new thread starts from that turn's checkpoint. Because workspace files are not checkpointed, the branch only receives a best-effort copy of the current workspace when you branch from the latest turn; branching from an older turn keeps just the restored message history so the branch never inherits files that were created in a later part of the conversation.

Completed assistant turns also expose like and dislike controls in the turn toolbar. Feedback is stored against the owning run, survives history reloads, and can be switched or cleared by clicking the selected control again.

```
# Paths inside the sandbox container
/mnt/skills/public
├── research/SKILL.md
├── report-generation/SKILL.md
├── slide-creation/SKILL.md
├── web-page/SKILL.md
└── image-generation/SKILL.md

/mnt/skills/custom
└── your-custom-skill/SKILL.md      ← yours
```

#### Claude Code Integration

The `claude-to-deerflow` skill lets you interact with a running DeerFlow instance directly from [Claude Code](https://docs.anthropic.com/en/docs/claude-code). Send research tasks, check status, manage threads — all without leaving the terminal.

**Install the skill**:

```bash
npx skills add https://github.com/bytedance/deer-flow --skill claude-to-deerflow
```

Then make sure DeerFlow is running (default at `http://localhost:2026`) and use the `/claude-to-deerflow` command in Claude Code.

**What you can do**:

- Send messages to DeerFlow and get streaming responses
- Choose execution modes: flash (fast), standard, pro (planning), ultra (sub-agents)
- Check DeerFlow health, list models/skills/agents
- Manage threads and conversation history
- Upload files for analysis

**Environment variables** (optional, for custom endpoints):

```bash
DEERFLOW_URL=http://localhost:2026            # Unified proxy base URL
DEERFLOW_GATEWAY_URL=http://localhost:2026    # Gateway API
DEERFLOW_LANGGRAPH_URL=http://localhost:2026/api/langgraph  # LangGraph API
```

See [`skills/public/claude-to-deerflow/SKILL.md`](skills/public/claude-to-deerflow/SKILL.md) for the full API reference.

### Session Goals

Use `/goal <completion condition>` to attach one active completion condition to the current thread. The goal is thread-scoped state, not a skill activation, so it stays active across turns until DeerFlow determines it has been satisfied or you clear it.

Supported commands:

```text
/goal finish the implementation and make all tests pass
/goal              # show the active goal
/goal clear        # clear it
```

After each Gateway-backed run, DeerFlow evaluates the visible conversation against the active goal with a non-thinking evaluator model. The evaluator must return a typed blocker (`missing_evidence`, `needs_user_input`, `run_failed`, `external_wait`, or `goal_not_met_yet`) plus visible evidence. DeerFlow only injects a hidden continuation when the latest assistant turn is durably checkpointed, the blocker is `goal_not_met_yet`, the thread did not change during evaluation, and the no-progress breaker has not fired. The safety cap defaults to 8 hidden continuations, and repeated identical non-progress evaluations stop after 2 attempts. `/goal clear` and any user-authored new input win over queued continuations. When the goal is satisfied, DeerFlow clears it automatically and publishes the updated thread state.

The Web UI shows the active goal above the composer. The same command is available from the TUI and supported IM channels. In the Web UI and supported IM channels, setting `/goal <completion condition>` also starts a run with the condition as the task; status and clear commands only manage goal state.

### Manual Context Compaction

Use `/compact` in the Web UI composer to summarize older context for the current thread. DeerFlow keeps the full chat visible, but future model calls use the compacted summary plus recent messages. The command is ignored when there is not enough history to compact, and it is blocked while the thread has a run in flight.

### Sub-Agents

The Web UI's custom-agent builder follows the selected interface language when
it saves `SOUL.md` and the one-line card description. Simplified Chinese is the
platform default; a user can still explicitly request another language. Lead
and delegated agents apply the same default to user-visible answers, titles,
descriptions, summaries, and reports while preserving code, API fields, paths,
standards, and proper nouns where their original spelling matters.

Custom-agent chat routes decode the URL path segment before looking up the
agent, displaying its name, or creating the run context, then re-encode it for
subsequent navigation. This keeps Chinese names readable and routes them to the
correct agent. The welcome avatar uses a separate translucent background layer
instead of slash-alpha Tailwind colors so the robot glyph remains visible in
older enterprise browsers without `color-mix()` support.

Ultra/parallel runs keep every subtask in the current user turn marked as
running until that subtask returns a terminal result. If the parent run stops
first, the UI reports the subtask as incomplete rather than failed; long task
names are truncated inside their cards so the conversation stays within the
viewport.

Built-in subagents do not automatically load every enabled Skill, because some
Skills contain lead-agent-only orchestration instructions. Configure a
compatible Skill explicitly for a subagent when its workflow or bundled assets
are needed.

Complex tasks rarely fit in a single pass. DeerFlow decomposes them.

If Stop interrupts a delegated task before it can return a reply, the next
user turn marks that earlier task as cancelled in durable context so the lead
agent can delegate it again. Existing replies are preserved. Legacy replies
without structured status metadata may remain `in_progress`; DeerFlow does not
guess their outcome from reply text.

The lead agent can spawn sub-agents on the fly — each with its own scoped context, tools, and termination conditions. Sub-agents run in parallel when possible, report back structured results, and the lead agent synthesizes everything into a coherent output. Their internal AI and tool messages stay scoped to the delegated graph instead of entering the parent chat stream. Long-running sub-agents compact older history when summarization is enabled and re-inject the summary as guarded, hidden durable context before continuing, so recent assistant/tool activity remains grounded in the task. Provider/model request failures are reported as failed sub-agent tasks rather than successful results, so the lead agent and Web UI can react to them correctly. Collapsed sub-agent cards show the effective model and, when the provider returns usage metadata, a cumulative token total that updates after each completed sub-agent LLM call and persists after a reload. When token usage tracking is enabled, completed sub-agent usage is also attributed back to the dispatching step.

This is how DeerFlow handles tasks that take minutes to hours: a research task might fan out into a dozen sub-agents, each exploring a different angle, then converge into a single report — or a website — or a slide deck with generated visuals. One harness, many hands.

Content-less sub-agent final messages report `No response generated` instead of the literal text `None`. A content-less provider-error fallback reports its structured error detail when available.

### Sandbox & File System

DeerFlow doesn't just _talk_ about doing things. It has its own computer.

Each task gets its own execution environment with a full filesystem view — skills, workspace, uploads, outputs. The agent reads, writes, and edits files. It can view images and, when configured safely, execute shell commands.

Browser uploads are validated before they atomically replace thread files.
Browser-active/server-executable formats such as HTML, SVG, PHP, JSP, Python,
shell scripts, and executables are rejected; upload source code as plain text
or inside a reviewed archive. PDF uploads must have a real PDF signature and
must not contain JavaScript, automatic actions, embedded executable content, or
encryption that prevents inspection. Original uploaded documents are delivered
as downloads rather than rendered in the authenticated application origin
(safe raster images, media, and passive text remain viewable). Inert generated
PDF outputs retain the right-side preview with response CSP and an iframe
sandbox. Both ordinary generated PDFs and PowerPoint companion PDFs use a
server-rasterized, scriptless page gallery, so enterprise browsers do not
depend on a native PDF plug-in. LibreOffice's strictly shaped first-page destination is treated as
inert navigation metadata; indirect actions, scripts, attachments, encryption,
oversized files, original uploads, and other suspicious PDF features remain
excluded from the gallery and fall back to download.
PPT/PPTX files attached to an explicit `/ppt-master` request are preserved as
presentation templates rather than converted through the document parser.

When `WEKNORA_MINERU_ENDPOINT` is configured, conversation PDF, DOC/DOCX, PPT,
and PPTX uploads reuse that same enterprise parser without creating a
knowledge-base record. The Gateway keeps the original document and writes a
same-stem Markdown companion for the current task. Searchable PDFs retain their
native route, scanned PDFs use enterprise OCR, and presentations use the strict
presentation route. Markdown companions with or without a leading UTF-8 BOM
produce identical outlines and fallback previews; the original file bytes,
physical heading line numbers, and embedded U+FEFF characters remain unchanged.
Interactive DeerFlow DOCX attachments use pinned local
AnyDoc first. Ordinary WeKnora knowledge-base documents retain their explicit
parser rules, while only structurally large knowledge-base DOCX may use the
same bounded AnyDoc fast path. Large DOCX bypasses `StdMarkitdownParser` for
the official DocReader v0.8 native parser and has an isolated low-memory text
recovery for files that reach AnyDoc's XML-node safety limit; that limit is
never weakened. LibreOffice and MinerU remain bounded fallbacks, and legacy DOC
remains native-first. An explicitly enabled visual
supplement remains best-effort and cannot invalidate recovered native text,
tables, and images. Set
`DEERFLOW_UPLOAD_DOCUMENT_PARSER_ENABLED=false` to disable this bridge, or
override its base URL with
`DEERFLOW_UPLOAD_DOCUMENT_PARSER_ENDPOINT`. The older
`DEERFLOW_UPLOAD_PDF_*` names remain supported for deployment compatibility.
The enterprise overlay sets `DEERFLOW_UPLOAD_DOCUMENT_PARSE_TIMEOUT_SECONDS=1860`,
giving the Gateway 60 seconds beyond the Adapter's bounded 1800-second upstream
wait to receive and persist the completed response, and explicitly sets
`DEERFLOW_UPLOAD_DOCUMENT_PARSER_ENABLED=true`. The local Docker/PVC overlay
keeps that switch disabled because it cannot reach the enterprise MinerU
service.
For knowledge-base ingestion, the enterprise WeKnora 0.7.0 deployment keeps
MinerU admission on the small core/shared worker budget while assigning image
OCR/caption and other model-heavy fan-out to its independent 12-worker
enrichment pool. This keeps a large `multimodal` backlog from delaying newly
parsed PDF and Word documents without increasing concurrent MinerU calls.
When `uploads.auto_convert_documents=true`, or the enterprise-only
`DEERFLOW_UPLOAD_DOCUMENT_FALLBACK_ENABLED=true` override is set, an adapter
failure falls back to DeerFlow's local `convert_file_to_markdown()` conversion.
Legacy Word `.doc` files receive one additional text-recovery step: the task
sandbox converts the binary document to a temporary DOCX with LibreOffice, then
the normal local Markdown converter processes that DOCX. Temporary conversion
files are removed after the request.
The original upload is retained in every case; a processing warning is returned
only when all applicable parser and recovery paths fail.

When a generated Office artifact (DOC/DOCX, XLS/XLSX, ODT/ODS/ODP, or
PPT/PPTX) has no sibling preview, `present_files` asks the active sandbox's
LibreOffice installation to create `<stem>.preview.pdf` automatically. The
original Office file remains the selected download artifact while the generated
PDF is persisted and rendered through the server-side static page gallery.
The artifact panel keeps a visible loading indicator over that gallery until
the preview iframe's real load event fires. A bounded timeout replaces the
loader with a frame-only retry action instead of leaving an unexplained blank
panel.
Publication fails closed when conversion cannot produce the companion, so a
successful file card never points at an empty preview. This applies only to
task-generated outputs; original uploads retain their hardened download and
document-parsing boundaries.
For an explicitly activated Slash Skill, DeerFlow also checkpoints the original
task, selected Skill version, workflow/profile reads, project path, tool-backed
stage receipts, failed-gate resume pointer, and final artifacts. A later
confirmation or `continue` turn can therefore restore the same workflow after a
run boundary or context compaction. This host adapter does not copy a Skill's
workflow into application code. Project roots are accepted only from a
successful owning-tool receipt (for example `project_manager.py init`) or a
successful project-contract read/write; sibling research supplements and other
files under `projects/` cannot replace the active project directory. A newly
initialized project does not acquire `design_spec.md` or `spec_lock.md` as
resume requirements until those contracts have actually been created. A
resumed run must re-read the checkpointed
top-level route and latest stage authority before any other tool executes; the
selected confirmation surface is retained. The route is exclusive until a new
explicit Skill activation, and the checkpoint keeps up to 24 recent stage
authority documents separately from the route and durable project contracts so
large conditional branches survive compaction. If that loaded authority explicitly
declares that an artifact must be authored from a `${SKILL_DIR}/...` reference,
DeerFlow checkpoints the dependency and requires a real current-run read of the
reference before the artifact can be changed. User/tool-managed receipts named
by the loaded authority cannot be fabricated with generic file or shell tools.
A failed owning Skill script clears its gate when the same script later succeeds
for the same project and explicit stage. Retry comments, environment exports,
and whitespace may differ; a command for another project or stage cannot clear
the failure. The checkpoint stores that gate owner as one bundle (receipt, tool,
semantic action, stage, detail, and resume pointer). Auxiliary reads, project
repairs, user continuation messages, and confirmation fallbacks remain evidence
or waiting state only; they cannot replace or clear the owning gate. Legacy
checkpoints rebuild this bundle from their failed receipt before processing new
work. Long failed receipts persist a bounded head-and-tail diagnostic so the
owner command and its final blocker summary both survive compaction without
unbounded checkpoint growth. The model-facing `read_file` tool keeps `path`
required while treating its
display-only `description` as optional; the same rule applies to the Sandbox
command, listing, search, write, and replacement tools. Host UI metadata can
therefore never reject an otherwise valid Skill action.
The owning failure and any generated validation report are tied to the project's
mutation receipt at failure time. The generic rule requires the same owner to run
again after one durable repair, before another repair or an old-report read, so
the model cannot keep applying obsolete diagnostics to newer files. The
`ppt-master` first-page and final SVG gates are the narrow exception: their
official checker reports a complete issue batch, so the adapter permits one
project-scoped consolidated read/write repair pass across the affected SVGs,
blocks stale validation-report reads and stage advancement, and still requires
the exact checker to succeed before the gate clears. This remains a transaction
check over host receipts, not a copy of the Skill's validation schema.
Active `ppt-master` presentation-delivery model turns additionally have a
600-second total wall-clock limit. Empty, keepalive, and hidden-reasoning stream
chunks cannot renew it; timeout preserves the project plus its current stage and
resume pointer for a later `继续`. Other Skills and ordinary chats are unchanged.
For `ppt-master`, DeerFlow binds loaded authority to the current run rather than
to the summary fingerprint. Repeated same-run compaction therefore cannot turn
the Executor phase into a full-reference reload loop. A new run or an owning
Skill-script failure still requires exact authority re-reads; other Skills keep
their generic summary-fingerprint invalidation behavior.
When that owner is an exact local, project-scoped read-only Python validator,
DeerFlow can replay the checkpointed command deterministically; image generation,
export, publication, network submission, and other side-effecting actions remain
model/user owned.
While another owner failure remains active, an official recovery stage may run
a different Skill-owned read-only Python validator against the selected Skill
workspace before performing one atomic project repair. This diagnostic exception
rejects mutating flags and arbitrary Bash, and it does not clear the failed gate;
the original owner still has to succeed.
For selected-Skill Python owner commands, DeerFlow also removes a terminal
display-only `head`/`tail` pipe before Bash execution. This preserves the owning
script's real exit code and prevents SIGPIPE from turning partial generation into
a false success; unrelated pipelines are unchanged.
For `ppt-master` specifically, the host closes the official Executor authority
set before an SVG write: shared/SVG/native-shape/semantic references are always
present, while image, chart/native-data, structured, web-image, notes, animation,
audio, and locked mode/style references follow the persisted design/lock facts
and attempted action. Executor entry is also blocked while any `ai`/`web`/`slice`
resource remains `Pending` or `Failed`; a partial Design Spec replacement must be
followed by a complete read before those facts are trusted again. This adapter is
scoped to the vendored presentation workflow and does not alter other Skills.
For a confirmed Deck/Layout, that entry gate also reconciles the official
template handoff, Design Spec, and `spec_lock.md` before page generation. A
Design Spec that names installed prototypes cannot be paired with flat/style
reuse, and structured page writes must preserve the mapped prototype's real
Master/Layout identity, inherited background/layout atoms and definitions, and
slot bounds. This prevents a visually similar SVG from replacing genuine Deck
reuse and catches shell-authored pages immediately after the write as well.
For a single confirmed workspace, DeerFlow additionally compares the complete
portable `templates`, `images`, and `icons` mapping before allowing template
completion or Strategist Stage 1. A lone copied `design_spec.md` therefore cannot
produce a ready handoff while its page prototypes or background media are absent.
When the official Confirm UI is active and a valid
`recommendations.stage1/2/3.json` write is the last durable action, DeerFlow
treats the ready page as a real user-wait boundary. If the model ends on future
plan prose instead of invoking the owner wait, the run returns a resumable
confirmation handoff rather than consuming the generic unfinished-plan retry or
failing the presentation task. Other Skills retain the normal terminal guard.
For `ppt-master`, final publication invokes the
Skill's own delivery checker and verifies each capability requested by the user
or enabled in the generated design contract before the deck is accepted.
When that workflow uses the Confirm UI, DeerFlow requires the current run to
load the UI's Skill-owned contract before it can continue. This preserves the
official staged JSON schema across resumptions without copying that schema into
DeerFlow or fabricating a user confirmation file. Before a staged confirmation
wait begins, DeerFlow also asks the running official Confirm UI service to
validate its own recommendation. Invalid Stage 1/2/3 input is returned to the
agent immediately for correction instead of leaving the confirmation page in a
permanent “preparing” state; the validation rules remain owned by `ppt-master`.
After a successful owner-server launch, the host checkpoints its loopback URL
and reminds the model to surface it in visible Markdown; the frontend rewrites
that address to the authenticated thread-scoped proxy, replaces a visible
loopback address with a localized preview label, and selects the allowlisted
Sandbox port through AIO Sandbox's trusted proxy header. The owner process stays
alive on its documented wait command, and a timestamped owner `[ERROR]` remains
a failed receipt even when a shell pipeline masks the process exit code.
Dependencies declared by the bundled `ppt-master` are release-image content:
conversational `pip`/OS/Node package installation is rejected, and a genuine
missing import requires rebuilding and redeploying the Sandbox image.
Long artifact workflows also receive bounded frequency headroom for both
`bash` and `read_file` (warning at 120 calls and stopping at 240). The stricter
identical-call guard remains active at 3/5, so raising the cross-file read
allowance does not permit an agent to repeat the same read indefinitely.
Repetition history starts fresh for each run. A loop hard stop is terminal for
every downstream middleware layer; no workflow adapter may delete it and jump
back to the model. When a successful
`write_file`/`str_replace` actually changes a file, the window also resets so a
safe `read_file -> edit -> read_file -> edit` sequence can continue; writing
unchanged content does not count as progress.
The current separation between upstream Skill ownership and DeerFlow host
enforcement is tracked in
[`docs/ppt-master-host-adaptation-audit.md`](docs/ppt-master-host-adaptation-audit.md).

After each run, DeerFlow records a workspace change summary for the run-owned `workspace` and `outputs` directories. The Web UI shows a compact "files changed" badge on the assistant turn; opening it reveals created, modified, and deleted files with text diffs when safe to display. Uploads are excluded because they are user inputs, not agent-generated changes. Large, binary, or sensitive-looking files are shown as metadata only.

With `AioSandboxProvider`, shell execution runs inside isolated containers. With `LocalSandboxProvider`, file tools still map to per-thread directories on the host, but host `bash` is disabled by default because it is not a secure isolation boundary. Re-enable host bash only for fully trusted local workflows. Host bash commands have a wall-clock timeout, and long-lived processes should be started in the background with output redirected to a workspace log.

Enterprise provisioner deployments inject outbound proxy settings and the
current enterprise CA bundle into each generated Sandbox Pod. This keeps
provider-backed media skills working after proxy credential or CA rotation
without rebuilding the large Sandbox base image; provider-specific legacy TLS
compatibility and the generic Sandbox OpenSSL policy remain Pod-scoped, while
CA-chain and hostname verification stay enabled.

For provisioner/Kubernetes deployments, `sandbox.replicas` bounds active plus
warm Sandbox capacity per Gateway process. After a Gateway restart, inherited
Sandbox Pods are reconciled immediately and excess warm Pods are removed oldest
first. `sandbox.idle_timeout` controls reclamation of inactive entries.

The Provisioner accepts `SANDBOX_*_PROBE_*` environment variables for
startup/readiness/liveness timing and `SANDBOX_{CPU,MEMORY}_{REQUEST,LIMIT}`
for generated Sandbox Pod resources. Heavy all-in-one images should keep a
startup window of at least five minutes and a liveness failure window of about
one minute so browser/PPT workloads are not restarted during a transient CPU
stall. Readiness failures only remove the Pod from service; liveness failures
restart it, so avoid an aggressively short liveness window for long tasks.
For fixed-name Sandbox replacement, `SANDBOX_POD_DELETE_WAIT_SECONDS` defaults
to 30 seconds: the Provisioner waits for a terminating Pod name to disappear
before recreating it and never reports a Service-only Sandbox as ready.

This is the difference between a chatbot with tool access and an agent with an actual execution environment.

```
# Paths inside the sandbox container
/mnt/user-data/
├── uploads/          ← your files
├── workspace/        ← agents' working directory
└── outputs/          ← final deliverables
```

### Context Engineering

**Isolated Sub-Agent Context**: Each sub-agent runs in its own isolated context. This means that the sub-agent will not be able to see the context of the main agent or other sub-agents. This is important to ensure that the sub-agent is able to focus on the task at hand and not be distracted by the context of the main agent or other sub-agents.

**Summarization**: Within a session, DeerFlow manages context aggressively — summarizing completed sub-tasks, offloading intermediate results to the filesystem, compressing what's no longer immediately relevant. This lets it stay sharp across long, multi-step tasks without blowing the context window.

**Strict Tool-Call Recovery**: When a provider or middleware interrupts a tool-call loop, DeerFlow now strips provider-level raw tool-call metadata on forced-stop assistant messages and injects placeholder tool results for dangling calls before the next model invocation. This keeps OpenAI-compatible reasoning models that strictly validate `tool_call_id` sequences from failing with malformed history errors.

**Visible Tool-Run Completion**: For interactive turns, DeerFlow retries an empty post-tool final response once, then surfaces a visible error instead of reporting a silent successful run.

### Long-Term Memory

Most agents forget everything the moment a conversation ends. DeerFlow remembers.

Across sessions, DeerFlow builds a persistent memory of your profile, preferences, and accumulated knowledge. The more you use it, the better it knows you — your writing style, your technical stack, your recurring workflows. Memory is stored locally and stays under your control.

Memory updates now skip duplicate fact entries at apply time, so repeated preferences and context do not accumulate endlessly across sessions.
Operational task state remains thread-local: durable memory rejects knowledge-base IDs,
container/file paths, filenames, tool errors, and one-off artifact progress so an old
task cannot silently steer an unrelated future conversation.
One-off deliverable constraints such as page count, outline, template, brand/VI,
colors, logo, watermark, layout, and chart/matrix requirements are also kept
thread-local. They become durable preferences only when the user explicitly says
they apply in the future, by default, always, or across tasks.
Single-turn operational commands are treated the same way: an extractor rewrite
such as "User requires no network access" is rejected unless the conversation
contains explicit durable scope. Injection also hides legacy operational facts
and summaries, and the lead agent treats memory as descriptive background rather
than authority for network, tool, clarification, template, or output decisions.
Runs that explicitly select a WeKnora knowledge base do not enter the durable-memory
queue at all; their retrieved document facts remain scoped to that task's history.
Persisted memory defaults to Simplified Chinese unless the user explicitly
chooses another language. Existing summaries and facts written in another
language are normalized on the next eligible background memory update without
changing their identity or provenance. Memory updates are debounced, so the
Settings page may update shortly after a conversation rather than immediately.

During graceful Gateway shutdown, configuration lookup, memory-backend lookup,
and the bounded pending-update flush run off the event loop as one owned drain.
Repeated lifespan cancellation waits for that drain before propagating, so a
rolling restart cannot detach a still-running memory worker from process
teardown. Keep the configured flush budget plus surrounding shutdown work
inside Kubernetes `terminationGracePeriodSeconds`.

### Knowledge Base SSO

The workspace knowledge-base integration uses the current DeerFlow identity to
create or reuse a matching WeKnora account, then forwards knowledge-base and
document operations with that user's bearer token. DeerFlow administrator roles
are synchronized to the linked knowledge platform during SSO.
The DeerFlow knowledge page embeds the complete native WeKnora workspace on the
same origin and gives the iframe the full knowledge-page area without an extra
DeerFlow title/action bar. New Chat, Knowledge Bases, Shared Spaces, and Agents
therefore use the same native behavior and session as direct WeKnora access
instead of maintaining a second, reduced knowledge-management UI. Gateway's
knowledge-base APIs remain the integration boundary for the DeerFlow chat
composer and agent tools.
Knowledge answers preserve WeKnora 0.7's structured reference metadata while
normalizing native `<kb>` tags into same-origin citation links before the answer
is shown in DeerFlow. Lead and delegated agents may repair a model-truncated
`[citation:文件名]` only from a unique reference in the current turn; duplicate
file names across knowledge bases remain unresolved rather than being guessed.
Ownership is derived from the native creator ID, joined knowledge bases remain
distinct from resources created by the user, and organization shares remain
available through Shared Spaces. Resource reads and writes carry the tenant
identity that granted access, so cross-space lists do not lose permissions or
leak another tenant's data.
If a stale browser bundle or direct API client omits the tenant on a document-list
request, Gateway resolves the knowledge base only across the signed-in user's live
tenant memberships and forwards the matching consumer tenant as `X-Tenant-ID`.
It returns an authorization error instead of falling back to an unrelated active
tenant when the knowledge base is not present in those memberships.
Knowledge bases selected in the chat composer are forwarded as the exact scope
for DeerFlow's ten permission-scoped WeKnora wrappers, using a server-derived
per-user token. The tool family covers knowledge-base/document discovery,
parsed-document reading, search, RAG answer generation, agent discovery, and
official WeKnora agent-chat invocation. The wrapper names are DeerFlow-specific;
the underlying APIs are official WeKnora capabilities. Internal mutation-capable
agent tools remain behind WeKnora authorization and pending human approval is
never auto-approved. When no knowledge base is selected, the complete
`weknora_*` family is removed from lead and subagent toolsets, and historical
knowledge-base references in memory are explicitly treated as non-operative
context. See [docs/weknora_integration.md](docs/weknora_integration.md).
For the local Kubernetes stack, the native knowledge UI opens on port `32090`;
the optional same-origin `/platform/knowledge-bases` fallback is routed through
the stable `weknora-frontend` service name rather than an ephemeral ClusterIP.
The audited ARM64 enterprise overlay uses a locally imported PostgreSQL 16 image
with pg_search because the upstream ParadeDB PostgreSQL 17 image is not CPU-
compatible with the current datax nodes; see `k8s/enterprise/README.md` before
applying that overlay.
Its knowledge-base link is intentionally configured as the same-origin path
`/platform/knowledge-bases`, so deployments must not replace it with a browser-
local loopback address.
The embedded WeKnora UI also performs its session exchange through the
same-origin `/api/weknora/sso-session` endpoint rather than a fixed host or
port. A successful DeerFlow SSO exchange clears any stale standalone WeKnora
Lite-mode fallback, ensuring the full workspace navigation (including shared
spaces for authorized tenant roles) remains available after earlier auth
failures.
That exchange also carries a DeerFlow-owned UI policy. DeerFlow system
administrators retain the complete native WeKnora interface. Ordinary users are
presented as personal-workspace Owners and can use New Chat, Knowledge Bases,
Shared Spaces, and Agents. The global Publish Integrations navigation, system-level settings,
tenant-management controls, and logout remain hidden in the embedded
presentation. Entering Knowledge automatically collapses DeerFlow's sidebar and
expands the embedded WeKnora sidebar for both ordinary users and administrators;
users can then use WeKnora's native toggle normally. The product mark is rendered
as the text `知识库` instead of an image logo. The WeKnora deployment keeps
`create_personal` registration while disabling ordinary self-service tenant
creation, so first login creates one personal workspace but users cannot create
additional workspace/tenant boundaries. Shared Spaces remain the collaboration
and selective-sharing mechanism. This presentation policy does not replace
WeKnora RBAC; native authorization remains authoritative.
The enterprise WeKnora App mounts the deployment-specific combined CA bundle
from the `deer-flow-enterprise-ca` Secret so outbound HTTPS requests continue
to verify correctly behind an enterprise TLS proxy. Its `text-embedding-v4`
model uses an authenticated in-cluster Gateway relay; Gateway forwards the
unchanged OpenAI-compatible embedding request through its scoped enterprise
TLS compatibility path without persisting document text. In the enterprise
overlay, `WEKNORA_BAILIAN_TLS_COMPAT=true` applies the legacy-key compatibility
context only to the authenticated Bailian embedding and ASR clients while
retaining CA-chain and hostname verification.
The enterprise retrieval catalog uses the approved internal
`Qwen3-Embedding-8B` and `Qwen3-Reranker-8B` services through authenticated
Gateway relays. Gateway owns upstream credentials and direct-network routing;
WeKnora receives only a distinct relay credential. Embeddings are fixed at 1024
Matryoshka dimensions and L2-normalized so the PostgreSQL vector schema remains
stable. The approved internal retrieval credential admits up to 200 concurrent
upstream requests. Gateway therefore keeps a bounded 200-request embedding
ceiling with bounded busy retries; WeKnora's separate 2+1 document-worker limit
continues to control parser pressure, while the independent rerank path shares
the upstream application quota without exposing its credential. Existing populated knowledge bases remain on their prior embedding
model until a controlled re-parse succeeds; only then may the old external
embedding/rerank/chat entries be hidden.
Enterprise PDF/DOC/DOCX/PPT/PPTX uploads pass through a private compatibility
adapter. Legacy OLE2 `.doc` and `.ppt` files are identified from their compound
document root streams when WeKnora forwards the upload with a generic multipart
filename, so parser routing never depends on the original extension surviving
the internal handoff. Office files are converted to a temporary PDF for the
visual pass and, when needed, the approved internal MinerU-2605 API. The
enterprise adapter uses the provider-supported asynchronous,
Bearer-authenticated multipart `POST /tasks` contract with a bounded
1800-second end-to-end timeout. It submits each parse once, polls only the
accepted task ID, and retrieves that same task's result; short-lived status or
result HTTP 404 responses, business code `20006`, and transient status/result
connection or read-timeout failures receive bounded same-task retries. It never
duplicates the task submission or silently falls back to a second synchronous
request. The synchronous 20-page batching configuration is
retained only as an explicit emergency rollback path. Word's native
text/table extraction is merged only after its complete PDF visual pass
succeeds; conversion, MinerU, or required source-page rendering failure rejects
the incomplete document instead of falsely reporting visual success.
The Adapter requests Markdown plus inline images and treats
HTTP-200 business-error envelopes as failures. The
direct client disables environment proxy inheritance
but explicitly binds the mounted enterprise CA bundle, preserving certificate
and hostname verification even though Requests no longer merges CA environment
variables when `trust_env` is disabled. The upstream service admits 20 concurrent requests, but
each local Adapter process remains internally serialized because PDFium and the
document readers are not concurrency-safe. PDF pages are first inspected
locally: fully searchable PDFs stay on
WeKnora's builtin parser, while scanned or mixed PDFs are sent to MinerU for
OCR. MinerU inline images and scanned-page/presentation visuals are returned
through WeKnora's persistent image contract with document references instead
of being discarded during embedding. The MinerU endpoint and Bearer key are
supplied only through the runtime `deer-flow-mineru` Secret; they are never
stored in source or images. Other document formats continue to use WeKnora's
native parser selection. The official DocReader image is augmented at Pod
startup by a DeerFlow-owned, source-mounted openpyxl compatibility shim. It
ignores only the formatting-only `defaultColWidthPt` worksheet extension found
in some enterprise XLSX exports; the uploaded workbook is not rewritten, normal
XLSX behavior is unchanged, and unrelated workbook corruption still fails
closed. URL knowledge imports use the same source-mounted compatibility layer:
server-rendered HTTP(S) pages are fetched first through the dedicated DocReader
proxy and verified enterprise CA, every redirect is rechecked by the upstream
SSRF policy, and genuinely dynamic pages retain Playwright as a fallback. A
hard browser/network failure is surfaced as a failed parse instead of indexing
the text `Error parsing web page` as successful knowledge. Unknown, damaged, or encrypted OLE2 containers are
rejected rather than guessed as Word or PowerPoint. PDF files that open normally in browsers but are
rejected by PDFium during route inspection first try the independent builtin
parser before falling back to MinerU. A later source-page rendering error keeps
successfully parsed text instead of failing the entire upload. See
`k8s/enterprise/README.md` for the image build, Secret creation, thresholds,
and real-server validation procedure.
Knowledge images retrieved by WeKnora, including standalone image uploads and
images embedded in PDF/Word/PowerPoint documents, are displayed in both the
native knowledge workspace and DeerFlow chat. DeerFlow converts stable
`resource://` handles into authenticated, same-origin, knowledge-base-scoped
image URLs and restores an image selected by the current retrieval result even
when the final language-model rewrite drops its Markdown. Historical raw
resource handles use a tenant-authenticated compatibility endpoint; neither
path exposes WeKnora credentials or storage locations to the browser.
Large WeKnora search and answer responses keep their full result as a thread
output artifact while passing a compact, valid result envelope to the agent.
This preserves ranked excerpts, citations, selected answer images, and image
Markdown without consuming the model context with every full chunk.
The SSO bridge automatically registers the adapter as the WeKnora MinerU
default in spaces owned or administered by the signed-in user, while preserving
their other parser settings.
The enterprise Gateway bypasses the outbound proxy for the internal
`model.ai.cnooc` model endpoint while continuing to use the proxy for external
providers. The WeKnora App includes that exact hostname and the authenticated
in-cluster `gateway` model relay in its deployment-managed SSRF allowlist. This
permits the approved internal endpoints while retaining private-network blocking
for every other unapproved destination.
The Gateway image also carries a hash-verified `cl100k_base` tiktoken cache, so
memory token budgeting does not require a runtime download from public blob
storage.
In the enterprise deployment, WeKnora uses the internal
`DeepSeek-V4-Flash` (CNOOC Haineng) model as its default KnowledgeQA model;
`qwen-plus` and Bailian `deepseek-v4-pro` remain selectable alternatives.
Embedding, rerank, vision, and speech keep their dedicated built-ins. The
enterprise default initializer also enables VLM/ASR-backed image, scanned
attachment, and audio handling for existing objects and newly inserted
knowledge bases/agents. The Haineng model reads `DeepSeek_V4_API_KEY` from the
runtime Secret and is omitted by Gateway's seed path when that credential is
absent.

For ordinary users, the native New Chat page starts with
`DeepSeek-V4-Flash`. The four built-in agents are normalized to that chat model,
the enterprise reranker, VLM, and ASR models, with image and audio upload
enabled. New Knowledge Base dialogs keep only Basic Information in their left
navigation for ordinary users; creation retains all native type and indexing
choices in that page. Existing knowledge-base Settings additionally expose
Publish Integrations > Sharing Management so an owner can share the knowledge
base to a shared space with native read-only or editable permissions. Hidden
model, embedding, parser, chunking, image, audio, and storage settings still use
deployment-managed defaults. System administrators retain the complete
native configuration interface.

When `TAVILY_API_KEY` is configured for DeerFlow, SSO also creates or reuses a
native `DeerFlow Tavily` provider in each workspace the user owns or
administers, makes it the default search provider, and leaves the encrypted
credential under WeKnora's provider storage. This lets the built-in agents use
the same search service as DeerFlow without asking every user to enter a key.
In audited enterprise networks where a TLS interception proxy presents a
CA-validated legacy RSA certificate, set `TAVILY_TLS_COMPAT=true` only on the
Gateway. DeerFlow then uses a Tavily-scoped `DEFAULT:@SECLEVEL=1` HTTPS session
while preserving CA-chain and hostname verification.

The five fixed enterprise agent cards open same-origin paths below
`cnoocclaw.cnooc.com.cn`. In the Kubernetes local-PVC deployment, Nginx checks
the authenticated DeerFlow session before proxying each path to its private
tool service. No Data Lake Token or private service address is embedded in card
data or exposed to browser JavaScript.

## Recommended Models

DeerFlow is model-agnostic — it works with any LLM that implements the OpenAI-compatible API. That said, it performs best with models that support:

- **Long context windows** (100k+ tokens) for deep research and multi-step tasks
- **Reasoning capabilities** for adaptive planning and complex decomposition
- **Multimodal inputs** for image understanding and video comprehension
- **Strong tool-use** for reliable function calling and structured outputs

## Embedded Python Client

DeerFlow can be used as an embedded Python library without running the full HTTP services. The `DeerFlowClient` provides direct in-process access to all agent and Gateway capabilities, returning the same response schemas as the HTTP Gateway API. The HTTP Gateway also exposes `DELETE /api/threads/{thread_id}` to remove DeerFlow-managed local thread data after the LangGraph thread itself has been deleted:

```python
from deerflow.client import DeerFlowClient

client = DeerFlowClient()

# Chat
response = client.chat("Analyze this paper for me", thread_id="my-thread")

# Streaming (LangGraph SSE protocol: values, messages-tuple, end)
for event in client.stream("hello"):
    if event.type == "messages-tuple" and event.data.get("type") == "ai":
        print(event.data["content"])

# Configuration & management — returns Gateway-aligned dicts
models = client.list_models()        # {"models": [...]}
skills = client.list_skills()        # {"skills": [...]}
client.update_skill("web-search", enabled=True)
client.upload_files("thread-1", ["./report.pdf"])  # {"success": True, "files": [...]}
client.set_goal("thread-1", "finish the implementation and make all tests pass")
client.get_goal("thread-1")       # {"goal": {...}} or {"goal": None}
client.clear_goal("thread-1")
```

All dict-returning methods are validated against Gateway Pydantic response models in CI (`TestGatewayConformance`), ensuring the embedded client stays in sync with the HTTP API schemas. See `backend/packages/harness/deerflow/client.py` for full API documentation.

## Scheduled Tasks

DeerFlow now includes a first-class scheduled-task MVP in the workspace.

Editing a one-time task's title or prompt preserves its original execution time,
including seconds and the selected occurrence during a daylight-saving clock
rollback. Changing its date, time, or timezone recalculates the execution time;
switching tasks loads the selected task's own draft and schedule.

Current MVP capabilities:

- Manage tasks at `/workspace/scheduled-tasks`
- Search the currently authorized task list by title or prompt, composed with
  thread, status, and schedule-type filters.
- Reject one-time local times skipped by daylight-saving transitions before a
  task can be created or saved.
- Choose whether each scheduled task reuses a thread or creates a fresh thread per run
- Support `once` and `cron` schedules
- Preserve the selected model, mode, reasoning, subagent, and knowledge-base scope for every scheduled execution
- Select or edit the execution model per task; when configured, enterprise task creation defaults to `deepseek-v4-flash`
- Run background scheduled executions as non-interactive DeerFlow runs (`ask_clarification` is not exposed there)
- Persist overlapping occurrences in a durable queue, enforce one active occurrence per task, and expire entries that exceed `scheduler.queue_timeout_seconds`
- Apply the same global `scheduler.max_concurrent_runs` budget to scheduled and manual triggers, including concurrent claims in SQLite development and validation environments
- Pause, resume, trigger, duplicate, inspect paginated history, and delete tasks
- Optionally run multiple scheduler instances safely with database-backed leases and atomic completion bookkeeping
- Execute scheduled work through the normal DeerFlow run lifecycle

Scheduled execution is deliberately **non-interactive**: after a run starts, it cannot pause to ask the user a question, wait for confirmation, or collect temporary input. It is a good fit for fully specified recurring summaries, fixed-scope analysis, monitoring checks, and report generation. It is not a good fit for work that needs one-time codes, runtime uploads, temporary authorization, human approval, risk confirmation, dynamic choices, or iterative review. Move those inputs and approvals ahead of the run, or use a normal interactive thread instead.

Current MVP limits:

- No conversation-created `schedule_task` tool yet
- No text-only notification jobs
- No channel or GitHub dispatch targets
- No `interval` schedule type in this first cut

Enable background polling with `config.yaml -> scheduler.enabled` and restart the Gateway. The page reports the automatic poller's actual state in the current process; when it is stopped, tasks can still be saved and triggered manually, but they will not run automatically at their scheduled time. Manual trigger uses the same non-interactive scheduled-task execution path.

The Gateway stores only an allowlisted subset of the task's run context. Authentication identity, internal execution flags, runtime-only credentials, and unknown client fields are discarded. Tasks created before run-context persistence was added retain an empty context and therefore use the configured default model until they are edited or recreated.

## Terminal Workbench (TUI)

`deerflow` is a terminal-native workbench for people who live in the shell. It runs **embedded** over `DeerFlowClient` — no Gateway, frontend, nginx, or Docker required — while honoring the same `config.yaml`, checkpointer, skills, memory, MCP, and sandbox settings as the rest of DeerFlow.

![DeerFlow TUI](docs/tui/tui-preview.svg)

```bash
uv pip install 'deerflow-harness[tui]'        # optional 'textual' dependency

deerflow                                      # launch the terminal UI (TTY required)
deerflow --continue                           # resume the most recent thread
deerflow --resume THREAD                      # resume a thread by id
deerflow --print "summarize this repo"        # headless one-shot answer to stdout
deerflow --json  "hello"                       # headless newline-delimited StreamEvents
```

A keyboard-driven chat surface with a streaming transcript (Markdown-rendered answers), compact tool-activity cards, a `/` slash-command palette, `/goal` goal management, `/model` and `/threads` pickers, input history, and `Esc` / `Ctrl+C` interrupt. Sessions opened in the TUI also appear in the Web UI sidebar — it writes the shared thread store under the local default user, so terminal and web stay in sync **without running the Gateway**.

See [backend/docs/TUI.md](backend/docs/TUI.md) for the full guide.

## Documentation

- [Contributing Guide](CONTRIBUTING.md) - Development environment setup and workflow
- [Configuration Guide](backend/docs/CONFIGURATION.md) - Setup and configuration instructions
- [Architecture Overview](backend/CLAUDE.md) - Technical architecture details
- [Backend Architecture](backend/README.md) - Backend architecture and API reference

## ⚠️ Security Notice

### Improper Deployment May Introduce Security Risks

DeerFlow has key high-privilege capabilities including **system command execution, resource operations, and business logic invocation**, and is designed by default to be **deployed in a local trusted environment (accessible only via the 127.0.0.1 loopback interface)**. If you deploy the agent in untrusted environments — such as LAN networks, public cloud servers, or other multi-endpoint accessible environments — without strict security measures, it may introduce security risks, including:

- **Unauthorized illegal invocation**: Agent functionality could be discovered by unauthorized third parties or malicious internet scanners, triggering bulk unauthorized requests that execute high-risk operations such as system commands and file read/write, potentially causing serious security consequences.
- **Compliance and legal risks**: If the agent is illegally invoked to conduct cyberattacks, data theft, or other illegal activities, it may result in legal liability and compliance risks.

### Security Recommendations

See [scan and configuration baseline remediation](docs/security-scan-baseline-remediation.md) for the enterprise evidence collector, Nginx hardening, and on-host rollout checks.

For exposed host ports, use the [operator-run port guard](docs/high-risk-port-guard.md) with a reviewed per-node allowlist and timed rollback.

See [HTTP security remediation](docs/http-security-remediation.md) for the ingress method policy, exact Host allowlist (`cnoocclaw.cnooc.com.cn`, `heai.cnooc.com.cn`, and loopback development), forwarded-host sanitization, and server acceptance checks. Keep both approved public domains in the inner proxy and the separately managed outer TLS proxy, including during redeployment or rollback. Other public authorities require an explicit allowlist update; IP/NodePort checks must send an approved Host.

All proxy modes suppress Nginx numeric versions and upstream framework identification headers; Next.js also disables its own powered-by header. Apply the proxy configuration and rebuild/redeploy the frontend before verifying the public responses.

Enterprise external-employee proxy routes preserve their login requirement and upstream Host compatibility while applying the same forwarded-header and framework-version protections.



**Note: We strongly recommend deploying DeerFlow in a local trusted network environment.** If you need cross-device or cross-network deployment, you must implement strict security measures, such as:

- **IP allowlist**: Use `iptables`, or deploy hardware firewalls / switches with Access Control Lists (ACL), to **configure IP allowlist rules** and deny access from all other IP addresses.
- **Authentication gateway**: Configure a reverse proxy (e.g., nginx) and **enable strong pre-authentication**, blocking any unauthenticated access.
- **Network isolation**: Where possible, place the agent and trusted devices in the **same dedicated VLAN**, isolated from other network devices.
- **Use narrow, layered runtime controls**: Keep real credentials out of sandbox environments, redact secret values from tool output and persisted traces, and block exact destructive or cluster-control operations. Avoid broad keyword-based refusal of ordinary diagnostics, deployment work, user-provided logs/configuration, or document and data processing; those false positives can prevent legitimate tasks without improving the underlying secret boundary.
- **Stay updated**: Continue to follow DeerFlow's security feature updates.

## Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, workflow, and guidelines.

Regression coverage includes Docker sandbox mode detection and provisioner kubeconfig-path handling tests in `backend/tests/`.
Backend blocking-IO diagnostics are available from the repository root with
`make detect-blocking-io`: it statically scans backend business code for
blocking IO that may run on the backend event loop, prints a concise summary,
and writes complete JSON findings to `.deer-flow/blocking-io-findings.json`.
The JSON includes compact review records with `priority`, `location`,
`blocking_call`, `event_loop_exposure`, `reason`, and `code`.
Gateway artifact serving now forces active web content types (`text/html`, `application/xhtml+xml`, `image/svg+xml`) to download as attachments instead of inline rendering, reducing XSS risk for generated artifacts. Known binary MIME types such as PDF are served as binary inline responses rather than being misclassified by text-content heuristics, and the same endpoint accepts `HEAD` for lightweight preview discovery.
Final files published with `present_files` are persisted in the thread-scoped outputs directory before they appear in chat. A final file supplied from the current thread workspace is copied into outputs automatically, including when it must first be downloaded from a remote sandbox; arbitrary paths outside the current thread are still rejected.
Packaged `.skill` files are first-class downloadable artifacts: `present_files`
accepts valid ZIP-based packages containing `SKILL.md` and rejects malformed or
path-traversing archives before publication.
New tasks beginning with `/ppt-master` use a thin transport runtime on port
`8003`, while the real Agent CLI runs as a second container in that thread's
Kubernetes Sandbox Pod. The transport graph contains no LangChain model,
`create_agent`, or DeerFlow Agent middleware. The Sandbox-side host installs the
vendored upstream `ppt-master` v4.5.0 package into the Agent's normal Skill
directory, keeps an exact writable thread-persistent snapshot, persists the
Agent session pointer and command events, and exposes only a cluster-internal
authenticated control port. The upstream package owns route selection, stage
order, confirmations, validation, and recovery; DeerFlow carries authentication,
streaming, cancellation, Confirm UI proxying, Sandbox provisioning, and final
artifact/preview persistence. The host fails closed when no real Codex auth or
Responses-compatible provider is configured; a Chat-Completions-only endpoint
is not silently treated as a Codex Agent host. See
[`docs/ppt-master-isolated-runner.md`](docs/ppt-master-isolated-runner.md).
Provisioning creates the thread's writable `workspace`, `uploads`, and `outputs`
roots before either Sandbox container starts. The Agent host checks that contract
again before every initial or delivery-repair process and restores its writable
Skill/Codex runtime if the workspace disappeared, so a missing directory cannot
abort the PPT task and leave the UI polling a thread state that was never saved.
The bridge then waits for the Agent-host health endpoint independently of the
Sandbox command API and retries only bounded transient DNS/connection failures;
this prevents a newly provisioned Pod from failing merely because its sidecar
or ClusterIP DNS record became ready a few seconds later than the main container.
While an Agent turn is active, the runner also holds a cross-process filesystem
execution lease. Gateway warm-pool and explicit-destroy paths must obtain the
exclusive side of that lease before deleting the Sandbox, so an idle/capacity
decision in another Gateway process cannot terminate a live PPT task.
The shared and exclusive sides open the lease file read-write with append
semantics: Linux rejects `LOCK_SH` on a write-only descriptor, while opening an
existing lease must never truncate it.
Agent progress is normalized to the same plain message-dictionary contract used
by durable `task_*` events before it reaches the stream and event store; live
LangChain message objects never cross that transport boundary. The isolated
runtime derives the conversation title from the original Slash request without
invoking a second title model. Expanding the `ppt-master 官方工作流` card shows
the persisted command/query summary and provides on-demand tool input/output;
real Sandbox Live Preview URLs in that timeline are rewritten to the
authenticated thread proxy instead of exposing an unusable loopback link.
The browser keeps isolated-runner state/history reads on the same runtime after
refresh without first probing the ordinary checkpoint namespace. Thread task
cards are keyed by the current conversation, and stale artifact selection is
cleared before the next thread paints, preventing a running PPT workflow or a
brief file drawer from appearing in a newly opened ordinary conversation.

The local Kubernetes profile runs the host against Alibaba Cloud Model Studio's
native Responses API with `qwen3.7-max` and the existing
`DASHSCOPE_API_KEY`. The enterprise profile keeps the internal
`DeepSeek-V4-Flash` Chat Completions credential in Gateway and gives the Agent
host only a separate scoped relay key; Gateway translates the streamed
enterprise protocol to Responses. Codex namespace tool containers are flattened
to collision-safe Chat Completions function names at that boundary and restored
with their original namespace on the Responses stream, so newer Codex CLI
versions keep all grouped Agent tools without requiring native namespace support
from the internal model endpoint. The enterprise Codex profile disables its
provider-hosted `web_search`, and the relay defensively removes the two known
hosted web-search definitions while preserving ordinary local Agent tools;
unknown tool protocols remain hard errors. Provider configuration without its selected
`env_key` is rejected before Codex starts, and the enterprise bridge never
disables CA or hostname verification.

PPT Master publishes completed PowerPoint files into the thread-scoped outputs directory together with a same-stem `.preview.pdf`. The chat keeps the PPTX as the only user-facing downloadable deliverable while the artifact panel renders the paired PDF through a scriptless, server-rasterized page gallery; this remains usable in enterprise browsers whose native PDF plug-in cannot render the hardened sandboxed response. The companion PDF stays hidden from file cards and the artifact selector. A same-stem `.pdf` remains a compatibility preview fallback, ordinary artifact links in assistant messages open the right-side preview, explicit download links still download the presentation, and long filenames are truncated without displacing the panel controls. PPT completion requires the selected official route's successful native export and a real `.ppt`/`.pptx` publication; SVG pages, notes, specifications, reports, and other working files cannot be substituted for the final deck. Tool-bearing progress text remains visible in the conversation.
The same authenticated thread proxy makes PPT Master's standalone template and
design confirmation pages usable through the DeerFlow URL. Their state-changing
requests are accepted only when browser Fetch Metadata and Origin both prove the
request is same-origin; the rest of the Gateway remains protected by the normal
double-submit CSRF contract.
Writing or publishing a generated file no longer opens the artifact panel automatically. Deliverables remain available in the conversation and Files list; the chat column narrows only after the user explicitly opens a write step, artifact link, file card, or the Files control. Artifact reads use the authorized thread owner's storage bucket, so administrator read-only review and ordinary owner views resolve the same PPT preview companion.
Final assistant responses cannot claim a `/mnt/user-data/outputs/...` deliverable unless that file both exists in the current thread outputs directory and was successfully published with `present_files`. DeerFlow retries one invalid delivery claim with hidden execution guidance, then returns a visible delivery error instead of persisting a fabricated download link.

## GLM88 load diagnosis

The enterprise-only `release/mineru-provider-switch-20261014/glm-native/diagnose_glm88_bottleneck.py`
follows the accepted mixed-document load observation with bounded, read-only
MinerU timeout/quarantine summaries, indexed processing-stage statistics,
unexpired model-limit leases and shared-node Pod CPU attribution. Use its
separately packaged, hash-pinned operator script and runbook on the reviewed
`datax-01` deployment; it is not a generic local startup command. It preserves
88 WeKnora slots and16 MinerU workers and never submits model work or performs
tuning. Whole-image timings are not GLM-only latency, and incomplete history or
missing metrics must remain unknown. Further expansion requires result review.

The separate `release/mineru-provider-switch-20261014/glm-native/review_glm104.py`
prepares the next enrichment-only step (48 to64, aggregate88 to104). It binds the
accepted GLM88/load evidence and freshly checks queues, MinerU health, App-node
resources and DB connections before a server-only dry run. Core16, model128,
MinerU16, image and GLM references stay unchanged. It does not restart or stop
consumers; PASS is preparation for a separately reviewed controlled switch, not
performance acceptance. Pending queues need not empty. Future MinerU expansion
requires separate shared-node resource review.

After the reviewed104 preflight and explicit maintenance approval,
`glm-native/switch_glm104.py` performs one guarded enrichment-only restart. It
waits for three stopped/zero-claimed88 snapshots, preserves pending work, and
requires same-image104 pools plus unchanged GLM/model128/MinerU16 afterwards.
It has no automatic mutation retry, forced termination or rollback. Successful
runtime acceptance still requires post-switch real-backlog observation.

The104 environment patch can be acknowledged even when final MinerU acceptance
fails. Use `release/mineru-provider-switch-20261014/glm-native/diagnose_glm104_mineru.py`
to classify bounded logs and resource/health evidence without replaying the
switch. `live=16` counts live workers, not non-quarantined usable capacity;
`capacity_target_met` compares against the configured minimum6, not16. Retain
the original UNCONFIRMED receipt until a separate reviewed acceptance succeeds.

## License

This project is open source and available under the [MIT License](./LICENSE).

## Acknowledgments

DeerFlow is built upon the incredible work of the open-source community. We are deeply grateful to all the projects and contributors whose efforts have made DeerFlow possible. Truly, we stand on the shoulders of giants.

We would like to extend our sincere appreciation to the following projects for their invaluable contributions:

- **[LangChain](https://github.com/langchain-ai/langchain)**: Their exceptional framework powers our LLM interactions and chains, enabling seamless integration and functionality.
- **[LangGraph](https://github.com/langchain-ai/langgraph)**: Their innovative approach to multi-agent orchestration has been instrumental in enabling DeerFlow's sophisticated workflows.

These projects exemplify the transformative power of open-source collaboration, and we are proud to build upon their foundations.

### Key Contributors

A heartfelt thank you goes out to the core authors of `DeerFlow`, whose vision, passion, and dedication have brought this project to life:

- **[Daniel Walnut](https://github.com/hetaoBackend/)**
- **[Henry Li](https://github.com/magiccube/)**

Your unwavering commitment and expertise have been the driving force behind DeerFlow's success. We are honored to have you at the helm of this journey.

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=bytedance/deer-flow&type=Date)](https://star-history.com/#bytedance/deer-flow&Date)

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

### GLM104 failure diagnosis (2026-10-17)

`release/mineru-provider-switch-20261014/glm-native/GLM104-FAILURE-REASONS-RUNBOOK.txt`
documents a read-only follow-up for MinerU HTTP400/502 and WeKnora failed
processing steps. It separates parsing, Embedding, image enrichment and text
generation evidence without changing104 slots, MinerU16, models or queues.
Queries are index-backed and bounded; raw document/error content and credentials
are not exported. Collection completion does not prove recovery or authorize
batch retries or further expansion.

The subsequent `glm-native/MINERU-ERROR-MESSAGES-RUNBOOK.txt` describes a
log-only HTTP400/502 follow-up. It decodes escaped/truncated JSON diagnostics
and emits only a fixed vocabulary of error fragments with unknown text masked.
It does not export raw responses or call any database, health, model or parser
endpoint. Patterns are lossy clues; absent/rotated logs never prove recovery.

### WeKnora replica preparation (2026-10-17)

`release/weknora-replicas-preflight-20261017-r1/README.md` provides a standalone
Kubernetes GET-only inventory for cross-node replica planning. It examines
mounted PVC/PV bindings, placement, declared worker configuration and current
Pod/route identities without changing production or reading document data.
The official Helm guide supports multiple App replicas with verified shared
storage; the enterprise node-local volumes must not simply be scaled across
nodes. Successful collection is not approval to scale. File-access tests,
connection budgets and cross-instance task recovery remain separate gates.

### WeKnora104 expansion review (2026-10-17)

`release/weknora-glm168-review-20261017-r1/RUNBOOK.txt` describes the separate
read-only enrichment128/total168 proposal, with enrichment96/total136 as an
alternative. It binds the existing104 patch and corrected database diagnostic,
rechecks current consumers, MinerU16, model-default128, PostgreSQL waits and App
node resource reservations, and sends env-only server dry runs. Resource budgets
are unapproved planning scenarios, not applied changes. Candidate-specific PASS
does not authorize a restart, model override, storage move or live expansion.

The separate `release/weknora-glm168-gate-diagnostic-20261017-r1/RUNBOOK.txt`
explains the failed memory scenario and database gate without retrying review.
It keeps historical peak, current cache, process RSS and working set distinct,
prints every fresh gate snapshot, and never invents the discarded fifth sample.
Collection completion does not clear a failed expansion gate.
