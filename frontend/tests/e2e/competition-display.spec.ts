import { mkdir } from "node:fs/promises";
import path from "node:path";

import { expect, test } from "@playwright/test";

import { modelSquareFixture } from "./utils/model-square-fixture";

const names = [
  "制度文生图智能体",
  "地震数据提频专家",
  "科研报告编写专家",
  "合同审查助手",
  "安全生产助手",
  "油藏分析助手",
  "工程设计助手",
];
const employees = names.map((name, index) => ({
  identity: String(index + 1).repeat(64),
  publication: "a".repeat(64),
  name,
  source: index % 2 ? "external" : "team",
}));
const board = {
  title: "数字员工创新应用赛",
  updated_at: "2026-10-28T17:30:00+08:00",
  unavailable_count: 0,
  refresh_seconds: 10,
  items: employees.map((e, i) => ({
    identity: e.identity,
    name: e.name,
    total_clicks: 1284 - i * 117,
    today_clicks: 128 - i * 11,
  })),
};
const screenshotDir = process.env.FEATURE_QA_OUTPUT_DIR;
test.use({ locale: "zh-CN" });

for (const mode of ["desktop", "mobile"] as const) {
  test(`${mode} public competition display and administrator selection`, async ({
    page,
    baseURL,
  }) => {
    test.setTimeout(60_000);
    await page
      .context()
      .addCookies([{ name: "locale", value: "zh-CN", url: baseURL! }]);
    await page.setViewportSize(
      mode === "desktop"
        ? { width: 1440, height: 1000 }
        : { width: 390, height: 844 },
    );
    let config = {
      revision: 0,
      title: board.title,
      enabled: false,
      employees: [] as typeof employees,
    };
    await modelSquareFixture(page);
    await page.route("**/api/competition/display", (route) =>
      route.fulfill({ json: board }),
    );
    await page.route("**/api/admin/competition/employees?**", (route) => {
      const query =
        new URL(route.request().url()).searchParams.get("query") ?? "";
      const items = employees.filter((e) => e.name.includes(query));
      return route.fulfill({ json: { items, total: items.length } });
    });
    await page.route("**/api/admin/competition/display", (route) => {
      if (route.request().method() === "PUT")
        config = {
          ...(route.request().postDataJSON() as typeof config),
          revision: config.revision + 1,
        };
      return route.fulfill({ json: config });
    });
    await page.goto("/competition");
    await expect(
      page.getByRole("heading", { name: board.title }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: names[0], exact: true }),
    ).toBeVisible();
    await expect(page.getByText("1,284", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "暂停轮播" }).click();
    await page.getByRole("button", { name: "下一页", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: names[6], exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "上一页", exact: true }).click();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    if (screenshotDir) {
      await mkdir(screenshotDir, { recursive: true });
      await page.screenshot({
        path: path.join(screenshotDir, `${mode}-competition-display.png`),
        fullPage: true,
      });
    }
    await page.goto("/workspace/admin/dashboard");
    await page.getByRole("button", { name: "比赛大屏", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByText("已选 0 / 100")).toBeVisible();
    await dialog.getByRole("checkbox", { name: /制度文生图智能体/ }).check();
    await dialog.getByRole("checkbox", { name: /地震数据提频专家/ }).check();
    await dialog
      .getByRole("textbox", { name: "搜索参展数字员工" })
      .fill("科研");
    await dialog.getByRole("checkbox", { name: /科研报告编写专家/ }).check();
    await dialog.getByRole("checkbox", { name: "启用公开展示" }).check();
    await dialog.getByRole("button", { name: "保存设置" }).click();
    await expect(
      dialog.getByText("已发布。比赛展示页将在下次刷新时同步。"),
    ).toBeVisible();
    expect(config.employees.length).toBe(3);
    if (screenshotDir)
      await page.screenshot({
        path: path.join(screenshotDir, `${mode}-competition-selection.png`),
        fullPage: false,
      });
  });
}

test("failed refresh preserves last counts; unpublish clears them", async ({
  page,
}) => {
  let status = 200;
  await page.clock.install();
  await page.route("**/api/competition/display", (route) =>
    status === 200
      ? route.fulfill({ json: board })
      : route.fulfill({ status, json: { detail: "unavailable" } }),
  );
  await page.goto("/competition");
  await expect(page.getByText("1,284", { exact: true })).toBeVisible();
  status = 503;
  await page.clock.fastForward(11_000);
  await expect(page.getByText(/暂时无法更新/)).toBeVisible();
  await expect(page.getByText("1,284", { exact: true })).toBeVisible();
  status = 404;
  await page.clock.fastForward(11_000);
  await expect(page.getByText(/展示尚未开放/)).toBeVisible();
  await expect(page.getByText("1,284", { exact: true })).toHaveCount(0);
});
