import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

test("homepage shows Haineng links, model usage and contact details", async ({
  page,
}) => {
  mockLangGraphAPI(page);
  await page.route("**/api/home/statistics", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ todayUsers: 2, todayTasks: 4 }),
    }),
  );
  await page.route("**/integrations/haineng/model-call-count", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        todayModelCallCount: 12,
        totalModelCallCount: 1234,
      }),
    }),
  );

  await page.goto("/workspace/chats/new");

  await expect(page.locator(".cnooc-haineng-portal-link")).toHaveAttribute(
    "href",
    "https://ai.cnooc.com.cn/",
  );
  await expect(page.locator(".cnooc-home-usage")).toContainText("1,234");
  await expect(page.locator(".cnooc-home-usage")).toContainText("12");
  await expect(page.locator(".cnooc-home-usage > span")).toHaveCount(4);
  await expect(page.locator(".cnooc-home-usage")).toContainText(
    /今日用户|Today's users/,
  );
  await expect(page.locator(".cnooc-home-usage")).toContainText(
    /今日任务数|Today's tasks/,
  );

  await page.locator(".cnooc-home-contact button").hover();
  await expect
    .poll(() =>
      page
        .locator(".cnooc-home-footer")
        .evaluate((footer) => Number(getComputedStyle(footer).zIndex)),
    )
    .toBeGreaterThan(30);
  await expect(page.getByText("sh_tyznt@cnooc.com.cn")).toBeVisible();
  await expect(page.getByText("sh_ai@cnooc.com.cn")).toBeVisible();
  await expect(page.locator(".cnooc-home-contact-details > div")).toHaveCount(
    3,
  );
  await expect(page.getByText("8:30 ～ 17:30")).toBeVisible();
  await expect(
    page.locator(".cnooc-home-contact-card-header a"),
  ).toHaveAttribute("href", /mailto:sh_tyznt@cnooc\.com\.cn\?subject=/);
  await expect(page.locator(".cnooc-home-support-qr img")).toBeVisible();
  await page.mouse.move(0, 0);
  await expect(page.locator(".cnooc-home-contact-card")).toBeHidden();
  await page.locator(".cnooc-home-contact button").click();
  await page.mouse.move(0, 0);
  await expect(page.locator(".cnooc-home-contact-card")).toBeHidden();

  const agentSquare = page.getByRole("link", {
    name: /智能体广场|Agent Square/,
  });
  await expect(agentSquare).toBeVisible();
  await expect(agentSquare.locator(":scope > span:last-child")).toBeVisible();
  await expect(
    agentSquare.locator(".cnooc-sidebar-icon-tile svg"),
  ).toBeVisible();
  if (await page.locator('[data-collapsible="icon"]').count()) {
    const icon = await agentSquare
      .locator(".cnooc-sidebar-icon-tile svg")
      .boundingBox();
    const label = await agentSquare
      .locator(":scope > span:last-child")
      .boundingBox();
    expect(icon).not.toBeNull();
    expect(label).not.toBeNull();
    expect(icon!.y).toBeLessThan(label!.y);
  }
  await expect(agentSquare).toHaveAttribute(
    "href",
    "https://ai-dev.cnooc.com.cn/#/inferencePlatform/hyAppSquare",
  );
});
