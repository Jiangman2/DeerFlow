import { expect, test } from "@rstest/core";

import {
  buildHainengAuthHeaders,
  parseModelCallCount,
} from "@/server/haineng/model-call-count";

test("builds the documented MD5 header without exposing the raw app key", () => {
  expect(buildHainengAuthHeaders("app", "secret", 1_700_000_000_000)).toEqual({
    "APP-ID": "app",
    "APP-KEY": "fd990318d9db46cd00f7ebb7a396f735.1700000000000",
  });
});

test("accepts zero and positive model call counts", () => {
  expect(
    parseModelCallCount({
      code: 200,
      data: { todayModelCallCount: 0, totalModelCallCount: 1234 },
    }),
  ).toEqual({ todayModelCallCount: 0, totalModelCallCount: 1234 });
});

test("rejects failed or malformed upstream responses", () => {
  expect(parseModelCallCount({ code: 401, data: null })).toBeNull();
  expect(
    parseModelCallCount({
      code: 200,
      data: { todayModelCallCount: -1, totalModelCallCount: 1234 },
    }),
  ).toBeNull();
  expect(
    parseModelCallCount({
      code: 200,
      data: { todayModelCallCount: 2, totalModelCallCount: "1234" },
    }),
  ).toBeNull();
});
