import { mkdir } from "node:fs/promises";
import path from "node:path";

import { expect, test, type Page, type TestInfo } from "@playwright/test";

import { modelSquareFixture } from "./utils/model-square-fixture";

test.setTimeout(90_000);
test.use({ locale: "zh-CN" });

async function screenshot(page: Page, info: TestInfo, name: string) {
  const directory = process.env.FEATURE_QA_OUTPUT_DIR ?? info.outputDir;
  await mkdir(directory, { recursive: true });
  await page.screenshot({
    path: path.join(directory, `${name}.png`),
    animations: "disabled",
  });
}

for (const [size, width, height] of [
  ["desktop", 1440, 1000],
  ["mobile", 390, 844],
] as const) {
  test(`${size}: viewport pagination, matched filter panel and scoped launch`, async ({
    page,
    baseURL,
  }, info) => {
    await page.setViewportSize({ width, height });
    await page.context().addCookies([
      { name: "locale", value: "zh-CN", url: baseURL! },
      { name: "sidebar_state", value: "true", url: baseURL! },
    ]);
    const fixture = await modelSquareFixture(page);
    await page.goto("/workspace/agents");
    const cards = page.getByTestId("digital-employee-card");
    const slot = page.getByTestId("employee-card-slot");
    await expect(slot).toBeVisible();
    const expectedSize =
      size === "desktop"
        ? await slot.evaluate((element) => {
            const row = Math.max(
              112,
              parseFloat(getComputedStyle(element).fontSize) * 7,
            );
            return Math.max(
              1,
              Math.floor((element.clientHeight + 10) / (row + 10)),
            );
          })
        : 5;
    await expect(slot).toHaveAttribute("data-page-size", String(expectedSize));
    await expect(cards).toHaveCount(expectedSize);
    await expect(cards.first()).toContainText("制度研究助手");
    if (size === "desktop") {
      const left = await page.getByTestId("desktop-filter-panel").boundingBox();
      const right = await page.getByTestId("employee-list-panel").boundingBox();
      expect(Math.abs(left!.y - right!.y)).toBeLessThan(1);
      expect(Math.abs(left!.height - right!.height)).toBeLessThan(1);
      expect(Math.abs(right!.y + right!.height - (height - 24))).toBeLessThan(
        2,
      );
      expect(
        await slot.evaluate(
          (element) => element.scrollHeight - element.clientHeight,
        ),
      ).toBeLessThanOrEqual(1);
    }
    await screenshot(page, info, `${size}-employee-square`);
    await page
      .getByTestId("digital-employee-pagination")
      .getByRole("button", { name: "下一页" })
      .click();
    await expect(cards).toHaveCount(Math.min(expectedSize, 12 - expectedSize));
    await expect(cards.first()).toContainText(
      fixture.agents[expectedSize]!.display_name,
    );
    await page.getByRole("tab", { name: "我的数字员工" }).click();
    await expect(cards).toHaveCount(expectedSize);
    await expect(cards.first()).toContainText("制度研究助手");
    await screenshot(page, info, `${size}-employee-personal`);
    expect(fixture.launches).toHaveLength(0);
    await page.getByRole("tab", { name: "数字员工广场" }).click();
    await cards.first().getByRole("button", { name: "开始工作" }).click();
    await expect.poll(() => fixture.launches.length).toBe(1);
    expect(fixture.launches[0]!.body).toEqual({
      expected_identity: fixture.token,
    });
  });

  test(`${size}: account identity and own usage`, async ({
    page,
    baseURL,
  }, info) => {
    await page.setViewportSize({ width, height });
    await page.context().addCookies([
      { name: "locale", value: "zh-CN", url: baseURL! },
      { name: "sidebar_state", value: "true", url: baseURL! },
    ]);
    await modelSquareFixture(page);
    await page.goto("/workspace/chats/new");
    await expect(
      page.getByRole("button", { name: "DeepSeek-V4-Flash", exact: true }),
    ).toBeVisible();
    const refreshed = page.waitForResponse("**/api/v1/auth/me");
    await page.evaluate(() =>
      document.dispatchEvent(new Event("visibilitychange")),
    );
    await refreshed;
    if (size === "mobile") {
      await page.locator(".cnooc-mobile-menu-trigger:visible").click();
      await expect(page.locator('[data-mobile="true"]')).toBeVisible();
    }
    await page.getByRole("button", { name: "设置和更多", exact: true }).click();
    await expect(page.getByTestId("current-account-menu")).toContainText(
      "preview@example.test",
    );
    await expect(page.getByTestId("account-token-usage")).toContainText(
      "1,286,450",
    );
    await screenshot(page, info, `${size}-account-menu`);
    await page.getByRole("menuitem", { name: "设置", exact: true }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByRole("dialog")).toContainText("示例业务部门");
    await expect(page.getByRole("dialog")).toContainText("960,000");
    await screenshot(page, info, `${size}-account-settings`);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
  });

  test(`${size}: dashboard attribution, search and pagination`, async ({
    page,
    baseURL,
  }, info) => {
    await page.setViewportSize({ width, height });
    await page.context().addCookies([
      { name: "locale", value: "zh-CN", url: baseURL! },
      { name: "sidebar_state", value: "true", url: baseURL! },
    ]);
    await modelSquareFixture(page);
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/workspace/admin/dashboard");
    const actors = page.getByTestId("feedback-actors-panel"),
      clicks = page.getByTestId("employee-clicks-panel");
    await expect(actors).toContainText("示例用户01");
    await expect(actors).not.toContainText("示例用户06");
    await actors.evaluate((element) =>
      element.scrollIntoView({ block: "start" }),
    );
    await screenshot(page, info, `${size}-feedback-actors`);
    if (size === "desktop") {
      const box = await actors.boundingBox();
      expect(box!.height).toBeLessThan(540);
      await actors.evaluate((element) =>
        element.scrollIntoView({ block: "center" }),
      );
      await screenshot(page, info, "desktop-dashboard-feedback-context");
    }
    await actors.getByRole("radio", { name: "点踩", exact: true }).click();
    await expect(actors).toContainText("共 4 条");
    await actors.getByRole("textbox", { name: "搜索反馈人" }).fill("user4@");
    await expect(actors).toContainText("共 1 条");
    await expect(actors).toContainText("示例用户04");
    await actors.getByRole("textbox", { name: "搜索反馈人" }).fill("");
    await actors.getByRole("radio", { name: "全部", exact: true }).click();
    await actors.getByRole("button", { name: "反馈明细下一页" }).click();
    await expect(actors).toContainText("示例用户06");
    await expect(clicks).toContainText("制度研究助手");
    await clicks.evaluate((element) =>
      element.scrollIntoView({ block: "start" }),
    );
    await screenshot(page, info, `${size}-employee-clicks`);
    await clicks.getByRole("button", { name: "员工点击下一页" }).click();
    await expect(clicks).toContainText("人才发展助手");
    await clicks
      .getByRole("textbox", { name: "搜索点击统计中的数字员工" })
      .fill("制度");
    await expect(clicks).toContainText("共 1 条");
    await expect(page.getByText("外部智能体点击", { exact: true })).toHaveCount(
      0,
    );
    await expect(
      page.getByText("外部智能体点击（海能+协同）", { exact: true }),
    ).toHaveCount(0);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    expect(errors).toEqual([]);
  });
}

test("V4.1 is optional and leaves the existing default selected", async ({
  page,
  baseURL,
}, info) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page
    .context()
    .addCookies([{ name: "locale", value: "zh-CN", url: baseURL! }]);
  await modelSquareFixture(page);
  await page.goto("/workspace/chats/new");
  await page
    .getByRole("button", { name: "DeepSeek-V4-Flash", exact: true })
    .click();
  const menu = page.getByTestId("chat-model-menu");
  await expect(
    menu.getByRole("menuitemradio", { name: "DeepSeek-V4-Flash", exact: true }),
  ).toHaveAttribute("aria-checked", "true");
  await expect(
    menu.getByRole("menuitemradio", {
      name: "DeepSeek-V4.1-Flash-1017",
      exact: true,
    }),
  ).toHaveAttribute("aria-checked", "false");
  await screenshot(page, info, "desktop-v41-model-menu");
  await menu
    .getByRole("menuitemradio", {
      name: "DeepSeek-V4.1-Flash-1017",
      exact: true,
    })
    .click();
  await expect(
    page.getByRole("button", { name: "DeepSeek-V4.1-Flash-1017", exact: true }),
  ).toBeVisible();
});

test("desktop shell keeps a fixed module gap and fits short/tall viewports", async ({
  page,
  baseURL,
}, info) => {
  await page.context().addCookies([
    { name: "locale", value: "zh-CN", url: baseURL! },
    { name: "sidebar_state", value: "true", url: baseURL! },
  ]);
  await modelSquareFixture(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/workspace/agents");
  await expect(page.getByTestId("employee-card-slot")).toBeVisible();
  const sizes: number[] = [];
  for (const [width, height] of [
    [1280, 720],
    [1920, 1080],
    [2560, 1440],
  ] as const) {
    await page.setViewportSize({ width, height });
    const slot = page.getByTestId("employee-card-slot");
    await expect
      .poll(async () => Number(await slot.getAttribute("data-page-size")))
      .toBe(
        await slot.evaluate((e) =>
          Math.min(
            50,
            Math.max(
              1,
              Math.floor(
                (e.clientHeight + 10) /
                  (Math.max(112, parseFloat(getComputedStyle(e).fontSize) * 7) +
                    10),
              ),
            ),
          ),
        ),
      );
    sizes.push(Number(await slot.getAttribute("data-page-size")));
    const frame = await page
      .locator('[data-slot="sidebar-inner"]')
      .boundingBox();
    const title = await page
      .locator(".cnooc-agents-page > header > div")
      .boundingBox();
    expect(Math.abs(title!.x - frame!.x - frame!.width - 36)).toBeLessThan(2);
    expect(
      await slot.evaluate((e) => e.scrollHeight - e.clientHeight),
    ).toBeLessThanOrEqual(1);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    if (width === 1920)
      await screenshot(page, info, "desktop-1920-expanded-gallery");
  }
  expect(sizes[0]).toBeLessThan(sizes[1]!);
  expect(sizes[1]).toBeLessThan(sizes[2]!);
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page
    .locator('[data-slot="sidebar-inner"] [data-sidebar="trigger"]')
    .click();
  await expect(
    page.locator('[data-slot="sidebar"][data-state="collapsed"]'),
  ).toBeVisible();
  await expect
    .poll(async () => {
      const frame = await page
        .locator('[data-slot="sidebar-inner"]')
        .boundingBox();
      const title = await page
        .locator(".cnooc-agents-page > header > div")
        .boundingBox();
      return Math.round(title!.x - frame!.x - frame!.width);
    })
    .toBe(36);
  await screenshot(page, info, "desktop-1920-collapsed-gallery");
});

test("module gutters agree with the collapsed rail", async ({
  page,
  baseURL,
}, info) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.context().addCookies([
    { name: "locale", value: "zh-CN", url: baseURL! },
    { name: "sidebar_state", value: "false", url: baseURL! },
  ]);
  await modelSquareFixture(page);
  for (const [route, selector] of [
    ["agents", ".cnooc-agents-page > header > div"],
    ["scheduled-tasks", ".cnooc-scheduled-page > header"],
    ["chats", ".cnooc-list-search h1"],
    ["admin/dashboard", ".cnooc-admin-dashboard > div"],
  ]) {
    await page.goto(`/workspace/${route}`);
    await expect(page.locator(selector!)).toBeVisible();
    const frame = await page
      .locator('[data-slot="sidebar-inner"]')
      .boundingBox();
    const header = await page.locator(selector!).boundingBox();
    expect(Math.abs(header!.x - frame!.x - frame!.width - 36)).toBeLessThan(2);
    await screenshot(
      page,
      info,
      `desktop-shell-${route!.replaceAll("/", "-")}`,
    );
  }
  await page.goto("/workspace/chats/new");
  await expect(
    page.getByRole("button", { name: "DeepSeek-V4-Flash", exact: true }),
  ).toBeVisible();
  await screenshot(page, info, "desktop-home-responsive-sidebar");
});

test("narrow phone keeps employee actions and pagination reachable", async ({
  page,
  baseURL,
}, info) => {
  await page.setViewportSize({ width: 320, height: 640 });
  await page
    .context()
    .addCookies([{ name: "locale", value: "zh-CN", url: baseURL! }]);
  await modelSquareFixture(page);
  await page.goto("/workspace/agents");
  await expect(page.getByTestId("digital-employee-card")).toHaveCount(5);
  const action = page
    .getByTestId("digital-employee-card")
    .first()
    .getByRole("button", { name: "开始工作" });
  const box = await action.boundingBox();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(320);
  const next = page
    .getByTestId("digital-employee-pagination")
    .getByRole("button", { name: "下一页" });
  await next.click();
  await expect(page.getByTestId("digital-employee-card").first()).toContainText(
    "采购管理助手",
  );
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(320);
  await screenshot(page, info, "mobile-320-employee-square");
});
