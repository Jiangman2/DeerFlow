import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

const employee = {
  name: "report", display_name: "Report assistant", description: "Reviewed duty",
  skills: [], source: "personal" as const, draft_revision: "reviewed",
  category_id: "comprehensive-management",
};

for (const width of [1280, 390]) {
  test(`delete conflict requires a fresh owner confirmation at ${width}px`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 });
    mockLangGraphAPI(page, { agents: [employee] });
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => {
      if (message.type() === "error" && !message.text().includes("409 (Conflict)")) errors.push(message.text());
    });
    let revision = "reviewed";
    let removed = false;
    const deletes: (string | null)[] = [];
    await page.route("**/api/agents", (route) => route.fulfill({ json: { agents: removed ? [] : [employee] } }));
    await page.route("**/api/agents/report?*", (route) => {
      if (route.request().method() === "GET") return route.fulfill({ json: { ...employee, draft_revision: revision } });
      deletes.push(new URL(route.request().url()).searchParams.get("expected_draft_revision"));
      if (deletes.length === 1) {
        revision = "newer";
        return route.fulfill({ status: 409, json: { detail: "Draft changed" } });
      }
      removed = true;
      return route.fulfill({ status: 204 });
    });
    await page.goto("/workspace/agents");
    await expect(page).toHaveURL(/\/workspace\/agents$/);
    expect(await page.title()).not.toBe("");
    await page.getByRole("tab", { name: "My digital employees" }).click();
    const card = page.getByTestId("digital-employee-card");
    await expect(card).toHaveCount(1);
    await card.getByRole("button", { name: "More actions" }).click();
    await page.getByRole("menuitem", { name: "Delete", exact: true }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog.getByText("Reviewed duty", { exact: true })).toBeVisible();
    await dialog.getByRole("button", { name: "Delete", exact: true }).click();
    await expect(dialog.getByRole("alert")).toContainText("Deletion was rejected");
    await expect(dialog.getByRole("button", { name: "Delete", exact: true })).toBeDisabled();
    expect(deletes).toEqual(["reviewed"]);
    await expect(card).toHaveCount(1);
    await expect(page.locator("nextjs-portal").getByText("Runtime Error", { exact: false })).toHaveCount(0);
    expect(errors).toEqual([]);
    await page.screenshot({ path: info.outputPath(`employee-delete-conflict-${width}.png`) });
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await card.getByRole("button", { name: "More actions" }).click();
    await page.getByRole("menuitem", { name: "Delete", exact: true }).click();
    await expect(dialog.getByRole("button", { name: "Delete", exact: true })).toBeEnabled();
    await dialog.getByRole("button", { name: "Delete", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expect(card).toHaveCount(0);
    expect(deletes).toEqual(["reviewed", "newer"]);
    expect(errors).toEqual([]);
  });
}

test("missing personal revision never enables deletion", async ({ page }) => {
  mockLangGraphAPI(page, { agents: [employee] });
  const deletes: string[] = [];
  await page.route("**/api/agents/report?*", (route) => {
    if (route.request().method() === "DELETE") deletes.push(route.request().url());
    return route.fulfill({ json: { ...employee, draft_revision: null } });
  });
  await page.goto("/workspace/agents");
  await page.getByRole("tab", { name: "My digital employees" }).click();
  await page.getByTestId("digital-employee-card").getByRole("button", { name: "More actions" }).click();
  await page.getByRole("menuitem", { name: "Delete", exact: true }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog.getByRole("alert")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Delete", exact: true })).toBeDisabled();
  await dialog.getByRole("button", { name: "Cancel" }).click();
  expect(deletes).toEqual([]);
});
