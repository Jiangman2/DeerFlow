import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

test.describe("Enterprise entry page", () => {
  test.beforeEach(async ({ page }) => {
    mockLangGraphAPI(page);
  });

  test("opens a ready workspace composer from the root URL", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/workspace\/chats\/new$/);
    await expect(page.getByTestId("welcome-panel")).toBeVisible();
    await expect(page.getByRole("textbox").first()).toBeEditable();
  });

  for (const width of [320, 375, 390]) {
    test(`does not overflow at ${width}px width`, async ({ page }) => {
      await page.setViewportSize({ width, height: 812 });
      await page.goto("/");
      await expect(page).toHaveURL(/\/workspace\/chats\/new$/);
      await expect(page.getByRole("textbox").first()).toBeEditable();
      await expect
        .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
        .toBeLessThanOrEqual(width);
      await expect(page.locator("main").first()).toBeInViewport();
    });
  }

  test("root entry can submit a message in the workspace", async ({ page }) => {
    await page.goto("/");
    const composer = page.getByRole("textbox").first();
    await expect(composer).toBeEditable();
    await composer.fill("Hello");
    await composer.press("Enter");
    await expect(page.getByText("Hello from DeerFlow!")).toBeVisible();
  });
});
