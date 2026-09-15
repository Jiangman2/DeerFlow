import { afterEach, expect, rs, test } from "@rstest/core";

import {
  buildUserSkillLibrary,
  bootstrapSkillHubSession,
  hasSkillHubRole,
  hasSkillHubNamespaceReviewAccess,
  loadSkillHubManagedNamespaces,
  readSkillHubManagedNamespaces,
  readSkillHubDetail,
  readSkillHubSkillLocation,
  readSkillHubUser,
  sanitizeSkillHubVisibleText,
  skillHubDetailPath,
  skillHubDownloadPath,
  SKILLHUB_EMBED_PATH,
  SKILLHUB_MY_NAMESPACES_PATH,
  SKILLHUB_ROUTES,
  SKILLHUB_SESSION_BOOTSTRAP_PATH,
} from "@/core/skillhub";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

test("Skill Center opens discovery instead of the standalone landing page", () => {
  expect(SKILLHUB_EMBED_PATH).toContain("/platform/skillhub/search");
  expect(SKILLHUB_EMBED_PATH).not.toBe("/platform/skillhub/");
});

test("SkillHub identity envelope keeps native platform roles", () => {
  const user = readSkillHubUser({
    code: 0,
    data: {
      userId: "operator-1",
      displayName: "Operator",
      platformRoles: ["SKILL_ADMIN", "AUDITOR"],
      canChangePassword: true,
    },
  });

  expect(user?.displayName).toBe("Operator");
  expect(user?.canChangePassword).toBe(true);
  expect(hasSkillHubRole(user, "SUPER_ADMIN", "SKILL_ADMIN")).toBe(true);
  expect(hasSkillHubRole(user, "USER_ADMIN")).toBe(false);
});

test("SkillHub keeps personal namespace creation separate from global governance", () => {
  expect(SKILLHUB_ROUTES.myNamespaces).toBe(
    "/platform/skillhub/dashboard/namespaces",
  );
  expect(SKILLHUB_ROUTES.adminNamespaces).toBe(
    "/platform/skillhub/admin/namespaces",
  );
  expect(SKILLHUB_ROUTES.myNamespaces).not.toBe(
    SKILLHUB_ROUTES.adminNamespaces,
  );
});

test("SkillHub mirrors native namespace-owner review access", () => {
  const namespaces = readSkillHubManagedNamespaces({
    code: 0,
    data: [
      { type: "TEAM", status: "ACTIVE", currentUserRole: "MEMBER" },
      { type: "TEAM", status: "ACTIVE", currentUserRole: "OWNER" },
      { type: "GLOBAL", status: "ACTIVE", currentUserRole: "ADMIN" },
    ],
  });

  expect(hasSkillHubNamespaceReviewAccess(namespaces)).toBe(true);
  expect(
    hasSkillHubNamespaceReviewAccess([
      { type: "TEAM", status: "ACTIVE", currentUserRole: "MEMBER" },
    ]),
  ).toBe(false);
});

test("SkillHub loads native namespace memberships through the same-origin proxy", async () => {
  const fetchMock = rs.fn(async () =>
    Response.json({
      code: 0,
      data: [{ type: "TEAM", status: "ACTIVE", currentUserRole: "ADMIN" }],
    }),
  );
  globalThis.fetch = fetchMock as typeof fetch;

  await expect(loadSkillHubManagedNamespaces()).resolves.toEqual([
    { type: "TEAM", status: "ACTIVE", currentUserRole: "ADMIN" },
  ]);
  expect(fetchMock).toHaveBeenCalledWith(
    SKILLHUB_MY_NAMESPACES_PATH,
    expect.objectContaining({ credentials: "include", cache: "no-store" }),
  );
});

test("Skill Center establishes the native session before loading protected content", async () => {
  const fetchMock = rs.fn(async () =>
    Response.json({
      code: 0,
      data: {
        userId: "operator-1",
        displayName: "Operator",
        platformRoles: ["SUPER_ADMIN"],
        canChangePassword: true,
      },
    }),
  );
  globalThis.fetch = fetchMock as typeof fetch;

  await expect(bootstrapSkillHubSession()).resolves.toMatchObject({
    userId: "operator-1",
    platformRoles: ["SUPER_ADMIN"],
  });
  expect(fetchMock).toHaveBeenCalledWith(
    SKILLHUB_SESSION_BOOTSTRAP_PATH,
    expect.objectContaining({
      method: "POST",
      credentials: "include",
      cache: "no-store",
      body: JSON.stringify({ provider: "deerflow-session" }),
    }),
  );
});

test("Skill Center maps a native 401 to a recoverable Chinese message", async () => {
  globalThis.fetch = rs.fn(async () =>
    Response.json(
      { code: 401, msg: "需要先登录", data: null },
      { status: 401 },
    ),
  ) as typeof fetch;

  await expect(bootstrapSkillHubSession()).rejects.toMatchObject({
    message: "当前登录状态已失效，请重新登录后再试。",
    status: 401,
  });
});

test("invalid SkillHub identity envelopes are treated as anonymous", () => {
  expect(readSkillHubUser({ code: 0, data: null })).toBeNull();
  expect(
    readSkillHubUser({ code: 0, data: { displayName: "Missing id" } }),
  ).toBeNull();
});

test("embedded SkillHub detail paths expose a safe runtime identity", () => {
  expect(
    readSkillHubSkillLocation(
      "/platform/skillhub/space/@global/skillhub-hello",
    ),
  ).toEqual({ namespace: "global", slug: "skillhub-hello" });
  expect(
    readSkillHubSkillLocation("/platform/skillhub/search?q=hello"),
  ).toBeNull();
  expect(
    readSkillHubSkillLocation(
      "/platform/skillhub/space/@global/skillhub-hello/compare",
    ),
  ).toBeNull();
});

test("SkillHub detail envelope selects the published runtime version", () => {
  expect(
    readSkillHubDetail({
      code: 0,
      data: {
        namespace: "global",
        slug: "weather",
        displayName: "天气助手",
        summary: "查询天气",
        publishedVersion: { version: "1.2.0" },
      },
    }),
  ).toEqual({
    namespace: "global",
    slug: "weather",
    displayName: "天气助手",
    summary: "查询天气",
    version: "1.2.0",
  });
  expect(readSkillHubDetail({ code: 0, data: { slug: "weather" } })).toBeNull();
});

test("runtime package download stays on the same-origin SkillHub proxy", () => {
  expect(skillHubDownloadPath("global", "skillhub-hello", "1.0.0")).toBe(
    "/platform/skillhub/api/web/skills/global/skillhub-hello/versions/1.0.0/download",
  );
});

test("runtime skill detail links stay inside the embedded registry", () => {
  expect(skillHubDetailPath("global", "skillhub-hello")).toBe(
    "/platform/skillhub/space/@global/skillhub-hello",
  );
});

test("My Skills combines custom skills with SkillHub installation metadata", () => {
  const items = buildUserSkillLibrary(
    [
      {
        name: "platform-report",
        description: "Generate a report",
        category: "public",
        license: "MIT",
        enabled: true,
        editable: false,
      },
      {
        name: "my-reviewer",
        description: "Review a document",
        category: "custom",
        license: "",
        enabled: false,
        editable: true,
      },
      {
        name: "skillhub-hello",
        description: "Say hello",
        category: "custom",
        license: "Apache-2.0",
        enabled: true,
        editable: false,
      },
    ],
    [
      {
        namespace: "global",
        slug: "skillhub-hello",
        version: "1.0.0",
        skill_name: "skillhub-hello",
        enabled: false,
        installed: true,
        sha256: "abc",
        installed_at: "2026-09-05T00:00:00Z",
        updated_at: "2026-09-05T00:00:00Z",
      },
    ],
  );

  expect(items).toHaveLength(2);
  expect(
    items.find((item) => item.name === "my-reviewer")?.installation,
  ).toBeNull();
  expect(items.find((item) => item.name === "skillhub-hello")).toMatchObject({
    // The live /api/skills inventory wins over a stale registry snapshot.
    enabled: true,
    installation: {
      namespace: "global",
      version: "1.0.0",
    },
  });
});

test("embedded presentation removes host product names without changing registry data", () => {
  expect(
    sanitizeSkillHubVisibleText(
      "DeerFlow Local Admin / deerflow-local-admin / admin@deerflow.local",
    ),
  ).toBe("本地系统管理员 / local-platform-admin / admin@local.test");
});
