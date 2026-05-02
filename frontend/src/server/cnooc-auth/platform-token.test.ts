import assert from "node:assert/strict";
import test from "node:test";

const {
  buildCnoocPlatformTokenCookieOptions,
  resolveCnoocPlatformToken,
} = await import(new URL("./platform-token.ts", import.meta.url).href);

void test("prefers the login cookie token over the fallback token", () => {
  assert.equal(
    resolveCnoocPlatformToken(" login-token ", "fallback-token"),
    "login-token",
  );
});

void test("falls back to the configured download token when the cookie is missing", () => {
  assert.equal(
    resolveCnoocPlatformToken(" ", " fallback-token "),
    "fallback-token",
  );
});

void test("returns null when no usable token is available", () => {
  assert.equal(resolveCnoocPlatformToken(null, ""), null);
});

void test("uses an http-only lax cookie for the platform token", () => {
  assert.deepEqual(buildCnoocPlatformTokenCookieOptions(true), {
    httpOnly: true,
    maxAge: 60 * 60 * 24 * 7,
    path: "/",
    sameSite: "lax",
    secure: true,
  });
});
