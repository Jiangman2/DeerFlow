import { afterEach, beforeEach, describe, expect, test } from "@rstest/core";

import { buildLoginUrl } from "@/core/auth/types";

const ENV_KEY = "NEXT_PUBLIC_AUTH_LOGIN_URL";

describe("buildLoginUrl", () => {
  let savedLoginUrl: string | undefined;

  beforeEach(() => {
    savedLoginUrl = process.env[ENV_KEY];
    delete process.env[ENV_KEY];
  });

  afterEach(() => {
    if (savedLoginUrl === undefined) {
      delete process.env[ENV_KEY];
    } else {
      process.env[ENV_KEY] = savedLoginUrl;
    }
  });

  test("uses the built-in login page when no external login URL is configured", () => {
    expect(buildLoginUrl("/workspace/chats/new")).toBe(
      "/login?next=%2Fworkspace%2Fchats%2Fnew",
    );
  });

  test("uses the configured data-lake login entry and preserves existing query params", () => {
    process.env[ENV_KEY] = "https://datalake.cnooc/login?app=deerflow";

    expect(buildLoginUrl("/workspace/knowledge")).toBe(
      "https://datalake.cnooc/login?app=deerflow&next=%2Fworkspace%2Fknowledge",
    );
  });

  test("supports relative custom login entries", () => {
    process.env[ENV_KEY] = "/api/sso/login";

    expect(buildLoginUrl("/workspace")).toBe(
      "/api/sso/login?next=%2Fworkspace",
    );
  });
});
