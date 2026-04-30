# CNOOC OAuth Mock Login Design

## Goal

Add a mock CNOOC single sign-on entry point:

`/oauth/cnooc/login?token=<platform-token>`

The route accepts a platform token, validates it through a mock implementation of the CNOOC token check flow, automatically creates or updates a DeerFlow user, creates a normal DeerFlow login session, and redirects to `/workspace/chats/new`.

## Current Context

DeerFlow's private deployment authentication is implemented in the frontend Next.js app with better-auth and SQLite. Protected workspace pages call `getSession()`, and unauthenticated users are redirected to `/login?next=/workspace`.

User management currently extends better-auth with `platform_user_flags`, which stores only:

- `is_admin`
- `is_disabled`

This design keeps that table unchanged. It does not add role or department tables. Role and department values from the platform token check are only part of the token-check response shape for future real integration.

## Requirements

- Add a GET route at `/oauth/cnooc/login`.
- Read `token` from the query string.
- Reject missing or blank tokens with `400`.
- For the mock version, accept any non-empty token. There is no token whitelist.
- Mock the remote endpoint shape for `/providerportal/v1/zhySdk/tokenCheck?token=<platform-token>`.
- Use the returned platform user identity to create or update a better-auth user.
- Create a better-auth-compatible session cookie.
- Redirect successful logins to `/workspace/chats/new`.
- Do not change existing email/password login.
- Do not change existing admin and disabled-user behavior.

## User Identity Mapping

The mock token check produces a stable user from the token:

- Platform user ID: derived from a hash of the token.
- DeerFlow user ID: stable CNOOC-prefixed ID derived from the platform user ID.
- Email: generated from the derived ID, under a local mock domain.
- Name: generated from the derived ID.
- Roles and department: present in the mock response, not persisted in DeerFlow.

The same token must always map to the same DeerFlow user so repeated visits log in to the same account.

## Architecture

### Route Handler

Create a Next.js route handler under:

`frontend/src/app/oauth/cnooc/login/route.ts`

The handler:

1. Ensures better-auth migrations have run.
2. Reads and validates the query token.
3. Calls a CNOOC auth helper to check the token.
4. Upserts the better-auth user.
5. Creates a session row.
6. Sets the better-auth session cookie.
7. Redirects to `/workspace/chats/new`.

### CNOOC Auth Helper

Create a small server-only helper under `frontend/src/server/cnooc-auth/`.

The helper owns:

- mock token-check response generation
- platform response normalization
- deterministic ID/email/name derivation

Later, the helper can replace the mock function with a real `fetch` to:

`/providerportal/v1/zhySdk/tokenCheck?token=<platform-token>`

The route handler should not need structural changes when that happens.

### Database Helper

Extend the existing frontend server-side database helper area with focused functions for SSO:

- upsert a user by stable ID
- create a session for a user

The implementation should use the same better-auth SQLite database path already used by `frontend/src/server/admin/db.ts` and `frontend/src/server/better-auth/config.ts`.

## Session Behavior

The route creates the same class of session that better-auth expects, then sets the session cookie name used by the current better-auth configuration.

Successful navigation to `/workspace/chats/new` must pass the existing `getSession()` check without modifying workspace auth logic.

Disabled users stay disabled because `getSession()` already returns `null` when `isDisabledUser(userId)` is true.

## Error Handling

- Missing or blank token: `400` JSON response with `detail`.
- Token check failure in the future real implementation: `401` JSON response with `detail`.
- Database/session creation failure: `500` JSON response with `detail`.

The mock version should only fail for invalid input or internal errors.

## Testing

Add focused tests for the helper-level behavior where practical:

- Blank tokens are rejected.
- Non-empty tokens produce stable mock identities.
- Different tokens produce different mock users.

For route behavior, validate manually or with a minimal integration check:

1. Visit `/oauth/cnooc/login?token=abc`.
2. Confirm it redirects to `/workspace/chats/new`.
3. Confirm `/api/user/me` returns a user ID.
4. Visit again with the same token and confirm the same user is reused.

## Out Of Scope

- Real network call to the CNOOC provider.
- Token whitelist.
- Role and department persistence.
- User-management UI changes for role or department.
- Changes to email/password login.
