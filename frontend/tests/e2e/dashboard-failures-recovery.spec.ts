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
  for (const failedPage of [40, 41]) {
    test(`failed-run page ${failedPage} recovery at ${width}px`, async ({
      browser,
      baseURL,
    }) => {
      test.setTimeout(120_000);
      const context = await browser.newContext({
        viewport: { width, height: 1000 },
        locale: "zh-CN",
      });
      const page = await context.newPage();
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      const offsets: number[] = [];
      let failTarget = true;
      const failedOffset = (failedPage - 1) * 5;
      let release: (() => void) | undefined;
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
        else if (path.endsWith("/date-bounds"))
          json = {
            earliest_at: time,
            selectable_start_at: time,
            timezone: "Asia/Shanghai",
          };
        else if (path.endsWith("/dashboard/summary")) json = summary;
        else if (path.endsWith("/dashboard/knowledge"))
          json = { available: false };
        else if (path.endsWith("/dashboard/run-rankings"))
          json = { slow_runs: [], high_token_runs: [] };
        else if (path.endsWith("/dashboard/failures")) {
          const offset = Number(url.searchParams.get("offset"));
          offsets.push(offset);
          if (offset === 5)
            await new Promise<void>((resolve) => {
              release = resolve;
            });
          if (offset === failedOffset && failTarget)
            return route.fulfill({ status: 500, body: "PRIVATE SQL DETAILS" });
          json = {
            total: 205,
            items: Array.from({ length: 5 }, (_, i) => ({
              run_id: `run-${offset + i}`,
              thread_id: `thread-${offset + i}`,
              user_id: null,
              email: null,
              display_name: null,
              status: "error",
              error: "合成错误摘要",
              assistant_id: "lead_agent",
              model_name: "deepseek-v4-flash",
              started_at: time,
              updated_at: time,
              duration_ms: 1000,
              disposition_status: null,
              diagnostics_url: "",
              can_open: false,
              chat_url: null,
            })),
          };
        } else if (path.includes("/models")) json = { models: [] };
        else if (path.includes("/announcements")) json = { announcement: null };
        else if (path.includes("/features")) json = {};
        await route.fulfill({ json }).catch(() => undefined);
      });
      try {
        await page.goto(`${baseURL}/workspace/admin/dashboard#runs`);
        const card = page
          .locator('[data-slot="card"]')
          .filter({ hasText: "最近失败运行" });
        await expect(card.getByText("1 / 41", { exact: true })).toBeVisible();
        await card.getByRole("button", { name: "下一页", exact: true }).click();
        await expect(card.getByRole("status")).toContainText(
          "正在加载失败运行",
        );
        await expect(card.getByText(/最近失败运行（加载中）/)).toBeVisible();
        await expect.poll(() => typeof release).toBe("function");
        release!();
        for (let displayed = 2; displayed < failedPage; displayed++) {
          await expect(
            card.getByText(`${displayed} / 41`, { exact: true }),
          ).toBeVisible();
          await card
            .getByRole("button", { name: "下一页", exact: true })
            .click();
        }
        await expect(card.getByRole("alert")).toContainText("HTTP 500");
        await expect(card.getByText("最近失败运行（暂不可用）")).toBeVisible();
        await expect(card.getByText("最近失败运行（0）")).toHaveCount(0);
        await expect(page.getByText("PRIVATE SQL DETAILS")).toHaveCount(0);
        await page.waitForTimeout(3500);
        expect(offsets.filter((n) => n === failedOffset)).toHaveLength(1);
        await card.screenshot({
          path: `C:/Users/15285/.codex/tmp/dashboard-failures-error-${failedPage}-${width}.png`,
        });
        failTarget = false;
        await card
          .getByRole("button", { name: "重试加载失败运行", exact: true })
          .click();
        await expect(
          card.getByText(`${failedPage} / 41`, { exact: true }),
        ).toBeVisible();
        await expect(card.getByRole("alert")).toHaveCount(0);
        if (failedPage === 40)
          await card
            .getByRole("button", { name: "下一页", exact: true })
            .click();
        await expect(card.getByText("41 / 41", { exact: true })).toBeVisible();
        await expect(
          card.getByRole("button", { name: "下一页", exact: true }),
        ).toBeDisabled();
        await card.screenshot({
          path: `C:/Users/15285/.codex/tmp/dashboard-failures-success-${failedPage}-${width}.png`,
        });
        expect(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
          ),
        ).toBe(true);
        expect(errors).toEqual([]);
      } finally {
        release?.();
        await context.close();
      }
    });
  }
}
