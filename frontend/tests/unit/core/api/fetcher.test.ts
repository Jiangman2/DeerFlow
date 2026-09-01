import { afterEach, describe, expect, it, rs } from "@rstest/core";

import { fetch as fetchWithAuth } from "@/core/api/fetcher";

describe("authenticated fetch", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("returns an expected business 401 without redirecting to login", async () => {
    const unauthorizedResponse = new Response(
      JSON.stringify({ code: "data_lake_login_required" }),
      { status: 401 },
    );
    globalThis.fetch = rs.fn(async () => unauthorizedResponse) as typeof fetch;

    const response = await fetchWithAuth(
      "/api/v1/data-lake/auth/token",
      { method: "POST" },
      { redirectOnUnauthorized: false },
    );

    expect(response).toBe(unauthorizedResponse);
  });
});
