import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

test("knowledge-base list collapses and gives the document table more width", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  mockLangGraphAPI(page);
  await page.route("**/api/v1/auth/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "test-user",
        email: "test@example.com",
        username: "test-user",
        is_active: true,
        is_superuser: false,
      }),
    }),
  );
  await page.route("**/api/v1/auth/setup-status", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ needs_setup: false }),
    }),
  );

  await page.route("**/api/weknora/knowledge-bases", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [
          {
            id: "kb-1",
            name: "勘探开发项目资料库",
            description: "用于验证知识库侧栏折叠",
            access_scope: "owned",
            is_owner: true,
            knowledge_count: 1,
            chunk_count: 12,
          },
        ],
        total: 1,
      }),
    }),
  );
  await page.route("**/api/weknora/knowledge-bases/kb-1/knowledge**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [
          {
            id: "doc-1",
            title: "这是一个用于验证展开后可显示更完整名称的勘探开发长文档标题",
            file_type: "pdf",
            parse_status: "done",
            updated_at: "2026-07-24T01:00:00Z",
          },
        ],
        total: 1,
      }),
    }),
  );

  await page.goto("/workspace/knowledge");

  const sidebar = page.getByTestId("knowledge-base-sidebar");
  const documentPanel = page.getByTestId("knowledge-document-panel");
  await expect(sidebar).toBeVisible({ timeout: 15_000 });
  await expect(documentPanel).toBeVisible();

  const expandedPanelWidth = (await documentPanel.boundingBox())?.width ?? 0;
  await page.getByRole("button", { name: "收起知识库列表" }).click();

  await expect
    .poll(async () => (await sidebar.boundingBox())?.width ?? 0)
    .toBeLessThanOrEqual(1);
  const collapsedPanelWidth = (await documentPanel.boundingBox())?.width ?? 0;
  expect(collapsedPanelWidth).toBeGreaterThan(expandedPanelWidth + 250);

  await page.reload();
  await expect(
    page.getByRole("button", { name: "展开知识库列表" }),
  ).toBeVisible();

  await page.getByRole("button", { name: "展开知识库列表" }).click();
  await expect
    .poll(async () => (await sidebar.boundingBox())?.width ?? 0)
    .toBeGreaterThanOrEqual(280);
});
