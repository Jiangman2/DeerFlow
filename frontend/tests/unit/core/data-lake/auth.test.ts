import { beforeEach, describe, expect, it, rs } from "@rstest/core";

import { fetch as fetchWithAuth } from "@/core/api/fetcher";
import { ensureDataLakeAuthorization } from "@/core/data-lake/auth";

rs.mock("@/core/api/fetcher", () => ({
  fetch: rs.fn(),
}));

describe("ensureDataLakeAuthorization", () => {
  beforeEach(() => {
    rs.mocked(fetchWithAuth).mockReset();
  });

  it("authorizes through the same-origin CSRF-aware endpoint", async () => {
    rs.mocked(fetchWithAuth).mockResolvedValue(
      new Response(JSON.stringify({ code: "authorized" }), { status: 200 }),
    );

    await expect(ensureDataLakeAuthorization()).resolves.toEqual({ ok: true });
    expect(fetchWithAuth).toHaveBeenCalledWith(
      "/api/v1/data-lake/auth/token",
      { method: "POST", signal: expect.any(AbortSignal) },
      { redirectOnUnauthorized: false },
    );
  });

  it("does not enable Data Lake when token acquisition fails", async () => {
    rs.mocked(fetchWithAuth).mockResolvedValue(
      new Response(JSON.stringify({ message: "upstream unavailable" }), {
        status: 503,
      }),
    );

    await expect(ensureDataLakeAuthorization()).resolves.toEqual({
      ok: false,
      status: 503,
      message: "upstream unavailable",
    });
  });

  it("preserves the permission-denied error code for the Data Lake control", async () => {
    rs.mocked(fetchWithAuth).mockResolvedValue(
      new Response(
        JSON.stringify({
          code: "data_lake_permission_denied",
          message: "The current account does not have Data Lake access.",
        }),
        { status: 403 },
      ),
    );

    await expect(ensureDataLakeAuthorization()).resolves.toEqual({
      ok: false,
      status: 403,
      code: "data_lake_permission_denied",
      message: "The current account does not have Data Lake access.",
    });
  });

  it("preserves the Data Lake login-required response without throwing", async () => {
    rs.mocked(fetchWithAuth).mockResolvedValue(
      new Response(
        JSON.stringify({
          code: "data_lake_login_required",
          message: "Please sign in to Data Lake first.",
        }),
        { status: 401 },
      ),
    );

    await expect(ensureDataLakeAuthorization()).resolves.toEqual({
      ok: false,
      status: 401,
      code: "data_lake_login_required",
      message: "Please sign in to Data Lake first.",
    });
  });
  it("bounds an unresponsive authorization request and allows a later retry", async () => {
    rs.useFakeTimers();
    try {
      rs.mocked(fetchWithAuth).mockImplementationOnce(
        (_url, init) =>
          new Promise((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () =>
              reject(new Error("Aborted")),
            );
          }),
      );
      const pending = ensureDataLakeAuthorization();
      await rs.advanceTimersByTimeAsync(60_000);
      await expect(pending).resolves.toEqual({
        ok: false,
        code: "data_lake_authorization_timeout",
      });
      rs.mocked(fetchWithAuth).mockResolvedValueOnce(
        new Response("{}", { status: 200 }),
      );
      await expect(ensureDataLakeAuthorization()).resolves.toEqual({
        ok: true,
      });
    } finally {
      rs.useRealTimers();
    }
  });
});
