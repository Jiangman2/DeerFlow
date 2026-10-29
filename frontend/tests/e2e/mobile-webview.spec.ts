import { expect, test, type Page } from "@playwright/test";

import { MOCK_THREAD_ID, mockLangGraphAPI } from "./utils/mock-api";

const PHONE_VIEWPORTS = [
  { name: "compact-android", width: 320, height: 568 },
  { name: "iphone-standard", width: 375, height: 812 },
  { name: "iphone-modern", width: 390, height: 844 },
  { name: "large-android", width: 412, height: 915 },
] as const;

async function expectNoPageOverflow(page: Page, width: number) {
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
  expect(await page.evaluate(() => window.innerWidth)).toBe(width);
}

function mobileNavigationTrigger(page: Page) {
  return page
    .getByRole("button", { name: /Open navigation menu|打开导航菜单/i })
    .or(page.locator("[data-sidebar='trigger']:visible"))
    .first();
}

test.describe("mobile WebView workspace", () => {
  test.describe.configure({ mode: "serial", timeout: 60_000 });

  test("workspace remains light and legible when the device prefers dark mode", async ({
    page,
  }) => {
    await page.emulateMedia({ colorScheme: "dark" });
    await page.setViewportSize({ width: 390, height: 844 });
    mockLangGraphAPI(page);

    await page.goto("/workspace/chats/new", {
      waitUntil: "domcontentloaded",
    });
    await expect(page.getByRole("textbox").first()).toBeVisible({
      timeout: 15_000,
    });
    await expect
      .poll(() =>
        page.evaluate(() => ({
          htmlLight: document.documentElement.classList.contains("light"),
          htmlDark: document.documentElement.classList.contains("dark"),
          colorScheme: getComputedStyle(
            document.querySelector(".cnooc-workspace-shell")!,
          ).colorScheme,
        })),
      )
      .toEqual({ htmlLight: true, htmlDark: false, colorScheme: "light" });

    await mobileNavigationTrigger(page).click();
    const drawer = page.locator("[data-mobile='true'][data-sidebar='sidebar']");
    await expect(drawer).toBeVisible();
    await expect
      .poll(() =>
        drawer.evaluate((element) => getComputedStyle(element).backgroundColor),
      )
      .not.toBe("rgb(0, 0, 0)");
    await page.screenshot({
      path: "test-results/workspace-dark-system-mobile.png",
      fullPage: true,
    });

    await page.setViewportSize({ width: 1440, height: 900 });
    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(page.locator(".cnooc-desktop-welcome-copy")).toBeVisible();
    await expect
      .poll(() =>
        page.evaluate(() => ({
          htmlLight: document.documentElement.classList.contains("light"),
          htmlDark: document.documentElement.classList.contains("dark"),
        })),
      )
      .toEqual({ htmlLight: true, htmlDark: false });
    await page.screenshot({
      path: "test-results/workspace-dark-system-desktop.png",
      fullPage: true,
    });
  });

  for (const viewport of PHONE_VIEWPORTS) {
    test(`${viewport.name} keeps the primary chat flow inside the viewport`, async ({
      page,
    }) => {
      await page.setViewportSize(viewport);
      mockLangGraphAPI(page);

      await page.goto("/workspace/chats/new", {
        waitUntil: "domcontentloaded",
      });

      await expect(page.getByRole("textbox").first()).toBeVisible({
        timeout: 15_000,
      });
      await expect(mobileNavigationTrigger(page)).toBeVisible();
      await expectNoPageOverflow(page, viewport.width);

      const shell = page.locator("[data-slot='sidebar-wrapper']");
      const shellHeight = await shell.evaluate(
        (element) => element.getBoundingClientRect().height,
      );
      expect(shellHeight).toBeLessThanOrEqual(viewport.height + 1);

      const [welcomeCardBox, composerDockBox] = await Promise.all([
        page.locator(".cnooc-mobile-welcome-card").boundingBox(),
        page.locator(".cnooc-composer-dock-welcome").boundingBox(),
      ]);
      expect(welcomeCardBox).not.toBeNull();
      expect(composerDockBox).not.toBeNull();
      expect(welcomeCardBox!.y + welcomeCardBox!.height).toBeLessThanOrEqual(
        composerDockBox!.y + 1,
      );
    });
  }

  test("mobile viewport metadata and composer prevent focus zoom", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    mockLangGraphAPI(page);
    await page.goto("/workspace/chats/new", { waitUntil: "domcontentloaded" });

    const viewportMeta = page.locator('meta[name="viewport"]');
    await expect(viewportMeta).toHaveAttribute(
      "content",
      /width=device-width.*initial-scale=1.*interactive-widget=resizes-content/,
    );

    const textbox = page.getByRole("textbox").first();
    await expect(textbox).toBeEditable({ timeout: 15_000 });
    await expect
      .poll(() =>
        textbox.evaluate((element) =>
          Number.parseFloat(getComputedStyle(element).fontSize),
        ),
      )
      .toBeGreaterThanOrEqual(16);
  });

  test("software keyboard reflows the welcome surface and sending releases focus", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    mockLangGraphAPI(page);
    await page.goto("/workspace/chats/new", { waitUntil: "domcontentloaded" });

    const shell = page.locator(".cnooc-workspace-shell");
    const textbox = page.getByRole("textbox").first();
    const welcomeStage = page.locator(".cnooc-welcome-stage");
    const actions = page.getByTestId("welcome-suggestion-rail");
    const composer = page.getByTestId("welcome-composer");

    await expect(textbox).toBeEditable({ timeout: 15_000 });
    await textbox.focus();
    await page.setViewportSize({ width: 390, height: 520 });

    await expect(shell).toHaveAttribute("data-mobile-keyboard-open", "true");
    await expect(welcomeStage).toBeHidden();
    await expect(actions).toBeHidden();
    await expect
      .poll(() =>
        shell.evaluate((element) => element.getBoundingClientRect().height),
      )
      .toBeLessThanOrEqual(521);

    const composerBox = await composer.boundingBox();
    expect(composerBox).not.toBeNull();
    expect(composerBox!.y + composerBox!.height).toBeLessThanOrEqual(521);

    await textbox.fill("移动端键盘恢复测试");
    await textbox.press("Enter");
    await expect
      .poll(() =>
        page.evaluate(
          () =>
            document.activeElement?.matches(
              '.cnooc-composer textarea, .cnooc-composer input, .cnooc-composer [contenteditable="true"]',
            ) ?? false,
        ),
      )
      .toBe(false);

    await page.setViewportSize({ width: 390, height: 844 });
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
    await expectNoPageOverflow(page, 390);
  });

  test("phone welcome keeps the composer compact and actions on horizontal rails", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 320, height: 568 });
    mockLangGraphAPI(page);
    await page.route("**/api/models", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          models: [
            {
              id: "deepseek-v4-flash",
              name: "deepseek-v4-flash",
              model: "DeepSeek-V4-Flash",
              display_name: "DeepSeek-V4-Flash",
              supports_thinking: true,
              supports_reasoning_effort: true,
            },
          ],
          token_usage: { enabled: false },
        }),
      }),
    );
    await page.goto("/workspace/chats/new", { waitUntil: "domcontentloaded" });

    const composer = page.getByTestId("welcome-composer");
    const welcome = page.getByTestId("welcome-panel");
    const tools = page.getByTestId("composer-primary-tools");
    const suggestions = page.getByTestId("welcome-suggestion-rail");
    const assistantAvatar = page.getByTestId("cnooc-assistant-avatar");
    const modeSelector = page.getByTestId("mobile-mode-selector");
    const suggestionViewport = suggestions.locator(
      "[data-slot='scroll-area-viewport']",
    );

    await expect(composer).toBeVisible({ timeout: 15_000 });
    await expect(assistantAvatar).toBeVisible();
    await expect(modeSelector).toBeVisible();
    await expect(
      welcome.getByRole("heading", {
        name: /你好，我是您的 海油智能助手|Hello, I am your intelligent offshore assistant/i,
      }),
    ).toBeVisible();
    await expect(
      welcome.getByText(
        /通过内置和自定义 Skills|With built-in and custom skills/,
      ),
    ).toBeHidden();

    const [composerBox, toolMetrics, suggestionMetrics] = await Promise.all([
      composer.locator(".cnooc-welcome-composer").boundingBox(),
      tools.evaluate((element) => ({
        clientWidth: element.clientWidth,
        scrollWidth: element.scrollWidth,
        flexWrap: getComputedStyle(element).flexWrap,
        left: element.getBoundingClientRect().left,
        right: element.getBoundingClientRect().right,
        buttons: [...element.querySelectorAll("button")].map((button) => {
          const box = button.getBoundingClientRect();
          return {
            left: box.left,
            right: box.right,
            top: box.top,
            height: box.height,
          };
        }),
      })),
      suggestionViewport.evaluate((element) => ({
        clientWidth: element.clientWidth,
        scrollWidth: element.scrollWidth,
      })),
    ]);

    expect(composerBox).not.toBeNull();
    expect(composerBox!.height).toBeLessThan(190);
    expect(toolMetrics.flexWrap).toBe("nowrap");
    // The refined three-button row fits this viewport; do not require overflow.
    expect(toolMetrics.scrollWidth).toBeLessThanOrEqual(
      toolMetrics.clientWidth + 1,
    );
    expect(toolMetrics.buttons).toHaveLength(3);
    for (const button of toolMetrics.buttons) {
      expect(button.left).toBeGreaterThanOrEqual(toolMetrics.left - 1);
      expect(button.right).toBeLessThanOrEqual(toolMetrics.right + 1);
      expect(button.height).toBeGreaterThanOrEqual(32);
      expect(
        Math.abs(button.top - toolMetrics.buttons[0]!.top),
      ).toBeLessThanOrEqual(1);
    }
    expect(suggestionMetrics.scrollWidth).toBeGreaterThan(
      suggestionMetrics.clientWidth,
    );

    await modeSelector.click();
    const flashMode = page.getByRole("menuitem", {
      name: /闪速|Flash/i,
    });
    await expect(flashMode).toBeVisible();
    await flashMode.click();
    await expect(modeSelector).toContainText(/闪速|Flash/i);
    await expectNoPageOverflow(page, 320);
  });

  test("mobile drawer uses a wide touch-friendly navigation surface", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    mockLangGraphAPI(page);
    await page.goto("/workspace/chats/new", { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("textbox").first()).toBeEditable({
      timeout: 15_000,
    });
    await mobileNavigationTrigger(page).click();

    const sidebar = page.locator(
      "[data-mobile='true'][data-sidebar='sidebar']",
    );
    await expect(sidebar).toBeVisible();
    const box = await sidebar.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.width).toBeGreaterThanOrEqual(319);
    expect(box!.width).toBeLessThanOrEqual(344);

    const navTargets = sidebar.locator("[data-sidebar='menu-button']:visible");
    expect(await navTargets.count()).toBeGreaterThanOrEqual(4);
    const targetHeights = await navTargets.evaluateAll((elements) =>
      elements.map((element) => element.getBoundingClientRect().height),
    );
    expect(Math.min(...targetHeights)).toBeGreaterThanOrEqual(43);
    await expectNoPageOverflow(page, 390);
  });

  test("mobile navigation closes after changing workspace routes", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    mockLangGraphAPI(page, { agents: [] });
    await page.goto("/workspace/chats/new", { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("textbox").first()).toBeEditable({
      timeout: 15_000,
    });

    const trigger = mobileNavigationTrigger(page);
    await trigger.click();
    const mobileSidebar = page.locator(
      "[data-mobile='true'][data-sidebar='sidebar']",
    );
    await expect(mobileSidebar).toBeVisible();
    await mobileSidebar.locator("a[href='/workspace/agents']").click();

    await page.waitForURL(/\/workspace\/agents$/, { timeout: 30_000 });
    await expect(mobileSidebar).toBeHidden();
    await expectNoPageOverflow(page, 390);
  });

  test("settings becomes a scrollable full-screen mobile surface", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    mockLangGraphAPI(page);
    await page.goto("/workspace/chats/new", { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("textbox").first()).toBeEditable({
      timeout: 15_000,
    });

    await mobileNavigationTrigger(page).click();
    const mobileSidebar = page.locator(
      "[data-mobile='true'][data-sidebar='sidebar']",
    );
    const settingsMenu = mobileSidebar.getByRole("button", {
      name: /Settings|设置/,
    });
    await settingsMenu.click();
    await page.getByRole("menuitem", { name: /Settings|设置/ }).click();

    const dialog = page.getByTestId("settings-dialog");
    await expect(dialog).toBeVisible();
    await expect.poll(() => dialog.boundingBox()).not.toBeNull();
    const box = await dialog.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(-1);
    expect(box!.width).toBeLessThanOrEqual(376);
    expect(box!.height).toBeLessThanOrEqual(813);
    await expect(dialog.locator("nav")).toBeVisible();
    await expectNoPageOverflow(page, 375);
  });

  test("desktop settings keeps a safe inset and separates its inner frame", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1744, height: 842 });
    mockLangGraphAPI(page);
    await page.goto("/workspace/chats/new", { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("textbox").first()).toBeEditable({
      timeout: 15_000,
    });

    await page
      .getByRole("button", { name: /Settings and more|设置和更多/ })
      .click();
    await page.getByRole("menuitem", { name: /^Settings$|^设置$/ }).click();

    const dialog = page.getByTestId("settings-dialog");
    const frame = page.getByTestId("settings-dialog-frame");
    await expect(dialog).toBeVisible();

    // Measure after the dialog's zoom transition, not different animation
    // frames in parallel bounding-box calls.
    await dialog.evaluate(async (element) => {
      await Promise.all(
        element.getAnimations().map((animation) => animation.finished),
      );
    });
    const [dialogBox, frameBox, headerBox, navigationBox, contentBox] =
      await Promise.all([
        dialog.boundingBox(),
        frame.boundingBox(),
        frame.locator("[data-slot='dialog-header']").boundingBox(),
        frame.locator("nav").boundingBox(),
        frame.locator("[data-slot='scroll-area']").boundingBox(),
      ]);

    expect(dialogBox).not.toBeNull();
    expect(frameBox).not.toBeNull();
    expect(headerBox).not.toBeNull();
    expect(navigationBox).not.toBeNull();
    expect(contentBox).not.toBeNull();
    expect(dialogBox!.x).toBeGreaterThanOrEqual(15);
    expect(dialogBox!.y).toBeGreaterThanOrEqual(15);
    expect(dialogBox!.width).toBeLessThanOrEqual(1153);
    expect(dialogBox!.height).toBeLessThanOrEqual(811);
    expect(frameBox!.x - dialogBox!.x).toBeGreaterThanOrEqual(0);
    expect(frameBox!.y - dialogBox!.y).toBeGreaterThanOrEqual(0);
    expect(headerBox!.x - dialogBox!.x).toBeGreaterThanOrEqual(23);
    expect(headerBox!.y - dialogBox!.y).toBeGreaterThanOrEqual(23);
    expect(navigationBox!.x - dialogBox!.x).toBeGreaterThanOrEqual(23);
    expect(navigationBox!.y).toBeGreaterThanOrEqual(
      headerBox!.y + headerBox!.height + 11,
    );
    expect(contentBox!.x).toBeGreaterThanOrEqual(
      navigationBox!.x + navigationBox!.width + 15,
    );
  });

  test("knowledge workspace keeps a DeerFlow navigation escape hatch", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 320, height: 568 });
    mockLangGraphAPI(page);
    await page.route("**/platform/knowledge-bases", (route) =>
      route.fulfill({
        status: 200,
        contentType: "text/html",
        body: "<!doctype html><meta name='viewport' content='width=device-width'><h1>Knowledge</h1>",
      }),
    );

    await page.goto("/workspace/knowledge", { waitUntil: "domcontentloaded" });
    const frame = page.getByTestId("weknora-workspace-frame");
    await expect(frame).toBeVisible({ timeout: 15_000 });
    const trigger = page.getByRole("button", {
      name: /Platform menu|平台导航/,
    });
    await expect(trigger).toHaveCount(1);
    await trigger.click();
    await expect(
      page.locator("[data-mobile='true'][data-sidebar='sidebar']"),
    ).toBeVisible();
    await expectNoPageOverflow(page, 320);
  });

  test("artifact preview uses the complete mobile viewport", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Mobile artifact",
          artifacts: ["reports/mobile.md"],
        },
      ],
    });

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`, {
      waitUntil: "domcontentloaded",
    });
    await page.getByTestId("chat-more-trigger").click();
    await page.getByTestId("artifact-trigger").click();
    const sheet = page.locator("[data-slot='sheet-content']");
    await expect(sheet).toBeVisible();
    const sheetBox = await sheet.boundingBox();
    expect(sheetBox).not.toBeNull();
    expect(sheetBox!.width).toBeLessThanOrEqual(391);
    expect(sheetBox!.height).toBeLessThanOrEqual(845);
    await expect(sheet.getByText("mobile.md")).toBeVisible();
    await expectNoPageOverflow(page, 390);
  });

  test("short landscape WebView keeps the composer reachable", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 844, height: 390 });
    mockLangGraphAPI(page);
    await page.goto("/workspace/chats/new", { waitUntil: "domcontentloaded" });

    await expect(page.getByRole("textbox").first()).toBeVisible({
      timeout: 15_000,
    });
    await expectNoPageOverflow(page, 844);
  });

  test("compact WebView keeps chat history, agents, and agent creation usable", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 320, height: 568 });
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    mockLangGraphAPI(page, {
      agents: [
        {
          name: "mobile-agent",
          description: "Mobile WebView regression agent",
        },
      ],
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Mobile history item",
          updated_at: "2026-08-06T00:00:00Z",
        },
      ],
    });

    await page.goto("/workspace/chats", { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("searchbox")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("Mobile history item")).toBeVisible({
      timeout: 15_000,
    });
    await expectNoPageOverflow(page, 320);

    await page.goto("/workspace/agents", { waitUntil: "domcontentloaded" });
    await page
      .getByRole("tab", { name: "My digital employees", exact: true })
      .click();
    await expect(page.getByText("mobile-agent")).toBeVisible({
      timeout: 15_000,
    });
    await expectNoPageOverflow(page, 320);

    await page.goto("/workspace/agents/new", {
      waitUntil: "domcontentloaded",
    });
    await expect(page.locator("main input").first()).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.locator("main button").last()).toBeVisible();
    await expectNoPageOverflow(page, 320);
    expect(pageErrors).toEqual([]);
  });

  test("scheduled task list and create form remain reachable on a phone", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    mockLangGraphAPI(page, {
      scheduledTasks: [
        {
          id: "mobile-task",
          thread_id: null,
          title: "Mobile scheduled task",
          prompt: "Create a mobile report",
          schedule_type: "cron",
          schedule_spec: { cron: "0 9 * * *" },
          timezone: "Asia/Shanghai",
          status: "enabled",
          next_run_at: "2026-08-07T01:00:00Z",
          last_run_at: null,
          last_run_id: null,
          last_error: null,
          run_count: 0,
          created_at: "2026-08-06T00:00:00Z",
          updated_at: "2026-08-06T00:00:00Z",
        },
      ],
    });

    await page.goto("/workspace/scheduled-tasks", {
      waitUntil: "domcontentloaded",
    });
    await expect(
      page.getByRole("button", { name: /Mobile scheduled task/i }),
    ).toBeVisible({ timeout: 15_000 });
    await expectNoPageOverflow(page, 375);

    await page
      .locator("header")
      .getByRole("button", { name: /Create scheduled task/i })
      .click();
    await expect(page.getByTestId("scheduled-task-create-form")).toBeVisible();
    await expect(
      page.getByTestId("scheduled-task-create-actions"),
    ).toBeVisible();
    await expectNoPageOverflow(page, 375);
  });
});
