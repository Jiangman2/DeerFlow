# DeerFlow Frontend

The October 31 incident-repair candidate bounds failed-run diagnostic responses
to 2 MiB and a 20-second request deadline. Closing the detail dialog cancels its
request; oversized or failed responses show a recoverable error. The preview
notice explains the server's latest-event and text limits. This candidate is
locally verified and has not been deployed.

Range application now shows a spinning button and a visible status panel, keeps
previous results explicitly labelled during updates, and offers retry after an
error or 60-second timeout. The date picker disables dates before the earliest
retained database day and validates typed values. Empty/unavailable bounds have
explicit states; the backend and frontend changes must be released together.

The October 2 dmdb frontend follow-up hardens tool-result navigation and
malformed argument rendering, presents clarification context once outside the
processing panel, shares the ordinary/custom-agent provider stack, encodes
upload deletion filenames, and preserves literal single-tilde temperature
ranges. Fixed employees keep the test hostname and precede the twelve contest
entries; production/test direct-link coverage is retained. Three incoming-only
reconnect-input hydration tests are deferred because the captured runtime lacks
that feature; the exact pre-merge reconnect test module is retained. See
[the follow-up record](docs/FRONTEND_RECONCILIATION_BATCH2_20261002.md).
This is not a completed merge or deployment acceptance.

The 2026-09-28 production source alignment on the enterprise 7·16 branch is
recorded in `../release/production-build-alignment-20260928/README.md`.

The isolated September 30 dmdb reconciliation preserves the existing interval,
custom-agent, duplicate-task, and paged-history workflows, together with their
Gateway route and pinned DOM-test dependencies. Haineng integration and incoming
PPT progress remain intact. Incomplete new status-filter, DST-edit/validation,
task-search, and Markdown-tag increments are deferred individually; this does
not remove preexisting tests or authorize deployment. See
[the merge record](../backend/docs/ENTERPRISE_MERGE_20260930.md).

Like the original DeerFlow 1.0, we would love to give the community a minimalistic and easy-to-use web interface with a more modern and flexible architecture.

## Tech Stack

- **Framework**: [Next.js 16](https://nextjs.org/) with [App Router](https://nextjs.org/docs/app)
- **UI**: [React 19](https://react.dev/), [Tailwind CSS 4](https://tailwindcss.com/), [Shadcn UI](https://ui.shadcn.com/), [MagicUI](https://magicui.design/) and [React Bits](https://reactbits.dev/)
- **AI Integration**: [LangGraph SDK](https://www.npmjs.com/package/@langchain/langgraph-sdk) and [Vercel AI Elements](https://vercel.com/ai-sdk/ai-elements)

## Quick Start

### Prerequisites

- Node.js 22+
- pnpm 10.26.2+

### Installation

```bash
# Install dependencies
pnpm install

# Copy environment variables
cp .env.example .env
# Edit .env with your configuration
```

### Development

The enterprise CNOOC workspace skin keeps the circular send action visibly
filled in both welcome and existing-thread composers, including before hover,
and renders the current `2026-2027` copyright notice on ordinary and custom
agent welcome pages.

Ordinary and Digital Employee chats share one provider stack for thread-scoped
subtasks, artifacts, Browser Live state, and prompt input, keeping both routes
behaviorally aligned.

```bash
# Start development server
pnpm dev

# The app will be available at http://localhost:3000
```

### Build & Test

```bash
# Type check
pnpm typecheck

# Check formatting
pnpm format

# Apply formatting
pnpm format:write

# Lint
pnpm lint

# Run unit tests
pnpm test

# One-time setup: install Playwright Chromium browser
pnpm exec playwright install chromium

# Run E2E tests (builds and starts production server automatically)
pnpm test:e2e

# Build for production
pnpm build

# Start production server
pnpm start
```

## Site Map

```
├── /                    # Landing page
├── /chats               # Chat list
├── /chats/new           # New chat page
└── /chats/[thread_id]   # A specific chat page
```

## Configuration

### Environment Variables

Key environment variables (see `.env.example` for full list):

```bash
# Backend API URL (optional, uses local Next.js/nginx proxy by default)
NEXT_PUBLIC_BACKEND_BASE_URL="http://localhost:8001"
# LangGraph-compatible API URL (optional, uses local Next.js/nginx proxy by default)
NEXT_PUBLIC_LANGGRAPH_BASE_URL="http://localhost:8001/api"
```

## Project Structure

```
tests/
├── e2e/                    # E2E tests (Playwright, Chromium, mocked backend)
└── unit/                   # Unit tests (mirrors src/ layout)
src/
├── app/                    # Next.js App Router pages
│   ├── api/                # API routes
│   ├── workspace/          # Main workspace pages
│   └── mock/               # Mock/demo pages
├── components/             # React components
│   ├── ui/                 # Reusable UI components
│   ├── workspace/          # Workspace-specific components
│   ├── landing/            # Landing page components
│   └── ai-elements/        # AI-related UI elements
├── core/                   # Core business logic
│   ├── api/                # API client & data fetching
│   ├── artifacts/          # Artifact management
│   ├── config/              # App configuration
│   ├── i18n/               # Internationalization
│   ├── mcp/                # MCP integration
│   ├── messages/           # Message handling
│   ├── models/             # Data models & types
│   ├── settings/           # User settings
│   ├── skills/             # Skills system
│   ├── threads/            # Thread management
│   ├── todos/              # Todo system
│   └── utils/              # Utility functions
├── hooks/                  # Custom React hooks
├── lib/                    # Shared libraries & utilities
├── server/                 # Server-side code
│   └── better-auth/        # Authentication setup and session helpers
└── styles/                 # Global styles
```

## Scripts

| Command             | Description                             |
| ------------------- | --------------------------------------- |
| `pnpm dev`          | Start development server with Turbopack |
| `pnpm build`        | Build for production                    |
| `pnpm start`        | Start production server                 |
| `pnpm test`         | Run unit tests with Rstest              |
| `pnpm test:e2e`     | Run E2E tests with Playwright           |
| `pnpm format`       | Check formatting with Prettier          |
| `pnpm format:write` | Apply formatting with Prettier          |
| `pnpm lint`         | Run ESLint                              |
| `pnpm lint:fix`     | Fix ESLint issues                       |
| `pnpm typecheck`    | Run TypeScript type checking            |
| `pnpm check`        | Run both lint and typecheck             |

## Development Notes

- Uses pnpm workspaces (see `packageManager` in package.json)
- Turbopack enabled by default in development for faster builds
- Environment validation can be skipped with `SKIP_ENV_VALIDATION=1` (useful for Docker)
- Backend API URLs are optional; nginx proxy is used by default in development

## License

MIT License. See [LICENSE](../LICENSE) for details.
