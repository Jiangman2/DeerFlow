import type { Message } from "@langchain/langgraph-sdk";
import { describe, expect, it } from "@rstest/core";

import {
  deriveAssistantTurnUsageState,
  deriveStableMessageGroups,
} from "@/core/messages/derived-state";
import { getMessageGroups } from "@/core/messages/utils";

function message(type: Message["type"], id: string, content: string): Message {
  return { type, id, content } as Message;
}

describe("incremental message derivation", () => {
  it("reuses completed groups when only the streaming turn changes", () => {
    const messages = Array.from({ length: 1_000 }, (_, turn) => [
      message("human", `h-${turn}`, `question ${turn}`),
      message("ai", `a-${turn}`, `answer ${turn}`),
    ]).flat();
    const initial = deriveStableMessageGroups(messages, false, [], false);
    const nextMessages = [
      ...messages.slice(0, -1),
      message("ai", "a-999", "answer 999 streaming"),
    ];

    const next = deriveStableMessageGroups(nextMessages, true, initial, false);

    expect(next).toHaveLength(initial.length);
    expect(next[0]).toBe(initial[0]);
    expect(next.at(-3)).toBe(initial.at(-3));
    expect(next.at(-1)).not.toBe(initial.at(-1));
  });

  it("matches reference grouping when older history is prepended", () => {
    const currentTurn = [
      message("human", "h-2", "current question"),
      message("ai", "a-2", "streaming answer"),
    ];
    const initial = deriveStableMessageGroups(currentTurn, true, [], false);
    const withHistory = [
      message("human", "h-1", "older question"),
      message("ai", "a-1", "older answer"),
      ...currentTurn,
    ];

    const derived = deriveStableMessageGroups(withHistory, true, initial, true);

    expect(derived).toEqual(
      getMessageGroups(withHistory, { isCurrentTurnLoading: true }),
    );
    expect(derived.map((group) => group.id)).toContain("h-1");
  });

  it("reuses completed turn usage arrays on a tail-only update", () => {
    const messages = [
      message("human", "h-1", "one"),
      message("ai", "a-1", "answer one"),
      message("human", "h-2", "two"),
      message("ai", "a-2", "answer two"),
    ];
    const groups = deriveStableMessageGroups(messages, false, [], false);
    const initial = deriveAssistantTurnUsageState(groups);
    const nextMessages = [
      ...messages.slice(0, -1),
      message("ai", "a-2", "answer two streaming"),
    ];
    const nextGroups = deriveStableMessageGroups(
      nextMessages,
      true,
      groups,
      false,
    );
    const next = deriveAssistantTurnUsageState(nextGroups, initial);

    expect(next.byGroupIndex[1]).toBe(initial.byGroupIndex[1]);
    expect(next.byGroupIndex.at(-1)).not.toBe(initial.byGroupIndex.at(-1));
  });
});
