import { expect, test } from "@rstest/core";

import {
  getThreadDataLakeStorageKey,
  parseThreadDataLakeEnabled,
} from "@/core/settings/local";

test("defaults a missing thread Data Lake setting to enabled", () => {
  expect(parseThreadDataLakeEnabled(null)).toBe(true);
  expect(parseThreadDataLakeEnabled("invalid")).toBe(true);
});

test("parses only explicit per-thread Data Lake booleans", () => {
  expect(parseThreadDataLakeEnabled("true")).toBe(true);
  expect(parseThreadDataLakeEnabled("false")).toBe(false);
  expect(parseThreadDataLakeEnabled("1")).toBe(true);
});

test("names Data Lake storage by thread id", () => {
  expect(getThreadDataLakeStorageKey("thread-a")).toBe(
    "platform.thread-data-lake.thread-a",
  );
  expect(getThreadDataLakeStorageKey("thread-b")).not.toBe(
    getThreadDataLakeStorageKey("thread-a"),
  );
});
