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
    expect(fetchWithAuth).toHaveBeenCalledWith("/api/v1/data-lake/auth/token", {
      method: "POST",
    });
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
});
