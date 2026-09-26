import { expect, test, type Page } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

const scope = {
  version: 1 as const,
  mode: "selected" as const,
  items: [
    {
      type: "file" as const,
      id: "doc-1",
      kb_id: "kb-1",
      tenant_id: "tenant-1",
      name: "Approved report",
      kb_name: "Reports",
    },
  ],
};
const employee = {
  name: "report-helper",
  display_name: "Report Helper",
  skills: [],
  source: "personal" as const,
  knowledge_scope: scope,
};

async function knowledgeApi(page: Page) {
  await page.route("**/api/weknora/knowledge-bases", (route) =>
    route.fulfill({
      json: {
        items: [
          {
            id: "kb-1",
            name: "Reports",
            type: "document",
            access_tenant_id: "tenant-1",
          },
        ],
        total: 1,
      },
    }),
  );
  await page.route("**/api/weknora/knowledge/search?*", (route) =>
    route.fulfill({
      json: {
        items: [
          {
            id: "doc-1",
            title: "Approved report",
            knowledge_base_id: "kb-1",
            file_type: "pdf",
          },
        ],
        total: 1,
      },
    }),
  );
}

test("creator binds an exact document without exposing dynamic tag selection", async ({
  page,
}, testInfo) => {
  mockLangGraphAPI(page);
  await knowledgeApi(page);
  await page.route("**/api/agents/check?*", (route) =>
    route.fulfill({ json: { name: "report-helper", available: true } }),
  );
  let submitted: { context?: Record<string, unknown> } | undefined;
  await page.route("**/api/langgraph/threads/*/runs/stream", (route) => {
    submitted = route.request().postDataJSON();
    return route.fallback();
  });
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  await page.goto("/workspace/agents/new");
  await expect(page).toHaveURL(/\/workspace\/agents\/new$/);
  await expect(page).toHaveTitle("中国海油通用智能体");
  await page.locator('input[maxlength="64"]').fill("report-helper");
  const selector = page.getByTestId("agent-knowledge-selection");
  await selector.getByRole("radio", { name: "Bind a scope" }).click();
  await expect(page.getByRole("button", { name: /Continue/ })).toBeDisabled();
  await selector.getByTestId("knowledge-scope-trigger").click();
  await expect(page.getByTestId("knowledge-scope-group-tag")).toHaveCount(0);
  await page.getByTestId("knowledge-scope-group-file").click();
  await page.getByTestId("knowledge-document-kb-1-doc-1").click();
  await page.keyboard.press("Escape");
  await expect(
    selector.getByTestId("knowledge-scope-chip-file-doc-1"),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await selector.scrollIntoViewIfNeeded();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("employee-knowledge-mobile.png"),
  });
  await page.getByRole("button", { name: /Continue/ }).click();
  await expect.poll(() => submitted).toBeDefined();
  expect(submitted?.context?.weknora_knowledge_base_ids).toEqual([]);
  expect(submitted?.context?.weknora_knowledge_ids).toEqual(["doc-1"]);
  await expect(
    page.getByText("Hello from DeerFlow!", { exact: true }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

test("editing metadata preserves binding and explicit No knowledge clears it", async ({
  page,
}, testInfo) => {
  mockLangGraphAPI(page, { agents: [employee] });
  await knowledgeApi(page);
  let update: Record<string, unknown> | undefined;
  await page.route("**/api/agents/report-helper", (route) => {
    if (route.request().method() !== "PUT") return route.fallback();
    update = route.request().postDataJSON();
    return route.fulfill({ json: { ...employee, ...update } });
  });
  await page.goto("/workspace/agents");
  await page.getByRole("tab", { name: "My digital employees" }).click();
  const openEditor = async () => {
    await page
      .getByTestId("digital-employee-card")
      .first()
      .getByRole("button", { name: "More actions" })
      .click();
    await page.getByRole("menuitem", { name: "Edit details" }).click();
  };
  await openEditor();
  const dialog = page.getByRole("dialog");
  const selector = dialog.getByTestId("agent-knowledge-selection");
  await expect(
    selector.getByText("Approved report", { exact: true }),
  ).toBeVisible();
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => update).toBeDefined();
  expect(update).not.toHaveProperty("knowledge_scope");
  await openEditor();
  await selector.getByRole("radio", { name: "No knowledge" }).click();
  await selector.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: testInfo.outputPath("employee-knowledge-editor.png"),
  });
  update = undefined;
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => update).toBeDefined();
  expect(update).toMatchObject({
    knowledge_scope: { version: 1, mode: "disabled", items: [] },
  });
});

test("bound employee chat displays the fixed scope and cannot broaden it in the composer", async ({
  page,
}) => {
  mockLangGraphAPI(page, { agents: [employee] });
  await knowledgeApi(page);
  await page.goto("/workspace/agents/report-helper/chats/new?source=personal");
  await expect(page.getByText("Report Helper").first()).toBeVisible();
  await expect(page.getByTestId("knowledge-scope-trigger")).toBeDisabled();
  await expect(
    page.getByTestId("knowledge-scope-chip-file-doc-1"),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "移除Approved report" }),
  ).toBeDisabled();
});
