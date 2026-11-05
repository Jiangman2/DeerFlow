import type { Message } from "@langchain/langgraph-sdk";
import { describe, expect, it } from "@rstest/core";

import { findAssistantTurnFeedbackTarget } from "@/core/messages/feedback";

describe("findAssistantTurnFeedbackTarget", () => {
  it("selects the latest persisted AI message in an assistant turn", () => {
    const messages = [
      { type: "ai", content: "working", run_id: "run-1" },
      { type: "tool", content: "result", run_id: "run-1" },
      {
        type: "ai",
        content: "final answer",
        run_id: "run-1",
        feedback: { feedback_id: "feedback-1", rating: 1, comment: null },
      },
    ] as unknown as Message[];

    expect(findAssistantTurnFeedbackTarget(messages)).toEqual({
      runId: "run-1",
      feedback: { feedback_id: "feedback-1", rating: 1, comment: null },
    });
  });

  it("ignores human messages and unpersisted streaming AI messages", () => {
    const messages = [
      { type: "human", content: "question", run_id: "run-human" },
      { type: "ai", content: "streaming" },
    ] as unknown as Message[];

    expect(findAssistantTurnFeedbackTarget(messages)).toBeNull();
  });
});
