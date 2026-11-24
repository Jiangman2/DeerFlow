import { expect, test } from "@playwright/test";

for (const width of [1280, 320, 375, 390, 412]) {
  test(`Haineng chat keeps state across platform switches at ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 844 });
    const requests: Record<string, unknown>[] = [];
    await page.route("**/api/haineng/chat", async (route) => {
      requests.push(route.request().postDataJSON() as Record<string, unknown>);
      await route.fulfill({
        contentType: "text/event-stream",
        body:
          'data:{"header":{"code":0},"payload":{"sessionId":"test-session","data":{"choices":{"text":"你好"}}}}\n\n' +
          'data:{"header":{"code":10000}}\n\n',
      });
    });
    await page.goto("/workspace/chats/new");
    await expect(page.getByTestId("platform-a-panel")).toBeVisible();
    await page.getByTestId("platform-b-switch").click();
    const panel = page.getByTestId("platform-b-panel");
    const input = panel.locator("textarea");
    await input.fill("第一个问题");
    await panel.locator('button[type="submit"]').click();
    await expect(panel).toContainText("你好");
    await input.fill("草稿");
    await page.getByTestId("platform-a-switch").click();
    await page.getByTestId("platform-b-switch").click();
    await expect(input).toHaveValue("草稿");
    await panel.locator('button[type="submit"]').click();
    await expect.poll(() => requests.length).toBe(2);
    expect(requests[1]?.sessionId).toBe("test-session");
  });
}
