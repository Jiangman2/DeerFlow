import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

for (const width of [1280, 390]) {
  test(`avatar conflict preserves the reviewed edit at ${width}px`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 });
    mockLangGraphAPI(page);
    const failures: string[] = [];
    page.on("pageerror", (error) => failures.push(error.message));
    page.on("console", (message) => {
      if (message.type() === "error" && !message.text().includes("409 (Conflict)")) failures.push(message.text());
    });
    const employee = { name: "report", display_name: "Report assistant", description: "Original duty", skills: [], knowledge_scope: null, source: "personal", draft_revision: "reviewed", category_id: "comprehensive-management" };
    const updates: Record<string, unknown>[] = [];
    const avatarRevisions: (string | null)[] = [];
    await page.route("**/api/agents/report?source=personal", (route) => route.fulfill({ json: employee }));
    await page.route("**/api/agents/report/publications", (route) => route.fulfill({ json: { revisions: [] } }));
    await page.route("**/api/agents/report/proposals", (route) => route.fulfill({ json: { proposals: [] } }));
    await page.route("**/api/agents/report", (route) => {
      updates.push(route.request().postDataJSON() as Record<string, unknown>);
      return route.fulfill({ json: { ...employee, draft_revision: "details-saved" } });
    });
    await page.route("**/api/agents/report/avatar?*", (route) => {
      avatarRevisions.push(new URL(route.request().url()).searchParams.get("expected_draft_revision"));
      return route.fulfill({ status: 409, json: { detail: "Another editor saved a newer avatar" } });
    });
    await page.goto("/workspace/agents/manage/report");
    await expect(page).toHaveURL(/\/workspace\/agents\/manage\/report$/);
    expect(await page.title()).not.toBe("");
    await page.getByRole("button", { name: "Edit draft", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("heading", { name: "Edit digital employee" })).toBeVisible();
    await dialog.locator("textarea").fill("My reviewed duty");
    await dialog.locator('input[type="file"]').setInputFiles({
      name: "portrait.png", mimeType: "image/png",
      buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=", "base64"),
    });
    await dialog.getByRole("button", { name: "Save", exact: true }).click();
    await expect(dialog.getByRole("alert").filter({ hasText: "details were saved" })).toBeVisible();
    await expect(dialog.getByRole("alert").filter({ hasText: "another session" })).toBeVisible();
    await expect(dialog.locator("textarea")).toHaveValue("My reviewed duty");
    await expect(dialog.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
    expect(updates).toHaveLength(1);
    expect(updates[0]).toMatchObject({ expected_draft_revision: "reviewed", description: "My reviewed duty" });
    expect(avatarRevisions).toEqual(["details-saved"]);
    expect(failures).toEqual([]);
    await expect(page.locator("nextjs-portal").getByText("Runtime Error", { exact: false })).toHaveCount(0);
    await dialog.getByRole("alert").first().scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath(`employee-avatar-conflict-${width}.png`) });
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await page.getByRole("button", { name: "Edit draft", exact: true }).click();
    await expect(dialog.locator("textarea")).toHaveValue("Original duty");
    await expect(dialog.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
  });
}
