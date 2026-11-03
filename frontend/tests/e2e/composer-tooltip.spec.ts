import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

for (const employee of [false, true]) {
  test(`composer hover hints share attachment appearance ${employee ? "employee" : "main"}`, async ({
    page,
    baseURL,
  }, info) => {
    await page
      .context()
      .addCookies([{ name: "locale", value: "zh-CN", url: baseURL! }]);
    mockLangGraphAPI(page, {
      agents: [{ name: "hover-test", display_name: "合成测试助手" }],
    });
    await page.goto(
      employee
        ? "/workspace/agents/hover-test/chats/new"
        : "/workspace/chats/new",
      { waitUntil: "domcontentloaded" },
    );
    await page.getByTestId("add-attachments-button").hover();
    const tooltip = page.locator('[data-slot="tooltip-content"]:visible');
    await expect(tooltip).toBeVisible();
    const appearance = () =>
      tooltip.evaluate((el) => {
        const s = getComputedStyle(el);
        return [
          s.backgroundColor,
          s.color,
          s.fontSize,
          s.borderRadius,
          s.padding,
        ];
      });
    const attachmentAppearance = await appearance();
    for (const [selector, message] of [
      ["knowledge-scope-trigger", "选择知识库、标签或文件"],
      ["web-search-toggle", "联网搜索已关闭，点击开启"],
    ]) {
      await page.mouse.move(1200, 850, { steps: 10 });
      await expect(tooltip).toBeHidden();
      const trigger = page.getByTestId(selector!);
      await expect(trigger).not.toHaveAttribute("title");
      await trigger.hover();
      await expect(tooltip).toHaveText(new RegExp(message!));
      expect(await appearance()).toEqual(attachmentAppearance);
      await page.screenshot({ path: info.outputPath(`${selector}.png`) });
    }
    await page.getByTestId("web-search-toggle").click();
    await expect(page.getByTestId("web-search-toggle")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await page.mouse.move(1200, 850, { steps: 10 });
    await page.getByTestId("web-search-toggle").hover();
    await expect(tooltip).toContainText("联网搜索已开启，点击关闭");
    await expect(tooltip).toHaveAttribute(
      "data-state",
      /^(instant|delayed)-open$/,
    );
    expect(await appearance()).toEqual(attachmentAppearance);
    await page.getByTestId("knowledge-scope-trigger").click();
    await expect(page.getByTestId("knowledge-scope-menu")).toBeVisible();
  });
}
