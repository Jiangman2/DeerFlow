# CNOOC OAuth Real Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the CNOOC mock login route with a production CNOOC OAuth token validation flow.

**Architecture:** Keep `/oauth/cnooc/login` responsible for HTTP/session work and move CNOOC API calls into a focused server helper. The helper validates env config, caches appCode, calls the token-check and user-info APIs, maps the returned profile, and throws typed sanitized errors for the route to convert into HTTP statuses.

**Tech Stack:** Next.js app route handlers, Node `fetch`, Node `node:test`, TypeScript, Better Auth SQLite helpers.

---

### Task 1: Real CNOOC Auth Helper Tests

**Files:**
- Create: `frontend/src/server/cnooc-auth/real-token-check.test.ts`

- [ ] **Step 1: Write failing tests**

Create tests that import `createCnoocAuthClient`, `CnoocAuthError`, `buildCnoocQueryPayload`, `buildCnoocUrl`, `mapCnoocUserInfo`, and `resetCnoocAppCodeCache` from `real-token-check.ts`. Cover:

- query payloads for `zhId` and `token`
- URL percent-encoding via `URLSearchParams`
- profile mapping to `platformUserId`, `email`, `name`, and `deerflowUserId`
- missing required fields
- successful fetch flow
- appCode cache reuse
- cached appCode retry once after an upstream failure
- token verification failure status

- [ ] **Step 2: Run tests and verify RED**

Run:

`cd frontend && node --test src/server/cnooc-auth/real-token-check.test.ts`

Expected: fails because `real-token-check.ts` does not exist.

### Task 2: Real CNOOC Auth Helper Implementation

**Files:**
- Create: `frontend/src/server/cnooc-auth/real-token-check.ts`
- Modify: `frontend/src/server/cnooc-auth/index.ts`

- [ ] **Step 1: Implement helper**

Create:

- `CnoocAuthError` with `status`.
- `buildCnoocQueryPayload(key, token)` returning `JSON.stringify({ jsonObj: { [key]: token } })`.
- `buildCnoocUrl(baseUrl, path, params)` using `URL` and `searchParams.set`.
- appCode in-memory cache with `resetCnoocAppCodeCache`.
- `createCnoocAuthClient({ fetchImpl, now })` for tests.
- default `checkCnoocToken(token)` using global `fetch`.

The route-facing result shape must remain compatible with the existing mock result: `user.deerflowUserId`, `user.platformUserId`, `user.name`, and `user.email`.

- [ ] **Step 2: Export real helper**

Update `frontend/src/server/cnooc-auth/index.ts` so `checkCnoocToken` comes from `real-token-check.ts`. Leave mock helper importable by direct file path for tests.

- [ ] **Step 3: Run helper tests and verify GREEN**

Run:

`cd frontend && node --test src/server/cnooc-auth/real-token-check.test.ts`

Expected: all tests pass.

### Task 3: Route and Env Integration

**Files:**
- Modify: `frontend/src/app/oauth/cnooc/login/route.ts`
- Modify: `frontend/src/env.js`

- [ ] **Step 1: Replace mock route call**

Change route import from `mockCheckCnoocToken` to `checkCnoocToken`.

Change token reading so it does not trim or normalize:

`const token = url.searchParams.get("token") ?? "";`

Map `CnoocAuthError.status` into HTTP status responses. Keep `BETTER_AUTH_SECRET` and session cookie behavior unchanged.

- [ ] **Step 2: Add env schema**

Add CNOOC server-only env vars to `frontend/src/env.js` server schema and `runtimeEnv`:

- `CNOOC_OAUTH_BASE_URL`
- `CNOOC_OAUTH_APP_NAME`
- `CNOOC_OAUTH_APP_ID`
- `CNOOC_OAUTH_TOKEN_CHECK_API_TOKEN`
- `CNOOC_OAUTH_TOKEN_CHECK_LOGIN_USER_CODE`
- `CNOOC_OAUTH_USER_INFO_API_TOKEN`

- [ ] **Step 3: Run existing route helper test**

Run:

`cd frontend && node --test src/app/oauth/cnooc/login/route-helpers.test.ts`

Expected: pass.

### Task 4: Full Verification

**Files:**
- All changed implementation files.

- [ ] **Step 1: Run CNOOC and route tests**

Run:

`cd frontend && node --test src/server/cnooc-auth/mock-token-check.test.ts src/server/cnooc-auth/real-token-check.test.ts src/app/oauth/cnooc/login/route-helpers.test.ts`

Expected: all pass.

- [ ] **Step 2: Run typecheck**

Run:

`cd frontend && corepack pnpm exec tsc --noEmit --tsBuildInfoFile /tmp/deer-flow-frontend.tsbuildinfo`

Expected: exit code 0.

- [ ] **Step 3: Inspect diff**

Run:

`git diff -- frontend/src/server/cnooc-auth frontend/src/app/oauth/cnooc/login frontend/src/env.js docs/superpowers/plans/2026-05-01-cnooc-oauth-real-login.md`

Expected: diff only contains real CNOOC OAuth changes and the implementation plan.

