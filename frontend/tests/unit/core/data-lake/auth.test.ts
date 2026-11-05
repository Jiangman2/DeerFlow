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
    rs.mocked(fetchWithAuth)
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ code: "authorized" })),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ authorized: true })),
      );

    await expect(ensureDataLakeAuthorization()).resolves.toEqual({ ok: true });

    expect(fetchWithAuth).toHaveBeenCalledWith(
      "/api/v1/data-lake/auth/token",
      { method: "POST", signal: expect.any(AbortSignal) },
      { redirectOnUnauthorized: false },
    );
    expect(fetchWithAuth).toHaveBeenNthCalledWith(
      2,
      "/api/v1/data-lake/auth/session",
      {
        method: "GET",
        cache: "no-store",
        signal: rs.mocked(fetchWithAuth).mock.calls[0]![1]!.signal,
      },
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
      rs.mocked(fetchWithAuth)
        .mockResolvedValueOnce(
          new Response(JSON.stringify({ code: "already_authorized" })),
        )
        .mockResolvedValueOnce(
          new Response(JSON.stringify({ authorized: true })),
        );
      await expect(ensureDataLakeAuthorization()).resolves.toEqual({
        ok: true,
      });
    } finally {
      rs.useRealTimers();
    }
  });

  it("does not resolve authorization until the fresh session response arrives", async () => {
    let confirm!: (response: Response) => void;
    rs.mocked(fetchWithAuth)
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ code: "authorized" })),
      )
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            confirm = resolve;
          }),
      );
    const done = rs.fn();
    const pending = ensureDataLakeAuthorization().then(done);
    await rs.waitFor(() => expect(fetchWithAuth).toHaveBeenCalledTimes(2));
    expect(done).not.toHaveBeenCalled();
    confirm(new Response(JSON.stringify({ authorized: true })));
    await pending;
    expect(done).toHaveBeenCalledWith({ ok: true });
  });

  it.each([
    [200, '{"authorized":false}'],
    [200, '{"authorized":"true"}'],
    [200, "{}"],
    [200, "null"],
    [200, "<html>login</html>"],
    [200, ""],
    [401, '{"authorized":true}'],
    [403, '{"authorized":true}'],
    [500, '{"authorized":true}'],
  ])("rejects unconfirmed session HTTP %i body %s", async (status, body) => {
    rs.mocked(fetchWithAuth)
      .mockResolvedValueOnce(new Response('{"code":"authorized"}'))
      .mockResolvedValueOnce(new Response(body, { status }));
    await expect(ensureDataLakeAuthorization()).resolves.toMatchObject({
      ok: false,
      status,
    });
    expect(fetchWithAuth).toHaveBeenCalledTimes(2);
  });

  it.each(["{}", "<html>login</html>", "", '{"code":"unknown"}'])(
    "rejects an invalid token success body %s",
    async (body) => {
      rs.mocked(fetchWithAuth).mockResolvedValueOnce(new Response(body));
      await expect(ensureDataLakeAuthorization()).resolves.toMatchObject({
        ok: false,
      });
      expect(fetchWithAuth).toHaveBeenCalledTimes(1);
    },
  );

  it("shares the 60 second deadline across both requests and allows a new attempt", async () => {
    rs.useFakeTimers();
    try {
      rs.mocked(fetchWithAuth)
        .mockImplementationOnce(
          () =>
            new Promise((resolve) =>
              setTimeout(
                () => resolve(new Response('{"code":"authorized"}')),
                45_000,
              ),
            ),
        )
        .mockImplementationOnce(
          (_url, init) =>
            new Promise((_resolve, reject) => {
              init!.signal!.addEventListener("abort", () =>
                reject(new Error("aborted")),
              );
            }),
        );
      const pending = ensureDataLakeAuthorization();
      await rs.advanceTimersByTimeAsync(45_000);
      expect(fetchWithAuth).toHaveBeenCalledTimes(2);
      const signal = rs.mocked(fetchWithAuth).mock.calls[1]![1]!.signal!;
      await rs.advanceTimersByTimeAsync(14_999);
      expect(signal.aborted).toBe(false);
      await rs.advanceTimersByTimeAsync(1);
      await expect(pending).resolves.toEqual({
        ok: false,
        code: "data_lake_authorization_timeout",
      });
      rs.mocked(fetchWithAuth)
        .mockResolvedValueOnce(new Response('{"code":"authorized"}'))
        .mockResolvedValueOnce(new Response('{"authorized":true}'));
      await expect(ensureDataLakeAuthorization()).resolves.toEqual({
        ok: true,
      });
    } finally {
      rs.useRealTimers();
    }
  });

  it("cancels a pending confirmation and ignores a late successful response", async () => {
    let confirm!: (response: Response) => void;
    rs.mocked(fetchWithAuth)
      .mockResolvedValueOnce(new Response('{"code":"authorized"}'))
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            confirm = resolve;
          }),
      );
    const controller = new AbortController();
    const pending = ensureDataLakeAuthorization(controller.signal);
    await rs.waitFor(() => expect(fetchWithAuth).toHaveBeenCalledTimes(2));
    controller.abort();
    confirm(new Response('{"authorized":true}'));
    await expect(pending).resolves.toEqual({
      ok: false,
      code: "data_lake_authorization_cancelled",
    });
  });

  it("does not call the server for an already cancelled attempt", async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(
      ensureDataLakeAuthorization(controller.signal),
    ).resolves.toMatchObject({ ok: false });
    expect(fetchWithAuth).not.toHaveBeenCalled();
  });

  it("keeps the control disabled on a session network failure", async () => {
    rs.mocked(fetchWithAuth)
      .mockResolvedValueOnce(new Response('{"code":"authorized"}'))
      .mockRejectedValueOnce(new TypeError("Network error"));
    await expect(ensureDataLakeAuthorization()).resolves.toMatchObject({
      ok: false,
    });
    expect(fetchWithAuth).toHaveBeenCalledTimes(2);
  });
});
