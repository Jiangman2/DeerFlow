import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

test("homepage shows Haineng links, model usage and contact details", async ({
  page,
}) => {
  mockLangGraphAPI(page);
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
  const address = await page.locator(".cnooc-home-address").boundingBox();
  const usage = await page.locator(".cnooc-home-usage").boundingBox();
  expect(address).not.toBeNull();
  expect(usage).not.toBeNull();
  expect(Math.abs(address!.y - usage!.y)).toBeLessThan(8);

  await page.locator(".cnooc-home-contact button").hover();
  await expect
    .poll(() =>
      page
        .locator(".cnooc-home-footer")
        .evaluate((footer) => Number(getComputedStyle(footer).zIndex)),
    )
    .toBeGreaterThan(30);
  await expect(page.getByText("shiyl19@cnooc.com.cn")).toBeVisible();
  await expect(page.getByText("姜彬")).toBeVisible();
  await expect(page.getByText("18810388532")).toBeVisible();
  await expect(page.getByText("8:30 ～ 17:30")).toBeVisible();
  await expect(
    page.locator(".cnooc-home-contact-card-header a"),
  ).toHaveAttribute("href", /mailto:shiyl19@cnooc\.com\.cn\?subject=/);
  await expect(page.locator(".cnooc-home-contact-card")).not.toContainText(
    "服务群",
  );
  await page.locator(".cnooc-home-address").hover();
  await expect(page.locator(".cnooc-home-contact-card")).toBeHidden();
  await page.locator(".cnooc-home-contact button").click();
  await page.locator(".cnooc-home-address").hover();
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
