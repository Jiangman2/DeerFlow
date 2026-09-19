import { expect, test, type Page } from "@playwright/test";

import { mockLangGraphAPI, type MockSkill } from "./utils/mock-api";

async function mockAuthenticatedSkillHub(
  page: Page,
  platformRoles: string[] = ["SUPER_ADMIN"],
  skills?: MockSkill[],
  requestEvents?: string[],
  managedNamespaces: Array<{
    type: "GLOBAL" | "TEAM";
    status: string;
    currentUserRole?: "OWNER" | "ADMIN" | "MEMBER";
  }> = [],
) {
  mockLangGraphAPI(page, skills ? { skills } : undefined);
  await page.route("**/api/runtime-config", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        skillHubEnabled: true,
        weknoraFrontendUrl: "/platform/knowledge-bases",
      }),
    }),
  );
  await page.route("**/api/v1/auth/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "test-user",
        email: "test@example.com",
        username: "test-user",
        is_active: true,
        is_superuser: false,
      }),
    }),
  );
  await page.route("**/api/v1/auth/setup-status", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ needs_setup: false }),
    }),
  );
  await page.route("**/platform/skillhub/**", (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (pathname.endsWith("/api/v1/auth/session/bootstrap")) {
      requestEvents?.push(`bootstrap:${request.method()}`);
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          code: 0,
          data: {
            userId: "skill-admin",
            displayName: "Skill Administrator",
            platformRoles,
            canChangePassword: true,
          },
        }),
      });
    }
    if (pathname.endsWith("/api/v1/auth/me")) {
      requestEvents?.push(`me:${request.method()}`);
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          code: 0,
          data: {
            userId: "skill-admin",
            displayName: "Skill Administrator",
            platformRoles,
            canChangePassword: true,
          },
        }),
      });
    }
    if (pathname.endsWith("/api/web/me/namespaces")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ code: 0, data: managedNamespaces }),
      });
    }
    requestEvents?.push(`frame:${request.method()}`);
    const frameMain = pathname.endsWith("/admin/users")
      ? `<h1>用户管理</h1><div role="dialog"><button>角色</button><div role="listbox"><div data-radix-select-viewport>${Array.from(
          { length: 20 },
          (_, index) => `<div style="height:40px">角色 ${index + 1}</div>`,
        ).join("")}</div></div></div>`
      : "<h1>技能搜索</h1>";
    return route.fulfill({
      status: 200,
      contentType: "text/html",
      body: `<!doctype html><html><head><meta name='viewport' content='width=device-width'><title>Astron SkillHub</title></head><body><div id='root'><div><div aria-hidden='true'>Decoration</div><header>SkillHub</header><main>${frameMain}</main><footer>© SkillHub</footer></div></div></body></html>`,
    });
  });
  await page.route("**/api/skillhub/runtime/skills", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        skills: [
          {
            namespace: "global",
            slug: "skillhub-hello",
            version: "1.0.0",
            skill_name: "skillhub-hello",
            enabled: true,
            installed: true,
            sha256: "abc123",
            installed_at: "2026-09-05T00:00:00Z",
            updated_at: "2026-09-05T00:00:00Z",
          },
        ],
      }),
    }),
  );
}

const NATIVE_OPERATOR_NAV_CASES = [
  {
    name: "skill administrator",
    roles: ["SKILL_ADMIN"],
    visible: ["发布技能", "审核中心", "审核与治理", "提升申请", "举报处理"],
    hidden: ["用户管理", "审计日志", "标签管理", "命名空间治理"],
  },
  {
    name: "user administrator",
    roles: ["USER_ADMIN"],
    visible: ["审核中心", "用户管理"],
    hidden: [
      "发布技能",
      "审核与治理",
      "提升申请",
      "举报处理",
      "审计日志",
      "标签管理",
      "命名空间治理",
    ],
  },
  {
    name: "auditor",
    roles: ["AUDITOR"],
    visible: ["审计日志"],
    hidden: [
      "发布技能",
      "审核中心",
      "审核与治理",
      "提升申请",
      "举报处理",
      "用户管理",
      "标签管理",
      "命名空间治理",
    ],
  },
  {
    name: "namespace administrator",
    roles: ["NAMESPACE_ADMIN"],
    visible: ["审核与治理"],
    hidden: [
      "发布技能",
      "审核中心",
      "提升申请",
      "举报处理",
      "用户管理",
      "审计日志",
      "标签管理",
      "命名空间治理",
    ],
  },
  {
    name: "super administrator",
    roles: ["SUPER_ADMIN"],
    visible: [
      "发布技能",
      "审核中心",
      "审核与治理",
      "提升申请",
      "举报处理",
      "用户管理",
      "审计日志",
      "标签管理",
      "命名空间治理",
    ],
    hidden: [],
  },
] as const;

for (const roleCase of NATIVE_OPERATOR_NAV_CASES) {
  test(`Skill Center mirrors native ${roleCase.name} navigation`, async ({
    page,
  }) => {
    await mockAuthenticatedSkillHub(page, [...roleCase.roles]);
    await page.goto("/workspace/skills");

    const menuTrigger = page.getByRole("button", { name: "管理中心" });
    await expect(menuTrigger).toBeVisible({ timeout: 15_000 });
    await menuTrigger.click();
    for (const label of roleCase.visible) {
      await expect(
        page.getByRole("menuitem", { name: label, exact: true }),
      ).toBeVisible();
    }
    for (const label of roleCase.hidden) {
      await expect(
        page.getByRole("menuitem", { name: label, exact: true }),
      ).toHaveCount(0);
    }
  });
}

test("Skill Center remains hidden and inert until the release gate is enabled", async ({
  page,
}) => {
  mockLangGraphAPI(page);
  let skillHubRequests = 0;
  await page.route("**/api/runtime-config", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ skillHubEnabled: false }),
    }),
  );
  await page.route("**/platform/skillhub/**", (route) => {
    skillHubRequests += 1;
    return route.abort();
  });

  await page.goto("/workspace/chats/new");
  await expect(
    page.locator("[data-sidebar='sidebar'] a[href='/workspace/skills']"),
  ).toHaveCount(0);

  await page.goto("/workspace/skills");
  await expect(page.getByText("技能中心尚未开放")).toBeVisible();
  await expect(page.getByTestId("skillhub-workspace-frame")).toHaveCount(0);
  expect(skillHubRequests).toBe(0);
});

test("Skill Center follows Knowledge and embeds native SkillHub", async ({
  page,
}) => {
  await mockAuthenticatedSkillHub(page);
  await page.goto("/workspace/chats/new");

  const sidebar = page.locator("[data-sidebar='sidebar']");
  const knowledgeLink = sidebar.locator("a[href='/workspace/knowledge']");
  const skillCenterLink = sidebar.locator(
    "a[data-sidebar='menu-button'][href='/workspace/skills']",
  );
  const agentsLink = sidebar.locator("a[href='/workspace/agents']");

  await expect(skillCenterLink).toBeVisible({ timeout: 15_000 });
  const navLinks = await sidebar
    .locator("a[href^='/workspace/']")
    .evaluateAll((links) => links.map((link) => link.getAttribute("href")));
  expect(navLinks.indexOf("/workspace/knowledge")).toBeLessThan(
    navLinks.indexOf("/workspace/skills"),
  );
  expect(navLinks.indexOf("/workspace/skills")).toBeLessThan(
    navLinks.indexOf("/workspace/agents"),
  );
  await expect(knowledgeLink).toBeVisible();
  await expect(agentsLink).toBeVisible();

  const nativeWindowLink = sidebar.locator(
    "a[href='/workspace/skills'][target='_blank']",
  );
  await expect(nativeWindowLink).toBeVisible();
  await expect(nativeWindowLink).toHaveAttribute("rel", /noopener/);

  await skillCenterLink.click();
  await page.waitForURL("**/workspace/skills");

  await expect(
    page.locator('[data-slot="sidebar"][data-state="collapsed"]'),
  ).toBeVisible({ timeout: 15_000 });
  const frame = page.getByTestId("skillhub-workspace-frame");
  await expect(frame).toBeVisible();
  await expect(frame).toHaveAttribute(
    "src",
    "/platform/skillhub/search?q=&sort=relevance&page=0&starredOnly=false",
  );
  await expect(
    page.getByRole("navigation", { name: "技能中心导航" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /管理中心/ })).toBeVisible();
  const skillHubFrame = frame.contentFrame();
  await expect(
    skillHubFrame.getByRole("heading", { name: "技能搜索" }),
  ).toBeVisible();
  await expect(skillHubFrame.locator("header")).toBeHidden();
  await expect(skillHubFrame.locator("footer")).toBeHidden();
});

test("Skill Center bootstraps the native account before protected content", async ({
  page,
}) => {
  const requestEvents: string[] = [];
  await mockAuthenticatedSkillHub(page, [], undefined, requestEvents);

  await page.goto("/workspace/skills");
  await expect(page.getByTestId("skillhub-workspace-frame")).toBeVisible({
    timeout: 15_000,
  });

  expect(requestEvents[0]).toBe("bootstrap:POST");
  expect(requestEvents).toContain("me:GET");
  expect(
    requestEvents.findIndex((event) => event.startsWith("frame:")),
  ).toBeGreaterThan(
    requestEvents.findIndex((event) => event.startsWith("bootstrap:")),
  );
});

test("Skill Center replaces a bootstrap 401 with a recoverable screen", async ({
  page,
}) => {
  await mockAuthenticatedSkillHub(page, []);
  await page.route(
    "**/platform/skillhub/api/v1/auth/session/bootstrap",
    (route) =>
      route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ code: 401, msg: "需要先登录", data: null }),
      }),
  );

  await page.goto("/workspace/skills");

  await expect(page.getByText("技能中心登录未完成")).toBeVisible({
    timeout: 15_000,
  });
  await expect(
    page.getByText("当前登录状态已失效，请重新登录后再试。"),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "重试登录" })).toBeVisible();
  await expect(page.getByTestId("skillhub-workspace-frame")).toHaveCount(0);
  await expect(page.getByText(/\{"code":401/)).toHaveCount(0);
});

test("Skill Center gives ordinary users the complete personal workbench without admin routes", async ({
  page,
}) => {
  await mockAuthenticatedSkillHub(page, []);
  await page.goto("/workspace/skills");

  await expect(page.getByRole("button", { name: "技能广场" })).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByRole("button", { name: "我的收藏" })).toBeVisible();
  await expect(page.getByRole("button", { name: "我的订阅" })).toBeVisible();
  await expect(page.getByRole("button", { name: "技能工作台" })).toBeVisible();
  await page.getByRole("button", { name: "技能工作台" }).click();
  await expect(page.getByRole("menuitem", { name: "个人概览" })).toBeVisible();
  await expect(
    page.getByRole("menuitem", { name: "已发布技能" }),
  ).toBeVisible();
  await expect(
    page.getByRole("menuitem", { name: "我的命名空间" }),
  ).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "审核进度" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "访问令牌" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "用户管理" })).toHaveCount(0);
  await expect(
    page.getByRole("menuitem", { name: "命名空间治理" }),
  ).toHaveCount(0);
});

test("team namespace owners receive the native review-center entry without a platform admin role", async ({
  page,
}) => {
  await mockAuthenticatedSkillHub(page, [], undefined, undefined, [
    { type: "TEAM", status: "ACTIVE", currentUserRole: "OWNER" },
  ]);
  await page.goto("/workspace/skills");

  const workbench = page.getByRole("button", { name: "管理中心" });
  await expect(workbench).toBeVisible({ timeout: 15_000 });
  await workbench.click();
  await expect(page.getByRole("menuitem", { name: "审核中心" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "用户管理" })).toHaveCount(0);
});

test("Skill Center separates native namespace creation from super-admin governance", async ({
  page,
}) => {
  await mockAuthenticatedSkillHub(page);
  await page.goto("/workspace/skills");

  const frame = page.getByTestId("skillhub-workspace-frame");
  const managementTrigger = page.getByRole("button", { name: "管理中心" });
  await expect(managementTrigger).toBeVisible({ timeout: 15_000 });

  await managementTrigger.click();
  await page.getByRole("menuitem", { name: "我的命名空间" }).click();
  await expect(frame).toHaveAttribute(
    "src",
    "/platform/skillhub/dashboard/namespaces",
  );

  await managementTrigger.click();
  await page.getByRole("menuitem", { name: "命名空间治理" }).click();
  await expect(frame).toHaveAttribute(
    "src",
    "/platform/skillhub/admin/namespaces",
  );
});

test("embedded native role selector remains scrollable inside the user dialog", async ({
  page,
}) => {
  await mockAuthenticatedSkillHub(page);
  await page.goto("/workspace/skills");

  await page.getByRole("button", { name: "管理中心" }).click();
  await page.getByRole("menuitem", { name: "用户管理" }).click();

  const frame = page.getByTestId("skillhub-workspace-frame").contentFrame();
  const dialog = frame.getByRole("dialog");
  const listbox = frame.getByRole("listbox");
  const viewport = frame.locator("[data-radix-select-viewport]");
  await expect(dialog).toHaveAttribute("data-deerflow-open-select", "true");
  await expect(listbox).toHaveCSS("overflow-y", "auto");
  await expect(viewport).toHaveCSS("overflow-y", "auto");
  expect(
    await viewport.evaluate(
      (element) => element.scrollHeight > element.clientHeight,
    ),
  ).toBe(true);
});

test("Skill Center shows the current user's complete custom skill library", async ({
  page,
}) => {
  await mockAuthenticatedSkillHub(
    page,
    [],
    [
      {
        name: "my-document-reviewer",
        description: "Review engineering documents.",
        category: "custom",
        enabled: false,
      },
      {
        name: "skillhub-hello",
        description: "Learn how registry skills work.",
        category: "custom",
        enabled: true,
      },
      {
        name: "public-research",
        description: "A public skill that must not appear here.",
        category: "public",
        enabled: true,
      },
    ],
  );
  await page.goto("/workspace/skills");

  await page.getByRole("button", { name: "我的技能", exact: true }).click();

  await expect(
    page.getByRole("heading", { name: "我的技能", level: 2 }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "skillhub-hello", level: 3 }),
  ).toBeVisible();
  await expect(page.getByText("技能中心 · @global · v1.0.0")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "my-document-reviewer", level: 3 }),
  ).toBeVisible();
  await expect(page.getByText("自建技能")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "public-research", level: 3 }),
  ).toHaveCount(0);
  await expect(page.getByRole("button", { name: "新建技能" })).toBeVisible();
  await expect(page.getByTestId("skillhub-workspace-frame")).toBeHidden();
});

test("Skill Center keeps a mobile navigation escape hatch", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockAuthenticatedSkillHub(page);
  await page.goto("/workspace/skills");

  const frame = page.getByTestId("skillhub-workspace-frame");
  await expect(frame).toBeVisible({ timeout: 15_000 });
  const managementTrigger = page.getByRole("button", { name: "管理中心" });
  await expect(managementTrigger).toBeVisible();
  await managementTrigger.click();
  await expect(
    page.getByRole("menuitem", { name: "我的命名空间" }),
  ).toBeVisible();
  await expect(
    page.getByRole("menuitem", { name: "发布技能", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "用户管理" })).toBeVisible();
  await expect(
    page.getByRole("menuitem", { name: "命名空间治理" }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  const frameBox = await frame.boundingBox();
  expect(frameBox).not.toBeNull();
  expect(frameBox!.x).toBeGreaterThanOrEqual(-1);
  expect(frameBox!.x + frameBox!.width).toBeLessThanOrEqual(391);

  await page.getByRole("button", { name: "我的技能", exact: true }).click();
  const skillRow = page.getByRole("article").first();
  await expect(skillRow).toBeVisible();
  const skillRowBox = await skillRow.boundingBox();
  expect(skillRowBox).not.toBeNull();
  expect(skillRowBox!.x).toBeGreaterThanOrEqual(-1);
  expect(skillRowBox!.x + skillRowBox!.width).toBeLessThanOrEqual(391);

  const trigger = page.locator("[data-sidebar='trigger']:visible").first();
  await expect(trigger).toBeVisible();
  await trigger.click();

  const mobileSidebar = page.locator(
    "[data-mobile='true'][data-sidebar='sidebar']",
  );
  await expect(mobileSidebar).toBeVisible();
  await expect(
    mobileSidebar.locator(
      "a[data-sidebar='menu-button'][href='/workspace/skills']",
    ),
  ).toBeVisible();
});
