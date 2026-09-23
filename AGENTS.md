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

The deployed Kubernetes stack runs the cooperating services below (the local
foreground/Docker development modes may omit Provisioner and PPT Runner):

| Service         | Port   | Role                                                                 |
| --------------- | ------ | ------------------------------------------------------------------- |
| **Nginx**       | `2026` | Unified reverse-proxy entry point — open this in the browser        |
| **Gateway API** | `8001` | FastAPI REST API + embedded LangGraph-compatible agent runtime      |
| **PPT Bridge**  | `8003` | Streaming/checkpoint bridge for the Sandbox-side external PPT Agent         |
| **Frontend**    | `3000` | Next.js web interface                                               |
| **Provisioner** | `8002` | Optional — only when sandbox is configured for provisioner/K8s mode |

Nginx is the single public entry: it serves the frontend and proxies `/api/langgraph/*`
to the Gateway's LangGraph runtime, rewriting it to Gateway's native `/api/*` routes; all
other `/api/*` go straight to the Gateway REST routers. See
[backend/AGENTS.md](backend/AGENTS.md) for the runtime and router detail.
The machine-to-machine OA policy-flow submit, status, and result routes under
`/api/v1/integrations/oa/policy-flow/jobs` are explicit Gateway exceptions that
must appear ahead of WeKnora's native `/api/v1/` catch-all and preserve the
`Authorization` header across Nginx.
The Next.js-owned `/api/runtime-config`, `/api/cnooc/external-agents/*`, and
`/api/shop-cart/*` routes are exceptions and must be proxied to Frontend before the
Gateway `/api/*` catch-all.

Gateway file security is the enforcement boundary for browser uploads and
artifact delivery. Reject active/server-executable upload extensions before
write, validate staged PDFs before atomic replacement, force original uploaded
documents and suspicious generated PDFs to hardened download responses, and
keep inert generated PDF previews behind response CSP plus a scriptless,
opaque-origin iframe sandbox. The browser-facing PDF preview route renders
validated generated PDFs from the thread outputs directory—including
PowerPoint companions—into a static HTML gallery of server-side PNG pages so
enterprise browsers do not depend on their native PDF plug-in. Original uploads
and suspicious PDFs remain download-only; keep PDFium work serialized and off
the event loop. LibreOffice's direct first-page destination
`OpenAction` is inert only when it matches the strict page-reference array;
indirect or executable actions remain download-only. Do not rely on filename
MIME guessing or frontend validation alone.
Treat all base XML and structured `+xml` MIME types as active content for both
ordinary artifacts and members read from `.skill` packages; they must be
attachments, never authenticated-origin inline responses.
After artifact read authorization succeeds, resolve the storage bucket from the
trusted `threads_meta.user_id` owner record rather than the current viewer. This
keeps owner and administrator read-only views on the same thread files while
preventing a browser from selecting an arbitrary user bucket.
Platform-managed `generate_image` and `generate_video` tools run in Gateway,
not in the Sandbox. Register each tool only when its operator-owned endpoint is
configured. Keep those clients direct (`trust_env=false`), redirect-disabled,
bounded by response/download limits, and restricted to the current thread's
outputs. Image generation accepts exactly one validated `b64_json` result.
Video generation persists an idempotent asynchronous job ID, permits only
current-thread local reference images or SSRF-screened public URLs, and trusts
download URLs only from the configured API host or an explicit allowlist. Never
silently resubmit an uncertain video job. Publish validated outputs atomically;
serve audio/video artifacts with `FileResponse` so Gateway never buffers a
large media file in memory. The public image/video Skills call these tools and
must not bypass them with provider SDKs, shell commands, or direct HTTP.
Conversation PDF/DOC/DOCX/PPT/PPTX uploads reuse the configured WeKnora/MinerU
adapter to write a same-stem Markdown companion without creating
knowledge-base records. The dispatcher marks only interactive DeerFlow uploads
with the trusted `conversation` parse profile; WeKnora knowledge ingestion
retains explicit `knowledge` routes. Conversation DOCX uses pinned local
AnyDoc first; only structurally large knowledge DOCX may use the same fast path.
Those large files bypass `StdMarkitdownParser` for official DocReader v0.8's
native parser and retain isolated low-memory text recovery without weakening
AnyDoc's XML-node limit. LibreOffice and MinerU remain bounded fallbacks, while
legacy DOC starts at DocReader. Searchable and scanned PDFs retain their
adapter-specific routing, and presentations use their strict presentation
conversion path. A hung local parser must be terminated with its process group
without failing the worker's liveness endpoint. Keep the complete interactive
adapter envelope below the Nginx upload timeout and do not repeat the same
unbounded local conversion in Gateway after that chain fails. The frontend marks PPT/PPTX
files submitted with an explicit `/ppt-master` request as
`presentation_template`; Gateway preserves those originals for the Skill and
skips both MinerU and local Markdown conversion. Ordinary presentation uploads
remain on the document-parser route. Presented Office outputs (DOC/DOCX,
XLS/XLSX, ODT/ODS/ODP, and PPT/PPTX) separately rely on the active sandbox's
LibreOffice to generate a missing `.preview.pdf`; keep this integration outside
individual Skill packages and read the original sandbox-visible workspace
source before Gateway publishes the normalized outputs copy. An Office document
is not published until that paired preview has been generated and persisted, so
clicking every successfully delivered Office artifact has a real browser
preview. Keep a visible loading state over the preview iframe until its real
load event fires, with a bounded retry state instead of an unexplained blank
panel. Hosted sandbox loopback URLs must never be
shown to browser users, and Chinese tasks publish a Chinese topic filename with
a date/time suffix instead of exposing the internal pinyin project slug.
New explicit `/ppt-master` tasks are routed through Nginx
`/api/ppt-master-runtime/*` to the dedicated `ppt-master-runner` transport
Service on port 8003. The runner has its own Deployment and Pod failure domain;
it must never be restored as a Gateway sidecar. Its startup gate requires three
consecutive database connection samples, so a transient Kubernetes DNS or
PostgreSQL readiness failure delays only the runner and cannot remove the
ordinary Gateway endpoint. Its graph is one external-process relay node:
it must never construct a LangChain model/Agent or attach DeerFlow Agent
middleware. The real Agent CLI runs in the `ppt-master-agent-host` container of
the thread's provisioned Sandbox Pod on authenticated ClusterIP port 8788. That
host installs the exact vendored upstream `ppt-master` v4.5.0 package into its
normal Agent Skill directory, refreshes a writable snapshot under
`/mnt/user-data/workspace/.ppt-master-runtime`, persists the Agent session and
public command/file events under `.ppt-master-agent`, and generates the paired
PowerPoint preview during final artifact handoff. Stream those receipts through
the existing durable `task_*` event protocol; never fabricate model/tool steps.
Provisioner initializes `/mnt/user-data/{workspace,uploads,outputs}` before the
Sandbox containers start. The Agent host revalidates those roots immediately
before every fresh or repair process launch and rebuilds its writable Skill and
Codex runtime if the workspace had to be restored; a missing directory must not
abort the task and leave the browser polling a state that was never persisted.
After the Sandbox command API is ready, the PPT Bridge must independently wait
for the Agent host `/health` endpoint because the two containers do not become
ready atomically. Treat bounded DNS/connect/read failures during startup and
polling as transient, but fail visibly after the configured deadline rather
than leaking a raw Kubernetes resolver exception to the browser.
The ordinary Gateway and `ppt-master-runner` run in separate Pods and have
independent in-memory Sandbox registries. Hold a shared cross-process execution
lease for the entire external
Agent turn; every idle, capacity, unhealthy, explicit, and shutdown destruction
path must obtain the corresponding non-blocking exclusive lease first. A busy
Sandbox is deferred and remains tracked, never deleted from another process.
Open that lease read-write without truncation: Linux `LOCK_SH` rejects a
write-only descriptor, while destruction still needs `LOCK_EX` through the
same helper.
Replay the original Slash request and require current upstream authority reads
on every Agent turn. Owning upstream commands must keep their real exit status:
do not allow terminal output-truncation pipes or shell fallbacks to mask a
validator/export failure. Before accepting completion, run the upstream delivery
checker and independently reconcile the new or changed PPTX package with every
explicit user capability. Reject visual chart imitations as native charts and
transitions as object animations, then resume the same Agent session and project
with the concrete mismatch for at most three repair attempts. Publish only the
accepted current-turn deck and a preview regenerated at least as recently as the
PPTX; stale output files from earlier turns are not deliverables.
The Agent host must fail closed without Codex auth or a Responses-compatible
provider and must never fall back to the old lead graph. DeerFlow owns only
authentication, streaming/checkpoint transport, cancellation, Confirm UI
proxying, Sandbox provisioning, and artifact persistence/preview; the upstream
package owns routes, stages, schemas, validators, and recovery. The control
Service must remain ClusterIP-only and reuse `SANDBOX_API_KEY`; every eligible
worker must preload the exact Sandbox image, and cross-node scheduling requires
RWX user-data plus ROX/RWX Skills storage. See
`docs/ppt-master-isolated-runner.md`.
Normalize Agent Host progress to plain message dictionaries before publishing
the shared durable `task_*` stream; live LangChain message objects must not cross
the PPT Bridge transport boundary.
The selected custom Agent provider's `env_key`, not merely its TOML, is the
credential boundary. Local Kubernetes uses Model Studio `qwen3.7-max` with
`DASHSCOPE_API_KEY` over native Responses. Enterprise Kubernetes instead gives
only `PPT_MASTER_RESPONSES_RELAY_KEY` to the Agent host; Gateway retains
`DeepSeek_V4_API_KEY` and exposes the exact self-authenticated
`/api/codex/deepseek/v1/responses` translation endpoint. Keep the relay key out
of the ordinary AIO Sandbox, force the audited enterprise upstream to stream,
discard raw reasoning, call `model.ai.cnooc` directly with `trust_env=false`,
and preserve CA-chain and hostname verification.
The enterprise Codex profile disables provider-hosted `web_search`, because the
internal Chat Completions endpoint cannot execute Responses-hosted tools. The
Gateway relay also omits the known `web_search` / `web_search_preview` tool
definitions defensively while preserving all ordinary function/custom tools and
namespace children; unknown future tool protocols remain fail-closed.

Explicit Slash Skill workflows other than the isolated runner, plus legacy
presentation threads retained during migration, persist their selected Skill/content hash,
original request, selected authority/profile, writable project root, bounded
tool receipts, current gate, and exact resume pointer in thread checkpoint
state. This is a generic host adaptation layer; do not duplicate a Skill's
workflow schemas, phase order, or validators in DeerFlow. A later `继续` or
structured confirmation restores the same enabled Skill and original task, and
durable context projects the workflow checkpoint after history compaction.
At each new run, the host must require the selected top-level workflow and the
latest stage authority documents to be re-read before any other tool executes;
remembering only their paths is not enough. Confirmation-surface choice is
checkpointed. When loaded Skill authority explicitly says to author, write, or
create a named artifact from a `${SKILL_DIR}/...` reference, checkpoint that
declared dependency and require a real current-run read receipt for the
reference before mutating the artifact. This is a generic authority edge; do
not infer it from filenames or duplicate the referenced schema in DeerFlow.
Files that the loaded Skill authority explicitly assigns to its
user/tool confirmation boundary must not be fabricated through generic file or
shell writes; they are produced only by the owning Skill server/command.
When that owner server starts successfully, checkpoint its loopback URL and
require the next visible progress response to include it as Markdown so the
frontend can rewrite it to the authenticated thread proxy. Keep the owner alive
on its documented default wait; do not shorten the wait or mask its exit status
through a tail/head pipeline. While that page owner remains active and its wait
has not failed, a plain-text chat confirmation request is also a surface switch;
discard it and continue through the Skill-defined page-owned action. Treat the
owner's timestamped `[ERROR]` output as
a failed receipt even when shell composition returns zero. Bundled `ppt-master`
dependencies are release-image content and must never be installed inside a
conversation; a real missing import is a Sandbox image release defect.
A failed Skill script receipt remains blocking until the same owning script
succeeds for the same project and explicit stage. Compare that semantic identity,
not the complete shell string: retry comments, environment exports, and whitespace
may change, but a different project or stage must never clear the gate.
When a forbidden generic write first exposes a Skill-managed confirmation
artifact and the official owner wait/completion command then fails on a real
project precondition, transfer recovery ownership to that command. This keeps
the managed artifact protected while allowing the documented project repair
and same-owner retry instead of trapping the run on the earlier fabrication.
For a single confirmed template workspace, check the complete portable file
mapping before Strategist contract authoring or SVG execution, and recheck it
after each template-copy command. Record a successful complete template copy as
a durable project mutation. An active Confirm UI page may be shut down or
replaced by chat only after its bounded wait truly times out or the user
explicitly requests that surface change.
Persist that failed gate as one owner bundle containing its receipt, tool,
semantic action, stage, detail, and resume pointer. Rebuild the bundle from legacy
failed evidence before processing later receipts. Auxiliary reads, project repair
writes, user continuations, and clarification waits may add evidence or mutation
progress but cannot replace or clear the owner; `read_file` requires only its path,
and all Sandbox tools keep their execution arguments required while treating the
display description as optional host metadata. Bound long failed
receipt details with both the diagnostic head and blocker-summary tail so context
compaction cannot discard the actionable end of an owning validator result.
Keep the selected top-level route, a bounded recent window of up to 24 Skill
stage-authority documents, and durable project contracts as separate retention
classes so a complete conditional branch can survive compaction without stage
churn evicting `design_spec.md` or `spec_lock.md`. Once a top-level route is
selected, a competing top-level workflow document is not a supporting stage;
reject that route switch until a new explicit Skill activation establishes a
new workflow.
An owning Skill-script failure invalidates the current authority-load receipt:
persist its bounded redacted detail, re-read those checkpointed authorities in
the same run, and only then choose a project-scoped repair. A changed compacted
summary likewise invalidates the authority-load receipt even when the run ID is
unchanged. The reload is a prerequisite inside the existing failed gate; it
must not replace that gate or its owning resume pointer with a temporary reload
gate that can later be mistaken for success.
Scope synthetic authority-reload tool-call ids to the exact recovery boundary
(run, compacted-context fingerprint, and failed-owner identity), not only to the
document path. Otherwise a legitimate second read in the same run is mistaken
for an already-collected receipt and the workflow can loop forever at the reload
barrier.
Permit one unchanged semantic owner retry for transient recovery. If that retry
fails again without a successful durable mutation inside the active project,
retain the completed authority reload and require a project repair before the
owner can run again; never cycle through unchanged validator retries and repeated
authority reloads.
Treat an owning failure receipt and every report it generated as a snapshot of
the project mutation version at which it ran. Generically, after the first
successful durable project repair, require an immediate semantic retry of that
same owner before any second repair, old-report read, or later-stage action. The
new owner receipt then either clears the gate or becomes the fresh diagnostic
snapshot. The `ppt-master` first-page/final SVG checker is a scoped exception:
allow one consolidated batch of project SVG reads/writes after its complete
report, while still blocking old validation-report reads and workflow advance;
only an exact successful checker retry clears the gate. Enforce both paths as
host transactions over receipt identity, never by duplicating the report schema.
For an active `ppt-master` presentation-delivery workflow, bound each async
model turn to 600 seconds of total wall-clock time. Provider keepalive, empty,
or hidden-reasoning chunks must not renew that boundary. End only the current
turn with the persisted stage and exact resume pointer so a later `继续` resumes
the same project; do not apply this deadline to unrelated Skills or chats.
For `ppt-master`, bind authority receipts to the current run rather than to the
mutable summary fingerprint. Long Executor phases may compact on nearly every
model/tool iteration; those same-run summary rewrites must not trigger a full
reference reload. A new run or an owning Skill-script failure still invalidates
the receipt and requires exact re-reads before execution resumes. Other Skills
retain the generic summary-fingerprint invalidation behavior.
If the saved owner is a single local, project-scoped Python validation command
declared under the selected Skill root, the host may replace a stale-report
diversion with that exact checkpointed validator call. Fail closed: never
automatically replay image generation, export, publication, network submission,
or another side-effecting owner action.
For any selected-Skill Python owner command, remove only a terminal display-only
`head`/`tail` pipe before execution. The Bash transport already bounds visible
output; allowing a truncator to own the pipeline exit code can hide a failed
validator/export or terminate an image job with SIGPIPE. Do not rewrite ordinary
non-Skill pipelines or non-terminal processing pipelines.
Treat an owning confirmation command's documented bounded user-wait timeout as
an external wait boundary, not a failed build/validation Gate. Keep the owner
server live, allow the Skill's documented page-to-chat fallback, and preserve
any owner-authored receipt found during the required one-time recheck. Do not
reset that receipt unless the user explicitly asks to reselect; an ordinary
`continue` reply may still confirm the most recent visible template question
when transparent tool progress or a host blocker appeared between them.
If a premature install attempt reopened the managed-artifact Gate after that
timeout, clear it through chat only when the timeout and surface-switch flags
remain checkpointed and a later real HumanMessage explicitly confirms the
selection; never let model prose alone satisfy that handoff. Restarting the
same Confirm UI daemon is transport recovery, not a new confirmation boundary,
so it must preserve those flags. For checkpoints written before that rule,
recover the boundary from the latest durable template-wait receipt unless a
later successful template wait or explicit template-phase reset consumed it.
If a legacy or resumed presentation workflow has no persisted top-level route,
preserve the Skill's declared serial bootstrap order: a successful
`attribution_guard.py` receipt, then `routing.md`, then exactly one selected
top-level workflow before build or delivery actions. When the model batches a
valid current prerequisite with redundant or later reads, execute only that
current prerequisite through the normal tool node instead of deleting the
whole batch and asking the model to rediscover it. Accept a project root only
from a successful owning-tool
receipt (for example `project_manager.py init`) or a successful project-contract
receipt; sibling research/image/export paths are not root authority. A resumed
default Generate route reloads `design_spec.md` and `spec_lock.md` only after
successful creation/read receipts have made them durable; a new project must
first complete template selection and Strategist confirmation. Stage-document
churn must not evict established contracts. Sequence-guard re-engagement is bounded to three unchanged invalid
attempts, after which the run reports the exact gate and resume pointer instead
of consuming the global recursion budget.
The DeerFlow adapter for the vendored `ppt-master` closes its documented
Executor reference set before SVG authoring. Always require the flat/shared
core, SVG effects, native-shape, semantic-SVG, and locked mode/style authorities;
add the documented image, chart/native-data, structured-PPTX, web-image, notes,
animation, and audio authorities only when their persisted design/lock facts and
attempted owner action trigger them. The adapter is scoped to `ppt-master` and
must not change another Skill's behavior. Step 5 is not complete while an
`ai`/`web`/`slice` resource row remains `Pending` or `Failed`; after a
`design_spec.md` replacement, require one complete read before Executor entry so
the resource and capability projection cannot remain stale.
When an owning Skill command fails, project-scoped repair commands remain
runnable without clearing that failed receipt; only a semantically identical
owner retry may clear it. Confirm UI wait, reset, daemon, and template-phase
completion modes are distinct owner operations even though they share one
Python entry point. Persist successful page template confirmation as a durable
milestone instead of inferring it from the bounded receipt tail. Read-only
diagnostic commands may inspect either the active project or the selected
read-only Skill root, because an owning script may direct recovery to its own
bundled resource library. A failed gate may also run one non-mutating,
Skill-owned Python validator against the active project or selected Skill
workspace when an official recovery stage requires that preflight before an
atomic project repair; reject write/fix/output modes and arbitrary Bash. Sequence-guard attempts are consecutive only within
one durable owner/mutation boundary; a project mutation or fresh owner failure
starts a new bounded audit instead of inheriting earlier invalid decisions.
For `ppt-master`, `present_files` is the final acceptance boundary: before a
deck is published it runs the vendored Skill's own delivery checker and compares
the actual PPTX package against the persisted user/design requirements (notes,
AI images, native charts, template masters/layouts, transitions, timed advance,
object animations, and narration/audio timing). SVG/specification intermediates
and a deck missing a requested capability never satisfy completion. A failed
delivery acceptance receipt may run project-scoped repair commands and retry
`present_files`; only a successful delivery retry clears that gate. Parse the
Skill's normal Markdown-emphasized contract labels such as
`**Native-ready**: yes` and `**Template Application**:` without losing those
requirements. When the official Confirm UI selects a Deck or Layout, delivery
must also reconcile its bound `template_selection.json` / `template_handoff.json`
with `spec_lock.md`: Strategist must persist `template_reuse_scope`; `style`
requires flat output, while `layout` / `mirror` require structured mode,
`template_adherence`, complete prototype/Master/Layout/page mappings, and an
actual package matching those declared counts. A single default Master and Blank
Layout must never masquerade as structural template reuse. Enforce the same
handoff before Executor SVG authoring, not only at publication: template-backed
Design Spec/lock writes require the current Strategist template authorities,
named prototypes cannot be downgraded to flat style, and every structured SVG
mutation must preserve the mapped prototype's Master/Layout root identity,
inherited layout/master atoms and referenced definitions, plus slot identity and
bounds. For a single selected workspace, template completion and Strategist
Stage 1 also compare every portable `templates`/`images`/`icons` file with the
project-local install; copying only `design_spec.md` can never create a ready
handoff. Shell-authored SVGs receive the same post-write check. A failed subprocess must not disable the general Bash transport; one unsupported explicit
completion claim may recover, while repeated no-progress completion becomes a
visible bounded failure. Keep tool-bearing progress text visible while its tools
execute; completion is derived from owning receipts and `present_files`, not by
hiding model text. A loop hard stop is terminal across middleware layers and must
never be deleted to auto-resume the same run.
For the `ppt-master` Confirm UI only, a successful
`recommendations.stage1/2/3.json` write while the owner page is active establishes
a durable user-wait boundary. If the model then emits future-plan prose without
the documented wait command, convert that terminal response into a visible,
resumable confirmation handoff instead of applying the generic unfinished-plan
retry/failure. Do not extend this exception to other Skills or to non-confirmation
tool results.

In the `k8s/local-pvc` stack, Nginx also serves WeKnora at
`/platform/knowledge-bases`. Keep that proxy on the `weknora-frontend` Kubernetes
service DNS name; never pin a ClusterIP, because recreating the cluster changes it.
The DeerFlow knowledge page embeds that same-origin native workspace. Its SSO
response assigns either the full `system_admin` presentation or the simplified
`personal_workspace_owner` presentation (Knowledge Q&A, Knowledge Bases, Shared
Spaces, and Custom Q&A). New SSO-provisioned accounts must use the deterministic
32-character credential accepted by WeKnora 0.8; login must retain the exact
pre-0.8 deterministic credential as a fallback so existing account hashes and
workspaces remain usable without reset. Keep WeKnora configured to create the first personal workspace while
disabling ordinary self-service creation of additional tenant workspaces;
native RBAC, not UI hiding, remains the authorization boundary. The embedded
page has no extra DeerFlow header. Entering it collapses DeerFlow's sidebar and
expands WeKnora's sidebar once for both ordinary users and administrators; the
native WeKnora toggle remains available afterwards so chat history stays
reachable. Ordinary users see Knowledge Q&A, Knowledge Bases, Shared Spaces,
and Custom Q&A in the native sidebar; the global Publish Integrations navigation and
system settings remain hidden and the embedded
user menu also hides logout. Their New Knowledge Base dialog exposes only Basic
Information, while preserving all native knowledge-base type and indexing choices
on that page. Existing knowledge-base Settings expose Basic Information plus
Publish Integrations > Sharing Management so owners can share their knowledge base
to a shared space with native read-only or editable permissions. Deployment defaults
supply the other hidden configuration. System
administrators retain the expandable complete navigation and full editor.

The phase-1 iFLYTEK SkillHub integration presents native SkillHub content as a
DeerFlow module, not as a standalone application or DeerFlow reimplementation.
The workspace entry `/workspace/skills` owns the visible header, role-aware
secondary navigation, embedded DeerFlow theme, loading state, and removal of
SkillHub's standalone header/footer; its iframe starts on native search under
the same-origin `/platform/skillhub/` prefix. Nginx routes that prefix to the
stable `skillhub-web` Service name. Keep that exact location more specific than
the generic WeKnora `/platform/` route, and keep SkillHub's `publicBaseUrl`, web
base path, and API base URL aligned with it. SkillHub owns authentication,
RBAC, publishing, review, governance, versioning, and withdrawal behavior.
Use `/api/v1/auth/me` roles only for navigation presentation: ordinary users
see discovery, stars, and subscriptions; native operator roles expose their
matching management entries. SkillHub's server remains the authorization
boundary. Reuse the authenticated DeerFlow user only through the exact Nginx
passive-bootstrap route: Gateway issues a short-lived role-free HMAC assertion,
Nginx overwrites browser-supplied identity headers, and the customized SkillHub
v0.2.18 server verifies it before creating a native session. Roles must still
come only from SkillHub's database; do not start the workspace iframe on
SkillHub's public landing page or account dashboard.
Because the passive-bootstrap request creates the first SkillHub browser
session, the customized server exempts only the exact
`POST /api/v1/auth/session/bootstrap` route from SkillHub CSRF validation while
the DeerFlow session plus short-lived HMAC assertion remain mandatory. Never
broaden that exemption to another SkillHub write route.
The workspace owner must explicitly POST that passive-bootstrap route before it
renders the iframe or calls a protected SkillHub API. Repeat the idempotent
bootstrap whenever the current DeerFlow identity changes and on every fresh
Skill Center mount so first-time accounts are created and returning profile
fields are reconciled. Never expose SkillHub's raw 401 response as a page; keep
the iframe unmounted on bootstrap failure and offer a bounded retry. On mobile,
place the native-role-derived management entry outside the horizontally
scrolling personal navigation so an operator cannot lose access merely because
the viewport is narrow.
DeerFlow must not infer SkillHub administrator rights from a DeerFlow session.
The detail-page runtime adapter downloads through the browser's native SkillHub
session, but the Gateway independently resolves the DeerFlow identity, limits
and hashes the uploaded archive, requires the registry slug to match
`SKILL.md`, runs safe extraction plus deterministic and LLM security scans, and
atomically installs only under that user's `skills/custom` tree. Record
namespace/version/SHA-256 provenance outside the installed tree and refuse to
overwrite an unmanaged custom skill. Install, upgrade, enable/disable, and
uninstall invalidate only the affected user's prompt cache. An unavailable LLM
scanner fails closed. A role expert with `skills: null` discovers the new skill
on its next run; an explicit whitelist or `skills: []` remains authoritative.
See `docs/skillhub-integration.md`.
When `TAVILY_API_KEY` is present, Gateway SSO also idempotently provisions a
native default `DeerFlow Tavily` provider in every workspace the user owns or
administers. The key must remain a runtime secret and must never be exposed in
the browser or committed to Kubernetes manifests.
The six built-in external-agent cards use their fixed same-origin HTTPS entry
points under `cnoocclaw.cnooc.com.cn` directly. Keep those six aliases on a
strict allowlist and do not rewrite them through the legacy launch API. Legacy
`datalake.cnooc` card records may still use the server launch route under
`/api/cnooc/external-agents/<agent>/launch`; that route reads the validated
HttpOnly `cnooc.platform_token` cookie and redirects only to the five legacy canonical
Data Lake paths, adding the current Token at redirect time. Keep `no-store` and
`no-referrer` headers, server-only cookie access, and the exact Nginx launch
prefix ahead of the general `/api/` Gateway catch-all. Never hardcode a Token,
expose it to frontend JavaScript, or restore the legacy `/agent-tools/*`
reverse proxies.

The mobile H5 sub-application is the same workspace frontend opened by a
trusted Android WebView or iOS WKWebView at `/workspace/chats/new`; it is not a
second deployment or a forked business flow. Keep authentication in the
existing same-origin HttpOnly cookie, preserve safe-area and dynamic-viewport
handling, and retain the mobile navigation escape hatch over the embedded
WeKnora workspace. File upload, artifact preview/download, agents, scheduled
tasks, and settings must continue to use their normal APIs and authorization
boundaries. See `docs/mobile-webview-integration.md` for the App contract and
release matrix.
On the mobile welcome surface, keep the product introduction compact, pin the
composer above the safe area, keep its primary controls on one line, and expose
welcome actions through a horizontally scrollable rail. The mobile navigation
drawer may adapt width and touch targets, but it must reuse the existing routes,
permissions, history, and settings components instead of creating a second
navigation or conversation state model. The top mobile mode pill is another
view of the existing thread mode and reasoning settings, not independent mobile
state. Keep its labels localized. The CNOOC assistant avatar is a real-alpha
transparent character cutout with reduced-motion support; never place a
rectangular or circular background plate behind it. Desktop composition remains
unchanged.

The shared settings dialog keeps safe-area ownership on its outer shell and
content padding on a separate inner flex frame. Preserve that separation and a
bounded viewport inset so legacy enterprise browsers cannot overlap the title,
navigation border, and settings content. The dialog owner must live outside the
mobile Sidebar Sheet so closing the drawer cannot destroy a deferred Settings
open; wait for the drawer transition before opening the dialog on mobile.

External click telemetry from the Haineng and collaboration platforms enters
through `POST /api/agent_click`. Keep `EXTERNAL_AGENT_CLICK_INGEST_KEY` in the
runtime Secret and require it as a Bearer token or
`X-External-Agent-Click-Key`; never expose the key in browser JavaScript. The
Gateway normalizes the legacy five agent names and stores events in PostgreSQL
audit logs. Dashboard totals preserve Haineng and collaboration as independent
sources, exclude `通用智能体平台`, use the Asia/Shanghai calendar day, and show a
fixed latest-30-natural-day trend independent of the dashboard's main range.
The administrator dashboard uses bounded time-indexed projections, a
single-flight response cache, and an optional shared Redis cache. Its compact
frontend summary excludes WeKnora and unused structured-event detail; WeKnora
loads through the independent `/api/admin/dashboard/knowledge` route. Keep
browser responses `private, no-store`, keep cache keys deployment-scoped, and
retain `0015_dashboard_query_indexes` for online-safe server upgrades. Short
rolling windows refresh once per minute, while fixed or greater-than-seven-day
ranges are manual-only.
Large compact ranges use versioned daily dashboard facts from migration
`0016_dashboard_daily_rollups`; migration `0017_dashboard_hourly_rollups`
adds a 72-hour hot window so rolling 24-hour queries read completed-hour facts
and only fractional edge-hour raw data. Applying those additive migrations must
not switch an existing installation. With
`DEER_FLOW_DASHBOARD_ROLLUPS_ENABLED=false` (the default), the bounded cached
compatibility path continues reading source history. Enable the flag only after
daily/hourly backfill, readiness, and scheduled maintenance are verified. Once
enabled, incomplete required facts fail fast instead of triggering a full
historical scan. Daily boundary slices recursively use available hourly facts.
Gateway startup and HTTP requests never maintain these facts; the suspended-by-
default, CPU/memory-limited CronJob invokes the single-connection maintenance
CLI in bounded day/hour batches. Preserve its advisory lock, SQL timeouts,
durable late-change backlogs, admin-only readiness endpoint, and isolated
`DEER_FLOW_DASHBOARD_DATABASE_URL` read pool. Enterprise strongest-isolation
mode requires and verifies a transaction-read-only physical standby; it must
disable only the dashboard rather than falling back to the primary. Dashboard
failure or read-replica misconfiguration must not prevent normal DeerFlow
startup, chat, or role-expert execution. In that mode the maintenance CLI reads
source rows and EXPLAIN plans from the standby and uses the primary only for
small atomic fact/state upserts; its change cursor must never advance beyond
`pg_last_xact_replay_timestamp()`.
Slow-run and high-token dashboard cards use the bounded
`/api/admin/dashboard/run-rankings` top-N projection rather than the legacy
full-window observability analytics response.
External-platform cumulative clicks and its fixed 30-day trend use the projected
compatibility audit stream while rollups are disabled. Once enabled they use
daily facts plus today's bounded slice; missing history then degrades only that
panel and must never trigger an all-history audit-log scan.

Group OA policy-to-JointJS requests enter only through the dedicated
machine-authenticated `/api/v1/integrations/oa/policy-flow/jobs` API. Pin the
target owner and personal custom Agent in Gateway runtime Secrets. Polling-only
mode sets `OA_POLICY_FLOW_CALLBACK_ENABLED=false`; callback URL and callback
credentials are then optional, terminal jobs use `callback_status=disabled`,
and no callback attempt is scheduled. When callback delivery is enabled, keep
its target and credentials operator-owned. Callers may submit only one document, a business request ID, and
bounded JSON fields. The durable SQL job must launch through the existing
Gateway run path as the real owner, require exactly one valid JSON artifact
from `present_files`, and always keep authenticated status/result polling.
Never let OA select an owner, Agent, callback URL,
internal thread/run endpoint, or filesystem path. See
`docs/oa-policy-flow-integration.md`.

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
- Agent-managed creation is opt-in through `skill_evolution.enabled`. When
  enabled, `skill-creator` must use `skill_manage`, which writes only to the
  effective user's custom-skill root, refreshes that user's prompt cache, and
  keeps deterministic SkillScan plus fail-closed LLM moderation in front of
  every write. Normalize moderation output through the shared LLM text helper:
  Responses reasoning/tool blocks are not trusted decision text.
- SkillScan and Skill Review share one code/executable-file classifier. Review
  snapshots retain binary bytes as base64, scan every non-symlink file except
  deliberately unsafe eval-fixture `SKILL.md` samples, analyze undecodable code
  through a lossy decode with an explicit finding, and fail closed on missing or
  colliding snapshot content.

Durable-memory boundary note:
- Long-term memory is descriptive background, not task authority. One-turn
  operational directives about network access, tools, clarification, templates,
  or output formats remain thread-local unless the user explicitly marks them
  as future/default/always/cross-task preferences.

Platform-confidentiality boundary note:
- Do not use broad keyword refusal for platform, diagnostic, deployment, log,
  configuration, environment, database, Kubernetes, or Docker questions.
  Those requests must reach the model so normal troubleshooting and engineering
  work can proceed. The shared confidentiality middleware is advisory only and
  narrowly prohibits disclosure of actual system prompts, credentials, API
  keys, tokens, and passwords or assistance bypassing access controls.
- Concrete enforcement remains layered at execution and data boundaries:
  sandbox auditing blocks exact cluster-control commands, protected secret
  paths, secret environment reads, internal platform service targeting, and
  destructive operations; sandbox environment policy removes unapproved
  platform secrets; bash output and persisted/observable run context redact
  secret values. Harmless runtime diagnostics, bounded network probes, long
  generated document/presentation commands, and configured business-data tools
  must remain usable. Dynamically created Kubernetes sandbox Pods must keep
  `automountServiceAccountToken: false`; conversational tasks never need
  cluster API credentials.
- These boundaries must not block public product-usage help, WeKnora retrieval,
  operator guidance, analysis of user-provided logs/configuration/code, or
  analysis of user-provided files under `/mnt/user-data` (including Excel, CSV,
  SQLite, and DuckDB). Never weaken it by adding platform database credentials
  to a skill or by treating platform storage as user-uploaded data.

User-scoped Data Lake note:
The chat composer keeps the existing file-list/API implementation behind a
Data Lake control. `datalake_enabled` is persisted in the user's frontend local
settings, defaults to `false`, applies to every thread, and is sent explicitly
in each normal, custom-agent, regenerated, and branched run. Enabling it first
calls the authenticated `POST /api/v1/data-lake/auth/token` route. If the
HttpOnly platform-token cookie is absent, Gateway persists only the SHA-256
digest of a user-bound 60-second challenge, reuses the bounded appCode cache,
calls the platform token exchange, verifies the returned Token against the
CNOOC identity service, binds only its unique SHA-256 digest to the current
`users` row, and writes the raw Token to the HttpOnly cookie only after that
binding commits. CNOOC-authenticated users match the trusted platform `userid`
exactly. IAM-authenticated users require a non-empty IAM `oauth_id` and an exact
canonical `@cnooc.com.cn` email match with the trusted Data Lake identity;
local users, other OAuth providers, and non-corporate or mismatched emails stay
fail-closed. The appCode request's `timeStamp` is the first eight digits of
the Unix-seconds string, not the complete ten-digit epoch. CNOOC Token login
establishes the same binding before issuing
cookies. The authenticated `GET /api/v1/data-lake/auth/session` route compares
the Cookie digest with the current user's binding; every Next.js file-list,
download, and external-agent launch route must pass that check before consuming
the Token. A mismatch clears the stale Cookie and the frontend resets
`datalake_enabled` to `false`; a temporary Gateway failure returns 503 without
destroying the possibly valid authorization. A token-exchange HTTP 200 response
whose business code still rejects the account after appCode refresh maps to
`403 data_lake_permission_denied`; the frontend shows the localized no-access
message and stays disabled. The shared Nginx entry must route the
`^~ /api/v1/data-lake/auth/` browser-auth prefix to Gateway before the WeKnora
`/api/v1/` catch-all. The public exact callback
`POST /api/v1/data-lake/auth/callback` is CSRF-exempt, body-limited, documented
with a strict OpenAPI request body, and rate-limited by the shared Nginx entry
before it resolves the one-time persisted challenge. Rate-limit responses use
the callback's JSON error contract. When the
persisted setting is already `true`, opening the file list repeats the
idempotent token call before loading files so an expired or logout-cleared
cookie cannot bypass authorization. Logout resets the setting to `false`.
When the setting is `false`, Gateway and lead/delegated agent assembly remove
the three AppForge Data Lake tools, and the invocation guard rejects stale direct calls
before configuration or network access. Internal `/api/shop-cart/*` routes and
source module names remain unchanged for compatibility.

Academic research skill note:
- `skills/public/academic-research/` is the single platform-visible compatibility
  entry for the vendored academic research package.
- The package itself lives at `skills/third_party/academic-research-skills/`, outside
  the recursively scanned public/custom/legacy categories. Keep the adapter runtime
  paths under `/mnt/skills/third_party/academic-research-skills/`, upstream license,
  and package manifest aligned; its component `SKILL.md` files must not be exposed as
  separate DeerFlow public skills.

Scheduled-task note:
- Scheduled tasks add a workspace page at `/workspace/scheduled-tasks` plus a background scheduler service gated by `config.yaml -> scheduler.enabled`. Keep `once`, `cron`, and minimum-60-second `interval` schedules aligned across API schemas, persistence, and the UI; cron previews are computed by the Gateway.
- Scheduled background runs are intentionally non-interactive: they execute through the normal run lifecycle, but the lead-agent toolset excludes `ask_clarification` when `context.non_interactive=true`. The key is honored only for internally-authenticated callers (the scheduler launch path); client-supplied `context.non_interactive` is dropped.
- Scheduled tasks persist an allowlisted run context (model, mode, reasoning, subagent, and WeKnora scope) and pass it through the normal Gateway run path. The creation UI prefers the enterprise `deepseek-v4-flash` model when available, lets users choose another configured model, and does not inherit later chat-page model changes. Never persist caller identity, internal execution flags, runtime-only credentials, or arbitrary client context on the task row.
- Scheduled occurrences use durable `queued`/`launching`/`running` admission, one active occurrence per task, a global concurrency budget shared with manual triggers, and atomic terminal bookkeeping. Do not restore the former `skip` overlap default. Multi-instance mode is opt-in and requires PostgreSQL, database run events, and run-ownership heartbeats.
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
- Keep the official WeKnora DocReader image unmodified. XLSX exporters that add
  the formatting-only `defaultColWidthPt` worksheet attribute are supported by
  the DeerFlow-owned `weknora-docreader-xlsx-compat` ConfigMap mounted through
  `PYTHONPATH`. The shim may discard only that confirmed unsupported attribute;
  never rewrite source workbooks or broadly swallow unknown openpyxl fields.
- The patched WeKnora UI must fetch `/api/weknora/sso-session` through a same-origin relative URL in both `config.js` and the `index.html` fallback. These ConfigMap files are mounted with `subPath`, so restart `weknora-frontend` after changing them.
- Keep the WeKnora UI on the official v0.7.0 multi-platform manifest digest declared in both the base and enterprise Kustomize bundles. That build understands the App's `resource://` and request-local image handles and uses authenticated, knowledge-base-scoped file retrieval; the earlier UI digest renders retrieved knowledge images as failed previews. DeerFlow chat separately rewrites those handles to its tenant-aware `/api/weknora/knowledge-bases/{id}/files` proxy; never expose WeKnora bearer tokens or direct storage paths to the browser.
- DeerFlow chat exposes ten permission-scoped WeKnora wrappers for knowledge-base and document discovery/read, search, RAG ask, and native agent discovery/invocation. The wrapper names are DeerFlow-owned while the endpoints are official WeKnora v0.7.0 APIs. Keep every tool bound to the current user's server-derived token and explicit selected knowledge-base IDs. Native agent tool execution stays inside WeKnora; omit raw system prompts and never auto-approve a returned human-approval event.
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
  fallback in generated Sandbox Pods. The vendored upstream `ppt-master`
  selects this provider with `IMAGE_BACKEND=qwen` and reads
  `QWEN_BASE_URL`/`QWEN_MODEL`; do not assume its Qwen backend reads the
  generic `DASHSCOPE_BASE_URL`/`DASHSCOPE_IMAGE_MODEL` variables used by other
  image-generation code. Keep the domestic endpoint root and approved model in
  the Provisioner passthrough contract while continuing to source
  `DASHSCOPE_API_KEY` from the runtime Secret. The audited enterprise network permits
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
- Long presentation workflows may legitimately invoke `bash` and `read_file`
  far more often than ordinary tools. Keep the enterprise runtime's per-tool
  loop-detection overrides bounded (currently warn at 120 and stop at 240)
  while retaining the global 30/50 limits for all other tools and the
  identical-call loop guard. Repetition history is scoped to the current run,
  and a successful `write_file`/`str_replace` that changes durable content
  resets its repetition window. This keeps required read-before-write repair
  cycles from being mistaken for a loop while unchanged writes remain bounded
  by the 3/5 identical-call guard. A successful recovery must not be mistaken
  for a cross-file read loop merely because the workflow has already loaded
  many authority and project files.
  Interactive web runs request a bounded `recursion_limit=2000`; LangGraph
  super-steps include middleware and tool nodes, so reaching the cap does not
  by itself prove a model loop. Diagnose durable tool progress before changing
  either the budget or loop thresholds.
  The PPT-capable Sandbox image must also extend both outer AIO authentication
  Nginx proxy locations beyond the stock 60-second idle timeout. The client
  command hard timeout remains authoritative, so a legitimate long image or
  document command can finish without making shell execution unbounded.
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
- The audited enterprise TLS proxy presents a CA-validated legacy RSA certificate that OpenSSL 3 rejects at its default security level. Keep `gateway-openssl-compat.yaml` scoped to the Gateway and PPT Runner Pods through `OPENSSL_CONF`, retain `WEKNORA_BAILIAN_TLS_COMPAT=true` for the authenticated Bailian embedding/ASR adapter clients, and retain `TAVILY_TLS_COMPAT=true` for DeerFlow's Tavily-only verified HTTPS session. These clients construct explicit `DEFAULT:@SECLEVEL=1` SSL contexts while CA-chain and hostname verification remain enabled. Never replace this with `verify=false`/`--insecure` or a host/cluster-wide downgrade, and remove the compatibility flags after the proxy certificate is modernized.
- Keep the internal model host `model.ai.cnooc` in both Gateway proxy-bypass variables. Its streamed chat endpoints are direct-only in the audited enterprise network; sending them through the outbound proxy causes connection resets.
- Configure the internal DeepSeek chat endpoint with
  `deerflow.models.robust_openai:RobustChatOpenAI`. The enterprise gateway can
  return application errors as HTTP 200 envelopes with `choices=null`; this
  adapter preserves bounded `code`/`msg` diagnostics so the LLM error
  middleware can distinguish transient failures from authentication, quota,
  and context-limit errors. Keep streaming enabled: the audited upstream safety
  gateway rejects non-streaming OpenAI tool-call completions with business code
  `20009`, while the equivalent streamed tool call succeeds. The adapter
  normalizes JSON-encoded streamed chunks before LangChain consumes them so a
  scalar compatibility response cannot regress to `str.model_dump()`. The
  enterprise DeepSeek model also sets `cumulative_stream_usage: true` because
  its stream repeats cumulative token snapshots; the adapter converts those
  snapshots to completion-scoped deltas before LangChain aggregation. Never
  enable that option for providers that already return per-chunk usage, and
  never recover or execute nested tool calls from an upstream error message.
  Null-choices failures receive at most one retry.
- Enterprise Qwen3 Embedding and Reranker requests pass through authenticated Gateway relays. Keep the upstream `CNOOC_RETRIEVAL_API_KEY` separate from the independently generated `WEKNORA_INTERNAL_MODEL_RELAY_KEY`, never expose either key to browsers, and keep the upstream client direct with `trust_env=false`. The Qwen3 embedding relay requests 1024-dimensional Matryoshka output; if the approved endpoint returns 4096 values, Gateway truncates the prefix to 1024 and L2-normalizes it before returning the OpenAI-compatible response. Changing this dimension after documents are indexed requires a complete re-parse of every affected knowledge base. The approved retrieval credential admits 200 concurrent upstream requests, so keep `WEKNORA_INTERNAL_EMBEDDING_CONCURRENCY=200` as both the default and hard Gateway ceiling; WeKnora's 2+1 worker limits continue to control document parsing pressure. Retain bounded retries for transient business code `10020` to absorb brief shared-capacity races with rerank traffic.
- Keep the exact internal model hostname `model.ai.cnooc` and the authenticated in-cluster `gateway` relay Service in the WeKnora App's comma-separated `SSRF_WHITELIST_EXTRA` as well as both proxy-bypass variables. WeKnora correctly blocks private addresses by default; this narrow deployment allowlist permits only the approved internal endpoints without opening the surrounding private network or disabling SSRF validation.
- Route additional enterprise embedding/rerank providers through authenticated Gateway adapters when practical, so WeKnora continues to call the stable `gateway` Service while Gateway owns upstream credentials, CA trust, proxy routing, timeouts, and audit-safe error mapping. If a provider must be called directly from WeKnora, add only its reviewed exact hostname to `SSRF_WHITELIST_EXTRA`, `NO_PROXY`, and `no_proxy`; never pre-authorize a broad RFC1918 CIDR for anticipated providers.
- Keep the CNOOC data-lake OAuth endpoint in both Gateway proxy-bypass variables by hostname and audited service IP (`datalake.cnooc` and `10.77.78.223`). The SSO validation endpoint is direct-only in the enterprise network; sending it through the outbound proxy times out before the platform Token can be validated.
- Enterprise deployments set `DEER_FLOW_SSO_ONLY=true` on both Gateway and
  Frontend. Auth pages must redirect unauthenticated browsers to the validated
  HTTPS `DEER_FLOW_UNAUTHENTICATED_HOME_URL`, and Gateway must reject local
  login, registration, setup initialization, password changes, and generic
  OIDC flows even when called directly. Keep the CNOOC token SSO route enabled;
  UI hiding alone is not an authorization boundary.
- The three DataLake QA tools run only in Gateway. `datalake_smart_qa` calls the
  approved internal Retrieve API and returns ranked source chunks; Metrics and
  Detail continue to call AppForge. Keep the Retrieve endpoint, project ID, and
  document-store ID plus `APPFORGE_API_KEY` and the two AppForge workflow IDs in
  `deer-flow-env`. Keep `trust_env=false` and both exact internal addresses in
  the Gateway proxy-bypass variables. Never expose those values as model tool
  arguments or return raw upstream errors. A single document store is referenced
  from `config.yaml` as `$DATALAKE_RETRIEVE_DOCUMENT_STORE_ID`; the code also
  accepts comma-separated `DATALAKE_RETRIEVE_DOCUMENT_STORE_IDS`.
- AppForge knowledge results may contain image URLs served from the reviewed
  internal image-preview address. Gateway rewrites only that exact HTTP origin
  to the authenticated same-origin `/api/datalake/image-preview` route, which
  rejects redirects, non-image content, oversized responses, traversal paths,
  and every other host/port. Final answers embed that proxy URL directly; they
  must not expose the raw internal URL or download/publish a duplicate artifact.
  Retain the narrow address in proxy-bypass settings for the Gateway's direct
  fetch and never replace it with a broad private-network CIDR.
- The WeKnora built-in Bailian ASR model points to the internal Gateway adapter because WeKnora 0.7.0 and `qwen3-asr-flash` use different request shapes. Keep the bare `gateway` Service name in the WeKnora App proxy-bypass variables; the adapter reuses the Bailian LLM key and never persists uploaded audio.
- The WeKnora built-in `text-embedding-v4` model also points to `http://gateway:8001/api/weknora/bailian/v1`. Gateway validates `EMBEDDING_API_KEY` and relays `/embeddings` through its scoped enterprise proxy/CA/OpenSSL compatibility path without persisting document text; do not restore a direct DashScope URL in the WeKnora database.
- Enterprise PDF/DOC/DOCX/PPT/PPTX parsing runs through `docker/mineru_adapter/`. It keeps searchable PDF and native Word text on the bundled WeKnora parsers, returns a non-empty native Word result immediately by default, adds captioned full-page evidence for searchable PDF pages containing images/vector figures, converts presentations to PDF, and sends scanned or mixed documents to the internal MinerU API. The approved upstream is MinerU-2605 and production uses its supported synchronous REST contract: Bearer-authenticated multipart `POST /file_parse` with `return_md=true`, `return_images=true`, and a bounded 1800-second timeout; the configured URL may be the service root or a legacy full `/file_parse` or `/tasks` endpoint. The current provider does not guarantee that IDs returned by `/tasks` remain queryable, so keep enterprise `MINERU_REQUEST_MODE=sync`. Losslessly split large PDFs into configured page batches before synchronous submission, then merge Markdown/images in page order so one provider connection does not need to survive an entire large visual document. Retry only early transient connection failures; never resubmit a whole document after a late disconnect. An explicitly enabled Word visual supplement is best-effort: once native extraction succeeds, supplement failure preserves native text/tables/images instead of failing the document. The reusable client retains asynchronous compatibility for other reviewed providers: treat HTTP-200 `success=false` envelopes as failures except for transient status/result code `20006`, and retry that code or a short-lived polling HTTP 404 with bounded backoff against the same locally constructed task URL. Reject untrusted task/result URLs, never submit a duplicate task, and never fall back from an accepted asynchronous task to a second synchronous submission. Before returning Markdown to WeKnora, demote implausibly large ATX headings to ordinary text while preserving their complete content and all image references; otherwise WeKnora repeats a malformed page-sized heading in every child chunk and can exceed the embedding provider's per-input limit. Keep `MINERU_TRUST_ENV=false` on the direct internal route, but explicitly assign the mounted enterprise CA bundle to the Requests session because disabling `trust_env` also disables environment CA merging; a configured missing CA path must fail closed, and certificate/hostname verification must never be disabled. WeKnora 0.7.0 can forward legacy OLE2 files with the generic multipart filename `document`; identify `.doc` and `.ppt` from their root compound-document streams and never regress to requiring the lost extension or blindly guessing an unknown/encrypted container. The PDF detector uses text length plus image-area coverage. If PDFium rejects a browser-readable document during routing inspection, the adapter first attempts the independent builtin parser and otherwise continues to MinerU; source-page rendering failures must not discard already recovered text. Keep presentation conversion strict. MinerU inline images and all visual evidence are extracted into WeKnora's persistent `images` contract with Markdown references. The MinerU-2605 service admits 20 concurrent requests, but the adapter intentionally serializes every PDFium/DocReader parse in one process because those native libraries are not concurrency-safe; its threaded HTTP server keeps `/live` responsive and reports parser availability through `/ready`. Do not bypass that coordinator, raise in-process parser concurrency to 20, or multiply WeKnora workers without a separate memory/capacity review. Keep the upstream URL and Bearer token only in the runtime `deer-flow-mineru` Secret; documents indexed by an older parser release must be re-parsed before their missing images can be retrieved.
- Word production policy: ordinary WeKnora knowledge ingestion and legacy DOC remain native-first. A dispatcher-authenticated DeerFlow `conversation` DOCX uses pinned local AnyDoc first, and only structurally large knowledge-base DOCX may use that same fast path. Inspect compressed, expanded, and main-XML sizes first. Large DOCX bypasses `StdMarkitdownParser` for official DocReader v0.8's native parser and has a separately killable low-memory text recovery; never weaken AnyDoc's XML-node safety limit. A successful result returns immediately; a thin, failed, or timed-out result retains the LibreOffice → MinerU fallbacks. Run AnyDoc and DocReader in bounded isolated subprocesses, and keep Word-specific LibreOffice and MinerU timeouts bounded. `MINERU_WORD_PDF_VISUAL_SUPPLEMENT_ENABLED=true` remains best-effort for native success and cannot invalidate recovered text/tables/images. Presentation conversion remains strict.
- MinerU-2605 asynchronous production override: the reviewed provider now guarantees the `/tasks` lifecycle, so this supersedes the older synchronous-production clause above. Keep `MINERU_REQUEST_MODE=async`, submit exactly one `POST /tasks`, and poll only its locally constructed `/tasks/{task_id}` and `/tasks/{task_id}/result` URLs within the shared 1800-second deadline. Retry transient business code `20006`, short-lived HTTP 404, and transient status/result connection or read-timeout failures with bounded backoff against that same task ID. Never repeat the accepted submission or fall back to a second synchronous request. Keep synchronous 20-page batching only as an explicit emergency rollback path.
- Enterprise conversation uploads set `DEERFLOW_UPLOAD_DOCUMENT_PARSE_TIMEOUT_SECONDS=570`; the dispatcher uses a 45-second admission wait and a 550-second interactive response timeout, all below Nginx's 600-second upload timeout. Each conversation Word fallback stage is bounded at 120 seconds, including child-process termination grace within the shared envelope. `DEERFLOW_UPLOAD_DOCUMENT_LOCAL_FALLBACK_AFTER_ENTERPRISE_ENABLED=false` prevents Gateway from repeating a potentially unbounded local parse after the Adapter has exhausted AnyDoc, DocReader, LibreOffice, and MinerU. Keep `DEERFLOW_UPLOAD_DOCUMENT_FALLBACK_ENABLED=true` for deployments without a reachable enterprise Adapter and preserve the code-default fallback for upstream compatibility. A user-visible controlled failure is required when the complete enterprise chain fails; never surface an unexplained proxy 504 as the intended parser result.
- The audited three-node enterprise layout runs six MinerU Adapter worker replicas, spread evenly at two independent processes per node. Every worker admits exactly one parse through `ParseCoordinator`; a busy worker returns retryable HTTP 429 and never keeps an in-process queue. `/live` checks only the HTTP process and owns Kubernetes readiness/liveness, while `/ready` returns 503 during parsing and is used only for dispatcher admission. LibreOffice runs in its own POSIX process group; timeout handling must terminate the whole group and retain Linux child-subreaper cleanup so orphaned `soffice.bin` processes and zombies cannot accumulate. A separate dispatcher resolves the worker headless Service, probes `/ready` before assignment, owns the bounded cross-Pod queue, round-robins free workers, quarantines timeout/5xx workers until they prove idle, and gives `X-DeerFlow-Parse-Priority: interactive` conversation uploads priority over knowledge-base batch work that has not started. Keep its single Deployment replica on the `Recreate` strategy because admission state is process-local. Keep the public `deer-flow-mineru-adapter` Service pointed only at that dispatcher. Keep the single WeKnora App replica at `WEKNORA_ASYNQ_CORE_CONCURRENCY=4` plus `WEKNORA_ASYNQ_SHARED_CONCURRENCY=2`, so at most six parser calls are active and later uploads stay durable in Redis/Asynq. WeKnora 0.7.0 isolates post-processing and model-heavy enrichment from that parser pool; keep `WEKNORA_ASYNQ_POSTPROCESS_CONCURRENCY=2`, `WEKNORA_ASYNQ_ENRICHMENT_CONCURRENCY=12`, and `WEKNORA_ASYNQ_MAINTENANCE_CONCURRENCY=4`. Increasing core/shared or the WeKnora replica count requires a matching serialized-worker capacity review and 3/10/30-document acceptance run. Keep worker topology `maxSkew: 1`, RollingUpdate `maxSurge: 1`/`maxUnavailable: 1`, worker PDB `minAvailable: 4`, and dispatcher PDB `minAvailable: 1`.

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

Personal Digital Employee definitions may use the configured `agent_storage`
backend. The `db` backend stores configuration and SOUL in the unified SQL
database with strict user ownership; custom avatars, administrator-managed
category definitions, and team-published snapshots remain on the shared data
volume. Browser Live sessions are Gateway-process-local, so keep one Gateway
replica and one uvicorn worker while Browser Live is enabled unless sticky
thread routing or a distributed session broker is added.

## Optional Kubernetes logging

`k8s/local-pvc/observability` owns the standalone Alloy/Loki/Grafana filesystem transition stack. Keep it separate from application releases, retain local PVs, scope file collection to the DeerFlow namespace, and preserve node-local Alloy positions/WAL. Do not expose unauthenticated Loki publicly or claim local disks provide high availability. Installation, credential creation, runtime validation, and retention verification are documented in `k8s/local-pvc/observability/README.md`.

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
