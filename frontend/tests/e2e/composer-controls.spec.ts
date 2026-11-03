import { expect, test } from "@playwright/test";

import { mockLangGraphAPI, handleRunStream } from "./utils/mock-api";
import { modelSquareFixture } from "./utils/model-square-fixture";

test.setTimeout(90_000);

for (const width of [1440, 390]) {
  test(`user ranking fills its card ${width}`, async ({
    page,
    baseURL,
  }, info) => {
    await page.setViewportSize({ width, height: 1000 });
    await page
      .context()
      .addCookies([{ name: "locale", value: "zh-CN", url: baseURL! }]);
    await modelSquareFixture(page, true);
    await page.goto("/workspace/admin/dashboard#users");
    const card = page.getByTestId("user-run-ranking");
    const list = page.getByTestId("user-run-ranking-list");
    await expect(card.getByText("合成用户1", { exact: true })).toBeVisible();
    await card.scrollIntoViewIfNeeded();
    if (width >= 1280) {
      const box = (await card.boundingBox())!;
      const next = (await card
        .getByRole("button", { name: "下一页", exact: true })
        .boundingBox())!;
      expect(box.y + box.height - next.y - next.height).toBeLessThan(36);
      expect((await list.boundingBox())!.height).toBeGreaterThan(400);
    }
    await card.getByRole("button", { name: "下一页", exact: true }).click();
    await expect(card.getByText("合成用户11", { exact: true })).toBeVisible();
    expect(await list.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(
      true,
    );
    await page.screenshot({ path: info.outputPath("ranking.png") });
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
  });
}

test("account totals use Chinese units and retain exact totals", async ({
  page,
  baseURL,
}, info) => {
  await page
    .context()
    .addCookies([{ name: "locale", value: "zh-CN", url: baseURL! }]);
  await modelSquareFixture(page);
  let total = 9999;
  await page.route("**/api/console/token-summary", (route) =>
    route.fulfill({
      json: {
        scope: "current_user_recorded_platform_runs",
        total_tokens: total,
        input_tokens: total,
        output_tokens: 0,
        unclassified_tokens: 0,
        run_count: 1,
        recorded_since: "2026-11-01T00:00:00Z",
      },
    }),
  );
  for (const [value, formatted] of [
    [9999, "9,999"],
    [10000, "1万"],
    [1286450, "128.65万"],
    [123456789, "1.23亿"],
    [465800241, "4.66亿"],
  ] as const) {
    total = value;
    await page.goto("/workspace/chats/new");
    await page.getByRole("button", { name: "设置和更多", exact: true }).click();
    const menuUsage = page.getByRole("menu").getByTestId("account-token-usage");
    await expect(menuUsage.getByText(formatted, { exact: true })).toBeVisible();
    await expect(menuUsage.locator("span[title]")).toHaveAttribute(
      "title",
      new RegExp(value.toLocaleString("zh-CN")),
    );
    if (value === 465800241) {
      await page
        .getByRole("menu")
        .screenshot({ path: info.outputPath("tokens-menu-465800241.png") });
    }
    await page.getByRole("menuitem", { name: "设置", exact: true }).click();
    const usage = page.getByRole("dialog").getByTestId("account-token-usage");
    await expect(
      usage.getByText(formatted, { exact: true }).first(),
    ).toBeVisible();
    await expect(usage.locator("span[title]")).toHaveAttribute(
      "title",
      new RegExp(value.toLocaleString("zh-CN")),
    );
    await page.screenshot({ path: info.outputPath(`tokens-${value}.png`) });
  }
});

for (const width of [1440, 390, 320]) {
  for (const employee of [false, true]) {
    test(`web switch reaches run context ${employee ? "employee" : "main"} ${width}`, async ({
      page,
      baseURL,
    }, info) => {
      await page.setViewportSize({ width, height: 900 });
      await page
        .context()
        .addCookies([{ name: "locale", value: "zh-CN", url: baseURL! }]);
      mockLangGraphAPI(page, {
        agents: [
          {
            name: "web-qa",
            display_name: "联网测试助手",
            description: "合成测试",
            system_prompt: "synthetic",
          },
        ],
      });
      const sent: boolean[] = [];
      await page.route("**/api/langgraph/threads/*/runs/stream", (route) => {
        const body = route.request().postDataJSON() as {
          context: { web_search_enabled: boolean };
        };
        sent.push(body.context.web_search_enabled);
        return handleRunStream(route);
      });
      await page.goto(
        employee
          ? "/workspace/agents/web-qa/chats/new"
          : "/workspace/chats/new",
      );
      const toggle = page.getByTestId("web-search-toggle");
      await expect(toggle).toHaveAttribute("aria-pressed", "false");
      await page.locator("textarea:visible").fill("合成测试一");
      await page.locator(".cnooc-submit:visible").click();
      await expect.poll(() => sent).toEqual([false]);
      await expect(page.locator("textarea:visible")).toBeEditable();
      await toggle.click();
      await expect(toggle).toHaveAttribute("aria-pressed", "true");
      await expect(page.locator("[data-sonner-toast]")).toContainText(
        /已开启联网搜索[\s\S]*后续提问可使用网页搜索和读取。/,
      );
      await page.locator("textarea:visible").fill("合成测试二");
      await page.locator(".cnooc-submit:visible").click();
      await expect.poll(() => sent).toEqual([false, true]);
      await expect(toggle).toBeEnabled();
      await toggle.click();
      await expect(toggle).toHaveAttribute("aria-pressed", "false");
      await expect(page.locator("[data-sonner-toast]")).toContainText(
        /已关闭联网搜索[\s\S]*后续提问将停用网页搜索和读取。/,
      );
      const tools = page.locator(".cnooc-primary-tools:visible");
      const buttons = tools.locator(":scope > button:visible");
      const boxes = await buttons.evaluateAll((elements) =>
        elements.map((el) => {
          const r = el.getBoundingClientRect();
          return { x: r.x, y: r.y, right: r.right, bottom: r.bottom };
        }),
      );
      for (let a = 0; a < boxes.length; a++)
        for (let b = a + 1; b < boxes.length; b++) {
          const x =
            Math.min(boxes[a]!.right, boxes[b]!.right) -
            Math.max(boxes[a]!.x, boxes[b]!.x);
          const y =
            Math.min(boxes[a]!.bottom, boxes[b]!.bottom) -
            Math.max(boxes[a]!.y, boxes[b]!.y);
          expect(x > 1 && y > 1).toBe(false);
        }
      await page.screenshot({ path: info.outputPath("web-toggle.png") });
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth),
      ).toBeLessThanOrEqual(width);
    });
  }
}
