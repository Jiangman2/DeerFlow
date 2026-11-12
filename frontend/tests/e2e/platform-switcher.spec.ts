import { expect, test } from "@playwright/test";

for (const viewport of [
  { width: 1280, height: 800 },
  { width: 320, height: 720 },
  { width: 375, height: 812 },
  { width: 390, height: 844 },
  { width: 412, height: 915 },
  { width: 812, height: 375 },
]) {
  test(`platform switching retains iframe state at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    let requests = 0;
    await page.route("https://heq.cnooc.com.cn/**", async (route) => {
      requests++;
      await route.fulfill({
        contentType: "text/html",
        body: '<html><body><input aria-label="Draft" /></body></html>',
      });
    });
    await page.goto("/workspace/chats/new");
    await expect(page.getByTestId("platform-a-panel")).toBeVisible();
    await expect(page.getByTestId("haineng-frame")).toHaveCount(0);
    const originalUrl = page.url();
    await page.getByTestId("platform-b-switch").click();
    const draft = page.frameLocator('[data-testid="haineng-frame"]').getByLabel("Draft");
    await draft.fill("Keep my conversation draft");
    await expect(page.getByTestId("platform-a-panel")).toBeHidden();
    await page.getByTestId("platform-a-switch").click();
    await expect(page.getByTestId("haineng-frame")).toHaveCount(1);
    await expect(page.getByTestId("platform-a-panel")).toBeVisible();
    await page.getByTestId("platform-b-switch").click();
    await expect(draft).toHaveValue("Keep my conversation draft");
    expect(requests).toBe(1);
    expect(page.url()).toBe(originalUrl);
  });
}
