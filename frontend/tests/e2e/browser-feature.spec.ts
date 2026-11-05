import { expect, test } from "@playwright/test";

import { MOCK_THREAD_ID, mockLangGraphAPI } from "./utils/mock-api";

test.describe("Browser feature flag", () => {
  test("shows browser trigger only when Browser Live is enabled", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [{ thread_id: MOCK_THREAD_ID, title: "Browser Enabled" }],
    });

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await expect(page.getByTestId("browser-trigger")).toBeVisible({
      timeout: 15_000,
    });
  });

  test("shows Browser Live in an enabled digital employee chat", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Employee Browser",
          agent_name: "browser-employee",
        },
      ],
      agents: [
        {
          name: "browser-employee",
          display_name: "浏览器数字员工",
          tool_groups: ["browser"],
          source: "personal",
        },
      ],
    });

    await page.goto(
      `/workspace/agents/browser-employee/chats/${MOCK_THREAD_ID}`,
    );
    await expect(page.getByTestId("browser-trigger")).toBeVisible({
      timeout: 15_000,
    });
  });

  test("hides browser trigger when Browser Live is disabled", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [{ thread_id: MOCK_THREAD_ID, title: "Browser Disabled" }],
      features: { browserControlEnabled: false },
    });

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await expect(page.getByRole("main").first()).toBeVisible();
    await expect(page.getByTestId("browser-trigger")).toHaveCount(0);
  });
});
