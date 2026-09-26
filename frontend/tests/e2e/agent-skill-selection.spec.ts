import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

const employee = {
  name: "report-helper",
  display_name: "Report Helper",
  description: "Reports",
  skills: ["saved-missing"],
  source: "personal" as const,
};
const skills = [
  { name: "report", description: "Reports", enabled: true, category: "custom" },
  {
    name: "disabled-private",
    description: "Disabled",
    enabled: false,
    category: "custom",
  },
];

test("creator explicitly selects enabled skills and forwards the form choice", async ({
  page,
}, testInfo) => {
  mockLangGraphAPI(page, { skills });
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  let submitted: Record<string, unknown> | undefined;
  await page.route("**/api/langgraph/threads/*/runs/stream", (route) => {
    submitted = route.request().postDataJSON() as Record<string, unknown>;
    return route.fallback();
  });
  await page.route("**/api/agents/check?*", (route) =>
    route.fulfill({ json: { name: "report-helper", available: true } }),
  );
  await page.goto("/workspace/agents/new");
  const selector = page.getByTestId("agent-skill-selection");
  await selector.getByRole("radio", { name: "Choose explicitly" }).click();
  await selector.getByRole("checkbox", { name: /^report/ }).check();
  await expect(selector.getByRole("checkbox")).toHaveCount(1);
  await expect(selector.getByRole("checkbox")).toBeChecked();
  await page.setViewportSize({ width: 390, height: 844 });
  await selector.scrollIntoViewIfNeeded();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("creator-skill-selection-mobile.png"),
  });
  await page.locator('input[maxlength="64"]').fill("report-helper");
  await page.getByRole("button", { name: /Continue/ }).click();
  await expect.poll(() => submitted).toBeDefined();
  expect(
    (submitted?.context as Record<string, unknown>).agent_skill_selection,
  ).toEqual(["report"]);
  expect(errors).toEqual([]);
});

test("editor preserves missing skills until an explicit change and saves an empty selection", async ({
  page,
}, testInfo) => {
  mockLangGraphAPI(page, { agents: [employee], skills });
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  let update: Record<string, unknown> | undefined;
  await page.route("**/api/agents/report-helper", (route) => {
    if (route.request().method() !== "PUT") return route.fallback();
    update = route.request().postDataJSON() as Record<string, unknown>;
    return route.fulfill({ json: { ...employee, ...update } });
  });
  await page.goto("/workspace/agents");
  await page.getByRole("tab", { name: "My digital employees" }).click();
  await page
    .getByTestId("digital-employee-card")
    .first()
    .getByRole("button", { name: "More actions" })
    .click();
  await page.getByRole("menuitem", { name: "Edit details" }).click();
  const dialog = page.getByRole("dialog");
  const selector = dialog.getByTestId("agent-skill-selection");
  await expect(
    selector.getByRole("checkbox", { name: /saved-missing/ }),
  ).toBeChecked();
  await expect(selector.getByText(/Disabled or unavailable/)).toBeVisible();
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => update).toBeDefined();
  expect(update).not.toHaveProperty("skills");
  expect(update).not.toHaveProperty("model");
  await page
    .getByTestId("digital-employee-card")
    .first()
    .getByRole("button", { name: "More actions" })
    .click();
  await page.getByRole("menuitem", { name: "Edit details" }).click();
  // Removing an unavailable dependency removes its row immediately.
  await selector.getByRole("checkbox", { name: /saved-missing/ }).click();
  await expect(
    selector.getByRole("checkbox", { name: /saved-missing/ }),
  ).toHaveCount(0);
  await expect(selector.getByText(/0\/128/)).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("employee-skills-editor-desktop.png"),
  });
  update = undefined;
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => update).toBeDefined();
  expect(update).toHaveProperty("skills", []);
  expect(errors).toEqual([]);
});

test("catalog failure retains saved skill names and reports a visible error", async ({
  page,
}) => {
  mockLangGraphAPI(page, { agents: [employee] });
  await page.route("**/api/skills", (route) =>
    route.fulfill({ status: 503, json: { detail: "unavailable" } }),
  );
  await page.goto("/workspace/agents");
  await page.getByRole("tab", { name: "My digital employees" }).click();
  await page
    .getByTestId("digital-employee-card")
    .first()
    .getByRole("button", { name: "More actions" })
    .click();
  await page.getByRole("menuitem", { name: "Edit details" }).click();
  const selector = page.getByTestId("agent-skill-selection");
  await expect(selector.getByRole("alert")).toContainText(
    "Your selection was preserved",
  );
  await expect(
    selector.getByRole("checkbox", { name: /saved-missing/ }),
  ).toBeChecked();
});
