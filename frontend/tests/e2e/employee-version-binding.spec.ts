import { expect, test } from "@playwright/test";

import { mockLangGraphAPI, MOCK_THREAD_ID } from "./utils/mock-api";

test("existing conversation shows pinned version and starts an independent latest-version chat", async ({
  page,
}, info) => {
  const team = {
    name: "reports",
    source: "team" as const,
    display_name: "Shared Reporter",
    skills: [],
  };
  mockLangGraphAPI(page, {
    teamAgents: [team],
    threads: [
      {
        thread_id: MOCK_THREAD_ID,
        agent_name: "reports",
        title: "Existing report",
        metadata: { agent_source: "team" },
      },
    ],
  });
  await page.route("**/api/agents/reports?source=team", (route) =>
    route.fulfill({ json: team }),
  );
  await page.route("**/api/threads/*/employee-publication", (route) =>
    route.fulfill({
      json: {
        bound: true,
        name: "reports",
        revision: "old-version-123456",
        current_revision: "new-version-654321",
        update_available: true,
        knowledge_mode: "selected",
      },
    }),
  );
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(
    `/workspace/agents/reports/chats/${MOCK_THREAD_ID}?source=team`,
  );
  await expect(page).toHaveTitle(/中国海油通用智能体$/);
  const notice = page.getByTestId("employee-version-notice");
  await expect(notice).toContainText("old-version-");
  await expect(notice).toContainText("A new version is available");
  await page.screenshot({
    path: info.outputPath("employee-pinned-version.png"),
  });
  await page.getByTestId("agent-new-chat-button").click();
  await expect(page).toHaveURL(/\/reports\/chats\/new\?source=team$/);
  await expect(notice).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("failed version authorization blocks continuation without falling back", async ({
  page,
}) => {
  const team = { name: "reports", source: "team" as const, skills: [] };
  mockLangGraphAPI(page, {
    teamAgents: [team],
    threads: [{ thread_id: MOCK_THREAD_ID, agent_name: "reports" }],
  });
  await page.route("**/api/agents/reports?source=team", (route) =>
    route.fulfill({ json: team }),
  );
  await page.route("**/api/threads/*/employee-publication", (route) =>
    route.fulfill({ status: 403, json: { detail: "Approval revoked" } }),
  );
  await page.goto(
    `/workspace/agents/reports/chats/${MOCK_THREAD_ID}?source=team`,
  );
  await expect(page.getByTestId("employee-version-notice")).toContainText(
    "no other version will be substituted",
  );
  await expect(page.locator("textarea")).toBeDisabled();
  // Opening a new chat is allowed; its first run still requires fresh server
  // admission. Never mutate the old thread's binding as a retry workaround.
  await expect(page.getByTestId("agent-new-chat-button")).toBeEnabled();
});
