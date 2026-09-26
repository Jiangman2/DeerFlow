import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

const employee = {
  name: "report-helper",
  display_name: "Report Helper",
  description: "cached description",
  skills: [],
  source: "personal" as const,
};

test("editing loads the current private revision and a conflict retains unsaved input", async ({
  page,
}, testInfo) => {
  mockLangGraphAPI(page, { agents: [employee] });
  let writes = 0;
  let submitted: Record<string, unknown> | undefined;
  await page.route("**/api/agents/report-helper?source=personal", (route) =>
    route.fulfill({
      json: {
        ...employee,
        description: "fresh description",
        draft_revision: "draft-1",
      },
    }),
  );
  await page.route("**/api/agents/report-helper", (route) => {
    if (route.request().method() !== "PUT") return route.fallback();
    writes++;
    submitted = route.request().postDataJSON() as Record<string, unknown>;
    return route.fulfill({
      status: 409,
      json: { detail: "Employee draft changed" },
    });
  });
  await page.goto("/workspace/agents");
  await expect(page).toHaveURL(/workspace\/agents/);
  await page.getByRole("tab", { name: "My digital employees" }).click();
  await page
    .getByTestId("digital-employee-card")
    .first()
    .getByRole("button", { name: "More actions" })
    .click();
  await page.getByRole("menuitem", { name: "Edit details" }).click();
  const dialog = page.getByRole("dialog");
  const description = dialog.locator("#employee-description");
  await expect(description).toHaveValue("fresh description");
  await description.fill("my unsaved change");
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText(
    "Your input has been kept",
  );
  await expect(description).toHaveValue("my unsaved change");
  expect(submitted?.expected_draft_revision).toBe("draft-1");
  expect(writes).toBe(1);
  await expect(
    dialog.getByRole("button", { name: "Save", exact: true }),
  ).toBeDisabled();
  await page.screenshot({
    path: testInfo.outputPath("employee-draft-conflict.png"),
  });
});
