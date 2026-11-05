import { expect, test } from "@playwright/test";

import {
  PORTAL_QUICK_TOOLS,
  PORTAL_TOOLS,
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
  test(`all office and guide links open isolated tabs without losing draft at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 850 });
    await page.goto("/workspace/chats/new");
    const input = page.locator('textarea[name="message"]');
    await expect(input).toBeEditable();
    await input.fill("保留外链点击前的草稿");
    for (const tool of [
      ...PORTAL_TOOLS,
      { id: "guide", placement: "guide", href: USER_GUIDE_URL },
    ]) {
      if (tool.placement === "more")
        await page.getByTestId("portal-tools-trigger").click();
      if (tool.placement === "ppt")
        await page.getByTestId("ppt-generation-menu-trigger").click();
      const link =
        tool.id === "guide"
          ? page.locator('[data-testid="user-guide-link"]:visible')
          : page.getByTestId(`portal-tool-${tool.id}`);
      const requestURL = new URL(tool.href);
      requestURL.hash = "";
      await page.context().route(requestURL.href, (route) =>
        route.fulfill({
          contentType: "text/html",
          body: "<title>Offline office target</title><p>Fixture only</p>",
        }),
      );
      const opened = page.waitForEvent("popup");
      await link.click();
      const popup = await opened;
      await popup.waitForLoadState();
      await expect(popup).toHaveURL(tool.href);
      expect(await popup.evaluate(() => window.opener === null)).toBe(true);
      await popup.close();
      // Complete dismissal before another link reopens the same menu.
      await page.bringToFront();
      await page.keyboard.press("Escape");
      await expect(page.getByTestId("portal-tools-menu")).toBeHidden();
      await expect(page.getByTestId("ppt-generation-menu")).toBeHidden();
      await expect(input).toHaveValue("保留外链点击前的草稿");
      await expect(page).toHaveURL(/\/workspace\/chats\/new$/);
    }
  });
}

for (const { width, height } of [
  { width: 1440, height: 850 },
  { width: 1024, height: 768 },
  { width: 960, height: 540 },
  { width: 768, height: 720 },
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
    // The released rail scrolls horizontally when its seven actions do not
    // fit, including a desktop viewport reduced by the sidebar or zoom.
    // Each control must remain fully reachable, not simultaneously onscreen.
    for (const action of await actions.all()) {
      // Match the rail's start-aligned scroll snap; centering an item can
      // snap to its neighbour and leave the requested control clipped.
      await action.evaluate((element) =>
        element.scrollIntoView({ block: "nearest", inline: "start" }),
      );
      await expect(action).toBeInViewport({ ratio: 1 });
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
      .locator('[data-testid="portal-icon-pptAction"] img')
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
      ).toBe(22);
      await expect(link.locator("img")).toBeVisible();
      await expect(link.locator("img")).toHaveAttribute(
        "src",
        /^data:image\/webp;base64,/,
      );
      await expect(link.locator("svg")).toHaveCount(0);
    }
    const bounds = await menu.boundingBox();
    expect(bounds?.x).toBeGreaterThanOrEqual(0);
    expect((bounds?.x ?? 0) + (bounds?.width ?? 0)).toBeLessThanOrEqual(width);
    expect(bounds?.width).toBeLessThanOrEqual(320);
    // The released More/PPT menus use four stacked touch-sized rows.
    expect(bounds?.height).toBeLessThanOrEqual(320);
    const rows = await menu.getByRole("menuitem").all();
    for (let index = 0; index < rows.length; index++) {
      const row = await rows[index]!.boundingBox();
      expect(row!.height).toBeGreaterThanOrEqual(44);
      if (index > 0) {
        const previous = await rows[index - 1]!.boundingBox();
        expect(row!.y).toBeGreaterThanOrEqual(previous!.y + previous!.height);
        expect(Math.abs(row!.x - previous!.x)).toBeLessThanOrEqual(1);
      }
    }
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
    const pptLink = pptMenu.getByTestId("portal-tool-ppt");
    await pptLink.scrollIntoViewIfNeeded();
    await expect(pptLink).toBeInViewport();
    await expect(pptLink).toHaveAttribute("href", PPT_ASSISTANT_TOOL.href);
    await expect(pptLink).toHaveAttribute("rel", "noopener noreferrer");
    await expect(pptLink.locator("img")).toBeVisible();
    await expect(pptLink.locator("img")).toHaveAttribute(
      "src",
      /^data:image\/webp;base64,/,
    );
    await expect(pptLink.locator("svg")).toHaveCount(0);
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
    // Popup closure and Radix's menu exit animation finish independently.
    // Return focus and establish a closed menu before opening it again.
    await page.bringToFront();
    await page.keyboard.press("Escape");
    await expect(pptMenu).toBeHidden();
    await page.getByTestId("ppt-generation-menu-trigger").click();
    await expect(pptMenu).toBeVisible();
    await pptMenu.getByTestId("ppt-route-generate").click();
    await expect(input).toHaveValue(/^\/ppt-master /);
    await expect(page).toHaveURL(/\/workspace\/chats\/new$/);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    expect(errors).toEqual([]);
    expect(consoleErrors).toEqual([]);
  });
}
