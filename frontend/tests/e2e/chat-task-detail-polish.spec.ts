import { expect, test } from "@playwright/test";

import { MOCK_THREAD_ID, mockLangGraphAPI } from "./utils/mock-api";

for (const viewport of [
  { width: 320, height: 720 },
  { width: 375, height: 812 },
  { width: 390, height: 844 },
  { width: 412, height: 915 },
  { width: 740, height: 360 },
]) {
  test(`answer actions and task form remain usable at ${viewport.width}×${viewport.height}`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Preview conversation",
          messages: [
            { type: "human", id: "question", content: "Explain the task" },
            {
              type: "ai",
              id: "answer",
              content: "This is the complete answer.",
            },
          ],
        },
      ],
      scheduledTasks: [],
    });
    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    const actions = page
      .locator(".cnooc-message-actions")
      .filter({ has: page.getByRole("button", { name: /copy/i }) })
      .last();
    await expect(actions).toHaveCSS("opacity", "1");
    const buttons = await actions.getByRole("button").all();
    for (const button of buttons) {
      const box = await button.boundingBox();
      expect(box?.width).toBeGreaterThanOrEqual(44);
      expect(box?.height).toBeGreaterThanOrEqual(44);
    }
    await page.goto("/workspace/scheduled-tasks");
    await page
      .getByRole("button", { name: "Create scheduled task", exact: true })
      .first()
      .click();
    const recipes = page.getByTestId("schedule-recipes");
    await expect(recipes.locator("[aria-expanded]")).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    await recipes.locator("[aria-expanded]").click();
    await expect(recipes.locator("[aria-expanded]")).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    await recipes.locator("[aria-expanded]").click();
    await page.getByLabel("Task title", { exact: true }).fill("A local task");
    await page
      .getByLabel("Prompt", { exact: true })
      .fill("Summarize the supplied documents");
    await expect(
      page.getByTestId("scheduled-task-create-actions"),
    ).toBeInViewport();
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
      .toBeLessThanOrEqual(viewport.width);
    await page.goto("/workspace/agents/new");
    await expect(
      page.getByLabel("Display name", { exact: true }),
    ).toBeEditable();
    const fields = page.getByTestId("agent-create-scroll-region");
    await expect
      .poll(async () => (await fields.boundingBox())?.height ?? 0)
      .toBeGreaterThan(120);
    await expect(
      page.getByRole("button", { name: "Continue", exact: true }),
    ).toBeInViewport();
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
      .toBeLessThanOrEqual(viewport.width);
  });
}

test("loading and unavailable tasks never masquerade as an empty list", async ({
  page,
}) => {
  mockLangGraphAPI(page, { scheduledTasks: [] });
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let failed = true;
  await page.route("**/api/scheduled-tasks", async (route) => {
    await gate;
    await route.fulfill({
      status: failed ? 503 : 200,
      contentType: "application/json",
      body: failed ? JSON.stringify({ detail: "Unavailable" }) : "[]",
    });
  });
  await page.goto("/workspace/scheduled-tasks");
  await expect(page.getByRole("status")).toContainText(
    "Loading scheduled tasks",
  );
  await expect(page.getByText("No scheduled tasks yet")).toHaveCount(0);
  release();
  await expect(
    page
      .getByRole("alert")
      .filter({ hasText: "Failed to load scheduled tasks" }),
  ).toContainText("Failed to load scheduled tasks", { timeout: 20000 });
  await expect(page.getByText("No scheduled tasks yet")).toHaveCount(0);
  failed = false;
  await page.getByRole("button", { name: "Reload", exact: true }).click();
  await expect(page.getByText("No scheduled tasks yet")).toBeVisible();
});
