# AGENTS.md

The October 31 failed-run detail candidate uses a streamed 2 MiB response bound
before JSON parsing, a 20-second deadline, and the query cancellation signal.
Keep diagnostic queries free of automatic retries, focus refetches and retained
cache after unmount. Preserve the server-preview notice and recoverable dialog
error; never fetch an unbounded historical event payload to display the preview.
This is a local candidate, not evidence of a production rollout.

Custom dashboard ranges use the admin date-bounds endpoint for both datetime
input minima and manual-input validation. Before bounds load, on failure, or
with an empty database, disable custom Apply and show an explicit state/retry;
keep preset ranges usable. Show progress immediately while summary fetches,
label retained results as old, cancel superseded reads, and expose a retry after
errors/timeouts. Never invent a numeric completion percentage. Same-range
refreshes use the serialized dashboard refresh helper to preserve DB admission.

The main new-chat welcome page displays the complete non-confidential-system
warning in red below the first branding/navigation row on desktop (`md` and
wider). Bind it to the page's visual `isWelcomeMode`: submission immediately
removes the warning without waiting for the new thread response. Existing chats
must have neither the warning nor its reserved top space. Mobile does not
display the warning or reserve space for it. Keep the composer usable. The released
source inventory is pinned in `deploy/enterprise/production-source-lock-20261024.json`.
On mobile, the main chat header occupies normal document flow; the message
scroll area must not reserve another header-sized top offset. Keep that offset
desktop-only while its header remains positioned above the message list.

Haineng home footer has four counters. Fetch existing signed Haineng usage and
authenticated `/api/home/statistics` independently every minute; one failing
source must not erase the other, and unavailable counters show a dash, not zero.
Deploy Frontend and Gateway together so both new counters have their endpoint.
Keep hover-only contact visibility and the raised footer stacking context.
Contact shows 通用智能体 (`sh_tyznt@cnooc.com.cn`) and 人工智能平台
(`sh_ai@cnooc.com.cn`); feedback still uses the agent email. Preserve the supplied
QR pixels in `public/images/haineng-work-support-qr-20261012.jpg`; never regenerate
its code. Announcement behavior is unchanged by this contact-only update.

Digital Employee Square governance: `useSquareSettings` reads server-owned SQL
overrides for both bundled external cards and runtime-published employees. Apply
stable `external/team/legacy:<original name>` keys and global sorting before
filtering/pagination; never mutate the source roster or use localStorage for
admin settings. Fail closed on governance reads rather than re-showing withdrawn
cards. The trusted auth user `system_role=admin` shows public edit/order/withdraw
menus and a withdrawn-entry view/restoration. Regular users retain only their
own published employee withdrawal; their private editing/new publishing is
unchanged. The backend independently authorizes every write and rejects stale
versions. Public editing changes presentation, not model/skill/SOUL settings.
After publishing, invalidate and await both team-agent and square-settings
queries; otherwise a republished employee stays hidden by cached overrides.

October 2 dmdb frontend reconciliation: `MessageGroup` normalizes unknown tool
argument/result records before using string display fields. Every tool-result
link permits only absolute HTTP(S), rejects controls/backslashes and embedded
credentials, and renders blocked navigation as an inert label plus the unsafe
link marker. Safe links reuse the thread-aware Markdown preview renderer;
never expose a Sandbox loopback navigation. Image thumbnails are likewise
HTTP(S)-only. Clarification message content is displayed once outside the
processing panel, while ordinary tool-bearing progress remains visible within
it. Keep the first-non-empty tool-result index and task-call suppression intact.
The regular chat layout and legacy provider import share the custom-agent
`ChatProviders` stack, without making the server layout a Client Component or
changing static demo parameters. Upload deletion encodes the filename component
and GFM disables only single-tilde strikethrough. Existing fixed employees precede
the eighteen contest entries (six additions and four renamed entries). The latest
enterprise production-link commits (975c8cd72 and ff96696ae) supersede the earlier
test-host alignment: all six built-in entries now use cnoocclaw.cnooc.com.cn.
Keep both catalogue and launch tests on those reviewed production URLs; do not
change the runtime merely to satisfy stale test-host assertions. Incoming-only
reconnect-input hydration's three tests are deferred; the runtime and exact
pre-merge reconnect tests remain unchanged. See
`docs/FRONTEND_RECONCILIATION_BATCH2_20261002.md`; validation is still required
before completing the merge or deploying.

The enterprise 7·16 production source alignment is recorded in
`../release/production-build-alignment-20260928/README.md`. For production
comparisons, use the captured Frontend image source rather than later local
branch changes.

This file provides guidance to AI coding agents (Claude Code, Codex, and others) when working with the DeerFlow frontend. It is the source of truth; the sibling `CLAUDE.md` imports it via `@AGENTS.md`.

## Project Overview

DeerFlow Frontend is a Next.js 16 web interface for an AI agent system. It communicates with a LangGraph-based backend to provide thread-based AI conversations with streaming responses, artifacts, and a skills/tools system.

**Stack**: Next.js 16, React 19, TypeScript 5.8, Tailwind CSS 4, pnpm 10.26.2. Requires Node.js 22+ and pnpm 10.26.2+.

### Core dependencies

- **LangGraph SDK** (`@langchain/langgraph-sdk` ^1.5.3) — Agent orchestration and streaming
- **LangChain Core** (`@langchain/core` ^1.1.15) — Fundamental AI building blocks
- **TanStack Query** (`@tanstack/react-query` ^5.90.17) — Server state management
- **UI**: Shadcn UI, MagicUI, React Bits, and Vercel AI SDK elements (generated from registries — see Code Style)

## Commands

| Command          | Purpose                                           |
| ---------------- | ------------------------------------------------- |
| `pnpm dev`       | Dev server with Turbopack (http://localhost:3000) |
| `pnpm build`     | Production build                                  |
| `pnpm check`     | Lint + type check (run before committing)         |
| `pnpm lint`      | ESLint only                                       |
| `pnpm lint:fix`  | ESLint with auto-fix                              |
| `pnpm format`    | Prettier check (`pnpm format:write` to apply)     |
| `pnpm test`      | Run unit tests with Rstest                        |
| `pnpm test:e2e`  | Run E2E tests with Playwright (Chromium)          |
| `pnpm typecheck` | TypeScript type check (`tsc --noEmit`)            |
| `pnpm start`     | Start production server                           |

Unit tests live under `tests/unit/` and mirror the `src/` layout (e.g., `tests/unit/core/api/stream-mode.test.ts` tests `src/core/api/stream-mode.ts`). Powered by Rstest; import source modules via the `@/` path alias.

E2E tests live under `tests/e2e/` and use Playwright with Chromium. They mock all backend APIs via `page.route()` network interception and test real page interactions (navigation, chat input, streaming responses). Config: `playwright.config.ts`.

## Architecture

```
Frontend (Next.js) ──▶ LangGraph SDK ──▶ LangGraph Backend (lead_agent)
                                              ├── Sub-Agents
                                              └── Tools & Skills
```

The frontend is a stateful chat application. Users create **threads** (conversations), send messages, set thread-scoped `/goal` completion conditions, and receive streamed AI responses. The backend orchestrates agents that can produce **artifacts** (files/code), **todos**, and goal state updates.

### Source Layout (`src/`)

- **`app/`** — Next.js App Router. Routes include `/` (landing), `/workspace/chats/[thread_id]` (chat), `/workspace/agents/[agent_name]` and `/workspace/agents/new` (custom agents), `/blog/…`, the `(auth)/{login,setup,auth/callback}` flow, `/[lang]/docs/…`, and `/api/…` route handlers (e.g. `/api/memory`).
- **`components/`** — React components:
  - `ui/` — Shadcn UI primitives (auto-generated, ESLint-ignored)
  - `ai-elements/` — Vercel AI SDK elements (auto-generated, ESLint-ignored)
  - `workspace/` — Chat page components (messages, artifacts, settings)
  - `landing/` — Landing page sections
  - `docs/` — Docs / MDX rendering components
- **`core/`** — Business logic, the heart of the app. Domains include `threads/` (creation, streaming, state), `api/` (LangGraph client singleton), `agents/` (custom agents), `auth/` (authentication), `artifacts/`, `channels/` (IM connections), `i18n/` (en-US, zh-CN), `settings/`, `memory/`, `skills/`, `messages/`, `mcp/`, `models/`, `input-polish/` (pre-send draft rewrite API), `voice-input/` (secure microphone PCM capture and authenticated Gateway transcription), `suggestions/`, `tasks/`, `todos/`, `tools/`, `workspace-changes/` (run-scoped changed-file summaries and diff fetching), `config/`, `notification/`, `blog/`, plus rendering helpers (`rehype/`, `streamdown/`) and `utils/`.
- **`hooks/`** — Shared React hooks
- **`lib/`** — Utilities (`cn()` from clsx + tailwind-merge)
- **`content/`** — MDX content (blog posts, docs) rendered by the app
- **`styles/`** — Global CSS with Tailwind v4 `@import` syntax and CSS variables for theming
- **`typings/`** — Ambient TypeScript declarations
- Root files: `env.js` (env validation), `mdx-components.ts` (MDX component map)

The custom-agent builder's hidden save instruction is locale-owned. The
`zh-CN` instruction requires both the initial `SOUL.md` and the persisted
one-line card description to use Simplified Chinese; the backend's shared
language policy provides the same default for lead and delegated model output.
Do not reintroduce an unconditional English requirement into the bootstrap
copy.

The conversational step of `/workspace/agents/new` intentionally follows the
upstream text-only builder composer. Keep it visually aligned with the CNOOC
workspace and responsive to the mobile visual viewport, but do not silently
add the main chat's attachment, Data Lake, knowledge-base, voice, polish, or
mode state to this workflow. Its existing model selector, `agent_name`,
`is_bootstrap`, hidden human-input responses, and save interaction must remain
intact.

The Agent gallery presents custom Agents as **数字员工 / Digital Employees**.
Keep its compact horizontal portrait-and-details rows, circular CNOOC cartoon
portraits, desktop sidebar filters, mobile filter drawer, and fuzzy discovery
across name, description, model, category, tags, tools, and skills. Personal
employees persist category, tags, bundled/custom avatar selection, model,
temperature, output limit, thinking default, and reasoning effort through the
Agent API; never restore the former localStorage-only portrait contract.
Categories are the server-owned corporate business-domain taxonomy and are not
mutable from the UI. Preserve the `domain_group` distinction between the
headquarters-governed “统” domains and business-unit-implemented “分” domains
with a compact badge on every domain row in gallery, create, and edit surfaces;
keep both groups in one ordered list rather than separate columns. The gallery directory is collapsed by default,
shows the selected domain in its trigger, and exposes an independently bounded
scroll area when expanded. Per-employee tags support manual add/edit/delete and
deterministic local suggestions. Browser Live is feature-gated and only appears
for a custom employee when its tool-group allowlist is unset or includes
`browser`; on mobile it is a bottom sheet, while desktop uses the side panel.
Do not fork the conversational builder or weaken its existing save workflow.
The UI must submit only the fixed category identifier and must not attempt to
rewrite or migrate complete Agent configuration objects; persistence migration
is a raw-field operation owned by Gateway.
The directory paginates both the shared square and personal list at the UI
boundary; filtering returns to page one. Popular tags and the new-agent avatar
picker show compact previews with explicit expand/collapse controls. The
new-agent name step is a viewport-bounded card with a fixed header/footer and
an independently scrolling form body, so the primary action never falls below
the browser viewport. The personal edit dialog intentionally omits dedicated
model and inference controls; updates must leave any existing model,
temperature, output-token, thinking, and reasoning configuration unchanged.

Ordinary and Digital Employee chat layouts reuse the single
`components/workspace/chats/chat-providers.tsx` provider stack. Keep thread-keyed
subtasks/artifacts, Browser Live state, and prompt-input state in that shared
stack so the two routes cannot drift or leave one chat mode without a provider.

Recent-chat organization follows upstream DeerFlow's namespaced metadata
contracts. `deerflow_pinned` sorts pinned chats first without touching their
activity timestamp, and `deerflow_archived` removes archived chats from the
sidebar while keeping them restorable from the Chats page. Keep archive and
delete mutations hosted above rows that may unmount, and retain the upstream
confirmation dialog before destructive chat deletion. Workspace change badges
show at most three file rows by default and expose a reversible remainder
toggle; the full diff panel remains unchanged.

The main workspace composer's knowledge control mirrors WeKnora New Chat's
mention scope without copying WeKnora conversation state. It supports complete
knowledge bases, individual files, and tags. Preserve each file/tag's parent
knowledge-base identity in `weknora_scope_items`, group tags in
`weknora_tag_scopes`, and keep legacy KB-only context readable. Selecting a
complete KB replaces its child selections; selecting a file/tag replaces the
complete-KB selection for that parent. The selector must remain usable in both
desktop and mobile dialogs. Keep the native mention interaction model: one
grouped Knowledge Base / Tag / File menu, drill-in navigation, global search,
immediate selection, and removable selected-scope chips above the prompt. A
file row must retain and display its parent knowledge base and native tags;
switching groups or search terms must never reuse stale rows from a previous
knowledge base. The selected chips are not presentation-only: submit the same
scope with every run so Gateway can enforce it on all `weknora_*` tools.
Keep the selector header and selected-summary footer fixed while the result
viewport scrolls independently. Selection stays open for consecutive
multi-select, long rows must remain inside the popup, and selected knowledge
chips share the responsive composer material area with attachments so neither
can overlap the model selector or crowd mobile primary controls. Desktop
materials flow at their intrinsic bounded width and wrap only when the row is
full; mobile keeps one horizontally scrollable material rail. One run may
select at most 20 knowledge-scope items, with a visible rejection message for
the next item; attachment limits remain owned by the existing upload policy.

The new-agent name-step badge must remain compatible with enterprise browsers
that lack CSS `color-mix()`. Keep its pale primary background as a separate
`bg-primary opacity-10` layer behind the inline icon; Tailwind opacity-color
utilities such as `bg-primary/10` fall back to an opaque primary color in those
browsers and hide the same-color icon.
The custom-agent chat route must safely decode its dynamic `agent_name` segment
before API lookup, run-context creation, or display, and must re-encode that
decoded name when constructing navigation URLs. Its welcome avatar reuses the
same separate-opacity icon treatment so enterprise browsers do not render an
opaque blue disk over the robot glyph.

The Kubernetes WeKnora UI bootstrap is served through the shared DeerFlow
origin. Keep its session exchange URL relative as
`/api/weknora/sso-session`; loopback, fixed-port, or node-pinned URLs bypass the
user's actual browser origin and break SSO in enterprise deployments. After a
successful DeerFlow exchange, the bootstrap must clear the standalone
`weknora_lite_mode` fallback and its session-only last path; a prior native-auth
failure can persist Lite mode, which hides WeKnora organizations/shared spaces
even after SSO recovers.
The enterprise root entry sets the server-only
`DEER_FLOW_UNAUTHENTICATED_HOME_URL`. The exact `/` route always renders the
public landing page without checking the Gateway session. Login, New Chat, and
Get Started all enter the existing server-side `/login` auth route, which sends
an authenticated browser to the workspace and an unauthenticated browser to the
configured HTTPS portal. APIs, static assets, workspace deep links, and Gateway
authorization remain unchanged.
The enterprise overlay also sets `DEER_FLOW_SSO_ONLY=true`. In that mode the
entire `(auth)` route group redirects any unauthenticated state—including setup
required or a temporarily unavailable Gateway—to the validated HTTPS portal;
it must never render local login, registration, or setup forms. Missing or
invalid portal configuration fails closed to `/`. Gateway independently blocks
the corresponding APIs, so this redirect is presentation rather than the sole
security control.
DeerFlow chat Markdown may receive stable WeKnora `resource://` image handles
from older persisted messages. `resolveMessageImageURL()` must convert only the
strict stable-handle shape to the authenticated same-origin
`/api/weknora/files` fallback. Current tool output already carries the safer
knowledge-base-scoped Gateway image URL.

`/workspace/knowledge` embeds the same-origin native WeKnora workspace rather
than reimplementing its knowledge-management UI. Keep the iframe entry at
`/platform/knowledge-bases` and let the iframe fill the whole page without a
second DeerFlow title/action bar. The mounted WeKnora bootstrap persists the
Gateway-provided `ui_policy`: system administrators retain the complete native
navigation, while ordinary personal-workspace Owners see Knowledge Q&A,
Knowledge Bases, Shared Spaces, and Custom Q&A but do not see Publish Integrations, system
settings, or tenant-creation controls. Entering the
embedded workspace collapses DeerFlow's sidebar and expands WeKnora's sidebar
once for both ordinary users and administrators. The automatic collapse must
run only once per route mount so the user can immediately reopen DeerFlow's
sidebar without navigating away first; the native WeKnora toggle remains
usable afterwards so knowledge-base and shared-space navigation stays
reachable. The native WeKnora deployment and RBAC remain
responsible for authorization; DOM filtering is only the simplified enterprise
presentation. The ordinary-user New Knowledge Base dialog exposes only Basic
Information while preserving all native type and indexing choices on that page.
Existing knowledge-base Settings expose Basic Information plus Publish
Integrations > Sharing Management; the global Publish Integrations navigation
remains hidden. Deployment defaults supply the other hidden model, parser,
chunking, VLM, ASR, and storage values. System
administrators retain the complete native editor.

`/workspace/skills` is the phase-1 DeerFlow shell for native iFLYTEK SkillHub
content. It owns the visible Skill Center header, discovery/personal tabs,
role-aware management menu, loading state, and embedded DeerFlow theme. Keep the
iframe on the authenticated same-origin `/platform/skillhub/` mount, start it
on native search, and hide SkillHub's standalone header/footer and
dashboard-return control. Do not recreate SkillHub marketplace, publishing,
review, governance, or administrator content in DeerFlow. The shell reads
native `/api/v1/auth/me` roles only to present the matching navigation; native
SkillHub API authorization remains the security boundary. The Skill Center
sidebar item stays immediately below Knowledge, its new-window action preserves
the DeerFlow shell, entering the route collapses the DeerFlow sidebar once per
mount, and mobile retains the sidebar escape hatch. The native session is
established only through the exact same-origin passive-bootstrap route backed by
Gateway's short-lived role-free HMAC assertion. The feature gate must hide the
entry and keep the page inert until that server path is ready. SkillHub sessions
and RBAC remain native and independent from DeerFlow authorization.
The first-party **My Skills** tab is the one exception to the embedded native
content: it is a DeerFlow-owned view of the authenticated user's complete
`custom` skill library. Build it from the existing `/api/skills` inventory and
`/api/skillhub/runtime/skills` installation registry rather than introducing a
second skill store. It must include both user-created and SkillHub-installed
skills, show registry provenance/version where available, retain the existing
create-skill route, and preserve the existing authorization boundary for
user-created skill switches. SkillHub-installed entries use their per-user
runtime enable/disable and uninstall APIs. Keep the detail-page action labelled
**Add to My Skills** (localized in the UI) so installation is not presented as
binding the skill to one Digital Employee.
On native skill detail routes, the DeerFlow-owned runtime bar may fetch the
published version with the browser's SkillHub session and upload its bytes to
the authenticated Gateway runtime endpoint. Keep registry coordinates parsed
from the same-origin detail route/API, never accept an arbitrary download URL,
and do not present a client-side success state until the Gateway confirms the
per-user install. Enable/disable and uninstall use only the matching
namespace/slug installation record. SkillHub still owns publication and review;
DeerFlow owns runtime installation and its security result.

### Data Flow

1. Optional composer helpers such as `core/input-polish` can rewrite the local draft before submission. `core/voice-input` records microphone input only in a secure browser context, converts it to 16 kHz mono PCM16, and streams it over the same-origin authenticated `/api/voice-input/stream` WebSocket; Gateway returns partial/final text into that same local draft. Do not restore browser-vendor `SpeechRecognition` as an implicit fallback. Confirmed user input then flows to thread hooks (`core/threads/hooks.ts`) → LangGraph SDK streaming. When that draft explicitly starts with `/ppt-master`, `core/api/ppt-master-runtime.ts` marks the thread with `runtime_profile=ppt-master-runner` **before** `useStream.submit()` can start state hydration, then routes stream, reconnect, cancel, and LangGraph checkpoint state/history through `/api/ppt-master-runtime`; paged conversation history, uploads, metadata, and artifact APIs remain on the main Gateway. Delaying the marker until the wrapped `runs.stream()` call creates a first-submit race in which `useStream` can query the ordinary checkpoint namespace and log misleading 404s. If the same request includes a PPT/PPTX attachment, the pre-submit upload uses `upload_purpose=presentation_template` so Gateway preserves the original template instead of routing it through the document parser.
   Welcome-page quick actions fill editable prompts that begin with their real
   Slash Skill name. The PPT action is a four-item route menu for Generate
   PPTX, Create Template, Fill Native PPTX, and Enhance Native PPTX; selecting
   an item fills its `/ppt-master` example without submitting it. Keep these
   labels and prompts locale-owned, and do not replace them with implicit
   non-Skill prose.
2. Stream events update thread state (messages, artifacts, todos, goal). The main thread stream uses the LangGraph SDK's `throttle: true` mode so updates received in the same macrotask coalesce before React is notified; do not replace it with a numeric delay without validating the SDK's trailing-debounce behavior on a continuous stream. Browser submit and regenerate paths intentionally omit `streamSubgraphs`: delegated progress already arrives through root custom events, and subgraph value frames must not duplicate another agent's messages in the root conversation. File-tool artifact auto-open work must run in an effect with timer cleanup; never schedule timers while rendering streamed `write_file` or `str_replace` updates.
   When an existing chat page mounts without the SDK's tab-local reconnect key,
   `core/threads/hooks.ts` must query the Gateway run store for the newest
   `pending`/`running` run and join that run's stream. Keep the composer busy
   during discovery and recovery so reopening a running chat cannot submit a
   competing run and surface HTTP 409. The same recovery check runs when the
   page regains focus or visibility.
3. `useThreadHistory` loads persisted conversation pages from `GET /api/threads/{id}/messages/page`, preserving the backend's thread-global event `seq`; rendering overlays checkpoint/live copies at their matching canonical identities (a summarized checkpoint may contain a protected early input plus a recent tail), suppresses checkpoint/transient prefixes whose canonical position is still behind an unloaded cursor page instead of collapsing that unknown gap before a recent anchor, then adds optimistic messages without timestamp re-sorting. History invalidation preserves already-loaded pages so their established ordering positions are not discarded.
4. Stop actions call the LangGraph SDK stream stop path; `core/threads/hooks.ts` invalidates current-thread, thread-history, token-usage, and sidebar/search caches immediately and schedules one follow-up refetch because SDK stop may finish via abort + fire-and-forget cancel before backend title finalization commits
5. TanStack Query manages server state; localStorage stores user settings
6. Components subscribe to thread state and render updates. Completed message groups and turn-usage derivations retain stable references while only the active tail changes; histories with at least 60 groups use measured row virtualization while keeping the live tail mounted. Streaming Markdown uses a deferred, bounded 50 ms reveal cadence so provider token frequency cannot force a complete Markdown parse and React commit for every chunk.

The composer labels the legacy shop-cart attachment flow as **Data Lake** while
keeping its internal routes and modules unchanged. The control stores
`datalake_enabled` in the user's shared local settings, defaults missing values
to `false`, and offers **Do not use Data Lake** / re-enable actions. Re-enabling
must first call the CSRF-aware same-origin
`POST /api/v1/data-lake/auth/token`; update the shared setting only after that
request succeeds. This call keeps the shared credential and CSRF handling but
opts out of the generic 401 login redirect because Gateway can return the
scoped `data_lake_login_required` business code; show the localized **Please
sign in to Data Lake first** message without navigating away. The platform
token remains HttpOnly and must never be read or stored by client code. Opening
the file list repeats that idempotent request
before loading files, including when the persisted setting is already `true`,
and logout resets the setting to `false`. Every Next.js file-list, download, and
external-agent launch route must call Gateway's authenticated
`GET /api/v1/data-lake/auth/session` before consuming the Cookie. A 401 from
that binding check clears the stale Cookie, closes the Data Lake UI, and resets
the shared setting to `false`; a temporary Gateway failure returns 503 without
deleting a possibly valid Cookie or preference. When the shop-cart list upstream
instead returns HTTP 401 with `code=1` and `msg=invalid_token`, map it to
`data_lake_login_required`, show the localized **Please sign in to Data Lake
first** message, and retain both the HttpOnly Cookie and `datalake_enabled=true`.
Data Lake authorization shows a busy button plus a persistent loading toast;
the token request is bounded to 60 seconds. Only `data_lake_login_required`
opens the sign-in dialog, with a fixed `https://datalake.cnooc/` link opened
using `noopener noreferrer` and a reauthorize/retry action that resumes the
original enable or file-list operation. A business login failure preserves an
existing enabled preference. Permission denial and transient failures must not
be classified as missing Data Lake login. Never put a Token in the entry URL.
The Data Lake control maps `data_lake_permission_denied` to the localized **No
Data Lake permission** message and `data_lake_login_required` to the localized
login prompt; other authorization failures keep their normal diagnostic or
retry message. Async enable/disable completion must merge into the
latest context rather than overwrite model, mode, reasoning, or knowledge
changes made while authorization was pending. Disabling affects every thread,
removes the file-list
menu item, closes the dialog, invalidates in-flight list loads, and sends the
resolved boolean on normal, custom-agent, regenerated, and branched runs.

Generated Office artifacts use a paired-preview contract: `/mnt/user-data/outputs/<name>.<office-extension>` remains the selected and downloadable artifact, while `<name>.preview.pdf` is rendered in the right-side iframe when present; `<name>.pdf` is accepted as a compatibility fallback for older/model-authored outputs. Supported owners are DOC/DOCX, XLS/XLSX, ODT/ODS/ODP, and PPT/PPTX. Paired preview PDFs are internal companions: keep them in complete thread artifact state for iframe resolution, but omit them from file cards and the artifact selector, and normalize any legacy direct companion selection back to its owning Office document. `core/artifacts/utils.ts` owns the deterministic pairing rule, including extraction from a persisted thread-state snapshot. On chat reload, `ChatBox` uses the live stream artifact channel when it is populated and otherwise fetches `threads.getState(threadId)` explicitly so paged message hydration cannot make the top-level Files control disappear. That restoration must not auto-open the panel. The artifact detail component may probe the sibling preview path for histories whose artifact state predates automatic preview publication. Artifact headers must let long filenames shrink and truncate while keeping preview controls, downloads, and the close button fixed inside the panel. Markdown links to a current-thread artifact open that same right-side artifact panel; links carrying `?download=true` retain normal browser download behavior. The PDF iframe must load Gateway's browser-compatible `/artifact-previews/pdf/` gallery, which rasterizes validated generated output PDFs—including Office companions—server-side. Original uploads and unsafe PDFs remain download-only. Keep the iframe sandboxed without `allow-scripts` or `allow-same-origin`, use `no-referrer`, and do not restore a raw unrestricted same-origin PDF iframe.
Interactive lead-agent submissions and regenerations share `core/threads/run-config.ts::LEAD_AGENT_RECURSION_LIMIT` (currently 2000). This is a bounded LangGraph super-step budget, not an LLM-call count; the Gateway still clamps it and backend loop/token guards remain independent. Keep both submission paths on the shared constant so long, progressing Skill workflows do not diverge by UI action.
Every recorded or guessed Office preview companion must pass a same-origin
HEAD probe before the iframe is rendered. A stale/missing companion may show a
download fallback for legacy history, but every newly successful Office
publication is backend-gated on a real persisted preview PDF.
After that probe succeeds, keep an accessible loading surface over the preview
until the iframe's real load event fires; never expose its intermediate blank
document. A bounded timeout must replace the loader with a retry action that
reloads only the preview frame.
Writing or publishing an artifact must not automatically open the right-side panel. Keep files and final deliverables visible in the conversation and top-level Files list, but require an explicit user click on a write step, artifact link, file card, or Files control before selecting an artifact and narrowing the chat column.
`MessageResponse` must use React's normal shallow-prop memoization (or compare every rendering prop), not a children-only comparator: thread-aware Markdown `components` arrive independently of unchanged text, and retaining an older link renderer turns preview links back into full-page artifact navigation. Interactive artifact links use an inert `#artifacts` href when a panel callback is available so an early pre-hydration click cannot replace the chat page with the raw file response.

`/goal` and `/compact` are built-in composer commands, not skill activations. `src/components/workspace/input-box.tsx` intercepts `/goal`, `/goal clear`, and `/goal <condition>` before normal chat submission, calling Gateway `GET/PUT/DELETE /api/threads/{thread_id}/goal`. Setting `/goal <condition>` also submits the condition text as the next user task so the agent starts running immediately; status and clear do not start a run. Goal and compact requests are tied to the current `threadId` with an `AbortController`, so switching threads or unmounting the composer aborts in-flight requests and stale responses cannot update the new thread's composer state. The chat pages render `GoalStatus` above the composer from `AgentThreadState.goal`, with local optimistic state until the next stream `values` update arrives. `/compact` calls `POST /api/threads/{thread_id}/compact` to summarize older active context while leaving the full visible chat history intact; it is skipped on new/empty threads and blocked server-side while a run is in flight.

Human input requests are a structured message protocol layered on normal chat history. The backend writes request payloads to `ToolMessage.artifact.human_input`, `src/core/messages/human-input.ts` owns the runtime validators/types, and `src/components/workspace/messages/human-input-card.tsx` renders the reusable card. The AI `ask_clarification` tool call uses the same canonical `clarification:<tool_call_id>` request identity as the authoritative ToolMessage, and message grouping replaces the streamed call card when that ToolMessage arrives so one request renders only once. `MessageList` owns answered/latest/pending state for visible cards, but derives answered responses from raw `thread.messages` because replies are hidden; pending cards clear when the hidden reply appears, when dispatch is dropped, or when a new `thread.error` reports an async stream failure. Page-level submit callbacks must send a normal human message and put `hide_from_ui: true` plus the response payload in the fourth `sendMessage(..., options)` argument as `options.additionalKwargs`; the third argument remains run context such as `{ agent_name }`. Composer entry points should disable normal bottom input while `hasOpenHumanInputRequest(...)` is true so users answer through the card and preserve response metadata.

The authoritative clarification ToolMessage also delimits the next streaming run even though the user's reply is hidden. Keep completed assistant answers stable across that boundary, and render any user-facing context from the AI clarification call once as normal dialogue outside the processing disclosure while retaining its tool association internally.

Tool-calling AI messages can contain user-visible text as well as `tool_calls`. `core/messages/utils.ts` keeps these turns in an `assistant:processing` group, and `components/workspace/messages/message-group.tsx` must render the visible text as a processing step instead of treating the message as only tool metadata. This preserves provider text such as error explanations or "trying another approach" notes during tool-heavy runs.

Sidecar threads use `metadata.platform_sidecar` for newly created records. Thread
lookup, primary-list filtering, and parent-thread cascade deletion must also recognize
the legacy `metadata.deerflow_sidecar` marker so enterprise-branded deployments retain
access to sidecars created before the metadata rename.

`MessageGroup` builds its tool-result lookup once per processing group before converting messages to steps. The lookup preserves the first non-empty result for each tool-call ID, matching the streamed-message display semantics without repeatedly scanning the full group for every tool call. The browser-preview half of the upstream lookup remains out of scope until the enterprise branch carries that upstream browser surface.

### Key Patterns

Live assistant Markdown opts into `core/streamdown/use-smooth-streaming-text.ts`
with a thread/message `streamingKey`. Preserve the existing deferred scheduling
and 50 ms reveal cadence, but do not impose a minimum 64-character display batch.
The presentation buffer reveals small incoming batches within a 300 ms window
without restarting pending frames or extending the deadline, and bounds animated
tails to 256 UTF-16 units with grapheme-safe boundaries when supported. Initial
hydration, identity changes, replacement, stop/completion, hidden tabs and
reduced-motion preferences show all received text immediately. Never defer a
different message's snapshot across a thread/key change. Keep raw SDK/history/
copy state, tool/file/error state, Markdown security, history virtualization,
stable group/usage references, and Redis/SSE publication unchanged. Cancel timers
and media/visibility listeners on unmount.

- **Server Components by default**, `"use client"` only for interactive components
- **Thread hooks** (`useThreadStream`, `useSubmitThread`, `useThreads`) are the primary API interface
- **LangGraph client** is a singleton obtained via `getAPIClient()` in `core/api/`
- **Environment validation** uses `@t3-oss/env-nextjs` with Zod schemas (`src/env.js`). Skip with `SKIP_ENV_VALIDATION=1`
- **Subtask step history and runtime metadata** (`core/tasks/`) — the subtask card shows a subagent's full step timeline (#3779): its assistant reasoning turns interleaved with the tools it ran. `Subtask.steps[]` is accumulated live from `task_running` events (appended via `mergeSteps`, not overwritten) and backfilled on expand for historical runs by `fetchSubtaskSteps`, which pages the events endpoint scoped to one task (GET `/runs/{runId}/events?event_types=subagent.step&task_id=…&after_seq=…`) until a short page, so the run-wide limit can't truncate the timeline. `task_started` carries the effective `model_name`; `task_running` carries a cumulative usage snapshot after each completed LLM call. `core/tasks/lifecycle.ts` normalizes these additive events, and `computeNextSubtask` keeps the largest cumulative total so replayed or late SSE frames cannot double-count or roll the folded card backward. Terminal ToolMessage metadata (`subagent_model_name` / `subagent_token_usage`) restores the same values from normal history after reload; no per-card event fetch is needed. `core/tasks/steps.ts` is the pure step model: `messageToStep` (live), `eventsToSteps` (reload), `mergeSteps` (dedup by `message_index`), and `stepsForDisplay` (what the card renders — keeps tool steps + AI steps with text, drops the trailing final-answer AI step when completed since it's shown as `result`). `core/tasks/context.tsx`'s `useUpdateSubtask` applies updates against a `tasksRef` mirroring the latest state (not a closure snapshot), so a late-resolving `fetchSubtaskSteps` backfill merges into current state instead of clobbering SSE steps or sibling subtasks that arrived meanwhile. The owning `run_id` is carried onto history content messages in `buildVisibleHistoryMessages` so the card can resolve the events endpoint. Next.js preserves dynamic layouts while only `[thread_id]` changes, so each chat layout must pass that id as `SubtasksProvider.scopeKey`; otherwise a previous thread's live workflow card is rendered in the new conversation until another event replaces it.
- Expanded subtask cards retain tool-only assistant request steps and show a
  bounded command/query summary for every request plus an on-demand full input
  or output body. Long timelines use an internal scroll area, lazy detail
  mounting, and `content-visibility`; do not eagerly render every persisted
  command result. Assistant/tool text containing a Sandbox `:5050` URL expands
  automatically and uses the same thread-aware Live Preview proxy link renderer
  as normal chat messages. When the link text itself is the loopback URL, render
  the localized Live Preview label while preserving the authenticated proxy URL;
  never display `127.0.0.1` or `localhost` as a browser destination.
- All subtask groups after the latest human message remain live while that turn
  streams; a later processing/tool group must not make earlier parallel task
  cards look failed. If the parent run ends without a terminal task ToolMessage,
  the card becomes `incomplete` rather than claiming a subagent failure. Long
  task descriptions are width-constrained and truncated to prevent page-level
  horizontal scrolling.

### Interaction Ownership

- `src/app/workspace/chats/[thread_id]/page.tsx` owns composer busy-state wiring.
- `src/app/workspace/chats/[thread_id]/page.tsx` owns branch-from-turn submission and navigation; sidecar `MessageList` instances do not receive the branch action.
- `src/app/workspace/chats/[thread_id]/page.tsx` and `src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx` own active-goal display state for their composer overlays.
- `src/components/workspace/messages/message-list.tsx` owns human-input card answered/latest/pending gating; entry pages only translate a submitted card response into `sendMessage` calls.
- `src/core/threads/hooks.ts` owns pre-submit upload state and thread submission.

## Code Style

- **Imports**: Enforced ordering (builtin → external → internal → parent → sibling), alphabetized, newlines between groups. Use inline type imports: `import { type Foo }`.
- **Unused variables**: Prefix with `_`.
- **Class names**: Use `cn()` from `@/lib/utils` for conditional Tailwind classes.
- **Path alias**: `@/*` maps to `src/*`.
- **Components**: `ui/` and `ai-elements/` are generated from registries (Shadcn, MagicUI, React Bits, Vercel AI SDK) — don't manually edit these.

### Admin dashboard consolidation

Admin operational data is consolidated in `/workspace/admin/dashboard`. The
former `/workspace/admin/observability` route redirects there, and the UI only
surfaces database-backed usage, run, failure, token, feedback, and management
audit data. Incomplete component-health heuristics are intentionally hidden.
The dashboard ranks users by run volume, reports launch clicks for the five
built-in external agents, and links slow/high-token runs back to their chats.
Its headline cards keep conversation inventory and run execution as separate
dimensions. The conversation card shows the selected window's append-only
creation count, all-time creations, current all-time retained inventory, and
the derived all-time deletion count. The run card shows runs started in the
selected window, split into successful, failed/timeout/interrupted, and still
active counts; "ended" never implies successful completion. The
activity chart shows conversation creations plus active users, adaptively
switches from daily to seven-day, 30-day, and 365-day buckets for arbitrary
custom ranges, and remains horizontally readable instead of compressing every
date label. Both bar series share one numeric scale. Active users are distinct
inside each activity bucket, while the headline active-user count is distinct
across the full selected range. Success/failure counts reconcile to completed
runs, while pending/running runs are reported separately. The registered-user
panel uses server pagination for every account and every currently existing
conversation, with per-session run counts and chat links. It uses SSO display
names as the primary user label, with the login account and
organization name as secondary context; local and legacy users safely fall
back to the login account when those optional profile fields are absent.
Administrators have
cross-user read-only access to thread content, runs, uploads, and artifacts;
that exception must never extend to create, continue, cancel, mutate, or delete
actions on another user's thread. Dashboard chat links carry
`admin_review=1`; the chat page disables its composer, regeneration, branching,
human-input replies, and scheduled-task shortcut in this review presentation.
The same page displays direct WeKnora inventory/history metrics for users,
knowledge bases, documents, file formats, knowledge-scoped native conversation
sessions, and question turns. Knowledge-base inventory detail shows the creator's
SSO display name and login account but intentionally omits conversation/question
columns; those remain aggregate metrics above. The knowledge usage ranking shows
the user's SSO name and account, contains every user with inventory or activity,
omits conversation/question columns, and uses five-row client pagination instead
of truncating the backend query. Shared-space detail shows the owner's SSO name
and account alongside organizations, member workspaces, role composition, shared
knowledge bases/documents, and join requests. The all-history failed-run table
supports server-side account/name filtering and opens a read-only diagnostic
dialog with the error stack, failed node, tool calls, and terminal events; it must
not add retry or mutation actions. The dashboard also reports
current and historically-created scheduled tasks plus run outcomes. Do not
substitute DeerFlow `weknora_search`/`weknora_ask` tool-call counts for native
WeKnora usage. Dashboard progress bars, including the six external-agent
launch bars, use explicit solid `backgroundColor` values instead of gradients
or alpha-color syntax so the audited enterprise browser renders them reliably.
The backend remains the authoritative enforcement boundary. The panel must not mix runs from deleted
conversations into that current-session view.
The six built-in external-agent cards navigate directly to their fixed
same-origin HTTPS aliases under the active DeerFlow hostname
(`cnoocclawtest.cnooc.com.cn` in test and `cnoocclaw.cnooc.com.cn` in production);
do not rewrite those
current aliases through the legacy launch API. Legacy `datalake.cnooc` card
records may still use `/api/cnooc/external-agents/<agent>/launch`. Those routes
read the HttpOnly `cnooc.platform_token` cookie, validate its current-user
binding through Gateway's Data Lake session endpoint, and redirect only to the
fixed canonical Data Lake paths, adding the current Token at redirect time.
Keep the strict target allowlist plus `no-store` and `no-referrer` headers, and
keep the shared Nginx entry's exact launch-prefix location ahead of its general
`/api/` Gateway catch-all. The Token must never be hardcoded, returned to client
JavaScript, or stored in card data; do not restore the legacy `/agent-tools/*`
proxies.
Slow-run, high-token, and recent-failure cards share five-row pagination plus a
bounded vertical scroll area.
The visible dashboard requests a compact summary with native WeKnora and unused
business-event detail excluded; the WeKnora panel has its own parallel query.
Short rolling summaries poll every 60 seconds and knowledge data every five
minutes. Fixed custom windows and windows longer than seven days do not poll.
Editing a custom date/time input must not change the active query until the user
clicks Apply. Heavier observability rankings poll every 60 seconds.
External-agent click telemetry must use the shared
authenticated fetch wrapper so the CSRF token is sent; telemetry failure must
not block navigation.

The enterprise Settings dialog intentionally hides the Appearance navigation
entry and opens on Account by default. Keep the underlying `next-themes`
provider and stored theme compatibility intact; hiding the entry must not reset
an existing user preference or delete the reusable Appearance page. If the
entry is restored later, its previews must remain shrinkable inside the dialog
grid: avoid fixed-width inner preview columns and retain `min-w-0` on nested
grid items. Preview colors must use explicit light/dark palette classes rather
than Tailwind slash-alpha utilities such as `bg-current/15` or
`border-current/10`; audited enterprise browsers can drop the alpha modifier
and render those shapes as opaque blocks. User-visible workspace and auth
status surfaces such as active microphone and input-polish overlays must follow
the same rule: use a separate `bg-primary` layer with explicit opacity or fixed
light/dark palette classes. The desktop settings dialog uses the available
viewport height with a bounded maximum and keeps both navigation and content in
their own scroll regions; do not restore the fixed 75vh layout that clips lower
sections on short enterprise-browser viewports. Keep its safe-area shell at
zero padding and put desktop/mobile content spacing on the inner flex frame;
mixing those responsibilities lets legacy browsers collapse the inset and place
the title or pane borders against the outer frame. User-visible workspace and auth
status text must come from `core/i18n`, with Simplified Chinese entries kept in
sync with English. This enterprise branch defaults the first render and
missing/unknown locale state to `zh-CN`; users may still explicitly select
English. The About markdown heading contains the product name only;
build version metadata must not be appended to that heading.

The CNOOC composer uses a white paper-plane image, so both the welcome composer
and the existing-thread `chat-composer` must provide an explicit filled blue
send-button background in their resting state; hover-only fill makes the action
effectively invisible on the white task-page composer. Ordinary and custom-agent
welcome footers share the `2026-2027` copyright year range.

Scheduled tasks own their execution-model choice independently of chat settings.
The create form prefers `deepseek-v4-flash` when that configured enterprise model
is available, falls back to the first available model elsewhere, and persists the
selection in `run_context`. Editing a task may change that saved model; switching
models in a chat must not mutate scheduled tasks. Keep create/edit/duplicate flows
aligned for `once`, `cron`, and minimum-60-second `interval` schedules and for the
pinned custom-agent selection. Run history uses server pagination; task ID and
page belong to the query/cache identity. Older pages pause live refresh and must
not be merged with the latest-page cache. The September 30 isolated merge
restores these pre-merge implementations and the matching Gateway route; keep
their existing regression tests and pinned DOM-test dependencies intact.
Incoming occurrence-status filtering, exact-instant preservation on one-time
edits, nonexistent-wall-time validation, and task-text search remain deferred:
their required implementations or UI integration are absent from the incoming
production baseline. The existing timezone offset correction stays active, but
must not be described as a DST-gap validator. Existing task status/type filters
remain supported. New leaked-system-tag Markdown handling is likewise deferred;
retain the existing nesting/math/Mermaid preprocessing and all its original tests.
See `../backend/docs/ENTERPRISE_MERGE_20260930.md` for the deferral inventory and
required frontend/backend acceptance gates. Do not delete or skip old tests to
make this reconciliation pass.
The scheduled-task page's enterprise card grid and modal detail/edit workflow are
an owned customization, not an upstream mirror. Adapt useful official behavior to
those surfaces and their tests; do not replace the page wholesale or assume the
official side-by-side detail layout.

PPT confirmation summary values use `pptSummaryValue` in the locale-owned copy
module. Preserve the fallback for both empty and missing values, plus literal
nonempty values such as `"0"`; a blind `||` to `??` replacement changes empty
string behavior. Existing PPT confirmation transport and acceptance are unchanged.

Workspace authentication is fail-closed: on `gateway_unavailable`, render only
the recovery screen and auth probe banner, never WorkspaceContent or its private
children. After a valid identity is recovered, refresh the server layout so it
rechecks authentication before rendering business pages.

### Workspace announcements

Administrators manage announcements in Settings > Announcement Management. `WorkspaceContent` mounts the active announcement above workspace pages, and the presentation uses a severity-aware animated card with reduced-motion fallbacks. Dismissal is stored per announcement revision, so editing and republishing an announcement makes the updated content visible again.

Public Skill enabled state is platform-wide. Settings > Skills must therefore
present public Skill switches as read-only for ordinary users; platform
administrators may change them, and the backend admin check remains the
authorization boundary. Custom Skill behavior remains governed by its existing
scoped authorization.

### Mobile WebView contract

The mobile H5 surface is the existing workspace opened at
`/workspace/chats/new`, not a separate frontend. Keep viewport metadata,
safe-area padding, dynamic-height fallbacks, touch-safe navigation, full-screen
mobile settings/artifacts, and the knowledge-page navigation escape hatch in
the shared component tree. Do not pass SSO tokens through URLs or browser
JavaScript. Mobile changes must retain desktop behavior and add Playwright
coverage at 320, 375, 390, and 412 pixel widths plus a short landscape view.
The site root redirects to `/workspace/chats/new`; IAM may interpose its normal
login, but its return target remains new chat on desktop and mobile.
`StyleAssetGuard` performs one cache-busting recovery when stale HTML references
a missing hashed stylesheet. Its probe must depend on the external Tailwind
`hidden` utility; adding inline `display:none` would defeat the health check.
`WorkspaceViewportController` is the shared keyboard/viewport owner: it mirrors
the VisualViewport height into `--app-viewport-height`, marks a composer-only
software-keyboard state, and restores the page origin after focus leaves. Keep
mobile editable text at least 16 CSS pixels to prevent iOS focus zoom; never
solve keyboard overlap by disabling the user's pinch zoom.
The empty mobile chat uses a compact welcome message and a bottom composer. Its
primary composer tools stay on a single horizontal line, and the welcome action
chips form one touch-scrollable row instead of wrapping. The mobile workspace
drawer uses a bounded viewport width and touch-sized rows while preserving the
same route, permission, conversation-history, and settings sources as desktop;
do not fork those business flows for mobile styling.
The mobile header's mode selector must read and update the same thread mode,
reasoning effort, thinking, and subagent settings as the composer selector. Keep
all visible labels in the active locale. The welcome avatar is a transparent
CNOOC-branded character cutout with a reduced-motion fallback; do not restore a
rectangular or circular background plate around it. Settings state is owned
above the mobile Sidebar Sheet so the drawer can finish closing before the
full-screen Settings dialog opens without losing the requested section.
Custom-agent `/workspace/agents/[agent_name]/chats/new` pages reuse the same
CNOOC header, welcome-stage, composer, quick-action rail, copyright, safe-area,
and task backdrop classes as the ordinary new-chat route. Keep the agent name
and description in the independent welcome stage rather than placing them in
`InputBox.extraHeader`; the custom `agent_name` send context and native task
header actions remain unchanged.
The shared chat settings default a missing mode preference to `flash`. Preserve
an explicitly saved `thinking`, `pro`, or `ultra` choice; do not reset existing
user intent during settings hydration or login.
The native App integration and release checklist live in
`docs/mobile-webview-integration.md`.

## Environment

`next.config.js` disables `poweredByHeader` at the application source. Preserve this after the Nextra wrapper; `tests/unit/config/http-identification.test.ts` loads the actual Next configuration and verifies both identification suppression and the API fallback rewrite ordering. Nginx suppresses upstream framework headers separately, so frontend rebuild/deployment remains required.

Backend API URLs are optional; an nginx proxy is used by default:

```
NEXT_PUBLIC_BACKEND_BASE_URL=http://localhost:8001
NEXT_PUBLIC_LANGGRAPH_BASE_URL=http://localhost:8001/api
```

Leave these unset for the standard `make dev` / Docker flow, where nginx serves the public `/api/langgraph/*` prefix and rewrites it to Gateway's native `/api/*` routes.

## Resources

- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
- [LangChain Core Concepts](https://js.langchain.com/docs/concepts)
- [TanStack Query Documentation](https://tanstack.com/query/latest)
- [Next.js App Router](https://nextjs.org/docs/app)

## Contributing

When adding features:

1. Follow the established `src/` structure
2. Add TypeScript types and proper error handling
3. Write unit tests under `tests/unit/` (`pnpm test`) and E2E tests under `tests/e2e/` (`pnpm test:e2e`)
4. Run `pnpm check` before committing
5. Update this `AGENTS.md` when architecture, commands, or conventions change

<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

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
