import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

// Enterprise deployments enter the workspace instead of exposing public docs.
// The localized link helpers remain covered by their unit tests.
test.describe("Enterprise documentation entry", () => {
  for (const path of [
    "/en/docs/introduction/core-concepts",
    "/zh/docs/introduction/core-concepts",
    "/en/docs/application/workspace-usage",
  ]) {
    test(`${path} redirects to the workspace`, async ({ page }) => {
      mockLangGraphAPI(page);
      await page.goto(path);
      await expect(page).toHaveURL(/\/workspace\/chats\/new$/);
      await expect(page.getByTestId("welcome-panel")).toBeVisible();
      await expect(page.getByRole("textbox").first()).toBeEditable();
    });
  }
});
