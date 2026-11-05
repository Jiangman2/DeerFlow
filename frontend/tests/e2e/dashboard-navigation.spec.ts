import { expect, test, type Page } from "@playwright/test";

import { modelSquareFixture } from "./utils/model-square-fixture";
import { monitor } from "./utils/monitor-fixture";

test.setTimeout(120_000);
const labels = [
  "总览",
  "运行诊断",
  "用户与员工",
  "知识库",
  "模型监控",
  "OA 接口",
];
async function fixture(page: Page) {
  await modelSquareFixture(page, true);
  await page.route("**/api/runtime-config", (r) =>
    r.fulfill({ json: { skillHubEnabled: false, weknoraFrontendUrl: null } }),
  );
  await page.route("**/api/admin/dashboard/date-bounds", (r) =>
    r.fulfill({
      json: {
        earliest_at: "2026-09-01T00:00:00Z",
        selectable_start_at: "2026-08-31T16:00:00Z",
        timezone: "Asia/Shanghai",
      },
    }),
  );
  await page.route("**/api/admin/model-monitor?*", (r) =>
    r.fulfill({
      json: monitor(
        Number(new URL(r.request().url()).searchParams.get("minutes")),
      ),
    }),
  );
  await page.route("**/api/admin/dashboard/failures?*", (r) =>
    r.fulfill({
      json: {
        total: 10,
        items: Array.from({ length: 5 }, (_, i) => ({
          run_id: `synthetic-${Number(new URL(r.request().url()).searchParams.get("offset")) + i}`,
          thread_id: "synthetic-thread",
          user_id: "synthetic-user",
          email: "qa@example.test",
          display_name: "合成用户",
          status: "error",
          error: "合成错误摘要",
          assistant_id: "lead_agent",
          model_name: "DeepSeek-V4.1-Flash",
          started_at: "2026-11-03T08:00:00Z",
          updated_at: "2026-11-03T08:01:00Z",
          duration_ms: 60000,
          disposition_status: null,
          diagnostics_url: "",
          can_open: false,
          chat_url: null,
        })),
        limit: 5,
        offset: Number(new URL(r.request().url()).searchParams.get("offset")),
      },
    }),
  );
}

for (const [width, height] of [
  [1440, 1000],
  [320, 568],
  [375, 812],
  [390, 844],
  [412, 915],
  [844, 390],
]) {
  test(`dashboard sections fit ${width}x${height}`, async ({
    page,
    baseURL,
  }, info) => {
    await page.setViewportSize({ width: width!, height: height! });
    await page
      .context()
      .addCookies([{ name: "locale", value: "zh-CN", url: baseURL! }]);
    await fixture(page);
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto("/workspace/admin/dashboard");
    const nav = page.getByRole("tablist", { name: "看板模块" });
    await expect(nav.getByRole("tab")).toHaveText(labels);
    await expect(page.getByRole("tabpanel")).toHaveCount(1);
    await expect(page.getByText("用户活跃趋势", { exact: true })).toBeVisible();
    await expect(page.getByText("86", { exact: true }).first()).toBeVisible();
    await page.screenshot({
      path: info.outputPath("dashboard-overview.png"),
      animations: "disabled",
    });
    for (const name of labels) {
      await nav.getByRole("tab", { name, exact: true }).click();
      await expect(nav.getByRole("tab", { name, exact: true })).toHaveAttribute(
        "aria-selected",
        "true",
      );
      await expect(page.getByRole("tabpanel")).toHaveCount(1);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      ).toBe(true);
      await expect(nav).toBeInViewport();
    }
    await nav.getByRole("tab", { name: "运行诊断", exact: true }).click();
    await expect(page.getByText(/最新 1000 条范围内筛选/)).toBeVisible();
    await page.screenshot({
      path: info.outputPath("dashboard-runs.png"),
      animations: "disabled",
    });
    await nav.getByRole("tab", { name: "模型监控", exact: true }).click();
    await expect(page.getByLabel("开始时间")).toBeHidden();
    await expect(
      page
        .getByTestId("model-monitor")
        .getByRole("img", { name: "并发采样峰值" }),
    ).toBeVisible();
    await page.screenshot({
      path: info.outputPath("dashboard-models.png"),
      animations: "disabled",
    });
    expect(errors).toEqual([]);
  });
}

test("navigation preserves filters, isolates requests and supports back/reload", async ({
  page,
  baseURL,
}) => {
  await page.clock.install();
  await page
    .context()
    .addCookies([{ name: "locale", value: "zh-CN", url: baseURL! }]);
  await fixture(page);
  const requests: string[] = [];
  page.on("request", (r) => {
    const url = new URL(r.url());
    if (url.pathname.startsWith("/api/admin/"))
      requests.push(url.pathname + url.search);
  });
  await page.goto("/workspace/admin/dashboard");
  await expect(page.getByText("用户活跃趋势", { exact: true })).toBeVisible();
  await expect(page.getByText("86", { exact: true }).first()).toBeVisible();
  expect(
    requests.every((r) => /\/dashboard\/(summary\?|date-bounds)/.test(r)),
  ).toBe(true);
  await page.getByRole("button", { name: "自定义区间", exact: true }).click();
  await page.getByLabel("开始时间").fill("2026-10-01T10:00");
  await page.getByLabel("结束时间").fill("2026-10-31T10:00");
  await page.getByRole("button", { name: "应用区间", exact: true }).click();
  await page.getByRole("tab", { name: "运行诊断", exact: true }).click();
  const failure = page
    .locator('[data-slot="card"]')
    .filter({ hasText: "最新 1000 条范围内筛选" });
  const search = failure.getByRole("textbox");
  await search.fill("qa@example.test");
  await failure.getByRole("button", { name: "下一页", exact: true }).click();
  await expect(failure.getByText("2 / 2", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "用户与员工", exact: true }).click();
  await expect(page.getByTestId("user-run-ranking")).toContainText("合成用户1");
  await page.getByRole("tab", { name: "运行诊断", exact: true }).click();
  await expect(search).toHaveValue("qa@example.test");
  await expect(failure.getByText("2 / 2", { exact: true })).toBeVisible();
  await expect(page.getByLabel("开始时间")).toHaveValue("2026-10-01T10:00");
  for (const name of ["知识库", "总览", "用户与员工", "运行诊断"]) {
    await page.getByRole("tab", { name, exact: true }).click();
    await expect(page.getByLabel("开始时间")).toHaveValue("2026-10-01T10:00");
    await expect(page.getByLabel("结束时间")).toHaveValue("2026-10-31T10:00");
  }
  await page.getByRole("tab", { name: "模型监控", exact: true }).click();
  const model = page.getByTestId("model-monitor");
  await model.getByRole("combobox", { name: "统计窗口" }).click();
  await page.getByRole("option", { name: "最近 1 小时" }).click();
  await page.getByRole("tab", { name: "OA 接口", exact: true }).click();
  await expect(
    page
      .getByTestId("oa-policy-flow-monitor")
      .getByRole("combobox", { name: "统计窗口" }),
  ).toContainText("最近 15 分钟");
  await page
    .getByTestId("oa-policy-flow-monitor")
    .getByRole("combobox", { name: "统计窗口" })
    .click();
  await page.getByRole("option", { name: "最近 1 小时" }).click();
  await page.getByRole("tab", { name: "总览", exact: true }).click();
  await page.getByRole("tab", { name: "OA 接口", exact: true }).click();
  await expect(
    page
      .getByTestId("oa-policy-flow-monitor")
      .getByRole("combobox", { name: "统计窗口" }),
  ).toContainText("最近 1 小时");
  await page.getByRole("tab", { name: "模型监控", exact: true }).click();
  await page.getByRole("tab", { name: "OA 接口", exact: true }).click();
  await page.goBack();
  await expect(
    page.getByRole("tab", { name: "模型监控", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(model.getByRole("combobox", { name: "统计窗口" })).toContainText(
    "最近 1 小时",
  );
  await page.reload();
  await expect(
    page.getByRole("tab", { name: "模型监控", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(model.getByRole("table")).toContainText("DeepSeek-V4.1-Flash");
  // Hidden dashboard and OA queries must not poll while a model section is open.
  requests.length = 0;
  await page.clock.fastForward(65_000);
  await expect.poll(() => requests.length).toBeGreaterThan(0);
  expect(
    requests.every((r) => r.startsWith("/api/admin/model-monitor?minutes=15")),
  ).toBe(true);
});

test("unknown sections fall back and keyboard navigation is manual", async ({
  page,
  baseURL,
}) => {
  await page
    .context()
    .addCookies([{ name: "locale", value: "zh-CN", url: baseURL! }]);
  await fixture(page);
  await page.goto("/workspace/admin/dashboard#unknown");
  const overview = page.getByRole("tab", { name: "总览", exact: true });
  await expect(overview).toHaveAttribute("aria-selected", "true");
  await overview.focus();
  await page.keyboard.press("ArrowRight");
  await expect(
    page.getByRole("tab", { name: "运行诊断", exact: true }),
  ).toBeFocused();
  await expect(overview).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("tab", { name: "运行诊断", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
});

test("chat history labels match sidebar, page and document title", async ({
  page,
  baseURL,
}) => {
  await page.context().addCookies([
    { name: "locale", value: "zh-CN", url: baseURL! },
    { name: "sidebar_state", value: "true", url: baseURL! },
  ]);
  await fixture(page);
  await page.goto("/workspace/chats");
  await expect(
    page.getByRole("heading", { name: "历史对话", exact: true }),
  ).toBeVisible();
  await expect(
    page
      .locator('[data-sidebar="menu-button"]')
      .filter({ hasText: "历史对话" }),
  ).toBeVisible();
  await expect(page).toHaveTitle(/历史对话/);
});
