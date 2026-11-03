import type { Message } from "@langchain/langgraph-sdk";

import type { FeedbackData } from "../api/feedback";

export type FeedbackMessage = Message & {
  feedback?: FeedbackData | null;
  run_id?: string;
};

export function findAssistantTurnFeedbackTarget(
  messages: Message[],
): { feedback: FeedbackData | null; runId: string } | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index] as FeedbackMessage | undefined;
    if (message?.type === "ai" && typeof message.run_id === "string") {
      return {
        feedback: message.feedback ?? null,
        runId: message.run_id,
      };
    }
  }
  return null;
}
