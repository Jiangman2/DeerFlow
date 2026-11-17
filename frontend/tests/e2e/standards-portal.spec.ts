import { expect, test, type Page, type TestInfo } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

const FEATURES = [
  { key: "qa", title: "标准知识问答" },
  { key: "search", title: "标准信息检索" },
  { key: "policy", title: "政策研究解读" },
  { key: "comparison", title: "标准对比分析" },
  { key: "applicability", title: "标准适用性分析" },
  { key: "compliance", title: "合规自检与预警" },
  { key: "authoring", title: "标准编审辅助" },
] as const;

type ModuleKey = (typeof FEATURES)[number]["key"];
type ExpectedAgents = Partial<Record<ModuleKey, string>>;

const SMALL_VIEWPORTS = [
  { width: 320, height: 568 },
  { width: 375, height: 812 },
  { width: 390, height: 844 },
  { width: 412, height: 915 },
  { width: 844, height: 390 },
] as const;

// The runner supplies the same explicit fixture to its server environment and
// project metadata. Missing metadata describes the unconfigured deployment.
function expectedAgents(testInfo: TestInfo): ExpectedAgents {
  const raw: unknown = testInfo.project.metadata.standardsPortalAgents;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const values = raw as Record<string, unknown>;
  const agents: ExpectedAgents = {};
  for (const { key } of FEATURES) {
    const name = values[key];
    if (typeof name === "string" && name.trim()) {
      agents[key] = name.trim().toLowerCase();
    }
  }
  return agents;
}

async function expectNoHorizontalOverflow(page: Page) {
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          Math.max(
            document.body.scrollWidth,
            document.documentElement.scrollWidth,
          ) <=
          window.innerWidth + 1,
      ),
    )
    .toBe(true);
}

test.setTimeout(90_000);

test.beforeEach(async ({ page }, testInfo) => {
  const baseURL = String(testInfo.project.use.baseURL);
  const origin = new URL(baseURL).origin;
  const agentNames = [...new Set(Object.values(expectedAgents(testInfo)))];
  const agents = agentNames.map((name) => ({
    name,
    display_name: name,
    description: "Offline standards employee fixture",
    model: null,
    tool_groups: [],
    skills: [],
    source: "team" as const,
  }));

  // Unknown APIs must not reach another task's Gateway, and external requests
  // must not leave this local UI fixture. Native employee data is mocked too.
  await page.route("**/*", (route) => {
    if (new URL(route.request().url()).origin !== origin) {
      return route.abort();
    }
    return route.continue();
  });
  await page.route("**/api/**", (route) =>
    route.fulfill({ status: 404, json: { detail: "Unmocked UI fixture API" } }),
  );
  mockLangGraphAPI(page, { agents, teamAgents: agents });
  await page.route("**/api/announcements/active", (route) =>
    route.fulfill({ json: { announcement: null } }),
  );
  await page.route("**/integrations/haineng/model-call-count", (route) =>
    route.fulfill({ json: { todayModelCallCount: 0, totalModelCallCount: 0 } }),
  );
  await page.route("**/api/home/statistics", (route) =>
    route.fulfill({ json: { todayUsers: 0, todayTasks: 0 } }),
  );
  await page.route("**/api/weknora/knowledge-bases", (route) =>
    route.fulfill({ json: { items: [], total: 0 } }),
  );
  await page.context().addCookies([
    { name: "locale", value: "zh-CN", url: baseURL },
    { name: "sidebar_state", value: "true", url: baseURL },
  ]);
});

test("standard portal presents seven active entries with their configured destinations", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/workspace/standards");

  const portal = page.getByTestId("seastandard-portal");
  const agents = expectedAgents(testInfo);
  await expect(portal).toBeVisible();
  await expect(
    page.getByRole("heading", { level: 1, name: "中海油 SeaStandard" }),
  ).toBeVisible();
  await expect(page.locator("[data-sidebar='sidebar']")).toHaveCount(0);
  await expect(portal.locator("[data-module]")).toHaveCount(7);
  await expect(portal.getByText("进入使用", { exact: true })).toHaveCount(7);
  await expect(portal.getByText(/建设中|规划中/)).toHaveCount(0);
  await expectNoHorizontalOverflow(page);

  for (const { key, title } of FEATURES) {
    const entry = portal.locator(`[data-module="${key}"]`);
    await expect(entry).toBeVisible();
    await expect(entry).toContainText(title);
    await expect(entry).toBeEnabled();
    await expect(entry).not.toHaveAttribute("target", "_blank");
    const name = agents[key];
    if (name) {
      await expect(entry).toHaveRole("link");
      await expect(entry).toHaveAttribute(
        "href",
        `/workspace/agents/${encodeURIComponent(name)}/chats/new`,
      );
    } else {
      await expect(entry).toHaveRole("button");
      expect(await entry.getAttribute("href")).toBeNull();
    }
  }
  await expect(portal.locator('a[href*="mode=skill"]')).toHaveCount(0);
  const bounds = await portal.boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds!.x).toBeGreaterThanOrEqual(-1);
  expect(bounds!.width).toBeGreaterThanOrEqual(1438);
  await page.screenshot({
    path: testInfo.outputPath("standards-desktop.png"),
    fullPage: true,
  });

  await page.getByRole("link", { name: "返回智能体平台" }).click();
  await expect(page).toHaveURL(/\/workspace\/chats\/new$/);
  await expect(page.locator("[data-sidebar='sidebar']")).toBeVisible();
  await expect(page.locator('textarea[name="message"]')).toBeEditable();
  await page.screenshot({
    path: testInfo.outputPath("current-chat-desktop.png"),
    fullPage: true,
  });
});

test("unconfigured entries explain the missing mapping without leaving the portal", async ({
  page,
}, testInfo) => {
  const agents = expectedAgents(testInfo);
  const unconfigured = FEATURES.filter(({ key }) => !agents[key]);
  test.skip(
    unconfigured.length === 0,
    "This deployment configures all modules",
  );
  await page.goto("/workspace/standards");

  for (const { key, title } of unconfigured) {
    const entry = page.locator(`[data-module="${key}"]`);
    await entry.click();
    const dialog = page.getByRole("dialog", { name: title, exact: true });
    await expect(dialog).toBeVisible();
    await expect(
      dialog.getByText("尚未配置对应数字员工，请联系管理员。", { exact: true }),
    ).toBeVisible();
    await expect(page).toHaveURL(/\/workspace\/standards$/);
    await dialog.getByRole("button", { name: "我知道了", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await expect(entry).toBeFocused();
  }

  const first = unconfigured[0]!;
  await page.locator(`[data-module="${first.key}"]`).click();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await expect(page).toHaveURL(/\/workspace\/standards$/);
});

test("a configured module opens its native employee in the same tab", async ({
  page,
}, testInfo) => {
  const agents = expectedAgents(testInfo);
  const configured = FEATURES.filter(({ key }) => !!agents[key]);
  if (configured.length === 0) {
    test.skip(true, "This deployment has no configured employee targets");
    return;
  }
  const selected =
    configured.find(({ key }) => /[\u3400-\u9fff]/.test(agents[key]!)) ??
    configured[0]!;
  const name = agents[selected.key]!;
  const path = `/workspace/agents/${encodeURIComponent(name)}/chats/new`;
  const baseURL = String(testInfo.project.use.baseURL);
  await page.goto("/workspace/standards");
  const pageCount = page.context().pages().length;
  await page.locator(`[data-module="${selected.key}"]`).click();
  await expect(page).toHaveURL(new URL(path, baseURL).href);
  expect(page.context().pages()).toHaveLength(pageCount);
  await expect(
    page.getByRole("heading", { level: 1, name, exact: true }),
  ).toBeVisible();
  await expect(page.locator('textarea[name="message"]')).toBeEditable();
  await expect(
    page.getByRole("link", { name: "返回标准首页", exact: true }),
  ).toHaveCount(0);
});

for (const viewport of SMALL_VIEWPORTS) {
  test(`standard portal keeps all seven entries reachable at ${viewport.width}x${viewport.height}`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize(viewport);
    await page.goto("/workspace/standards");

    const portal = page.getByTestId("seastandard-portal");
    await expect(portal).toBeVisible();
    await expect(page.locator("[data-sidebar='sidebar']")).toHaveCount(0);
    await expectNoHorizontalOverflow(page);
    await expect(
      page.getByRole("heading", { level: 1, name: "中海油 SeaStandard" }),
    ).toBeVisible();

    for (const { key, title } of FEATURES) {
      const entry = portal.locator(`[data-module="${key}"]`);
      await entry.scrollIntoViewIfNeeded();
      await expect(entry).toBeInViewport();
      await expect(entry).toContainText(title);
      await expect(entry).toContainText("进入使用");
      await expect(entry).toBeEnabled();
      const bounds = await entry.boundingBox();
      expect(bounds).not.toBeNull();
      expect(bounds!.x).toBeGreaterThanOrEqual(-1);
      expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(viewport.width + 1);
    }

    await expectNoHorizontalOverflow(page);
    await page.screenshot({
      path: testInfo.outputPath(
        `standards-${viewport.width}x${viewport.height}.png`,
      ),
      fullPage: true,
    });
  });
}

test("standard portal uses English copy for its entries and missing-target feedback", async ({
  page,
}, testInfo) => {
  await page.context().addCookies([
    {
      name: "locale",
      value: "en-US",
      url: String(testInfo.project.use.baseURL),
    },
  ]);
  await page.goto("/workspace/standards");

  const portal = page.getByTestId("seastandard-portal");
  const agents = expectedAgents(testInfo);
  await expect(
    page.getByRole("heading", { level: 1, name: "CNOOC SeaStandard" }),
  ).toBeVisible();
  await expect(portal.locator('[data-module="qa"]')).toContainText(
    "Knowledge Q&A",
  );
  await expect(portal.getByText("Open service", { exact: true })).toHaveCount(
    7,
  );
  await expect(portal.getByText("进入使用", { exact: true })).toHaveCount(0);
  await expect(
    page.getByRole("heading", { level: 1, name: "中海油 SeaStandard" }),
  ).toHaveCount(0);
  await expectNoHorizontalOverflow(page);

  const missing = FEATURES.find(({ key }) => !agents[key]);
  if (missing) {
    await portal.locator(`[data-module="${missing.key}"]`).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(
      dialog.getByText(
        "No digital employee is configured for this service. Please contact an administrator.",
        { exact: true },
      ),
    ).toBeVisible();
    await dialog.getByRole("button", { name: "Got it", exact: true }).click();
    await expect(dialog).not.toBeVisible();
  }
});
