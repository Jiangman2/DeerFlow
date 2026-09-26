import { expect, test } from "@playwright/test";
import { mockLangGraphAPI } from "./utils/mock-api";

test("publication requires current resource approvals and explicit confirmation", async ({
  page,
}, testInfo) => {
  const employee = {
    name: "report-helper",
    display_name: "Report Helper",
    description: "reports",
    skills: ["report"],
    source: "personal" as const,
    draft_revision: "draft-reviewed",
  };
  mockLangGraphAPI(page, { agents: [employee] });
  let approved = false;
  let publication: Record<string, unknown> | null = null;
  const resource = {
    kind: "skill",
    skill_name: "report",
    content_digest: "a".repeat(64),
  };
  await page.route("**/api/agents/report-helper?source=personal", (route) =>
    route.fulfill({ json: employee }),
  );
  await page.route("**/api/agents/report-helper/publication-check", (route) =>
    route.fulfill({
      json: {
        publication_id: "employee-id",
        draft_revision: employee.draft_revision,
        resources: [
          {
            resource,
            grants: approved
              ? [
                  {
                    id: "approved-grant",
                    state: "approved",
                    expires_at: "2099-01-01T00:00:00Z",
                  },
                ]
              : [],
          },
        ],
      },
    }),
  );
  await page.route("**/api/agents/report-helper/publish", (route) => {
    publication = route.request().postDataJSON() as Record<string, unknown>;
    return route.fulfill({ json: employee });
  });
  await page.goto("/workspace/agents");
  await page.getByRole("tab", { name: "My digital employees" }).click();
  await page
    .getByTestId("digital-employee-card")
    .first()
    .getByRole("button", { name: "More actions" })
    .click();
  await page
    .getByRole("menuitem", { name: "Publish to Digital Employee Square" })
    .click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Invocation approval required")).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "Confirm publication" }),
  ).toBeDisabled();
  expect(publication).toBeNull();
  approved = true;
  await dialog.getByRole("button", { name: "Refresh checks" }).click();
  await expect(dialog.getByText("Approved", { exact: true })).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "Confirm publication" }),
  ).toBeDisabled();
  await dialog.getByRole("checkbox").check();
  await page.screenshot({
    path: testInfo.outputPath("employee-publication-review.png"),
  });
  await dialog.getByRole("button", { name: "Confirm publication" }).click();
  await expect(dialog).not.toBeVisible();
  expect(publication).toMatchObject({
    expected_draft_revision: employee.draft_revision,
    grant_ids: ["approved-grant"],
  });
});
