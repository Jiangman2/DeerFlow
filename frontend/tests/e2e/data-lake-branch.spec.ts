import { expect, test, type Page } from "@playwright/test";

import { parseNewShopCartListResponse } from "../../src/core/shop-cart/api-files";

import { mockLangGraphAPI } from "./utils/mock-api";

test.setTimeout(120_000);

const ready = {
  id: "ready",
  fileId: "mock-download-id",
  userId: "mock-user",
  isComplete: 1,
  createDate: "2026-11-11 10:00:00",
  fileName: "ready.txt",
  type: 1,
  ptCode: "mock",
  isStore: 0,
  dacCode: null,
  num: 1,
  remark: null,
  tipsInfo: null,
  extra: null,
  errorInfo: null,
  shopType: 0,
};
const failed = {
  ...ready,
  id: "failed",
  fileName: "failed.zip",
  isComplete: -1,
  fileId: null,
  num: null,
  errorInfo: "文件下载异常",
};
const parsed = parseNewShopCartListResponse({
  code: 0,
  data: [
    failed,
    ready,
    { ...ready, id: "oversized", fileName: "oversized.zip", isComplete: 2 },
    {
      ...failed,
      id: "pending",
      fileName: "pending.zip",
      isComplete: 0,
      errorInfo: null,
    },
    { ...failed, id: "unknown", fileName: "unknown.zip", errorInfo: "  " },
  ],
});
if (!parsed.ok) throw new Error("Mixed upstream fixture rejected");
const files = parsed.files;

test.beforeEach(async ({ page }, info) => {
  // No business request is allowed to reach a live backend. Specific mocks
  // registered below override this catch-all; static app assets remain local.
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (
      url.pathname.startsWith("/api/") ||
      url.pathname.startsWith("/integrations/")
    ) {
      return route.fulfill({ json: {} });
    }
    if (url.origin !== new URL(String(info.project.use.baseURL)).origin)
      return route.abort();
    return route.continue();
  });
  mockLangGraphAPI(page);
  await page
    .context()
    .addCookies([
      { name: "locale", value: "en-US", url: String(info.project.use.baseURL) },
    ]);
  await page.route("**/api/v1/data-lake/auth/token", (route) =>
    route.fulfill({ json: { code: "authorized" } }),
  );
  await page.route("**/api/v1/data-lake/auth/session", (route) =>
    route.fulfill({ json: { authorized: true } }),
  );
  await page.route("**/api/shop-cart/new/list", (route) =>
    route.fulfill({ json: { files } }),
  );
});

async function enable(page: Page, zh = false) {
  await page.goto("/workspace/chats/new");
  const trigger = page.locator(".cnooc-tool-datalake:visible");
  await expect(trigger).toBeVisible({ timeout: 90_000 });
  await trigger.click();
  await page
    .getByRole("menuitem", {
      name: zh ? "使用数据湖" : "Use Data Lake",
      exact: true,
    })
    .click();
  await expect(trigger).toHaveAttribute("data-active", "true");
  await expect(page.getByRole("menu")).toBeHidden();
  await expect(trigger).toHaveAttribute("aria-expanded", "false");
  return trigger;
}

async function openFiles(page: Page, zh = false) {
  const trigger = page.locator(".cnooc-tool-datalake:visible");
  await trigger.focus();
  await trigger.press("ArrowDown");
  await expect(page.getByRole("menu")).toBeVisible();
  await page
    .getByRole("menuitem", {
      name: zh ? "数据湖文件列表" : "Data Lake file list",
      exact: true,
    })
    .click();
}

for (const zh of [false, true]) {
  test(`mixed file states, reasons and ready-file download (${zh ? "Chinese" : "English"})`, async ({
    page,
  }, info) => {
    if (zh)
      await page.context().addCookies([
        {
          name: "locale",
          value: "zh-CN",
          url: String(info.project.use.baseURL),
        },
      ]);
    const downloaded: string[] = [];
    await page.route("**/api/shop-cart/new/download", async (route) => {
      downloaded.push((route.request().postDataJSON() as { id: string }).id);
      await route.fulfill({
        contentType: "text/plain",
        body: "Mock download content",
      });
    });
    await enable(page, zh);
    await openFiles(page, zh);
    const dialog = page.getByRole("dialog");
    const failedRow = dialog.getByRole("button", { name: /failed.zip/ });
    await expect(failedRow).toBeDisabled();
    await expect(failedRow).toContainText(zh ? "下载失败" : "Download failed");
    await expect(failedRow).toContainText("文件下载异常");
    await expect(
      dialog.getByRole("button", { name: /unknown.zip/ }),
    ).toContainText(zh ? "未提供失败原因" : "No failure reason provided");
    const pending = dialog.getByRole("button", { name: /pending.zip/ });
    await expect(pending).toBeDisabled();
    await expect(pending).toContainText(zh ? "下载中" : "Downloading");
    const oversized = dialog.getByRole("button", { name: /oversized.zip/ });
    await expect(oversized).toBeDisabled();
    await expect(oversized).toContainText(zh ? "超大文件" : "Oversized file");
    const readyRow = dialog.getByRole("button", { name: /ready.txt/ });
    await expect(readyRow).toBeEnabled();
    await expect(readyRow).toContainText(zh ? "可下载" : "Ready");
    const add = dialog.getByRole("button", {
      name: zh ? "添加所选文件" : "Add selected files",
      exact: true,
    });
    await expect(add).toBeDisabled();
    await readyRow.click();
    await expect(add).toBeEnabled();
    await page.screenshot({
      path: info.outputPath("mixed-files.png"),
      fullPage: true,
    });
    await add.click();
    await expect(dialog).toBeHidden();
    expect(downloaded).toEqual(["ready"]);
    await expect(
      page.getByText("ready.txt", { exact: true }).first(),
    ).toBeVisible();
  });
}

for (const status of [401, 403]) {
  test(`login-required list response (${status}) disables and restores state after retry`, async ({
    page,
  }, info) => {
    let calls = 0;
    await page.route("**/api/shop-cart/new/list", (route) => {
      calls++;
      return calls === 1
        ? route.fulfill({
            status,
            json: {
              code: "data_lake_login_required",
              detail: "Please sign in to Data Lake first.",
            },
          })
        : route.fulfill({ json: { files } });
    });
    const trigger = await enable(page);
    await openFiles(page);
    const login = page.getByRole("dialog");
    await expect(
      login.getByRole("link", { name: "Open Data Lake" }),
    ).toHaveAttribute("href", "https://datalake.cnooc/");
    await expect(trigger).toHaveAttribute("data-active", "false");
    await expect(page.locator(".cnooc-material-dialog")).toHaveCount(0);
    await page.screenshot({
      path: info.outputPath("login-required.png"),
      fullPage: true,
    });
    await login
      .getByRole("button", { name: "I have signed in, retry" })
      .click();
    await expect(trigger).toHaveAttribute("data-active", "true");
    await expect(page.getByRole("button", { name: /ready.txt/ })).toBeVisible();
    expect(calls).toBe(2);
  });
}

test("ordinary 401 clears state without offering Data Lake login; 502 preserves enabled state", async ({
  page,
}) => {
  const trigger = await enable(page);
  await page.route("**/api/shop-cart/new/list", (route) =>
    route.fulfill({ status: 502, json: { detail: "Mock temporary outage" } }),
  );
  await openFiles(page);
  await expect(
    page.getByText("Failed to load Data Lake files. Please retry.", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(trigger).toHaveAttribute("data-active", "true");
  await expect(page.getByRole("link", { name: "Open Data Lake" })).toHaveCount(
    0,
  );
  await page.route("**/api/shop-cart/new/list", (route) =>
    route.fulfill({ status: 401, json: { detail: "Mock invalid session" } }),
  );
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(trigger).toHaveAttribute("data-active", "false");
  await expect(page.locator(".cnooc-material-dialog")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Open Data Lake" })).toHaveCount(
    0,
  );
});
