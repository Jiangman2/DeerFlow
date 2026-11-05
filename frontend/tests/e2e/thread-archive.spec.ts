import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

const ACTIVE_THREAD_ID = "00000000-0000-0000-0000-000000000911";
const ARCHIVED_THREAD_ID = "00000000-0000-0000-0000-000000000912";

test("a recent chat can be archived, listed, and restored", async ({
  page,
}) => {
  mockLangGraphAPI(page, {
    threads: [
      {
        thread_id: ACTIVE_THREAD_ID,
        title: "Active project chat",
        updated_at: "2026-09-10T10:00:00Z",
      },
      {
        thread_id: ARCHIVED_THREAD_ID,
        title: "Already archived chat",
        updated_at: "2026-09-09T10:00:00Z",
        metadata: { deerflow_archived: true },
      },
    ],
  });

  await page.goto("/workspace/chats/new");
  await expect(page.getByText("Active project chat")).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByText("Already archived chat")).toBeHidden();

  const activeItem = page
    .locator(`a[href="/workspace/chats/${ACTIVE_THREAD_ID}"]`)
    .locator("xpath=..");
  await activeItem.hover();
  await activeItem.getByRole("button", { name: /More|更多/i }).click();
  await page.getByRole("menuitem", { name: /Archive chat|归档/i }).click();
  await expect(
    page.locator(
      `a[data-sidebar="menu-button"][href="/workspace/chats/${ACTIVE_THREAD_ID}"]`,
    ),
  ).toHaveCount(0);

  await page.goto("/workspace/chats");
  await page.getByRole("tab", { name: /Archived|已归档/i }).click();
  await expect(page.getByText("Already archived chat")).toBeVisible();
  const archivedRow = page
    .getByRole("link", { name: /Active project chat/i })
    .locator("..");
  await expect(archivedRow).toBeVisible();
  await archivedRow.getByRole("button", { name: /Restore chat|恢复/i }).click();
  await expect(
    page.locator(".cnooc-thread-list").getByText("Active project chat"),
  ).toBeHidden();

  await page.getByRole("tab", { name: /Recent chats|近期会话/i }).click();
  await expect(
    page.locator(".cnooc-thread-list").getByText("Active project chat"),
  ).toBeVisible();
});
