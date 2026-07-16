import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

const THREAD_ID = "00000000-0000-0000-0000-000000000451";
const RUN_ID = "run-message-feedback";
const FEEDBACK_URL = `**/api/threads/${THREAD_ID}/runs/${RUN_ID}/feedback`;

test("likes, switches, and clears feedback from the assistant-turn toolbar", async ({
  page,
}) => {
  test.setTimeout(60_000);
  mockLangGraphAPI(page, {
    threads: [
      {
        thread_id: THREAD_ID,
        title: "Message feedback",
        updated_at: "2026-07-16T08:00:00Z",
        messages: [
          {
            type: "human",
            id: "message-feedback-human",
            content: "How does enterprise feedback work?",
          },
          {
            type: "ai",
            id: "message-feedback-ai",
            content: "Feedback is stored against the completed run.",
          },
        ],
      },
    ],
  });

  await page.route(`**/api/threads/${THREAD_ID}/messages/page`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: [
          {
            run_id: RUN_ID,
            seq: 1,
            content: {
              type: "human",
              id: "message-feedback-human",
              content: "How does enterprise feedback work?",
            },
            feedback: null,
            metadata: { caller: "lead_agent" },
            created_at: "2026-07-16T08:00:00Z",
          },
          {
            run_id: RUN_ID,
            seq: 2,
            content: {
              type: "ai",
              id: "message-feedback-ai",
              content: "Feedback is stored against the completed run.",
            },
            feedback: null,
            metadata: { caller: "lead_agent" },
            created_at: "2026-07-16T08:00:01Z",
          },
        ],
        has_more: false,
        next_before_seq: null,
      }),
    }),
  );

  const submittedRatings: number[] = [];
  let deleteCount = 0;
  await page.route(FEEDBACK_URL, async (route) => {
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON() as { rating: number };
      submittedRatings.push(body.rating);
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          feedback_id: "feedback-message-1",
          rating: body.rating,
          comment: null,
        }),
      });
      return;
    }

    if (route.request().method() === "DELETE") {
      deleteCount += 1;
      await route.fulfill({ status: 204, body: "" });
      return;
    }

    await route.fallback();
  });

  await page.goto(`/workspace/chats/${THREAD_ID}`, {
    waitUntil: "domcontentloaded",
  });

  const answer = page.getByText(
    "Feedback is stored against the completed run.",
  );
  await expect(answer).toBeVisible({ timeout: 15_000 });
  await answer.hover();

  const like = page.getByRole("button", {
    name: "Like response",
    exact: true,
  });
  const dislike = page.getByRole("button", {
    name: "Dislike response",
    exact: true,
  });
  await expect(like).toHaveAttribute("aria-pressed", "false");
  await expect(dislike).toHaveAttribute("aria-pressed", "false");

  await like.click();
  await expect(like).toHaveAttribute("aria-pressed", "true");
  expect(submittedRatings).toEqual([1]);

  await dislike.click();
  await expect(like).toHaveAttribute("aria-pressed", "false");
  await expect(dislike).toHaveAttribute("aria-pressed", "true");
  expect(submittedRatings).toEqual([1, -1]);

  await dislike.click();
  await expect(dislike).toHaveAttribute("aria-pressed", "false");
  expect(deleteCount).toBe(1);
});
