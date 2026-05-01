# CNOOC OAuth Mock Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `/oauth/cnooc/login?token=<platform-token>` mock SSO that auto-registers/logs in a DeerFlow user and redirects to `/workspace/chats/new`.

**Architecture:** Keep the entry point in the frontend Next.js app because workspace auth already depends on better-auth session cookies there. Split token normalization from database/session work so the mock token check can later become a real provider call without changing route structure.

**Tech Stack:** Next.js App Router route handlers, TypeScript, Node `crypto`, Node `sqlite`, better-auth SQLite schema, Node 24 built-in test runner for focused TypeScript helper tests.

---

## File Structure

- Create `frontend/src/server/cnooc-auth/mock-token-check.ts`: deterministic mock token check and identity mapping.
- Create `frontend/src/server/cnooc-auth/mock-token-check.test.ts`: focused tests for blank-token rejection and stable token-derived identities.
- Create `frontend/src/server/cnooc-auth/index.ts`: public server helper exports.
- Modify `frontend/src/server/admin/db.ts`: add narrowly scoped SSO helpers for upserting users and creating sessions in the same better-auth SQLite database.
- Create `frontend/src/app/oauth/cnooc/login/route.ts`: GET route that validates token, calls helper, creates user/session, sets cookie, redirects.

## Task 1: Mock Token Check Helper

**Files:**
- Create: `frontend/src/server/cnooc-auth/mock-token-check.ts`
- Create: `frontend/src/server/cnooc-auth/mock-token-check.test.ts`
- Create: `frontend/src/server/cnooc-auth/index.ts`

- [ ] **Step 1: Write the failing tests**

```ts
import assert from "node:assert/strict";
import { describe, it } from "node:test";

const { mockCheckCnoocToken } = await import(
  new URL("./mock-token-check.ts", import.meta.url).href
);

describe("mockCheckCnoocToken", () => {
  it("rejects blank tokens", async () => {
    await assert.rejects(
      () => mockCheckCnoocToken("   "),
      /token is required/,
    );
  });

  it("returns a stable identity for the same non-empty token", async () => {
    const first = await mockCheckCnoocToken("platform-token-a");
    const second = await mockCheckCnoocToken("platform-token-a");

    assert.deepEqual(second.user, first.user);
    assert.match(first.user.deerflowUserId, /^cnooc_/);
    assert.match(first.user.email, /@mock\.cnooc\.local$/);
    assert.ok(first.roles.length > 0);
    assert.ok(first.department.name);
  });

  it("returns different users for different tokens", async () => {
    const first = await mockCheckCnoocToken("platform-token-a");
    const second = await mockCheckCnoocToken("platform-token-b");

    assert.notEqual(second.user.deerflowUserId, first.user.deerflowUserId);
    assert.notEqual(second.user.email, first.user.email);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && node --test src/server/cnooc-auth/mock-token-check.test.ts`

Expected: FAIL because the test file imports a helper that does not exist yet.

- [ ] **Step 3: Implement the helper**

```ts
import crypto from "node:crypto";

export type CnoocTokenCheckResult = {
  ok: true;
  provider: "cnooc";
  platformTokenHash: string;
  user: {
    platformUserId: string;
    deerflowUserId: string;
    name: string;
    email: string;
  };
  roles: Array<{ code: string; name: string }>;
  department: { code: string; name: string };
};

function digestToken(token: string) {
  return crypto.createHash("sha1").update(token, "utf8").digest("hex");
}

export async function mockCheckCnoocToken(token: string): Promise<CnoocTokenCheckResult> {
  const normalized = token.trim();
  if (!normalized) throw new Error("token is required");

  const digest = digestToken(normalized);
  const short = digest.slice(0, 12);
  const platformUserId = `mock-${short}`;
  const deerflowUserId = `cnooc_${short}`;

  return {
    ok: true,
    provider: "cnooc",
    platformTokenHash: digest,
    user: {
      platformUserId,
      deerflowUserId,
      name: `CNOOC Mock User ${short.slice(0, 6)}`,
      email: `${deerflowUserId}@mock.cnooc.local`,
    },
    roles: [{ code: "mock-user", name: "Mock User" }],
    department: { code: "mock-dept", name: "Mock Department" },
  };
}
```

- [ ] **Step 4: Export the helper**

```ts
export { mockCheckCnoocToken, type CnoocTokenCheckResult } from "./mock-token-check";
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && node --test src/server/cnooc-auth/mock-token-check.test.ts`

Expected: PASS.

## Task 2: SSO Database Helpers

**Files:**
- Modify: `frontend/src/server/admin/db.ts`

- [ ] **Step 1: Add SSO helper functions**

Add exports:

```ts
export type SsoUserInput = {
  id: string;
  name: string;
  email: string;
};

export type CreatedSsoSession = {
  token: string;
  expiresAt: Date;
};

export function upsertSsoUser(input: SsoUserInput) {
  const id = input.id.trim();
  const name = input.name.trim();
  const email = input.email.trim().toLowerCase();
  if (!id) throw new Error("user id is required");
  if (!name) throw new Error("user name is required");
  if (!email) throw new Error("user email is required");

  const db = openDb();
  ensurePlatformTables(db);
  db.prepare(
    `
    INSERT INTO user(id, name, email, emailVerified, image, createdAt, updatedAt)
    VALUES(?, ?, ?, 1, NULL, datetime('now'), datetime('now'))
    ON CONFLICT(id) DO UPDATE SET
      name=excluded.name,
      email=excluded.email,
      emailVerified=1,
      updatedAt=datetime('now')
    `,
  ).run(id, name, email);
}

export function createSsoSession(userId: string): CreatedSsoSession {
  const id = crypto.randomBytes(18).toString("base64url");
  const token = crypto.randomBytes(32).toString("base64url");
  const expiresAt = new Date(Date.now() + 60 * 60 * 24 * 30 * 1000);
  const db = openDb();
  ensurePlatformTables(db);
  db.prepare(
    `
    INSERT INTO session(id, expiresAt, token, createdAt, updatedAt, ipAddress, userAgent, userId)
    VALUES(?, ?, ?, datetime('now'), datetime('now'), NULL, NULL, ?)
    `,
  ).run(id, expiresAt.toISOString(), token, userId);
  return { token, expiresAt };
}
```

- [ ] **Step 2: Run typecheck**

Run: `cd frontend && pnpm run typecheck`

Expected: PASS, or dependency-install failure if frontend dependencies are unavailable in the environment.

## Task 3: OAuth Login Route

**Files:**
- Create: `frontend/src/app/oauth/cnooc/login/route.ts`

- [ ] **Step 1: Implement the route**

```ts
import { NextResponse } from "next/server";
import crypto from "node:crypto";

import { createSsoSession, upsertSsoUser } from "@/server/admin/db";
import { auth, ensureAuthMigrations } from "@/server/better-auth";
import { mockCheckCnoocToken } from "@/server/cnooc-auth";

const SUCCESS_REDIRECT_PATH = "/workspace/chats/new";

type BetterAuthCookieOptions = {
  advanced?: {
    cookiePrefix?: string;
    cookies?: { session_token?: { name?: string } };
    useSecureCookies?: boolean;
  };
  baseURL?: string;
  secret?: string;
};

function getAuthOptions() {
  return auth.options as BetterAuthCookieOptions;
}

function shouldUseSecureAuthCookies() {
  const options = getAuthOptions();
  if (typeof options.advanced?.useSecureCookies === "boolean") {
    return options.advanced.useSecureCookies;
  }
  if (options.baseURL) return options.baseURL.startsWith("https://");
  return process.env.NODE_ENV === "production";
}

function getSessionCookieName() {
  const options = getAuthOptions();
  const prefix = options.advanced?.cookiePrefix ?? "better-auth";
  const name =
    options.advanced?.cookies?.session_token?.name ?? `${prefix}.session_token`;
  return shouldUseSecureAuthCookies() ? `__Secure-${name}` : name;
}

function getAuthSecret() {
  const secret = getAuthOptions().secret ?? process.env.BETTER_AUTH_SECRET;
  if (!secret) throw new Error("BETTER_AUTH_SECRET is required");
  return secret;
}

function signSessionCookieValue(value: string) {
  const hmac = crypto.createHmac("sha256", getAuthSecret());
  hmac.update(value);
  return `${value}.${hmac.digest("base64")}`;
}

export async function GET(request: Request) {
  await ensureAuthMigrations();
  const url = new URL(request.url);
  const token = url.searchParams.get("token")?.trim() ?? "";
  if (!token) {
    return NextResponse.json({ detail: "token is required" }, { status: 400 });
  }

  try {
    const checked = await mockCheckCnoocToken(token);
    upsertSsoUser({
      id: checked.user.deerflowUserId,
      name: checked.user.name,
      email: checked.user.email,
    });
    const session = createSsoSession(checked.user.deerflowUserId);
    const redirectUrl = new URL(SUCCESS_REDIRECT_PATH, request.url);
    const response = NextResponse.redirect(redirectUrl);
    response.cookies.set(getSessionCookieName(), signSessionCookieValue(session.token), {
      httpOnly: true,
      sameSite: "lax",
      secure: shouldUseSecureAuthCookies(),
      path: "/",
      expires: session.expiresAt,
    });
    return response;
  } catch (error) {
    const detail = error instanceof Error ? error.message : "cnooc login failed";
    return NextResponse.json({ detail }, { status: 500 });
  }
}
```

- [ ] **Step 2: Run typecheck**

Run: `cd frontend && pnpm run typecheck`

Expected: PASS, or dependency-install failure if frontend dependencies are unavailable in the environment.

## Task 4: Final Verification

- [ ] **Step 1: Run focused tests**

Run: `cd frontend && node --test src/server/cnooc-auth/mock-token-check.test.ts`

Expected: PASS.

- [ ] **Step 2: Run frontend typecheck**

Run: `cd frontend && pnpm run typecheck`

Expected: PASS.

- [ ] **Step 3: Inspect git diff**

Run: `git diff --stat`

Expected: only the CNOOC mock login files and SSO helper additions are changed.
