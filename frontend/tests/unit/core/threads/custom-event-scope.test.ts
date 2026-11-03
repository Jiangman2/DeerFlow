import { describe, expect, test } from "@rstest/core";

import {
  customEventThreadId,
  isCustomEventForThread,
} from "@/core/threads/custom-event-scope";

describe("streamed custom-event thread scope", () => {
  test("reads the direct thread id", () => {
    expect(customEventThreadId({ thread_id: "thread-a" })).toBe("thread-a");
    expect(customEventThreadId({ threadId: "thread-b" })).toBe("thread-b");
  });

  test("reads a wrapped metadata thread id", () => {
    expect(customEventThreadId({ metadata: { thread_id: "thread-a" } })).toBe(
      "thread-a",
    );
  });

  test("rejects a late event from the previous conversation", () => {
    expect(
      isCustomEventForThread(
        { type: "task_running", thread_id: "old-thread" },
        new Set(["new-thread"]),
        "new-thread",
      ),
    ).toBe(false);
  });

  test("rejects unscoped events on a brand-new chat", () => {
    expect(
      isCustomEventForThread(
        { type: "task_started" },
        new Set(["client-generated-new-chat-id"]),
        "client-generated-new-chat-id",
        true,
      ),
    ).toBe(false);
  });

  test("keeps legacy unscoped events for an existing chat", () => {
    expect(
      isCustomEventForThread(
        { type: "task_completed" },
        new Set(["current-thread"]),
        "current-thread",
      ),
    ).toBe(true);
  });
});
