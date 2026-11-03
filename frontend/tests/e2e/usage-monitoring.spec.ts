import { expect, test, type Locator, type Page } from "@playwright/test";

import { modelSquareFixture } from "./utils/model-square-fixture";
import { monitor } from "./utils/monitor-fixture";

test.setTimeout(240_000);
const days = Array.from(
  { length: (Date.UTC(2026, 10, 2) - Date.UTC(2025, 11, 1)) / 86400000 + 1 },
  (_, i) => ({
    date: new Date(Date.UTC(2025, 11, 1 + i)).toISOString().slice(0, 10),
    tokens: i < 280 ? 0 : ((i % 7) + 1) * 345678,
    runs: i < 280 ? 0 : (i % 7) + 1,
  }),
);
const activity = {
  scope: "current_user_recorded_platform_runs",
  timezone: "Asia/Shanghai",
  attribution: "run_start_day",
  total_tokens: 465800241,
  input_tokens: 458621514,
  output_tokens: 7178727,
  peak_daily_tokens: 24200000,
  longest_task_seconds: 79560,
  longest_streak_days: 85,
  current_streak_days: 85,
  recorded_since: days[280]!.date,
  generated_at: "2026-11-02T08:00:00Z",
  days,
};
async function fixtures(page: Page) {
  await modelSquareFixture(page, true);
  await page.route("**/api/runtime-config", (r) =>
    r.fulfill({
      json: { skillHubEnabled: false, weknoraFrontendUrl: null },
    }),
  );
  await page.route("**/api/console/activity", (r) =>
    r.fulfill({ json: activity }),
  );
  await page.route("**/api/admin/model-monitor?*", (r) =>
    r.fulfill({
      json: monitor(
        Number(new URL(r.request().url()).searchParams.get("minutes")),
      ),
    }),
  );
}

async function tooltipAppearance(page: Page) {
  const content = page.locator('[data-slot="tooltip-content"]').last();
  await expect(content).toBeVisible();
  return content.evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      background: style.backgroundColor,
      color: style.color,
      border: style.borderColor,
      radius: style.borderRadius,
      fontSize: style.fontSize,
      padding: style.padding,
    };
  });
}

async function hoverActivityMark(page: Page, mark: Locator) {
  await page.mouse.move(0, 0);
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  await mark.scrollIntoViewIfNeeded();
  // Radix closes tooltips on ancestor scroll. Let the pending scroll event and
  // its layout paint finish before generating the new pointer entry.
  await mark.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  await mark.hover();
}
async function openAccount(page: Page, width: number) {
  await page.goto("/workspace/chats/new", { waitUntil: "domcontentloaded" });
  await expect(
    page.getByTestId("welcome-composer").locator("textarea"),
  ).toBeEditable({ timeout: 60_000 });
  await openSettingsMenu(page, width);
  await page.getByRole("menuitem", { name: "设置", exact: true }).click();
}
async function openSettingsMenu(page: Page, width: number) {
  if (width < 768) {
    // Wait for the real responsive sidebar branch after client hydration.
    await expect(page.locator('[data-slot="sidebar-container"]')).toHaveCount(
      0,
      { timeout: 60_000 },
    );
    await page
      .getByRole("button", { name: /Open navigation menu|打开导航菜单/i })
      .or(page.locator("[data-sidebar='trigger']:visible"))
      .first()
      .click();
    await page
      .locator("[data-mobile='true'][data-sidebar='sidebar']")
      .getByRole("button", { name: /设置/ })
      .click();
  } else
    await page.getByRole("button", { name: "设置和更多", exact: true }).click();
}
async function openDashboard(page: Page, width: number) {
  const settings = page.getByRole("dialog");
  await settings.locator('[data-slot="dialog-close"]').click();
  await expect(settings).toBeHidden();
  await openSettingsMenu(page, width);
  await page.getByRole("menuitem", { name: "看板", exact: true }).click();
  await expect(page).toHaveURL(/\/workspace\/admin\/dashboard/, {
    timeout: 60_000,
  });
}
for (const [width, height] of [
  [1440, 1000],
  [320, 568],
  [375, 812],
  [390, 844],
  [412, 915],
  [740, 360],
]) {
  test(`personal activity and model monitoring fit ${width}x${height}`, async ({
    page,
    baseURL,
  }, info) => {
    await page.setViewportSize({ width: width!, height: height! });
    await page
      .context()
      .addCookies([{ name: "locale", value: "zh-CN", url: baseURL! }]);
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await fixtures(page);
    await openAccount(page, width!);
    const account = page.getByTestId("account-activity");
    await expect(account.getByText("单日 Token 峰值")).toBeVisible();
    await expect(account.getByTestId("activity-day")).toHaveCount(days.length);
    await expect(
      account.getByTestId("activity-months").locator("span"),
    ).toHaveText([
      "12月",
      "1月",
      "2月",
      "3月",
      "4月",
      "5月",
      "6月",
      "7月",
      "8月",
      "9月",
      "10月",
      "11月",
    ]);
    await expect(account.locator('input[type="date"]')).toHaveCount(0);
    await hoverActivityMark(page, account.getByTestId("activity-day").last());
    await expect(page.getByRole("tooltip")).toContainText("2026-11-02");
    await expect(page.getByRole("tooltip")).toContainText(
      days.at(-1)!.tokens.toLocaleString("zh-CN"),
    );
    await expect(page.getByRole("tooltip")).toContainText("所在周合计");
    const dailyAppearance = await tooltipAppearance(page);
    await page.screenshot({
      path: info.outputPath("account-daily-tooltip.png"),
      animations: "disabled",
    });
    await account.getByTestId("activity-day").last().click();
    await expect(account.getByRole("status")).toContainText("2026-11-02");
    await account.getByRole("radio", { name: "每周", exact: true }).click();
    await expect(account.getByTestId("activity-day")).toHaveCount(0);
    const lastWeek = account.getByTestId("activity-week").last();
    await expect(lastWeek.locator("span")).toHaveCount(7);
    await expect(account.getByTestId("activity-heatmap")).toHaveAttribute(
      "data-mode",
      "weekly",
    );
    await hoverActivityMark(page, lastWeek);
    await expect(page.getByRole("tooltip")).toContainText("2026年11月2日");
    await expect(page.getByRole("tooltip")).toContainText(
      days.at(-1)!.tokens.toLocaleString("zh-CN"),
    );
    expect(await tooltipAppearance(page)).toEqual(dailyAppearance);
    await page.screenshot({
      path: info.outputPath("account-weekly-heatmap.png"),
      animations: "disabled",
    });
    await account.getByRole("radio", { name: "累计总量", exact: true }).click();
    await expect(account.getByTestId("activity-heatmap")).toHaveAttribute(
      "data-mode",
      "cumulative",
    );
    await expect(
      account.getByTestId("activity-week").last().locator("span"),
    ).toHaveCount(7);
    await expect(
      account
        .getByTestId("activity-week")
        .last()
        .locator('[data-filled="true"]'),
    ).toHaveCount(7);
    await hoverActivityMark(page, account.getByTestId("activity-week").last());
    await expect(page.getByRole("tooltip")).toContainText("累计至");
    await expect(page.getByRole("tooltip")).toContainText(
      activity.total_tokens.toLocaleString("zh-CN"),
    );
    expect(await tooltipAppearance(page)).toEqual(dailyAppearance);
    await page.screenshot({
      path: info.outputPath("account-cumulative-tooltip.png"),
      animations: "disabled",
    });
    await account.getByTestId("activity-week").last().click();
    await expect(account.getByRole("status")).toContainText(
      activity.total_tokens.toLocaleString("zh-CN"),
    );
    await page.screenshot({
      path: info.outputPath("account-cumulative-heatmap.png"),
      animations: "disabled",
    });
    await account.getByRole("radio", { name: "每天", exact: true }).click();
    await account.getByTestId("activity-chart-scroll").evaluate((el) => {
      el.scrollLeft = 0;
    });
    await account.scrollIntoViewIfNeeded();
    const viewport = page
      .getByTestId("settings-dialog")
      .locator("[data-radix-scroll-area-viewport]");
    expect(
      await viewport.evaluate((el) => el.scrollWidth <= el.clientWidth + 1),
    ).toBe(true);
    await page.screenshot({ path: info.outputPath("account-activity.png") });
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width! + 1);
    await openDashboard(page, width!);
    await page.getByRole("tab", { name: "模型监控", exact: true }).click();
    const panel = page.getByTestId("model-monitor");
    await expect(
      panel.getByText("模型调用监控", { exact: true }),
    ).toBeVisible();
    await expect(
      panel
        .getByRole("table")
        .getByText("DeepSeek-V4.1-Flash · 调用", { exact: true }),
    ).toBeVisible();
    await panel.getByRole("combobox", { name: "统计窗口" }).click();
    await page.getByRole("option", { name: "最近 1 小时" }).click();
    await expect(
      panel.getByRole("combobox", { name: "统计窗口" }),
    ).toContainText("最近 1 小时");
    await panel.scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath("model-monitor.png") });
    await expect(panel.getByRole("table").getByText(/OA 文生图/)).toHaveCount(
      0,
    );
    const oa = page.getByTestId("oa-policy-flow-monitor");
    await page.getByRole("tab", { name: "OA 接口", exact: true }).click();
    await expect(
      oa.getByRole("table").getByText("OA 文生图 · 提交", { exact: true }),
    ).toBeVisible();
    await expect(oa.getByRole("table").locator("tbody tr")).toHaveCount(3);
    await expect(oa).toContainText("不代表生成完成");
    await expect(
      oa.getByRole("region", { name: "后台任务执行" }),
    ).toContainText("最近观测执行中");
    const jobs = oa.getByRole("region", { name: "后台任务执行" });
    await expect(jobs.getByText("最近观测执行中").locator("..")).toContainText(
      "7",
    );
    await expect(
      jobs.getByText("生成成功", { exact: true }).locator(".."),
    ).toContainText("5");
    await oa.scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath("oa-monitor.png") });
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width! + 1);
    expect(errors).toEqual([]);
  });
}

test("monitor outage and usage outage preserve existing panels and totals", async ({
  page,
  baseURL,
}) => {
  await page
    .context()
    .addCookies([{ name: "locale", value: "zh-CN", url: baseURL! }]);
  await fixtures(page);
  let reads = 0;
  await page.route("**/api/console/activity", (r) =>
    r.fulfill({ status: 503, json: { detail: "unavailable" } }),
  );
  await page.route("**/api/admin/model-monitor?*", (r) => {
    reads++;
    return r.fulfill({
      json: { ...monitor(), status: "unavailable", series: [], trend: [] },
    });
  });
  await openAccount(page, 1440);
  await expect(
    page.getByTestId("account-activity").getByRole("status"),
  ).toContainText("使用记录暂时不可用");
  await expect(
    page.getByRole("dialog").getByTestId("account-token-usage"),
  ).toContainText("128.65万");
  await openDashboard(page, 1440);
  await page.getByRole("tab", { name: "模型监控", exact: true }).click();
  await expect(
    page.getByTestId("model-monitor").getByRole("status"),
  ).toContainText("模型监控暂时不可用");
  await page.getByRole("tab", { name: "用户与员工", exact: true }).click();
  await expect(
    page
      .getByTestId("user-run-ranking")
      .getByText("合成用户1", { exact: true }),
  ).toBeVisible();
  await page
    .getByTestId("user-run-ranking")
    .getByRole("button", { name: "下一页", exact: true })
    .click();
  await expect(
    page
      .getByTestId("user-run-ranking")
      .getByText("合成用户11", { exact: true }),
  ).toBeVisible();
  expect(reads).toBeLessThanOrEqual(2);
});
