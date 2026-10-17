import { expect, test } from "@playwright/test";

import {
  PORTAL_QUICK_TOOLS,
  PORTAL_MORE_TOOLS,
  PPT_ASSISTANT_TOOL,
  USER_GUIDE_URL,
} from "../../src/core/portal-links";

import { mockLangGraphAPI } from "./utils/mock-api";

test.setTimeout(90_000);

test.beforeEach(async ({ page }, info) => {
  // This isolated UI suite has no live Gateway; the shared announcement header
  // must not accidentally hit another task's localhost:8001 backend.
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
  mockLangGraphAPI(page, {
    teamAgents: [
      {
        name: "research",
        display_name: "科研助手",
        source: "team",
        tags: ["热门"],
      },
    ],
  });
  await page
    .context()
    .addCookies([
      { name: "locale", value: "zh-CN", url: String(info.project.use.baseURL) },
    ]);
});

for (const width of [1440, 390]) {
  test(`GLM selector preserves fixed thinking and submits the chosen model at ${width}px`, async ({
    page,
  }, info) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("**/api/models", (route) =>
      route.fulfill({
        json: {
          models: [
            {
              name: "deepseek",
              model: "deepseek",
              display_name: "DeepSeek-V4-Flash",
              supports_thinking: true,
            },
            {
              name: "glm-5.3-flash",
              model: "GLM-5.3-Flash",
              display_name: "GLM-5.3-Flash",
              supports_thinking: true,
              thinking_always_on: true,
              supports_vision: true,
              supports_reasoning_effort: false,
            },
          ],
          token_usage: { enabled: false },
        },
      }),
    );
    await page.setViewportSize({ width, height: 850 });
    await page.goto("/workspace/chats/new");
    await page.locator(".cnooc-model-selector:visible").click();
    await page.getByRole("option").filter({ hasText: "GLM-5.3-Flash" }).click();
    await expect(page.locator(".cnooc-model-selector:visible")).toContainText(
      "GLM-5.3-Flash",
    );
    const mode =
      width < 768
        ? page.getByTestId("mobile-mode-selector")
        : page.locator(".cnooc-tool-mode:visible");
    await expect(mode).toContainText("思考");
    await mode.click();
    await expect(
      page.getByRole("menuitem").filter({ hasText: "闪速" }),
    ).toHaveCount(0);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("menu")).toBeHidden();
    await expect
      .poll(() =>
        page
          .locator(".cnooc-welcome-actions img")
          .first()
          .evaluate(
            (image: HTMLImageElement) =>
              image.complete && image.naturalWidth > 0,
          ),
      )
      .toBe(true);
    await page.screenshot({
      path: info.outputPath(`glm-selector-${width}.png`),
    });
    const submission = page.waitForRequest(
      (request) =>
        request.method() === "POST" && request.url().endsWith("/runs/stream"),
    );
    const input = page.locator('textarea[name="message"]');
    await input.fill("GLM 离线界面测试");
    await input.press("Enter");
    const payload = (await submission).postDataJSON() as {
      context: { model_name: string; thinking_enabled: boolean };
    };
    expect(payload.context.model_name).toBe("glm-5.3-flash");
    expect(payload.context.thinking_enabled).toBe(true);
    expect(errors).toEqual([]);
  });
}

for (const { width, height } of [
  { width: 1440, height: 850 },
  { width: 1024, height: 768 },
  { width: 875, height: 500 },
  { width: 320, height: 720 },
  { width: 375, height: 812 },
  { width: 390, height: 844 },
  { width: 412, height: 915 },
  { width: 844, height: 390 },
]) {
  test(`office menu and guide remain accessible at ${width}px`, async ({
    page,
  }, info) => {
    const errors: string[] = [];
    const consoleErrors: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error")
        consoleErrors.push(`${message.text()} (${message.location().url})`);
    });
    page.on("pageerror", (error) => errors.push(error.message));
    await page.setViewportSize({ width, height });
    await page.goto("/workspace/chats/new", { waitUntil: "domcontentloaded" });
    await expect(page.locator('textarea[name="message"]')).toBeEditable({
      timeout: 30_000,
    });
    await expect(
      page.locator('[data-testid="user-guide-link"]:visible'),
    ).toHaveAttribute("href", USER_GUIDE_URL);
    await expect(page.getByText("小惊喜", { exact: true })).toHaveCount(0);
    await expect(page.getByTestId("create-menu-trigger")).toHaveCount(0);
    const rail = page.getByTestId("welcome-suggestion-rail");
    const actions = rail.locator(
      '[data-slot="suggestion-item"] > :is(button, a)',
    );
    await expect(actions).toHaveCount(7);
    await expect(rail.getByTestId("external-navigation-marker")).toHaveCount(2);
    await expect(
      rail.locator("button").getByTestId("external-navigation-marker"),
    ).toHaveCount(0);
    // The seven children enter with staggered transforms; measure settled layout.
    await rail.evaluate(async (element) => {
      await Promise.all(
        element
          .getAnimations({ subtree: true })
          .map((animation) => animation.finished),
      );
    });
    for (const tool of PORTAL_QUICK_TOOLS) {
      const link = rail.getByTestId(`portal-tool-${tool.id}`);
      await link.scrollIntoViewIfNeeded();
      await expect(link).toBeInViewport();
      await expect(link).toHaveAttribute("href", tool.href);
      await expect(link).toHaveAttribute("target", "_blank");
      await expect(link).toHaveAttribute("rel", "noopener noreferrer");
      await expect(
        link.getByTestId("external-navigation-marker"),
      ).toBeVisible();
      await expect(link).toHaveAttribute("title", "在新窗口打开");
    }
    const actionBoxes = await actions.evaluateAll((elements) =>
      elements.map((element) => {
        const { width, height, y } = element.getBoundingClientRect();
        return { width, height, y };
      }),
    );
    for (const box of actionBoxes) {
      expect(Math.abs(box.y - actionBoxes[0]!.y)).toBeLessThanOrEqual(1);
      expect(Math.abs(box.height - actionBoxes[0]!.height)).toBeLessThanOrEqual(
        1,
      );
      expect(Math.abs(box.width - actionBoxes[0]!.width)).toBeLessThanOrEqual(
        1,
      );
    }
    // The artwork (including its badge) and visible label form one centred row.
    for (const action of await actions.all()) {
      const offset = await action.evaluate((element) => {
        const box = element.getBoundingClientRect();
        const children = [...element.children]
          .filter((child) => !child.classList.contains("sr-only"))
          .map((child) => child.getBoundingClientRect());
        const left = Math.min(...children.map((child) => child.left));
        const right = Math.max(...children.map((child) => child.right));
        const top = Math.min(...children.map((child) => child.top));
        const bottom = Math.max(...children.map((child) => child.bottom));
        const centers = children.map((child) => (child.top + child.bottom) / 2);
        return {
          x: (left + right - box.left - box.right) / 2,
          y: (top + bottom - box.top - box.bottom) / 2,
          rowError: Math.max(...centers) - Math.min(...centers),
        };
      });
      expect(Math.abs(offset.x)).toBeLessThanOrEqual(1);
      expect(Math.abs(offset.y)).toBeLessThanOrEqual(2);
      expect(offset.rowError).toBeLessThanOrEqual(1);
    }
    await expect(rail.getByTestId("portal-tool-ppt")).toHaveCount(0);
    await page.getByTestId("portal-tools-trigger").click();
    const menu = page.getByTestId("portal-tools-menu");
    await expect(menu).toBeVisible();
    await menu.evaluate(async (element) => {
      await Promise.all(
        element
          .getAnimations({ subtree: true })
          .map((animation) => animation.finished),
      );
    });
    const originalIcon = page
      .locator('.cnooc-welcome-actions img[src*="action-ppt"]')
      .first();
    const originalSize = await originalIcon.evaluate(
      (element) => element.getBoundingClientRect().width,
    );
    const moreCanvas = page
      .getByTestId("portal-tools-trigger")
      .locator(".cnooc-portal-artwork");
    expect(
      await moreCanvas.evaluate(
        (element) => element.getBoundingClientRect().width,
      ),
    ).toBe(originalSize);
    for (const tool of PORTAL_QUICK_TOOLS) {
      expect(
        await rail
          .getByTestId(`portal-tool-${tool.id}`)
          .locator(".cnooc-portal-artwork")
          .evaluate((element) => element.getBoundingClientRect().width),
      ).toBe(originalSize);
    }
    await expect(menu.getByRole("menuitem")).toHaveCount(4);
    await expect(menu.getByTestId("external-navigation-marker")).toHaveCount(4);
    for (const item of PORTAL_MORE_TOOLS) {
      const link = menu.getByTestId(`portal-tool-${item.id}`);
      await expect(link).toBeVisible();
      await expect(link).toHaveAttribute("href", item.href);
      await expect(link).toHaveAttribute("target", "_blank");
      await expect(link).toHaveAttribute("rel", "noopener noreferrer");
      const canvas = link.locator(".cnooc-portal-artwork");
      expect(
        await canvas.evaluate(
          (element) => element.getBoundingClientRect().width,
        ),
      ).toBe(originalSize);
      await expect
        .poll(
          () =>
            link
              .locator("img")
              .evaluate(
                (img: HTMLImageElement) => img.complete && img.naturalWidth > 0,
              ),
          { timeout: 30_000 },
        )
        .toBe(true);
    }
    const bounds = await menu.boundingBox();
    for (const marker of await page
      .getByTestId("external-navigation-marker")
      .all()) {
      const position = await marker.evaluate((element) => {
        const badge = element.getBoundingClientRect();
        const artwork = element.parentElement!.getBoundingClientRect();
        return {
          right: badge.right - (artwork.left + artwork.right) / 2,
          bottom: badge.bottom - (artwork.top + artwork.bottom) / 2,
        };
      });
      // Preserve the exact badge placement in the user's approved mobile image.
      expect(position.right).toBeCloseTo(14, 0);
      expect(position.bottom).toBeCloseTo(14, 0);
    }
    expect(bounds?.x).toBeGreaterThanOrEqual(0);
    expect((bounds?.x ?? 0) + (bounds?.width ?? 0)).toBeLessThanOrEqual(width);
    expect(bounds?.width).toBeLessThanOrEqual(320);
    expect(bounds?.height).toBeLessThanOrEqual(160);
    expect(bounds?.y).toBeGreaterThanOrEqual(0);
    expect((bounds?.y ?? 0) + (bounds?.height ?? 0)).toBeLessThanOrEqual(
      height,
    );
    expect(errors).toEqual([]);
    await expect(page).toHaveURL(/\/workspace\/chats\/new$/);
    await expect(page).not.toHaveTitle("");
    await page.screenshot({ path: info.outputPath(`portal-${width}.png`) });
    await page.keyboard.press("Escape");
    await expect(menu).toBeHidden();
    const input = page.locator('textarea[name="message"]');
    await input.fill("保留这份草稿");
    await page.getByTestId("ppt-generation-menu-trigger").click();
    const pptMenu = page.getByTestId("ppt-generation-menu");
    await expect(pptMenu.getByRole("menuitem")).toHaveCount(5);
    await expect(pptMenu.getByTestId("external-navigation-marker")).toHaveCount(
      1,
    );
    const pptLink = pptMenu.getByTestId("portal-tool-ppt");
    await pptLink.scrollIntoViewIfNeeded();
    await expect(pptLink).toBeInViewport();
    await expect(pptLink).toHaveAttribute("href", PPT_ASSISTANT_TOOL.href);
    await expect(pptLink).toHaveAttribute("rel", "noopener noreferrer");
    await expect
      .poll(() =>
        pptLink
          .locator("img")
          .evaluate(
            (img: HTMLImageElement) => img.complete && img.naturalWidth > 0,
          ),
      )
      .toBe(true);
    await page.screenshot({ path: info.outputPath(`ppt-menu-${width}.png`) });
    // Verify new-tab behavior without accessing the enterprise portal locally.
    await page
      .context()
      .route(PPT_ASSISTANT_TOOL.href, (route) =>
        route.fulfill({ body: "offline portal fixture" }),
      );
    const popupPromise = page.waitForEvent("popup");
    await pptLink.click();
    const popup = await popupPromise;
    await popup.waitForLoadState();
    await expect(popup).toHaveURL(PPT_ASSISTANT_TOOL.href);
    await popup.close();
    await expect(input).toHaveValue("保留这份草稿");
    // Navigation opens the popup before Radix's selection/close event settles.
    // Observe the actual closed state before a second toggle, especially on
    // mobile where focus restoration also scrolls the action rail.
    await expect(pptMenu).toBeHidden();
    await page.getByTestId("ppt-generation-menu-trigger").click();
    await expect(pptMenu).toBeVisible();
    await page.getByTestId("ppt-route-generate").click();
    await expect(input).toHaveValue(/^\/ppt-master /);
    await expect(page).toHaveURL(/\/workspace\/chats\/new$/);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    expect(errors).toEqual([]);
    expect(consoleErrors).toEqual([]);
  });
}

test("all flags removed, show all, admin toggle, creator profile and persistence", async ({
  page,
}, info) => {
  let hot = false;
  let version = 0;
  await page.route("**/api/employee-directory**", async (route) => {
    if (route.request().method() === "PUT") {
      expect(route.request().postDataJSON()).toEqual({
        is_hot: true,
        expected_version: 0,
        expected_identity: "a".repeat(64),
      });
      hot = true;
      version++;
      return route.fulfill({
        json: { source: "team", name: "research", is_hot: hot, version },
      });
    }
    return route.fulfill({
      json: {
        employees: [
          {
            name: "research",
            source: "team",
            is_hot: hot,
            version,
            identity_token: "a".repeat(64),
            creator_name: "张研究员",
          },
        ],
      },
    });
  });
  await page.goto("/workspace/agents");
  await expect(page.getByTestId("hot-empty-state")).toBeVisible();
  await expect(page.getByTestId("digital-employee-card")).toHaveCount(0);
  await page
    .getByTestId("hot-empty-state")
    .getByRole("button", { name: "展示全部" })
    .click();
  await page.getByRole("searchbox").fill("科研助手");
  const card = page
    .getByTestId("digital-employee-card")
    .filter({ hasText: "科研助手" });
  await card.getByRole("button", { name: "更多" }).click();
  await page.getByTestId("employee-toggle-hot").click();
  await expect(card.getByTestId("employee-hot-badge")).toBeVisible();
  await card.getByRole("button", { name: "更多" }).click();
  await page.getByRole("menuitem", { name: "查看档案" }).click();
  await expect(page.getByTestId("employee-creator")).toContainText("张研究员");
  await page.screenshot({ path: info.outputPath("creator-profile.png") });
  const restoredDirectory = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/employee-directory") &&
      response.request().method() === "GET",
  );
  await page.reload({ waitUntil: "domcontentloaded" });
  expect((await (await restoredDirectory).json()).employees[0].is_hot).toBe(
    true,
  );
  await expect(page.getByTestId("digital-employee-card")).toHaveCount(1);
  await expect(page.getByTestId("employee-hot-badge")).toBeVisible();
});

test("existing and newly published employees default hot; admin removal survives refresh", async ({
  page,
}, info) => {
  mockLangGraphAPI(page, {
    teamAgents: [
      { name: "existing", display_name: "上线前员工", source: "team" },
      { name: "new", display_name: "上线后员工", source: "team" },
    ],
  });
  const flags = {
    existing: { is_hot: true, version: 1, identity_token: "a".repeat(64) },
    new: { is_hot: true, version: 0, identity_token: "b".repeat(64) },
  };
  await page.route("**/api/employee-directory**", async (route) => {
    if (route.request().method() === "PUT") {
      const input = route.request().postDataJSON();
      expect(input).toEqual({
        is_hot: false,
        expected_version: 1,
        expected_identity: flags.existing.identity_token,
      });
      flags.existing.is_hot = false;
      flags.existing.version = 2;
      return route.fulfill({
        json: { name: "existing", source: "team", ...flags.existing },
      });
    }
    return route.fulfill({
      json: {
        employees: Object.entries(flags).map(([name, flag]) => ({
          name,
          source: "team",
          creator_name: "张研究员",
          ...flag,
        })),
      },
    });
  });
  await page.goto("/workspace/agents");
  const cards = page.getByTestId("digital-employee-card");
  await expect(cards).toHaveCount(2);
  await expect(page.getByTestId("employee-hot-badge")).toHaveCount(2);
  await page.screenshot({
    path: info.outputPath("published-employees-default-hot.png"),
  });
  const existingCard = cards.filter({ hasText: "上线前员工" });
  await existingCard.getByRole("button", { name: "更多" }).click();
  await page.getByTestId("employee-toggle-hot").click();
  await expect(cards).toHaveCount(1);
  await expect(cards.first()).toContainText("上线后员工");
  await expect(cards.first().getByTestId("employee-hot-badge")).toBeVisible();
  await page.reload();
  await expect(cards).toHaveCount(1);
  await expect(cards.first()).toContainText("上线后员工");
  await page.getByTestId("employees-show-all").first().click();
  await page.getByRole("searchbox").fill("上线前员工");
  await expect(cards).toHaveCount(1);
  await expect(existingCard.getByTestId("employee-hot-badge")).toHaveCount(0);
  await page.screenshot({
    path: info.outputPath("admin-removed-still-available-in-all.png"),
  });
  await page.reload();
  await expect(cards).toHaveCount(1);
  await expect(cards.first()).toContainText("上线后员工");
});

test("creator publishes a private employee and sees it hot without page refresh", async ({
  page,
}, info) => {
  const browserErrors: string[] = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error")
      browserErrors.push(`${message.location().url}: ${message.text()}`);
  });
  const employee = {
    name: "publish-me",
    display_name: "新发布员工",
    source: "personal" as const,
  };
  mockLangGraphAPI(page, { agents: [employee], teamAgents: [] });
  let published = false;
  let metadataRefreshes = 0;
  await page.route("**/api/agents/publish-me/publish", async (route) => {
    expect(route.request().method()).toBe("POST");
    published = true;
    return route.fulfill({ json: { ...employee, source: "team" } });
  });
  await page.route("**/api/team-agents", (route) =>
    route.fulfill({
      json: { agents: published ? [{ ...employee, source: "team" }] : [] },
    }),
  );
  await page.route("**/api/employee-directory", (route) => {
    if (published) metadataRefreshes++;
    return route.fulfill({
      json: {
        employees: published
          ? [
              {
                source: "team",
                name: employee.name,
                is_hot: true,
                version: 0,
                identity_token: "c".repeat(64),
                creator_name: "张研究员",
              },
            ]
          : [],
      },
    });
  });
  await page.goto("/workspace/agents");
  await expect(page.getByTestId("hot-empty-state")).toBeVisible();
  await page.getByRole("tab", { name: "我的数字员工" }).click();
  const card = page
    .getByTestId("digital-employee-card")
    .filter({ hasText: employee.display_name });
  await expect(card.getByTestId("employee-hot-badge")).toHaveCount(0);
  await card.getByRole("button", { name: "更多" }).click();
  await page.getByRole("menuitem", { name: "发布到数字员工广场" }).click();
  await expect.poll(() => metadataRefreshes).toBeGreaterThan(0);
  await page.getByRole("tab", { name: "数字员工广场", exact: true }).click();
  await expect(card).toBeVisible();
  await expect(card.getByTestId("employee-hot-badge")).toBeVisible();
  await expect(page).toHaveURL(/\/workspace\/agents$/);
  await expect(page).not.toHaveTitle("");
  await expect(page.locator("[data-nextjs-dialog-overlay]")).toHaveCount(0);
  expect(browserErrors).toEqual([]);
  await page.screenshot({
    path: info.outputPath("just-published-default-hot.png"),
  });
});

test("directory outage is visible and does not expose hot management", async ({
  page,
}) => {
  await page.route("**/api/employee-directory", (route) =>
    route.fulfill({ status: 503, json: { detail: "unavailable" } }),
  );
  await page.goto("/workspace/agents");
  await expect(page.getByRole("alert")).toBeVisible();
  await page.getByTestId("employees-show-all").first().click();
  await page
    .getByTestId("digital-employee-card")
    .first()
    .getByRole("button", { name: "更多" })
    .click();
  await expect(page.getByTestId("employee-toggle-hot")).toHaveCount(0);
});

test("ordinary users cannot see hot management; mobile show all clears filters", async ({
  page,
}) => {
  await page.route("**/api/v1/auth/me", (route) =>
    route.fulfill({
      json: {
        id: "user",
        email: "test@example.test",
        system_role: "user",
        needs_setup: false,
      },
    }),
  );
  await page.route("**/api/employee-directory", (route) =>
    route.fulfill({ json: { employees: [] } }),
  );
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/workspace/agents");
  // AuthProvider starts with the SSR development admin. Exercise its real
  // visibility refresh to apply the mocked ordinary-user session.
  await expect(page.getByTestId("hot-empty-state")).toBeVisible();
  const refresh = page.waitForResponse("**/api/v1/auth/me");
  await page.evaluate(() =>
    document.dispatchEvent(new Event("visibilitychange")),
  );
  expect((await (await refresh).json()).system_role).toBe("user");
  await page
    .getByTestId("hot-empty-state")
    .getByRole("button", { name: "展示全部" })
    .click();
  await page.getByRole("searchbox").fill("not-found");
  await page.getByRole("button", { name: "筛选", exact: true }).click();
  await page
    .getByTestId("mobile-filter-scroll")
    .getByTestId("employees-show-all")
    .click();
  await expect(page.getByRole("searchbox")).toHaveValue("");
  await page
    .getByTestId("digital-employee-card")
    .first()
    .getByRole("button", { name: "更多" })
    .click();
  await expect(page.getByTestId("employee-toggle-hot")).toHaveCount(0);
});
