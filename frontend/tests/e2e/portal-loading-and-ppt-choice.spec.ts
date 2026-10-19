import { expect, test } from "@playwright/test";

import {
  PORTAL_MORE_TOOLS,
  PORTAL_QUICK_TOOLS,
} from "../../src/core/portal-links";

import { handleRunStream, mockLangGraphAPI } from "./utils/mock-api";

test.setTimeout(90_000);
test.beforeEach(async ({ page }, info) => {
  mockLangGraphAPI(page);
  // This suite has no live PPT runner; post-submission SDK history stays local.
  await page.route("**/api/ppt-master-runtime/threads/*/history", (r) =>
    r.fulfill({ json: [] }),
  );
  await page.route("**/api/announcements/active", (r) =>
    r.fulfill({ json: { announcement: null } }),
  );
  await page.route("**/integrations/haineng/model-call-count", (r) =>
    r.fulfill({ json: { todayModelCallCount: 0, totalModelCallCount: 0 } }),
  );
  await page.route("**/api/home/statistics", (r) =>
    r.fulfill({ json: { todayUsers: 0, todayTasks: 0 } }),
  );
  await page.route("**/api/features", (r) =>
    r.fulfill({
      json: {
        agents_api: { enabled: true },
        browser_control: { enabled: false },
        ppt_confirmation: { enabled: true, protocol: "ppt-confirmation-v1" },
      },
    }),
  );
  await page
    .context()
    .addCookies([
      { name: "locale", value: "zh-CN", url: String(info.project.use.baseURL) },
    ]);
});

for (const { width, height } of [
  { width: 1440, height: 900 },
  { width: 320, height: 720 },
  { width: 375, height: 812 },
  { width: 390, height: 844 },
  { width: 412, height: 915 },
  { width: 844, height: 390 },
]) {
  test(`navigation icons survive unavailable PNGs and cold load at ${width}x${height}`, async ({
    page,
  }, info) => {
    const errors: string[] = [];
    const imageRequests: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (m) => {
      if (m.type() === "error") errors.push(m.text());
    });
    await page.route(
      /(?:\/images\/portal[^?]*|\/_next\/image\?.*portal)/,
      (r) => {
        imageRequests.push(r.request().url());
        return r.abort();
      },
    );
    await page.setViewportSize({ width, height });
    await page.goto("/workspace/chats/new");
    await expect(page.locator('textarea[name="message"]')).toBeEditable();
    for (const t of PORTAL_QUICK_TOOLS) {
      const link = page.getByTestId(`portal-tool-${t.id}`);
      await link.scrollIntoViewIfNeeded();
      await expect(link.getByTestId(`portal-icon-${t.id}`)).toBeVisible();
      await expect(link.locator("img")).toHaveCount(1);
      await expect(link.locator("svg")).toHaveCount(0);
      await expect(link.locator("img")).toHaveAttribute(
        "src",
        /^data:image\/webp;base64,/,
      );
      await expect
        .poll(() =>
          link
            .locator("img")
            .evaluate(
              (img: HTMLImageElement) =>
                img.complete && img.naturalWidth === 144,
            ),
        )
        .toBe(true);
      await expect(
        link.getByTestId("external-navigation-marker"),
      ).toBeVisible();
      await expect(link).toHaveAttribute("target", "_blank");
    }
    await page.getByTestId("portal-tools-trigger").click();
    for (const t of PORTAL_MORE_TOOLS) {
      const link = page.getByTestId(`portal-tool-${t.id}`);
      await expect(link.getByTestId(`portal-icon-${t.id}`)).toBeVisible();
      await expect(link.locator("img")).toHaveCount(1);
      await expect(link.locator("svg")).toHaveCount(0);
      await expect(link.locator("img")).toHaveAttribute(
        "src",
        /^data:image\/webp;base64,/,
      );
      await expect
        .poll(() =>
          link
            .locator("img")
            .evaluate(
              (img: HTMLImageElement) =>
                img.complete && img.naturalWidth === 144,
            ),
        )
        .toBe(true);
    }
    expect(imageRequests).toEqual([]);
    expect(errors).toEqual([]);
    await expect(page).toHaveURL(/\/workspace\/chats\/new$/);
    await expect(page).not.toHaveTitle("");
    await page.screenshot({
      path: info.outputPath(`generated-icons-${width}.png`),
    });
  });

  test(`PPT choice appears in conversation only after send at ${width}x${height}`, async ({
    page,
  }, info) => {
    const runs: unknown[] = [];
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.route(/\/runs\/stream$/, (r) => {
      runs.push(r.request().postDataJSON());
      return handleRunStream(r);
    });
    await page.setViewportSize({ width, height });
    await page.goto("/workspace/chats/new");
    const input = page.locator('textarea[name="message"]');
    const featureReady = page.waitForResponse((r) =>
      r.url().endsWith("/api/features"),
    );
    await input.fill("/ppt-master 制作一份年度汇报演示文稿");
    await expect(page.getByTestId("ppt-confirmation-opt-in")).toHaveCount(0);
    await expect(page.getByTestId("ppt-submission-choice")).toHaveCount(0);
    // The feature query must settle before this initial send is eligible.
    await featureReady;
    await input.press("Enter");
    const card = page.getByTestId("ppt-submission-choice");
    await expect(card).toBeVisible();
    await expect(page.getByTestId("welcome-composer")).toBeHidden();
    expect(runs).toHaveLength(0);
    expect(await card.evaluate((e) => e.closest("form") === null)).toBe(true);
    await expect(card.getByRole("radio", { name: /^人工确认/ })).toBeChecked();
    await card.getByRole("button", { name: "返回修改" }).click();
    await expect(card).toHaveCount(0);
    await expect(input).toHaveValue("/ppt-master 制作一份年度汇报演示文稿");
    expect(runs).toHaveLength(0);
    await input.press("Enter");
    await expect(card).toBeVisible();
    const box = await card.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.y).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(width);
    expect(box!.y + box!.height).toBeLessThanOrEqual(height);
    await page.screenshot({ path: info.outputPath(`ppt-choice-${width}.png`) });
    const automatic = width === 1440 || width === 390;
    if (automatic) await card.getByRole("radio", { name: /^60 秒/ }).check();
    await card.getByRole("button", { name: "开始任务" }).click();
    await expect.poll(() => runs.length).toBe(1);
    const payload = JSON.stringify(runs[0]);
    expect(payload.includes('"ppt_confirmation_mode":"auto_after_60s"')).toBe(
      automatic,
    );
    expect(errors).toEqual([]);
  });
}

test("manual choice remains available when server automatic confirmation is disabled", async ({
  page,
}) => {
  await page.route("**/api/features", (r) =>
    r.fulfill({
      json: {
        agents_api: { enabled: true },
        browser_control: { enabled: false },
        ppt_confirmation: { enabled: false, protocol: "ppt-confirmation-v1" },
      },
    }),
  );
  await page.goto("/workspace/chats/new", { waitUntil: "domcontentloaded" });
  const input = page.locator('textarea[name="message"]');
  await input.fill("/ppt-master 人工确认测试");
  await input.press("Enter");
  const card = page.getByTestId("ppt-submission-choice");
  await expect(card).toBeVisible();
  await expect(card.getByRole("radio", { name: /^60 秒/ })).toBeDisabled();
  await expect(card.getByRole("radio", { name: /^人工确认/ })).toBeChecked();
  await expect(card.getByRole("button", { name: "开始任务" })).toBeEnabled();
});

test("returning to edit preserves the original file and sends no upload or run", async ({
  page,
}) => {
  const writes: string[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && /\/runs\/stream$|\/uploads/.test(r.url()))
      writes.push(r.url());
  });
  await page.goto("/workspace/chats/new", { waitUntil: "domcontentloaded" });
  const input = page.locator('textarea[name="message"]');
  await input.fill("/ppt-master 使用这份备注");
  const composer = page.getByTestId("welcome-composer");
  await composer.locator('input[type="file"]').setInputFiles({
    name: "notes.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("offline fixture"),
  });
  await expect(composer.getByText("notes.txt", { exact: true })).toBeVisible();
  await input.press("Enter");
  const card = page.getByTestId("ppt-submission-choice");
  await expect(card).toBeVisible();
  await expect(card).toContainText("已附文件: 1");
  await card.getByRole("button", { name: "返回修改" }).click();
  await expect(input).toHaveValue("/ppt-master 使用这份备注");
  await expect(composer.getByText("notes.txt", { exact: true })).toBeVisible();
  expect(writes).toEqual([]);
});
