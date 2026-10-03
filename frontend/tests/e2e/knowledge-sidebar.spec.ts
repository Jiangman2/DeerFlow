import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

test("knowledge page embeds the complete same-origin WeKnora workspace", async ({
  page,
}) => {
  mockLangGraphAPI(page);
  await page.route("**/api/v1/auth/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "test-user",
        email: "test@example.com",
        username: "test-user",
        is_active: true,
        is_superuser: false,
      }),
    }),
  );
  await page.route("**/api/v1/auth/setup-status", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ needs_setup: false }),
    }),
  );
  await page.route("**/platform/knowledge-bases", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/html",
      body: "<!doctype html><title>知识库</title><h1>WeKnora</h1>",
    }),
  );

  await page.goto("/workspace/knowledge");

  await expect(
    page.locator('[data-slot="sidebar"][data-state="collapsed"]'),
  ).toBeVisible({ timeout: 15_000 });
  await page.locator(".cnooc-collapsed-brand-slot").hover();
  await page.locator(".cnooc-collapsed-brand-trigger").click();
  await expect(
    page.locator('[data-slot="sidebar"][data-state="expanded"]'),
  ).toBeVisible({ timeout: 15_000 });
  const frame = page.getByTestId("weknora-workspace-frame");
  await expect(frame).toBeVisible({ timeout: 15_000 });
  await expect(frame).toHaveAttribute("src", "/platform/knowledge-bases");
  await expect(
    page.getByRole("heading", { name: "企业知识工作台" }),
  ).toHaveCount(0);
  await expect(page.getByRole("button", { name: "刷新" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "独立窗口" })).toHaveCount(0);
});
