import { describe, expect, test } from "@rstest/core";

import { resolveUnauthenticatedHomeRedirect } from "@/core/auth/unauthenticated-home";

describe("enterprise unauthenticated home redirect", () => {
  test("redirects an unauthenticated request to the configured HTTPS home", () => {
    expect(
      resolveUnauthenticatedHomeRedirect(
        { tag: "unauthenticated" },
        "https://datalake.cnooc/#/home",
      ),
    ).toBe("https://datalake.cnooc/#/home");
  });

  test("does not redirect an authenticated request", () => {
    expect(
      resolveUnauthenticatedHomeRedirect(
        {
          tag: "authenticated",
          user: {
            id: "user-1",
            email: "user@example.com",
            system_role: "user",
            needs_setup: false,
          },
        },
        "https://datalake.cnooc/#/home",
      ),
    ).toBeNull();
  });

  test("does not redirect setup or gateway failure states", () => {
    expect(
      resolveUnauthenticatedHomeRedirect(
        { tag: "system_setup_required" },
        "https://datalake.cnooc/#/home",
      ),
    ).toBeNull();
    expect(
      resolveUnauthenticatedHomeRedirect(
        { tag: "gateway_unavailable" },
        "https://datalake.cnooc/#/home",
      ),
    ).toBeNull();
  });

  test("rejects missing, malformed, and non-HTTPS destinations", () => {
    expect(
      resolveUnauthenticatedHomeRedirect(
        { tag: "unauthenticated" },
        undefined,
      ),
    ).toBeNull();
    expect(
      resolveUnauthenticatedHomeRedirect(
        { tag: "unauthenticated" },
        "not-a-url",
      ),
    ).toBeNull();
    expect(
      resolveUnauthenticatedHomeRedirect(
        { tag: "unauthenticated" },
        "http://datalake.cnooc/#/home",
      ),
    ).toBeNull();
  });
});
