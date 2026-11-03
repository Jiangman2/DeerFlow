import { expect, test } from "@playwright/test";

const time = "2026-10-31T01:00:00Z";
const overview = Object.fromEntries(
  [
    "total_users",
    "new_users",
    "active_users",
    "threads",
    "all_time_threads",
    "existing_threads",
    "runs",
    "terminal_runs",
    "successful_runs",
    "failed_runs",
    "running_runs",
    "success_rate",
    "avg_run_duration_ms",
    "total_tokens",
    "llm_calls",
    "artifact_runs",
    "knowledge_runs",
    "feedback_total",
    "satisfaction_rate",
    "estimated_minutes_saved",
  ].map((key) => [key, 0]),
);
const summary = {
  window: { start_at: time, end_at: time, hours: 24 },
  overview,
  user_activity: [],
  user_usage: [],
  activity_bucket: "day",
  feedback: { total: 0, positive: 0, negative: 0 },
  scheduled_task_usage: { users: [] },
  recent_failures: [],
  feature_usage: [],
  model_usage: [],
  agent_usage: [],
};

for (const width of [1440, 390]) {
  test(`dashboard range feedback and retained-date bounds at ${width}px`, async ({
    browser,
    baseURL,
  }) => {
    test.setTimeout(90_000);
    const context = await browser.newContext({
      viewport: { width, height: 900 },
      locale: "zh-CN",
      timezoneId: "Asia/Shanghai",
    });
    const page = await context.newPage();
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    let boundsMode = "normal";
    let summaryMode = "normal";
    let summaryRequests = 0;
    let releaseSummary: (() => void) | undefined;
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      const path = url.pathname;
      let json: unknown = { items: [], users: [], total: 0, rows: [] };
      if (path === "/api/runtime-config") return route.continue();
      if (path === "/api/v1/auth/me")
        json = {
          id: "synthetic-user",
          email: "qa@example.com",
          system_role: "admin",
          needs_setup: false,
        };
      else if (path.endsWith("/setup-status")) json = { needs_setup: false };
      else if (path.endsWith("/date-bounds")) {
        if (boundsMode === "error")
          return route.fulfill({
            status: 503,
            json: { detail: "Synthetic bounds failure" },
          });
        json = {
          earliest_at: boundsMode === "empty" ? null : "2026-09-15T01:00:00Z",
          selectable_start_at:
            boundsMode === "empty" ? null : "2026-09-14T16:00:00Z",
          timezone: "Asia/Shanghai",
        };
      } else if (path.endsWith("/dashboard/summary")) {
        summaryRequests++;
        if (summaryMode === "slow")
          await new Promise<void>((resolve) => {
            releaseSummary = resolve;
          });
        if (summaryMode === "error")
          return route.fulfill({
            status: 500,
            json: { detail: "Synthetic summary failure" },
          });
        json = {
          ...summary,
          overview: {
            ...overview,
            active_users: url.searchParams.has("start_at") ? 321 : 123,
          },
        };
      } else if (path.endsWith("/dashboard/knowledge"))
        json = { available: false };
      else if (path.endsWith("/dashboard/run-rankings"))
        json = { slow_runs: [], high_token_runs: [] };
      else if (path.includes("/models")) json = { models: [] };
      else if (path.includes("/announcements")) json = { announcement: null };
      else if (path.includes("/features")) json = {};
      await route.fulfill({ json }).catch(() => undefined);
    });
    try {
      await page.goto(`${baseURL}/workspace/admin/dashboard`);
      const start = page.getByLabel("开始时间");
      const end = page.getByLabel("结束时间");
      const apply = page.getByRole("button", { name: "应用区间", exact: true });
      await expect(start).toBeEnabled();
      await expect(start).toHaveAttribute("min", "2026-09-15T00:00");
      await expect(end).toHaveAttribute("min", "2026-09-15T00:00");
      await expect(
        page.getByText("123", { exact: true }).first(),
      ).toBeVisible();
      const beforeInvalid = summaryRequests;
      await start.fill("2026-09-01T11:04");
      await expect(apply).toBeDisabled();
      await expect(page.getByText(/所选时间早于现有数据/)).toBeVisible();
      expect(summaryRequests).toBe(beforeInvalid);
      await start.fill("2026-10-01T11:04");
      await end.fill("2026-10-31T11:04");
      summaryMode = "slow";
      await apply.click();
      await expect(
        page.getByText("正在加载所选区间", { exact: true }),
      ).toBeVisible();
      await expect(
        page.getByRole("button", { name: "应用中…", exact: true }),
      ).toBeDisabled();
      await expect(page.getByText(/当前暂时保留上次结果/)).toBeVisible();
      await expect.poll(() => typeof releaseSummary).toBe("function");
      summaryMode = "normal";
      releaseSummary!();
      await expect(
        page.getByText("321", { exact: true }).first(),
      ).toBeVisible();
      await expect(
        page.getByText("正在加载所选区间", { exact: true }),
      ).toHaveCount(0);
      summaryMode = "error";
      const beforeRetry = summaryRequests;
      await apply.click();
      await expect(page.getByText(/看板数据加载失败/)).toBeVisible();
      await page.waitForTimeout(1500);
      expect(summaryRequests - beforeRetry).toBe(1);
      summaryMode = "normal";
      await page.getByRole("button", { name: "重新加载", exact: true }).click();
      await expect(page.getByText(/看板数据加载失败/)).toHaveCount(0);
      await expect(
        page.getByText("正在加载所选区间", { exact: true }),
      ).toHaveCount(0);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      boundsMode = "empty";
      await page.reload();
      await expect(
        page.getByText("暂无可统计数据，暂不能选择自定义区间。"),
      ).toBeVisible();
      await expect(start).toBeDisabled();
      await expect(apply).toBeDisabled();
      boundsMode = "error";
      await page.reload();
      await expect(
        page.getByText("可选日期范围加载失败，暂不能应用自定义区间。"),
      ).toBeVisible();
      boundsMode = "normal";
      await page
        .getByRole("button", { name: "重试日期范围", exact: true })
        .click();
      await expect(start).toBeEnabled();
      expect(pageErrors).toEqual([]);
    } finally {
      releaseSummary?.();
      await context.close();
    }
  });
}
