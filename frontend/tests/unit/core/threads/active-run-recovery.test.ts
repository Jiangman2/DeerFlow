import type { Run } from "@langchain/langgraph-sdk";
import { afterEach, expect, test, rs } from "@rstest/core";

import {
  findLatestActiveRun,
  readReconnectRun,
  rememberReconnectRun,
} from "@/core/threads/active-run-recovery";

function run(runId: string, status: Run["status"], updatedAt: string): Run {
  return {
    run_id: runId,
    thread_id: "thread-1",
    assistant_id: "lead_agent",
    status,
    created_at: updatedAt,
    updated_at: updatedAt,
  } as Run;
}

function makeSessionStorage() {
  const values = new Map<string, string>();
  return {
    getItem: rs.fn((key: string) => values.get(key) ?? null),
    removeItem: rs.fn((key: string) => values.delete(key)),
    setItem: rs.fn((key: string, value: string) => values.set(key, value)),
  };
}

afterEach(() => {
  rs.unstubAllGlobals();
});

test("finds the newest pending or running run in an unsorted list", () => {
  const active = findLatestActiveRun([
    run("run-success", "success", "2026-08-03T10:00:00Z"),
    run("run-old", "pending", "2026-08-03T10:01:00Z"),
    run("run-new", "running", "2026-08-03T10:02:00Z"),
  ]);

  expect(active?.run_id).toBe("run-new");
});

test("returns undefined when all runs are terminal", () => {
  expect(
    findLatestActiveRun([
      run("run-success", "success", "2026-08-03T10:00:00Z"),
      run("run-error", "error", "2026-08-03T10:01:00Z"),
      run("run-timeout", "timeout", "2026-08-03T10:02:00Z"),
      run("run-interrupted", "interrupted", "2026-08-03T10:03:00Z"),
    ]),
  ).toBeUndefined();
});

test("persists and reads reconnect metadata", () => {
  const sessionStorage = makeSessionStorage();
  rs.stubGlobal("window", { sessionStorage });

  rememberReconnectRun("thread-1", "run-1");

  expect(sessionStorage.setItem).toHaveBeenCalledWith(
    "lg:stream:thread-1",
    "run-1",
  );
  expect(readReconnectRun("thread-1")).toBe("run-1");
});

test("storage access failures do not block recovery", () => {
  rs.stubGlobal("window", {
    get sessionStorage() {
      throw new DOMException("Blocked", "SecurityError");
    },
  });

  expect(() => rememberReconnectRun("thread-1", "run-1")).not.toThrow();
  expect(readReconnectRun("thread-1")).toBeNull();
});
