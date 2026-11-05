import { expect, test, type Page } from "@playwright/test";

import { MOCK_THREAD_ID, mockLangGraphAPI } from "./utils/mock-api";

const V4 = "deepseek-v4-flash";
const V41 = "deepseek-v4.1-flash-1017";

async function setup(page: Page, includeV41 = true) {
  mockLangGraphAPI(page, {
    threads: [{ thread_id: MOCK_THREAD_ID, title: "Existing V4 chat" }],
  });
  await page.addInitScript(
    ({ v4, oldId }) => {
      localStorage.setItem(
        "platform.local-settings",
        JSON.stringify({
          context: { model_name: v4, mode: "flash" },
        }),
      );
      localStorage.setItem(`platform.thread-model.${oldId}`, v4);
    },
    { v4: V4, oldId: MOCK_THREAD_ID },
  );
  await page.route("**/api/models", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 200));
    await route.fulfill({
      json: {
        models: [
          {
            name: V4,
            display_name: "DeepSeek-V4-Flash",
            supports_thinking: true,
          },
          ...(includeV41
            ? [
                {
                  name: V41,
                  display_name: "DeepSeek-V4.1-Flash-1017",
                  supports_thinking: true,
                },
              ]
            : []),
        ],
        token_usage: { enabled: false },
      },
    });
  });
}

async function sendAndExpectModel(page: Page, model: string) {
  const request = page.waitForRequest(
    (req) => req.method() === "POST" && req.url().includes("/runs/stream"),
  );
  const input = page.getByPlaceholder(/how can i assist you/i);
  await input.fill("Hello");
  await input.press("Enter");
  expect((await request).postDataJSON().context.model_name).toBe(model);
  await expect(page.getByText("Hello from DeerFlow!")).toBeVisible();
}

test("new chat ignores saved V4 and sends V4.1, existing chat keeps V4", async ({
  page,
}) => {
  await setup(page);
  await page.goto("/workspace/chats/new");
  await expect(page.locator(".cnooc-model-selector")).toContainText(
    "DeepSeek-V4.1-Flash-1017",
  );
  await sendAndExpectModel(page, V41);
  await expect(page.locator(".cnooc-model-selector")).toContainText(
    "DeepSeek-V4.1-Flash-1017",
  );
  await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
  await expect(page.locator(".cnooc-model-selector")).toContainText(
    "DeepSeek-V4-Flash",
  );
});

test("manual V4 selection is sent, next New chat initializes V4.1", async ({
  page,
}) => {
  await setup(page);
  await page.goto("/workspace/chats/new");
  await expect(page.locator(".cnooc-model-selector")).toContainText(
    "DeepSeek-V4.1-Flash-1017",
  );
  await page.locator(".cnooc-model-selector").click();
  await page
    .getByRole("menuitemradio", { name: "DeepSeek-V4-Flash", exact: true })
    .click();
  await sendAndExpectModel(page, V4);
  await page.getByRole("link", { name: "New chat", exact: true }).click();
  await expect(page.locator(".cnooc-model-selector")).toContainText(
    "DeepSeek-V4.1-Flash-1017",
  );
});

test("New chat clicked again on the welcome page resets the model", async ({
  page,
}) => {
  await setup(page);
  await page.goto("/workspace/chats/new");
  await expect(page.locator(".cnooc-model-selector")).toContainText(
    "DeepSeek-V4.1-Flash-1017",
  );
  await page.locator(".cnooc-model-selector").click();
  await page
    .getByRole("menuitemradio", { name: "DeepSeek-V4-Flash", exact: true })
    .click();
  await page.getByRole("link", { name: "New chat", exact: true }).click();
  await expect(page.locator(".cnooc-model-selector")).toContainText(
    "DeepSeek-V4.1-Flash-1017",
  );
  await sendAndExpectModel(page, V41);
});

test("unavailable V4.1 falls back to an available model", async ({ page }) => {
  await setup(page, false);
  await page.goto("/workspace/chats/new");
  await expect(page.locator(".cnooc-model-selector")).toContainText(
    "DeepSeek-V4-Flash",
  );
  await sendAndExpectModel(page, V4);
});
