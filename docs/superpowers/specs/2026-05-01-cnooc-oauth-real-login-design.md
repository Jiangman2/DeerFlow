# CNOOC OAuth Real Login Design

## Goal

Replace the current CNOOC mock login route with a production-ready CNOOC OAuth login flow based on the provided platform APIs.

The login entry remains:

`/oauth/cnooc/login?token=<platform-token>`

The route validates the incoming platform token through CNOOC APIs, maps the returned user profile into DeerFlow's existing user model, creates a normal Better Auth session, and redirects to `/workspace/chats/new`.

## Scope

In scope:

- Use the existing `/oauth/cnooc/login` route.
- Use CNOOC production APIs:
  - `/apigateway/appauth/getappid`
  - `/apigateway/kzxmw/zcjksjfw_9543`
  - `/apigateway/zhy/tokenCheck`
- Store CNOOC API configuration in server-only environment variables.
- Preserve the existing DeerFlow user-management extension tables.
- Do not add role or department tables.
- Do not add a token whitelist.
- Preserve the existing Better Auth session creation approach.
- Keep the mock helper and mock tests as development/reference fixtures, but stop using the mock helper from the production login route.

Out of scope:

- OAuth callback pages or browser-side SDK integration.
- Role/department synchronization tables.
- Token whitelist or local token allow-listing.
- Exposing CNOOC API credentials to client-side code.

## Environment Variables

Add the following server-only environment variables:

- `CNOOC_OAUTH_BASE_URL`, for example `http://10.77.78.223`
- `CNOOC_OAUTH_APP_NAME`, for example `DApi`
- `CNOOC_OAUTH_APP_ID`, for example `880CADF172AC4ABD8864440804EE216F`
- `CNOOC_OAUTH_TOKEN_CHECK_API_TOKEN`, for example `OXIQWQZDACWVMYKL`
- `CNOOC_OAUTH_TOKEN_CHECK_LOGIN_USER_CODE`, for example `dengxueliang`
- `CNOOC_OAUTH_USER_INFO_API_TOKEN`, for example `OCXQURVUFYIRCIHQ`

These values are read only on the server. They must be added to `frontend/src/env.js` under the server schema and `runtimeEnv`.

## Architecture

Keep the route thin:

`frontend/src/app/oauth/cnooc/login/route.ts`

Route responsibilities:

1. Read the `token` query parameter.
2. Treat missing or empty token as invalid.
3. Do not trim or otherwise normalize the token before sending it to CNOOC.
4. Call `checkCnoocToken(token)`.
5. Upsert the mapped DeerFlow user through `upsertSsoUser()`.
6. Create a Better Auth session through `createSsoSession()`.
7. Set the Better Auth session cookie.
8. Redirect to `/workspace/chats/new`.

Add a real CNOOC helper:

`frontend/src/server/cnooc-auth/real-token-check.ts`

Helper responsibilities:

1. Load and validate CNOOC env config.
2. Get and cache `appCode`.
3. Verify the access token through the CNOOC token-check API.
4. Fetch the user profile through the CNOOC user-info API.
5. Map the user profile into the same result shape currently consumed by the route.

Update:

`frontend/src/server/cnooc-auth/index.ts`

Export the real `checkCnoocToken()` helper for the route. Keep mock exports available by explicit file import in tests if needed.

## Data Flow

### 1. Login Request

User opens:

`http://10.77.78.224:3000/oauth/cnooc/login?token=<platform-token>`

The route reads `token` exactly as provided by the query parser. Empty string is rejected. Non-empty tokens are passed unchanged to the CNOOC helper.

### 2. Get appCode

Call:

`GET {CNOOC_OAUTH_BASE_URL}/apigateway/appauth/getappid`

Query parameters:

- `appName={CNOOC_OAUTH_APP_NAME}`
- `appId={CNOOC_OAUTH_APP_ID}`
- `timeStamp=<current timestamp value required by platform>`

Success criteria:

- HTTP response is 2xx.
- Response body is valid JSON.
- `code === 200`.
- `data` is a non-empty string.

The helper caches the returned `appCode`.

### 3. Verify Access Token

Call:

`GET {CNOOC_OAUTH_BASE_URL}/apigateway/kzxmw/zcjksjfw_9543`

Headers:

- `apiToken: {CNOOC_OAUTH_TOKEN_CHECK_API_TOKEN}`
- `loginUserCode: {CNOOC_OAUTH_TOKEN_CHECK_LOGIN_USER_CODE}`

Query parameters:

- `appCode=<cached-or-new-appCode>`
- `query={"jsonObj":{"zhId":"<platform-token>"}}`

The `query` value is supplied as raw JSON to `URLSearchParams.set()`, so the final request URL uses normal URL percent-encoding, for example `%7B%22jsonObj%22...%7D%7D`.

Success criteria:

- HTTP response is 2xx.
- Response body is valid JSON.
- `code === 200`.

### 4. Fetch User Info

Call:

`GET {CNOOC_OAUTH_BASE_URL}/apigateway/zhy/tokenCheck`

Headers:

- `apiToken: {CNOOC_OAUTH_USER_INFO_API_TOKEN}`

Query parameters:

- `appCode=<cached-or-new-appCode>`
- `query={"jsonObj":{"token":"<platform-token>"}}`

The `query` value is percent-encoded by `URLSearchParams` in the final URL.

Success criteria:

- HTTP response is 2xx.
- Response body is valid JSON.
- `code === 200`.
- `data.userid`, `data.username`, and `data.ryxm` are non-empty strings.

### 5. Map User

Mapping:

- `data.userid` -> `platformUserId`
- `data.username + "@cnooc.com.cn"` -> `email`
- `data.ryxm` -> `name`

The DeerFlow user id is a stable CNOOC-prefixed id derived from `platformUserId`:

`cnooc_<safe-platform-user-id>`

If the platform id contains unsupported characters or is too long, the helper should use the same safe-id strategy already used by server-side user management helpers: preserve safe characters where possible and add a short hash when needed.

## appCode Cache

The CNOOC API documentation says `appCode` is valid for 2 hours. The implementation caches it for 110 minutes to leave a safety margin.

Cache behavior:

- If cached `appCode` exists and has not expired, reuse it.
- If no cache exists or it is expired, call `getappid`.
- If token verification or user-info fetch fails while using a cached `appCode`, clear the cache, fetch a new `appCode`, and retry the login flow once.
- Do not retry indefinitely.

This cache is in-process only. Multi-instance deployments may fetch separate `appCode` values per instance, which is acceptable for the first production implementation.

## Error Handling

Error responses from `/oauth/cnooc/login`:

- Missing or empty `token`: HTTP 400 with `{ "detail": "token is required" }`.
- Token verification failure: HTTP 401.
- Missing CNOOC env config: HTTP 500 with a sanitized configuration error.
- Upstream network failure, non-2xx response, invalid JSON, appCode failure, user-info failure, or required user field missing: HTTP 502.

Security rules:

- Never include the raw platform token in logs or HTTP responses.
- Never include `apiToken` values in logs or HTTP responses.
- Do not expose `appCode` in logs unless explicitly needed for a sanitized debug mode; default implementation should omit it.

## Testing

Add focused tests for `real-token-check.ts`.

Pure function tests:

- Token-check query payload uses `{"jsonObj":{"zhId":"<token>"}}`.
- User-info query payload uses `{"jsonObj":{"token":"<token>"}}`.
- URL construction produces normal URL percent-encoding for `query`.
- User profile mapping returns:
  - `platformUserId = data.userid`
  - `email = data.username.toLowerCase() + "@cnooc.com.cn"`
  - `name = data.ryxm`
  - stable `deerflowUserId`
- Missing `userid`, `username`, or `ryxm` throws a sanitized error.

Fetch-flow tests with injected mock `fetch`:

- Successful flow calls `getappid`, token verification, and user-info APIs in order.
- Second successful login reuses cached `appCode`.
- Cached `appCode` failure clears cache, fetches a new `appCode`, and retries once.
- Token verification failure is classified as unauthorized.
- Upstream fetch failure is classified as bad gateway.
- User-info success response with missing required fields is classified as bad gateway.

Route tests remain lightweight:

- Keep redirect URL helper tests.
- Avoid heavy Better Auth/SQLite route integration tests in this change.

## Deployment Verification

1. Configure the CNOOC environment variables in `frontend/.env` or the production runtime.
2. Ensure `BETTER_AUTH_SECRET` and `BETTER_AUTH_URL` are configured.
3. Restart the frontend service.
4. Visit:

   `http://10.77.78.224:3000/oauth/cnooc/login?token=<platform-token>`

5. Expected result:

   - The route validates the token with CNOOC.
   - A Better Auth session cookie is set.
   - The user is created or updated in the Better Auth `user` table.
   - The user email is `<username>@cnooc.com.cn`.
   - The browser redirects to `/workspace/chats/new`.

