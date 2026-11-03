import { expect, test, type Page } from "@playwright/test";

import { mockLangGraphAPI, THREAD_PINNED_METADATA_KEY } from "./utils/mock-api";

const NEWEST_THREAD_ID = "00000000-0000-0000-0000-000000000901";
const OLDER_THREAD_ID = "00000000-0000-0000-0000-000000000902";

async function targetChatTitles(page: Page) {
  return page
    .locator(
      `a[href="/workspace/chats/${NEWEST_THREAD_ID}"], a[href="/workspace/chats/${OLDER_THREAD_ID}"]`,
    )
    .evaluateAll((links) =>
      links.map((link) => link.textContent?.replace(/\s+/g, " ").trim() ?? ""),
    );
}

test("recent chats can be pinned and unpinned", async ({ page }) => {
  mockLangGraphAPI(page, {
    threads: [
      {
        thread_id: NEWEST_THREAD_ID,
        title: "Newest chat",
        updated_at: "2026-07-04T10:00:00Z",
      },
      {
        thread_id: OLDER_THREAD_ID,
        title: "Older chat",
        updated_at: "2026-07-03T10:00:00Z",
      },
    ],
  });

  await page.goto("/workspace/chats/new");
  await expect(page.getByText("Newest chat")).toBeVisible({ timeout: 15_000 });
  await expect
    .poll(() => targetChatTitles(page))
    .toEqual(["Newest chat", "Older chat"]);

  const olderItem = page
    .locator(`a[href="/workspace/chats/${OLDER_THREAD_ID}"]`)
    .locator("xpath=..");
  await olderItem.hover();
  await olderItem.getByRole("button", { name: /More|更多/i }).click();
  await page.getByRole("menuitem", { name: /Pin chat|置顶对话/i }).click();

  await expect
    .poll(() => targetChatTitles(page))
    .toEqual(["Older chat", "Newest chat"]);
  await expect(
    page.locator(`a[href="/workspace/chats/${OLDER_THREAD_ID}"] svg`),
  ).toHaveCount(1);

  await olderItem.hover();
  await olderItem.getByRole("button", { name: /More|更多/i }).click();
  await page.getByRole("menuitem", { name: /Unpin chat|取消置顶/i }).click();

  await expect
    .poll(() => targetChatTitles(page))
    .toEqual(["Newest chat", "Older chat"]);
});

test("server ordering keeps an old pinned chat in the first page", async ({
  page,
}) => {
  mockLangGraphAPI(page, {
    threads: [
      ...Array.from({ length: 51 }, (_, index) => ({
        thread_id: `00000000-0000-0000-0000-000000001${String(index).padStart(3, "0")}`,
        title: `Recent chat ${index + 1}`,
        updated_at: new Date(
          Date.UTC(2026, 6, 25, 10, 0, 0) - index * 60_000,
        ).toISOString(),
      })),
      {
        thread_id: OLDER_THREAD_ID,
        title: "Old pinned chat",
        updated_at: "2026-01-01T10:00:00Z",
        metadata: { [THREAD_PINNED_METADATA_KEY]: true },
      },
    ],
  });

  await page.goto("/workspace/chats/new");
  await expect(page.getByText("Old pinned chat")).toBeVisible({
    timeout: 15_000,
  });
});
