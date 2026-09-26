import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

// Browser plugin not available: use the repository Playwright workflow.
// Flow: directory -> publish/withdraw -> stale revision rejected -> explicit retry.
const EMPLOYEE = {
  name: "retained-employee",
  display_name: "Existing Employee",
  description: "Original employee remains available",
  source: "personal" as const,
  skills: [],
  publication_revision: "revision-one",
};

test("publication conflict refreshes receipts but never automatically overwrites", async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  const consoleErrors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  mockLangGraphAPI(page, { agents: [EMPLOYEE] });
  let revision = "revision-one";
  await page.route("**/api/agents/retained-employee?source=personal", (route) =>
    route.fulfill({
      json: {
        ...EMPLOYEE,
        publication_revision: revision,
        draft_revision: "draft-reviewed",
      },
    }),
  );
  await page.route(
    "**/api/agents/retained-employee/publication-check",
    (route) =>
      route.fulfill({
        json: {
          draft_revision: "draft-reviewed",
          publication_id: "employee-id",
          resources: [],
        },
      }),
  );
  const requests: Array<{ expected_revision: string }> = [];
  await page.route("**/api/agents", (route) =>
    route.fulfill({
      json: { agents: [{ ...EMPLOYEE, publication_revision: revision }] },
    }),
  );
  await page.route("**/api/agents/retained-employee/publish", (route) => {
    requests.push(
      route.request().postDataJSON() as { expected_revision: string },
    );
    if (requests.length === 1) {
      revision = "revision-two";
      return route.fulfill({
        status: 409,
        json: { detail: "Publication changed; reload" },
      });
    }
    return route.fulfill({
      json: {
        ...EMPLOYEE,
        source: "team",
        publication_revision: "revision-three",
      },
    });
  });
  await page.goto("/workspace/agents");
  await expect(page).toHaveURL(/\/workspace\/agents$/);
  await expect(page).not.toHaveTitle("");
  await page.getByRole("tab", { name: /My digital employees/i }).click();
  const card = page
    .getByTestId("digital-employee-card")
    .filter({ hasText: "Existing Employee" });
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: /More/i }).click();
  const refreshed = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/agents") &&
      response.request().method() === "GET",
  );
  await page
    .getByRole("menuitem", { name: "Publish to Digital Employee Square" })
    .click();
  const review = page.getByRole("dialog");
  await review.getByRole("checkbox").check();
  await review.getByRole("button", { name: "Confirm publication" }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "Publication changed; reload" }),
  ).toBeVisible();
  await refreshed;
  expect(requests).toEqual([
    {
      overwrite: true,
      expected_revision: "revision-one",
      expected_draft_revision: "draft-reviewed",
      grant_ids: [],
    },
  ]);
  await expect(card).toBeVisible();
  await expect(review.getByRole("alert")).toBeVisible();
  await expect(
    review.getByRole("button", { name: "Confirm publication" }),
  ).toBeDisabled();
  await page.screenshot({
    path: testInfo.outputPath("publication-conflict.png"),
    fullPage: false,
  });
  await review.getByRole("button", { name: "Refresh checks" }).click();
  await review.getByRole("checkbox").check();
  await review.getByRole("button", { name: "Confirm publication" }).click();
  await expect(review).not.toBeVisible();
  expect(requests).toHaveLength(2);
  expect(requests[1]?.expected_revision).toBe("revision-two");
  expect(errors).toEqual([]);
  // The deliberately rejected publication is the only permitted HTTP error.
  expect(
    consoleErrors.filter((message) => !message.includes("409 (Conflict)")),
  ).toEqual([]);
  await expect(page.locator("nextjs-error-overlay")).toHaveCount(0);
});

test("withdrawal requires confirmation and sends the displayed revision", async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const shared = {
    ...EMPLOYEE,
    source: "team" as const,
    published_by_current_user: true,
  };
  mockLangGraphAPI(page, { agents: [EMPLOYEE], teamAgents: [shared] });
  let withdrawn = false;
  await page.route("**/api/team-agents", (route) =>
    route.fulfill({ json: { agents: withdrawn ? [] : [shared] } }),
  );
  const requests: string[] = [];
  await page.route("**/api/team-agents/retained-employee?*", (route) => {
    requests.push(route.request().url());
    withdrawn = true;
    return route.fulfill({ status: 204 });
  });
  await page.goto("/workspace/agents");
  await page
    .getByPlaceholder(/Search name, tag, business domain, or capability/i)
    .fill("Existing Employee");
  const card = page
    .getByTestId("digital-employee-card")
    .filter({ hasText: "Existing Employee" });
  await card.getByRole("button", { name: /More/i }).click();
  await page
    .getByRole("menuitem", { name: "Remove from Digital Employee Square" })
    .click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  expect(requests).toHaveLength(0);
  await page.screenshot({
    path: testInfo.outputPath("withdrawal-confirmation.png"),
    fullPage: false,
  });
  await dialog
    .getByRole("button", { name: "Remove from Digital Employee Square" })
    .click();
  await expect(dialog).toBeHidden();
  await expect(card).toHaveCount(0);
  expect(requests).toHaveLength(1);
  expect(new URL(requests[0]!).searchParams.get("expected_revision")).toBe(
    "revision-one",
  );
  await page.getByRole("tab", { name: /My digital employees/i }).click();
  await expect(card).toBeVisible();
  expect(errors).toEqual([]);
});
