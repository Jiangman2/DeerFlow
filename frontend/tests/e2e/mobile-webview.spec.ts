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

test.describe("mobile WebView workspace", () => {
  test.describe.configure({ mode: "serial", timeout: 60_000 });

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
      await expect(
        page.locator("[data-sidebar='trigger']:visible"),
      ).toBeVisible();
      await expectNoPageOverflow(page, viewport.width);

      const shell = page.locator("[data-slot='sidebar-wrapper']");
      const shellHeight = await shell.evaluate(
        (element) => element.getBoundingClientRect().height,
      );
      expect(shellHeight).toBeLessThanOrEqual(viewport.height + 1);
    });
  }

  test("mobile navigation closes after changing workspace routes", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    mockLangGraphAPI(page, { agents: [] });
    await page.goto("/workspace/chats/new", { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("textbox").first()).toBeEditable({
      timeout: 15_000,
    });

    const trigger = page.locator("[data-sidebar='trigger']:visible");
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

    await page.locator("[data-sidebar='trigger']:visible").click();
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
    const box = await dialog.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(-1);
    expect(box!.width).toBeLessThanOrEqual(376);
    expect(box!.height).toBeLessThanOrEqual(813);
    await expect(dialog.locator("nav")).toBeVisible();
    await expectNoPageOverflow(page, 375);
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
    const trigger = page.locator("[data-sidebar='trigger']:visible");
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
    await expect(page.getByText("Mobile history item")).toBeVisible();
    await expectNoPageOverflow(page, 320);

    await page.goto("/workspace/agents", { waitUntil: "domcontentloaded" });
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
