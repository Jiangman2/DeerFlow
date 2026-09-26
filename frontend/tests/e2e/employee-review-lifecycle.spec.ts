import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

test("conversational edits require creator confirmation and retain conflict evidence", async ({
  page,
}, info) => {
  mockLangGraphAPI(page);
  let approval: unknown = null;
  const path = "**/api/agents/report/proposals/proposal-one";
  await page.route(path, (route) =>
    route.fulfill({
      json: {
        id: "proposal-one",
        name: "report",
        state: "pending",
        expected_draft_revision: "original",
        current_draft_revision: "original",
        conflict: false,
        before: { description: "Original duty" },
        updates: { description: "Proposed duty" },
        before_soul: "Original SOUL",
        soul: "Proposed SOUL",
      },
    }),
  );
  await page.route(path + "/decision", (route) => {
    approval = route.request().postDataJSON();
    return route.fulfill({
      status: 409,
      json: {
        detail:
          "Employee was edited after this proposal; no stale changes were applied",
      },
    });
  });
  await page.goto("/workspace/agents/manage/report/proposals/proposal-one");
  const button = page.getByRole("button", { name: "Confirm draft changes" });
  await expect(page.getByText("Original SOUL", { exact: true })).toBeVisible();
  await expect(page.getByText("Proposed SOUL", { exact: true })).toBeVisible();
  await expect(button).toBeDisabled();
  expect(approval).toBeNull();
  await page.getByRole("checkbox").check();
  await button.click();
  await expect(
    page.getByRole("alert").filter({ hasText: "no stale changes" }),
  ).toBeVisible();
  expect(approval).toEqual({
    approve: true,
    expected_draft_revision: "original",
  });
  await expect(button).toBeDisabled();
  await page.screenshot({
    path: info.outputPath("employee-proposal-conflict.png"),
  });
});

test("knowledge approval is explicit and revocation requires a second action", async ({
  page,
}, info) => {
  mockLangGraphAPI(page);
  let state = "pending",
    revision = "first";
  const mutations: Record<string, unknown>[] = [];
  await page.route("**/api/employee-resource-grants/approval-one", (route) =>
    route.fulfill({
      json: {
        grant: {
          id: "approval-one",
          revision,
          state,
          publisher: "alice",
          publication_id: "employee-identity",
          resource: {
            kind: "knowledge",
            tenant_id: "tenant",
            kb_id: "approved-kb",
          },
          expires_at: null,
        },
        can_decide: state === "pending",
        can_revoke: state === "approved",
      },
    }),
  );
  await page.route(
    "**/api/employee-resource-grants/approval-one/*",
    (route) => {
      mutations.push(route.request().postDataJSON() as Record<string, unknown>);
      state = route.request().url().endsWith("/revoke")
        ? "revoked"
        : "approved";
      revision = "second";
      return route.fulfill({ json: {} });
    },
  );
  await page.goto("/workspace/agents/grants/approval-one");
  await expect(
    page.getByRole("button", { name: "Approve", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByText("Only the current knowledge owner", { exact: false }),
  ).toBeVisible();
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Revoke approval", exact: true }),
  ).toBeVisible();
  expect(mutations[0]).toMatchObject({
    expected_revision: "first",
    approve: true,
    acknowledge_onward_sharing: true,
  });
  await page
    .getByRole("button", { name: "Revoke approval", exact: true })
    .click();
  expect(mutations).toHaveLength(1);
  await page.screenshot({
    path: info.outputPath("employee-knowledge-revocation.png"),
  });
  await page
    .getByRole("button", { name: "Confirm revocation", exact: true })
    .click();
  await expect(page.getByText("revoked", { exact: true })).toBeVisible();
  expect(mutations[1]).toEqual({ expected_revision: "second" });
});

test("restoring a version only updates the draft and never automatically publishes", async ({
  page,
}, info) => {
  mockLangGraphAPI(page);
  const mutations: string[] = [];
  await page.route("**/api/agents/report?source=personal", (route) =>
    route.fulfill({
      json: {
        name: "report",
        description: "Existing employee",
        source: "personal",
        draft_revision: "current",
      },
    }),
  );
  await page.route("**/api/agents/report/publications", (route) =>
    route.fulfill({
      json: {
        revisions: [
          {
            revision: "prior-version",
            state: "published",
            created_at: "2026-09-23T08:00:00Z",
          },
        ],
      },
    }),
  );
  await page.route("**/api/agents/report/proposals", (route) =>
    route.fulfill({ json: { proposals: [] } }),
  );
  await page.route(
    "**/api/agents/report/publications/prior-version/restore-draft",
    (route) => {
      mutations.push(route.request().url());
      expect(route.request().postDataJSON()).toEqual({
        expected_draft_revision: "current",
      });
      return route.fulfill({
        json: { draft_revision: "restored", publication_changed: false },
      });
    },
  );
  await page.route("**/api/agents/report/publish", () => {
    throw new Error("Unexpected automatic publication");
  });
  await page.goto("/workspace/agents/manage/report");
  await page.getByRole("button", { name: "Restore to draft…" }).click();
  expect(mutations).toHaveLength(0);
  await expect(page.getByRole("dialog")).toContainText(
    "unknown settings, memory and uploaded avatar",
  );
  await page.screenshot({
    animations: "disabled",
    path: info.outputPath("employee-version-restore.png"),
  });
  await page
    .getByRole("button", { name: "Confirm restore", exact: true })
    .click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  expect(mutations).toHaveLength(1);
});
