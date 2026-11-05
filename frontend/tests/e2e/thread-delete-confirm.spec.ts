import { expect, test, type Page } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

const CHAT = "00000000-0000-0000-0000-000000000901";
const TITLE = "Report to keep";

async function openDeleteDialog(page: Page) {
  const link = page.locator(
    `a[data-sidebar="menu-button"][href="/workspace/chats/${CHAT}"]`,
  );
  await link.hover();
  await link.locator("..").getByRole("button", { name: "More" }).click();
  await page.getByRole("menuitem", { name: "Delete", exact: true }).click();
  return page.getByRole("dialog", { name: "Delete chat", exact: true });
}

test("dismissing deletion keeps the conversation", async ({ page }) => {
  mockLangGraphAPI(page, { threads: [{ thread_id: CHAT, title: TITLE }] });
  const deletes: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "DELETE") deletes.push(request.url());
  });

  await page.goto(`/workspace/chats/${CHAT}`);
  const dialog = await openDeleteDialog(page);
  await expect(dialog).toContainText(TITLE);
  await expect(dialog).toContainText("cannot be undone");
  await expect(
    dialog.getByRole("button", { name: "Cancel", exact: true }),
  ).toBeFocused();
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();

  await expect(dialog).toBeHidden();
  await expect(page).toHaveURL(new RegExp(CHAT));
  expect(deletes).toEqual([]);
});

test("confirming deletion removes the current conversation", async ({
  page,
}) => {
  mockLangGraphAPI(page, { threads: [{ thread_id: CHAT, title: TITLE }] });
  await page.goto(`/workspace/chats/${CHAT}`);

  const dialog = await openDeleteDialog(page);
  await dialog.getByRole("button", { name: "Delete", exact: true }).click();

  await expect(dialog).toBeHidden();
  await expect(page).toHaveURL(/\/workspace\/chats\/new$/);
  await expect(
    page.locator(
      `a[data-sidebar="menu-button"][href="/workspace/chats/${CHAT}"]`,
    ),
  ).toHaveCount(0);
});
