import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

const team = {
  name: "reports",
  display_name: "Square Reporter",
  description: "Shared work",
  source: "team" as const,
  skills: [],
};
const personal = {
  ...team,
  source: "personal" as const,
  display_name: "Private Reporter",
};

test("Square selection and first stream keep the explicit source despite a personal namesake", async ({
  page,
}) => {
  mockLangGraphAPI(page, { agents: [personal], teamAgents: [team] });
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  await page.route("**/api/agents/reports?source=team", (route) =>
    route.fulfill({ json: team }),
  );
  let submitted: Record<string, unknown> | undefined;
  await page.route("**/api/langgraph/threads/*/runs/stream", (route) => {
    submitted = route.request().postDataJSON() as Record<string, unknown>;
    return route.fallback();
  });
  await page.goto("/workspace/agents");
  await expect(page).toHaveTitle("中国海油通用智能体");
  await page.getByRole("searchbox").fill("Square Reporter");
  const card = page
    .getByTestId("digital-employee-card")
    .filter({ hasText: "Square Reporter" });
  await card.getByRole("button", { name: "Start work" }).click();
  await expect(page).toHaveURL(/\/agents\/reports\/chats\/new\?source=team$/);
  await expect(page.getByText("Square Reporter").first()).toBeVisible();
  await page.locator("textarea").fill("Summarize the report");
  await page.locator("textarea").press("Enter");
  await expect.poll(() => submitted).toBeDefined();
  expect(submitted?.context).toMatchObject({
    agent_name: "reports",
    agent_source: "team",
  });
  await expect(page).toHaveURL(
    /\/agents\/reports\/chats\/(?!new)[^?]+\?source=team$/,
  );
  // URL replacement happens at onCreated, before the SSE stream finishes.
  // This test covers persisted routing after completion, not an intentional
  // document unload in the middle of an active request.
  await expect(page.getByText("Hello from DeerFlow!", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByTestId("agent-new-chat-button")).toBeVisible();
  await page.getByTestId("agent-new-chat-button").click();
  await expect(page).toHaveURL(/\/agents\/reports\/chats\/new\?source=team$/);
  expect(errors).toEqual([]);
});

test("unavailable Square employee is visibly blocked without personal fallback", async ({
  page,
}) => {
  mockLangGraphAPI(page, { agents: [personal] });
  let streams = 0;
  await page.route("**/api/agents/reports?source=team", (route) =>
    route.fulfill({ status: 404, json: { detail: "Not available" } }),
  );
  await page.route("**/api/langgraph/threads/*/runs/stream", (route) => {
    streams++;
    return route.fallback();
  });
  await page.goto("/workspace/agents/reports/chats/new?source=team");
  await expect(
    page.getByRole("alert").filter({
      hasText: "no same-name employee will be substituted",
    }),
  ).toBeVisible();
  await expect(page.locator("textarea")).toBeDisabled();
  expect(streams).toBe(0);
});

test("invalid employee source shows a bounded error without loading any namesake", async ({ page }) => {
  mockLangGraphAPI(page, { agents: [personal] });
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  let reads = 0;
  await page.route("**/api/agents/reports*", (route) => {
    reads++;
    return route.fallback();
  });
  await page.goto("/workspace/agents/reports/chats/new?source=invalid");
  await expect(page.getByRole("alert").filter({ hasText: "no same-name employee will be substituted" })).toBeVisible();
  await expect(page.locator("textarea")).toBeDisabled();
  expect(reads).toBe(0);
  expect(errors).toEqual([]);
});
