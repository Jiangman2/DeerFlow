import { expect, test } from "@playwright/test";

import {
  handleRunStream,
  mockLangGraphAPI,
  MOCK_THREAD_ID,
} from "./utils/mock-api";

const MOCK_AGENTS = [
  {
    name: "test-agent",
    description: "A test agent for E2E tests",
    model: "builder-flash",
    tool_groups: ["search"],
    skills: ["report"],
    system_prompt: "You are a test agent.",
  },
];

const CHINESE_AGENT = {
  name: "月报助手",
  description: "生成中文月报",
  system_prompt: "你是月报助手。",
};

const BUILDER_MODEL = {
  id: "builder-flash",
  name: "builder-flash",
  model: "deepseek-v4-flash",
  display_name: "Builder Flash",
  supports_thinking: true,
  supports_reasoning_effort: true,
};

test.describe("Agent chat", () => {
  test("agent gallery page loads and shows agents", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    mockLangGraphAPI(page, { agents: MOCK_AGENTS });

    await page.goto("/workspace/agents");
    await page
      .getByRole("tab", { name: /My digital employees|我的数字员工/i })
      .click();

    // The agent card should appear with the agent name
    await expect(page.getByText("test-agent")).toBeVisible({
      timeout: 15_000,
    });
    const card = page.getByTestId("digital-employee-card");
    await expect(card).toHaveCount(1);
    await expect(card.getByText("builder-flash")).toBeVisible();
    await expect(card.getByText("search")).toBeVisible();
    await expect(card.getByText("report")).toBeVisible();
    await expect(card.locator("img")).toHaveAttribute(
      "src",
      /\/images\/digital-employees\/cartoon\//,
    );

    await card.getByRole("button", { name: /More actions|更多操作/i }).click();
    await page
      .getByRole("menuitem", { name: /View profile|查看档案/i })
      .click();
    await expect(
      page.getByRole("heading", {
        name: /Digital employee profile|数字员工档案/i,
      }),
    ).toBeVisible();

    await page.keyboard.press("Escape");
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(card).toBeVisible();
    expect(
      await page.evaluate(
        () =>
          Math.max(
            document.body.scrollWidth,
            document.documentElement.scrollWidth,
          ) <=
          window.innerWidth + 1,
      ),
    ).toBe(true);
  });

  test("agent chat page loads with input box", async ({ page }) => {
    mockLangGraphAPI(page, { agents: MOCK_AGENTS });

    await page.goto("/workspace/agents/test-agent/chats/new");

    // The prompt input textarea should be visible
    const textarea = page.getByRole("textbox").first();
    await expect(textarea).toBeVisible({ timeout: 15_000 });
  });

  test("agent chat page shows agent badge", async ({ page }) => {
    mockLangGraphAPI(page, { agents: MOCK_AGENTS });

    await page.goto("/workspace/agents/test-agent/chats/new");

    // The agent badge should display in the header (scoped to header to avoid
    // matching the welcome area which also shows the agent name)
    await expect(
      page.locator("header span", { hasText: "test-agent" }),
    ).toBeVisible({ timeout: 15_000 });
  });

  test("agent builder keeps its minimal composer responsive and visible", async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    mockLangGraphAPI(page);
    await page.route("**/api/agents/check?*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ available: true, name: "builder-layout-agent" }),
      }),
    );
    await page.route("**/api/models", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          models: [BUILDER_MODEL],
          token_usage: { enabled: false },
        }),
      }),
    );

    await page.goto("/workspace/agents/new", {
      waitUntil: "domcontentloaded",
    });
    const createDialog = page.getByTestId("agent-create-dialog");
    const scrollRegion = page.getByTestId("agent-create-scroll-region");
    await expect(createDialog).toBeVisible({ timeout: 15_000 });
    expect(
      await scrollRegion.evaluate((node) => getComputedStyle(node).overflowY),
    ).toBe("auto");
    const dialogBounds = await createDialog.boundingBox();
    expect(dialogBounds).not.toBeNull();
    expect(dialogBounds!.y).toBeGreaterThanOrEqual(0);
    expect(dialogBounds!.y + dialogBounds!.height).toBeLessThanOrEqual(900);

    const avatarOptions = page.getByTestId("expert-avatar-option");
    await expect(avatarOptions).toHaveCount(6);
    const avatarToggle = page.getByRole("button", {
      name: /Show all 24 avatars|展开全部 24 个头像/i,
    });
    await avatarToggle.click();
    await expect(avatarOptions).toHaveCount(24);
    await avatarOptions.nth(10).click();
    await page
      .getByRole("button", { name: /Show fewer avatars|收起头像/i })
      .click();
    await expect(avatarOptions).toHaveCount(6);
    await expect(
      page.locator('[data-testid="expert-avatar-option"][aria-pressed="true"]'),
    ).toHaveCount(1);
    await page
      .getByRole("textbox", { name: /monthly-report-expert|月报/i })
      .fill("builder-layout-agent");
    await page.getByRole("button", { name: /Continue|继续/i }).click();

    const composer = page.getByTestId("agent-builder-composer");
    const textarea = composer.getByRole("textbox");
    await expect(composer).toBeVisible({ timeout: 15_000 });
    await expect(
      page.locator(".cnooc-agent-builder-model-strip"),
    ).toBeVisible();
    await expect(page.getByTestId("add-attachments-button")).toHaveCount(0);
    await expect(page.locator(".cnooc-tool-mode")).toHaveCount(0);
    await expect
      .poll(() =>
        composer.locator('[data-slot="input-group"]').evaluate((element) => ({
          radius: Number.parseFloat(getComputedStyle(element).borderRadius),
          minHeight: Number.parseFloat(getComputedStyle(element).minHeight),
        })),
      )
      .toMatchObject({ radius: 22, minHeight: 132 });
    await page.screenshot({
      path: testInfo.outputPath("agent-builder-desktop.png"),
      fullPage: true,
    });

    await page.setViewportSize({ width: 390, height: 844 });
    await expect(composer).toBeVisible();
    await expect
      .poll(() =>
        textarea.evaluate((element) =>
          Number.parseFloat(getComputedStyle(element).fontSize),
        ),
      )
      .toBeGreaterThanOrEqual(16);
    const mobileComposerBox = await composer.boundingBox();
    expect(mobileComposerBox).not.toBeNull();
    expect(mobileComposerBox!.x).toBeGreaterThanOrEqual(0);
    expect(mobileComposerBox!.x + mobileComposerBox!.width).toBeLessThanOrEqual(
      390,
    );
    expect(
      await page.evaluate(
        () =>
          Math.max(
            document.body.scrollWidth,
            document.documentElement.scrollWidth,
          ) <=
          window.innerWidth + 1,
      ),
    ).toBe(true);
    await page.screenshot({
      path: testInfo.outputPath("agent-builder-mobile.png"),
      fullPage: true,
    });

    await textarea.focus();
    await page.setViewportSize({ width: 390, height: 520 });
    await expect(page.locator(".cnooc-workspace-shell")).toHaveAttribute(
      "data-mobile-keyboard-open",
      "true",
    );
    await expect(page.locator(".cnooc-agent-builder-model-strip")).toBeHidden();
    const keyboardComposerBox = await composer.boundingBox();
    expect(keyboardComposerBox).not.toBeNull();
    expect(
      keyboardComposerBox!.y + keyboardComposerBox!.height,
    ).toBeLessThanOrEqual(520);
  });

  test("new-chat action is shown only for an existing agent conversation", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      agents: MOCK_AGENTS,
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Agent conversation",
          agent_name: "test-agent",
          messages: [
            {
              type: "human",
              id: "msg-human-agent-new-chat",
              content: [{ type: "text", text: "Existing agent question" }],
            },
          ],
        },
      ],
    });

    await page.goto("/workspace/agents/test-agent/chats/new");
    await expect(page.getByTestId("agent-new-chat-button")).toHaveCount(0);

    await page.goto(`/workspace/agents/test-agent/chats/${MOCK_THREAD_ID}`, {
      waitUntil: "domcontentloaded",
    });
    const newChatButton = page.getByTestId("agent-new-chat-button");
    await expect(newChatButton).toBeVisible({ timeout: 15_000 });
    await newChatButton.click();
    await expect(page).toHaveURL(
      /\/workspace\/agents\/test-agent\/chats\/new$/,
    );
    await expect(newChatButton).toHaveCount(0);

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`/workspace/agents/test-agent/chats/${MOCK_THREAD_ID}`);
    await expect(newChatButton).toBeVisible();
    await newChatButton.click();
    await expect(page).toHaveURL(
      /\/workspace\/agents\/test-agent\/chats\/new$/,
    );
    await expect(newChatButton).toHaveCount(0);
  });

  test("decodes a Chinese agent name and keeps its welcome icon visible", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { agents: [CHINESE_AGENT] });

    await page.goto(
      `/workspace/agents/${encodeURIComponent(CHINESE_AGENT.name)}/chats/new`,
    );

    await expect(
      page.locator("header span", { hasText: CHINESE_AGENT.name }),
    ).toBeVisible({ timeout: 15_000 });
    await expect(
      page.getByText(CHINESE_AGENT.name, { exact: true }),
    ).toHaveCount(2);
    await expect(page.getByText(/%E6%9C%88/)).toHaveCount(0);

    const welcomeIcon = page.locator(".cnooc-agent-welcome-icon", {
      has: page.locator("svg.lucide-bot"),
    });
    await expect(welcomeIcon).toBeVisible();
    await expect(welcomeIcon.locator("span.bg-primary.opacity-10")).toHaveCount(
      1,
    );
    await expect(welcomeIcon).not.toHaveClass(/bg-primary\/10/);
  });

  test("custom agent welcome reuses the responsive CNOOC composition", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    mockLangGraphAPI(page, { agents: [CHINESE_AGENT] });

    await page.goto(
      `/workspace/agents/${encodeURIComponent(CHINESE_AGENT.name)}/chats/new`,
    );

    const welcome = page.locator(".cnooc-agent-welcome-stage");
    const composer = page.getByTestId("welcome-composer");
    const rail = page.getByTestId("welcome-suggestion-rail");
    await expect(welcome).toBeVisible({ timeout: 15_000 });
    await expect(composer).toBeVisible();
    await expect(rail).toBeVisible();
    await expect(composer.locator(".cnooc-agent-welcome")).toHaveCount(0);

    const welcomeBox = await welcome
      .locator(".cnooc-agent-welcome")
      .boundingBox();
    const composerBox = await composer.boundingBox();
    const railBox = await rail.boundingBox();
    expect(welcomeBox).not.toBeNull();
    expect(composerBox).not.toBeNull();
    expect(railBox).not.toBeNull();
    expect(welcomeBox!.y + welcomeBox!.height).toBeLessThan(composerBox!.y);
    expect(composerBox!.width).toBeGreaterThanOrEqual(1000);
    expect(railBox!.width).toBeLessThan(composerBox!.width);
    expect(
      Math.abs(
        composerBox!.x +
          composerBox!.width / 2 -
          (railBox!.x + railBox!.width / 2),
      ),
    ).toBeLessThanOrEqual(3);

    await page.setViewportSize({ width: 390, height: 844 });
    await expect(welcome).toBeVisible();
    await expect(composer).toBeVisible();
    const mobileComposerBox = await composer.boundingBox();
    expect(mobileComposerBox).not.toBeNull();
    expect(mobileComposerBox!.x).toBeGreaterThanOrEqual(0);
    expect(mobileComposerBox!.x + mobileComposerBox!.width).toBeLessThanOrEqual(
      390,
    );
    expect(
      mobileComposerBox!.y + mobileComposerBox!.height,
    ).toBeLessThanOrEqual(844);
    await expect(page.locator(".cnooc-tool-attachments")).toBeVisible();
    await expect(page.locator(".cnooc-tool-datalake")).toBeVisible();
    await expect(page.locator(".cnooc-tool-knowledge")).toBeVisible();
    await expect(page.locator(".cnooc-model-selector")).toBeVisible();
  });

  test("agent chat can regenerate its latest response", async ({ page }) => {
    const humanMessage = {
      type: "human",
      id: "msg-human-agent",
      content: [{ type: "text", text: "Original agent question" }],
    };
    const aiMessage = {
      type: "ai",
      id: "msg-ai-agent",
      content: "Custom agent response",
    };
    mockLangGraphAPI(page, {
      agents: MOCK_AGENTS,
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Agent conversation",
          agent_name: "test-agent",
          messages: [humanMessage, aiMessage],
        },
      ],
    });

    let prepareMessageId: string | undefined;
    let streamBody: Record<string, unknown> | undefined;
    await page.route(
      `**/api/threads/${MOCK_THREAD_ID}/runs/regenerate/prepare`,
      (route) => {
        prepareMessageId = (
          route.request().postDataJSON() as { message_id?: string }
        ).message_id;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            input: { messages: [humanMessage] },
            checkpoint: {
              checkpoint_id: "checkpoint-before-human",
              checkpoint_ns: "",
              checkpoint_map: null,
            },
            metadata: {
              regenerate_from_message_id: aiMessage.id,
              regenerate_from_run_id: `run-${MOCK_THREAD_ID}`,
              regenerate_checkpoint_id: "checkpoint-before-human",
            },
            target_run_id: `run-${MOCK_THREAD_ID}`,
          }),
        });
      },
    );
    await page.route(
      `**/api/langgraph/threads/${MOCK_THREAD_ID}/runs/stream`,
      (route) => {
        streamBody = route.request().postDataJSON() as Record<string, unknown>;
        return handleRunStream(route);
      },
    );

    await page.goto(`/workspace/agents/test-agent/chats/${MOCK_THREAD_ID}`);
    await expect(page.getByText(aiMessage.content)).toBeVisible({
      timeout: 15_000,
    });

    await page.evaluate((selectedText) => {
      const element = Array.from(document.querySelectorAll("p")).find(
        (candidate) => candidate.textContent?.includes(selectedText),
      );
      const textNode = element?.firstChild;
      if (!element || !textNode) {
        throw new Error("Unable to find the custom agent response");
      }
      const range = document.createRange();
      range.selectNodeContents(textNode);
      const selection = window.getSelection();
      selection?.removeAllRanges();
      selection?.addRange(range);
      element.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    }, aiMessage.content);
    await expect(
      page.getByRole("button", { name: "Ask in side chat" }),
    ).toBeVisible();
    await page.keyboard.press("Escape");

    const assistantTurn = page.locator("[data-assistant-turn]").last();
    await assistantTurn.hover();
    await page.getByRole("button", { name: "Regenerate" }).click();

    await expect.poll(() => prepareMessageId).toBe(aiMessage.id);
    await expect.poll(() => streamBody).toBeDefined();
    expect(streamBody).toMatchObject({
      checkpoint: {
        checkpoint_id: "checkpoint-before-human",
        checkpoint_ns: "",
        checkpoint_map: null,
      },
      metadata: {
        regenerate_from_message_id: aiMessage.id,
        regenerate_from_run_id: `run-${MOCK_THREAD_ID}`,
        regenerate_checkpoint_id: "checkpoint-before-human",
      },
      context: {
        agent_name: "test-agent",
        thread_id: MOCK_THREAD_ID,
      },
    });
  });
});
