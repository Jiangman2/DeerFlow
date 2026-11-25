import { afterEach, beforeEach, expect, test } from "@rstest/core";

import { resolveHainengAuthentication } from "@/core/haineng/server/auth";

const keys = [
  "HAINENG_TEST_AUTH_ENABLED",
  "HAINENG_SESSION",
  "HAINENG_SESSION_USER_ID",
] as const;
const original = new Map(keys.map((key) => [key, process.env[key]]));
beforeEach(() => {
  for (const key of keys) delete process.env[key];
});
afterEach(() => {
  for (const key of keys) {
    const value = original.get(key);
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
});
function enable() {
  process.env.HAINENG_TEST_AUTH_ENABLED = "true";
  process.env.HAINENG_SESSION_USER_ID = "owner-id";
  process.env.HAINENG_SESSION = "dummy-test-session==";
}

test("disabled by default, ignores configured credentials until explicitly enabled", async () => {
  enable();
  delete process.env.HAINENG_TEST_AUTH_ENABLED;
  expect(await resolveHainengAuthentication({ id: "owner-id" })).toEqual({
    headers: {},
  });
  process.env.HAINENG_TEST_AUTH_ENABLED = "false";
  expect(await resolveHainengAuthentication({ id: "owner-id" })).toEqual({
    headers: {},
  });
});

test("matching platform user gets temporary upstream cookie", async () => {
  enable();
  expect(await resolveHainengAuthentication({ id: "owner-id" })).toEqual({
    headers: { Cookie: "SESSION=dummy-test-session==" },
  });
});

test("other users do not inherit the owner's credential or gain a global restriction", async () => {
  enable();
  expect(await resolveHainengAuthentication({ id: "other-user" })).toEqual({
    headers: {},
  });
});

test("missing SESSION or owner ID leaves existing upstream behavior intact", async () => {
  enable();
  delete process.env.HAINENG_SESSION;
  expect(await resolveHainengAuthentication({ id: "owner-id" })).toEqual({
    headers: {},
  });
  enable();
  process.env.HAINENG_SESSION = "";
  expect(await resolveHainengAuthentication({ id: "owner-id" })).toEqual({
    headers: {},
  });
  enable();
  delete process.env.HAINENG_SESSION_USER_ID;
  expect(await resolveHainengAuthentication({ id: "owner-id" })).toEqual({
    headers: {},
  });
});

test("rejects malformed credentials without including them in errors", async () => {
  enable();
  process.env.HAINENG_SESSION = "private-value\r\nInjected: header";
  await expect(
    resolveHainengAuthentication({ id: "owner-id" }),
  ).rejects.toThrow("Invalid temporary Haineng credential format");
  // An unrelated user never even consumes the invalid personal credential.
  expect(await resolveHainengAuthentication({ id: "other-user" })).toEqual({
    headers: {},
  });
});
