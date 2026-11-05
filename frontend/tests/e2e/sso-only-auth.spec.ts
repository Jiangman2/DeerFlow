import { expect, test } from "@playwright/test";

const ssoOnly = process.env.DEER_FLOW_SSO_ONLY === "true";

test.describe("enterprise SSO-only auth entrypoints", () => {
  test.skip(!ssoOnly, "requires DEER_FLOW_SSO_ONLY=true");

  for (const path of ["/login", "/register", "/setup"]) {
    test(`${path} redirects to the DataLake portal without rendering local credentials`, async ({
      page,
    }) => {
      await page.route("https://datalake.cnooc/**", async (route) => {
        await route.fulfill({
          contentType: "text/html",
          body: "<main data-testid='datalake-portal'>DataLake portal</main>",
        });
      });

      await page.goto(path);

      await expect(page).toHaveURL("https://datalake.cnooc/#/home");
      await expect(page.getByTestId("datalake-portal")).toBeVisible();
      await expect(page.locator("input[type='password']")).toHaveCount(0);
    });
  }
});
