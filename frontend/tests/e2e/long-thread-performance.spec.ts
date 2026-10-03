import { expect, test } from "@playwright/test";

import { MOCK_THREAD_ID, mockLangGraphAPI } from "./utils/mock-api";

const TURN_COUNT = 90;

const longThreadMessages = Array.from({ length: TURN_COUNT }, (_, index) => [
  {
    type: "human",
    id: `long-thread-human-${index}`,
    content: [{ type: "text", text: `Long thread question ${index}` }],
  },
  {
    type: "ai",
    id: `long-thread-ai-${index}`,
    content: `Long thread answer ${index}\n\n${"Rendered markdown content. ".repeat(16)}`,
  },
]).flat();

test("long conversations render a virtual window and keep the composer responsive", async ({
  page,
}) => {
  mockLangGraphAPI(page, {
    threads: [
      {
        thread_id: MOCK_THREAD_ID,
        title: "Long conversation performance regression",
        updated_at: "2026-09-01T00:00:00Z",
        messages: longThreadMessages,
      },
    ],
  });

  await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);

  await expect(page.getByText(`Long thread answer ${TURN_COUNT - 1}`)).toBeVisible({
    timeout: 15_000,
  });

  const renderedAssistantTurns = page.locator("[data-assistant-turn]");
  await expect
    .poll(() => renderedAssistantTurns.count())
    .toBeLessThan(TURN_COUNT);

  const messageList = page.getByTestId("main-message-list");
  const scrollMetrics = await messageList.evaluate((root) => {
    const scrollElement = root.firstElementChild;
    if (!(scrollElement instanceof HTMLElement)) {
      throw new Error("Message list scroll container was not found.");
    }
    return {
      clientHeight: scrollElement.clientHeight,
      scrollHeight: scrollElement.scrollHeight,
      scrollTop: scrollElement.scrollTop,
    };
  });
  expect(scrollMetrics.scrollHeight).toBeGreaterThan(scrollMetrics.clientHeight);
  expect(scrollMetrics.scrollTop).toBeGreaterThan(0);

  const textarea = page.locator("textarea[name='message']");
  await textarea.fill("Long thread composer remains responsive");
  await expect(textarea).toHaveValue("Long thread composer remains responsive");
});
