# AGENTS.md

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
- **`core/`** — Business logic, the heart of the app. Domains include `threads/` (creation, streaming, state), `api/` (LangGraph client singleton), `agents/` (custom agents), `auth/` (authentication), `artifacts/`, `channels/` (IM connections), `i18n/` (en-US, zh-CN), `settings/`, `memory/`, `skills/`, `messages/`, `mcp/`, `models/`, `input-polish/` (pre-send draft rewrite API), `voice-input/` (browser speech-recognition helpers), `suggestions/`, `tasks/`, `todos/`, `tools/`, `workspace-changes/` (run-scoped changed-file summaries and diff fetching), `config/`, `notification/`, `blog/`, plus rendering helpers (`rehype/`, `streamdown/`) and `utils/`.
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

The new-agent name-step badge must remain compatible with enterprise browsers
that lack CSS `color-mix()`. Keep its pale primary background as a separate
`bg-primary opacity-10` layer behind the inline icon; Tailwind opacity-color
utilities such as `bg-primary/10` fall back to an opaque primary color in those
browsers and hide the same-color icon.

The Kubernetes WeKnora UI bootstrap is served through the shared DeerFlow
origin. Keep its session exchange URL relative as
`/api/weknora/sso-session`; loopback, fixed-port, or node-pinned URLs bypass the
user's actual browser origin and break SSO in enterprise deployments. After a
successful DeerFlow exchange, the bootstrap must clear the standalone
`weknora_lite_mode` fallback and its session-only last path; a prior native-auth
failure can persist Lite mode, which hides WeKnora organizations/shared spaces
even after SSO recovers.
The enterprise root entry sets the server-only
`DEER_FLOW_UNAUTHENTICATED_HOME_URL`. The exact `/` route validates the current
Gateway session and redirects only an unauthenticated browser to that HTTPS
portal. Authenticated users, setup-required states, Gateway outages, APIs,
static assets, and workspace deep links must not be redirected.
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
navigation, while ordinary personal-workspace Owners can use New Chat,
Knowledge Bases, Shared Spaces, and Agents, and do not see Publish
Integrations, system settings, or tenant-creation controls. Entering the
embedded workspace collapses DeerFlow's sidebar and expands WeKnora's sidebar
once for both ordinary users and administrators. The automatic collapse must
run only once per route mount so the user can immediately reopen DeerFlow's
sidebar without navigating away first; the native WeKnora toggle remains
usable afterwards so chat history stays reachable. Agent creation,
editing, model/tool configuration, and chat usage must remain reachable for
ordinary users. The native
WeKnora deployment and RBAC remain
responsible for authorization; DOM filtering is only the simplified enterprise
presentation. The ordinary-user New Knowledge Base dialog and existing
knowledge-base Settings dialog expose only Basic Information while preserving
all native type and indexing choices on that page. Deployment defaults supply
the hidden model, parser, chunking, VLM, ASR, and storage values. System
administrators retain the complete native editor.

### Data Flow

1. Optional composer helpers such as `core/input-polish` can rewrite the local draft before submission, and `core/voice-input` can transcribe browser microphone input into that same local draft; confirmed user input then flows to thread hooks (`core/threads/hooks.ts`) → LangGraph SDK streaming
2. Stream events update thread state (messages, artifacts, todos, goal)
   When an existing chat page mounts without the SDK's tab-local reconnect key,
   `core/threads/hooks.ts` must query the Gateway run store for the newest
   `pending`/`running` run and join that run's stream. Keep the composer busy
   during discovery and recovery so reopening a running chat cannot submit a
   competing run and surface HTTP 409. The same recovery check runs when the
   page regains focus or visibility.
3. `useThreadHistory` loads persisted conversation pages from `GET /api/threads/{id}/messages/page`, preserving the backend's thread-global event `seq`; rendering overlays checkpoint/live copies at their matching canonical identities (a summarized checkpoint may contain a protected early input plus a recent tail), suppresses checkpoint/transient prefixes whose canonical position is still behind an unloaded cursor page instead of collapsing that unknown gap before a recent anchor, then adds optimistic messages without timestamp re-sorting. History invalidation preserves already-loaded pages so their established ordering positions are not discarded.
4. Stop actions call the LangGraph SDK stream stop path; `core/threads/hooks.ts` invalidates current-thread, thread-history, token-usage, and sidebar/search caches immediately and schedules one follow-up refetch because SDK stop may finish via abort + fire-and-forget cancel before backend title finalization commits
5. TanStack Query manages server state; localStorage stores user settings
6. Components subscribe to thread state and render updates

PowerPoint artifacts use a paired-preview contract: `/mnt/user-data/outputs/<name>.pptx` remains the selected and downloadable artifact, while `<name>.preview.pdf` is rendered in the right-side iframe when present; `<name>.pdf` is accepted as a compatibility fallback for older/model-authored outputs. Paired preview PDFs are internal companions: keep them in complete thread artifact state for iframe resolution, but omit them from file cards and the artifact selector, and normalize any legacy direct companion selection back to its owning presentation. `core/artifacts/utils.ts` owns the deterministic pairing rule, and the artifact detail component may probe the sibling preview path for histories whose artifact state predates automatic preview publication. Artifact headers must let long filenames shrink and truncate while keeping preview controls, downloads, and the close button fixed inside the panel. Markdown links to a current-thread artifact open that same right-side artifact panel; links carrying `?download=true` retain normal browser download behavior. The PDF iframe must load Gateway's browser-compatible `/artifact-previews/pdf/` gallery, which rasterizes validated companion pages server-side. Keep the iframe sandboxed without `allow-scripts` or `allow-same-origin`, use `no-referrer`, and do not restore a raw unrestricted same-origin PDF iframe.
Every recorded or guessed PowerPoint preview companion must pass a same-origin
HEAD probe before the iframe is rendered. A stale/missing companion may show a
download fallback for legacy history, but every newly successful PPT/PPTX
publication is backend-gated on a real persisted preview PDF.
Writing or publishing an artifact must not automatically open the right-side panel. Keep files and final deliverables visible in the conversation and top-level Files list, but require an explicit user click on a write step, artifact link, file card, or Files control before selecting an artifact and narrowing the chat column.
`MessageResponse` must use React's normal shallow-prop memoization (or compare every rendering prop), not a children-only comparator: thread-aware Markdown `components` arrive independently of unchanged text, and retaining an older link renderer turns preview links back into full-page artifact navigation. Interactive artifact links use an inert `#artifacts` href when a panel callback is available so an early pre-hydration click cannot replace the chat page with the raw file response.

`/goal` and `/compact` are built-in composer commands, not skill activations. `src/components/workspace/input-box.tsx` intercepts `/goal`, `/goal clear`, and `/goal <condition>` before normal chat submission, calling Gateway `GET/PUT/DELETE /api/threads/{thread_id}/goal`. Setting `/goal <condition>` also submits the condition text as the next user task so the agent starts running immediately; status and clear do not start a run. Goal and compact requests are tied to the current `threadId` with an `AbortController`, so switching threads or unmounting the composer aborts in-flight requests and stale responses cannot update the new thread's composer state. The chat pages render `GoalStatus` above the composer from `AgentThreadState.goal`, with local optimistic state until the next stream `values` update arrives. `/compact` calls `POST /api/threads/{thread_id}/compact` to summarize older active context while leaving the full visible chat history intact; it is skipped on new/empty threads and blocked server-side while a run is in flight.

Human input requests are a structured message protocol layered on normal chat history. The backend writes request payloads to `ToolMessage.artifact.human_input`, `src/core/messages/human-input.ts` owns the runtime validators/types, and `src/components/workspace/messages/human-input-card.tsx` renders the reusable card. The AI `ask_clarification` tool call uses the same canonical `clarification:<tool_call_id>` request identity as the authoritative ToolMessage, and message grouping replaces the streamed call card when that ToolMessage arrives so one request renders only once. `MessageList` owns answered/latest/pending state for visible cards, but derives answered responses from raw `thread.messages` because replies are hidden; pending cards clear when the hidden reply appears, when dispatch is dropped, or when a new `thread.error` reports an async stream failure. Page-level submit callbacks must send a normal human message and put `hide_from_ui: true` plus the response payload in the fourth `sendMessage(..., options)` argument as `options.additionalKwargs`; the third argument remains run context such as `{ agent_name }`. Composer entry points should disable normal bottom input while `hasOpenHumanInputRequest(...)` is true so users answer through the card and preserve response metadata.

Tool-calling AI messages can contain user-visible text as well as `tool_calls`. `core/messages/utils.ts` keeps these turns in an `assistant:processing` group, and `components/workspace/messages/message-group.tsx` must render the visible text as a processing step instead of treating the message as only tool metadata. This preserves provider text such as error explanations or "trying another approach" notes during tool-heavy runs.

Sidecar threads use `metadata.platform_sidecar` for newly created records. Thread
lookup, primary-list filtering, and parent-thread cascade deletion must also recognize
the legacy `metadata.deerflow_sidecar` marker so enterprise-branded deployments retain
access to sidecars created before the metadata rename.

### Key Patterns

- **Server Components by default**, `"use client"` only for interactive components
- **Thread hooks** (`useThreadStream`, `useSubmitThread`, `useThreads`) are the primary API interface
- **LangGraph client** is a singleton obtained via `getAPIClient()` in `core/api/`
- **Environment validation** uses `@t3-oss/env-nextjs` with Zod schemas (`src/env.js`). Skip with `SKIP_ENV_VALIDATION=1`
- **Subtask step history and runtime metadata** (`core/tasks/`) — the subtask card shows a subagent's full step timeline (#3779): its assistant reasoning turns interleaved with the tools it ran. `Subtask.steps[]` is accumulated live from `task_running` events (appended via `mergeSteps`, not overwritten) and backfilled on expand for historical runs by `fetchSubtaskSteps`, which pages the events endpoint scoped to one task (GET `/runs/{runId}/events?event_types=subagent.step&task_id=…&after_seq=…`) until a short page, so the run-wide limit can't truncate the timeline. `task_started` carries the effective `model_name`; `task_running` carries a cumulative usage snapshot after each completed LLM call. `core/tasks/lifecycle.ts` normalizes these additive events, and `computeNextSubtask` keeps the largest cumulative total so replayed or late SSE frames cannot double-count or roll the folded card backward. Terminal ToolMessage metadata (`subagent_model_name` / `subagent_token_usage`) restores the same values from normal history after reload; no per-card event fetch is needed. `core/tasks/steps.ts` is the pure step model: `messageToStep` (live), `eventsToSteps` (reload), `mergeSteps` (dedup by `message_index`), and `stepsForDisplay` (what the card renders — keeps tool steps + AI steps with text, drops the trailing final-answer AI step when completed since it's shown as `result`). `core/tasks/context.tsx`'s `useUpdateSubtask` applies updates against a `tasksRef` mirroring the latest state (not a closure snapshot), so a late-resolving `fetchSubtaskSteps` backfill merges into current state instead of clobbering SSE steps or sibling subtasks that arrived meanwhile. The owning `run_id` is carried onto history content messages in `buildVisibleHistoryMessages` so the card can resolve the events endpoint.
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
Its headline conversation value is the selected window's append-only creation
count; the same card labels current and all-time totals separately. The
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
sessions, and question turns, with per-user and per-knowledge-base detail.
Knowledge-base detail occupies a full-width row. The knowledge usage ranking
contains every user with inventory or activity and uses five-row client
pagination instead of truncating the backend query. Shared-space cards and
detail cover organizations, member workspaces, role composition, shared
knowledge bases/documents, and join requests. It also reports
current and historically-created scheduled tasks plus run outcomes. Do not
substitute DeerFlow `weknora_search`/`weknora_ask` tool-call counts for native
WeKnora usage. Dashboard progress bars, including the five external-agent
launch bars, use explicit solid `backgroundColor` values instead of gradients
or alpha-color syntax so the audited enterprise browser renders them reliably.
The backend remains the authoritative enforcement boundary. The panel must not mix runs from deleted
conversations into that current-session view.
The five built-in external-agent cards navigate through same-origin server
launch routes under `/api/cnooc/external-agents/<agent>/launch`. Those routes
read the HttpOnly `cnooc.platform_token` cookie and redirect only to the fixed
canonical HTTPS paths on `datalake.cnooc`, adding the current Token at redirect
time. Keep the strict target allowlist plus `no-store` and `no-referrer`
headers, and keep the shared Nginx entry's exact launch-prefix location ahead
of its general `/api/` Gateway catch-all. The Token must never be hardcoded,
returned to client JavaScript, or
stored in card data; do not restore the legacy `/agent-tools/*` proxies.
Slow-run, high-token, and recent-failure cards share five-row pagination plus a
bounded vertical scroll area.
Headline summaries poll every 15 seconds while heavier observability rankings
poll every 60 seconds. External-agent click telemetry must use the shared
authenticated fetch wrapper so the CSRF token is sent; telemetry failure must
not block navigation.

Settings appearance previews must remain shrinkable inside the dialog grid:
avoid fixed-width inner preview columns and retain `min-w-0` on nested grid
items. Preview colors must use explicit light/dark palette classes rather than
Tailwind slash-alpha utilities such as `bg-current/15` or
`border-current/10`; audited enterprise browsers can drop the alpha modifier
and render those shapes as opaque blocks. User-visible workspace and auth
status text must come from `core/i18n`, with Simplified Chinese entries kept in
sync with English. This enterprise branch defaults the first render and
missing/unknown locale state to `zh-CN`; users may still explicitly select
English. The About markdown heading contains the product name only;
build version metadata must not be appended to that heading.

Scheduled tasks own their execution-model choice independently of chat settings.
The create form prefers `deepseek-v4-flash` when that configured enterprise model
is available, falls back to the first available model elsewhere, and persists the
selection in `run_context`. Editing a task may change that saved model; switching
models in a chat must not mutate scheduled tasks.

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
The native App integration and release checklist live in
`docs/mobile-webview-integration.md`.

## Environment

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
