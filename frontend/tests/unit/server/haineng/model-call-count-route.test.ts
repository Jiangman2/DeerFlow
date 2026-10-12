import { afterEach, beforeEach, expect, rs, test } from "@rstest/core";

import { GET } from "@/app/integrations/haineng/model-call-count/route";
import { AUTH_DISABLED_USER } from "@/core/auth/auth-disabled-user";
import { getServerSideUser } from "@/core/auth/server";
import { DEFAULT_HAINENG_MODEL_CALL_COUNT_URL } from "@/server/haineng/model-call-count";

rs.mock("@/core/auth/server", () => ({ getServerSideUser: rs.fn() }));

const ENV_KEYS = [
  "HAINENG_MODEL_CALL_COUNT_URL",
  "HAINENG_MODEL_CALL_COUNT_APP_ID",
  "HAINENG_MODEL_CALL_COUNT_APP_KEY",
] as const;
let previousEnv: Array<string | undefined>;

beforeEach(() => {
  previousEnv = ENV_KEYS.map((key) => process.env[key]);
  delete process.env.HAINENG_MODEL_CALL_COUNT_URL;
  process.env.HAINENG_MODEL_CALL_COUNT_APP_ID = "app";
  process.env.HAINENG_MODEL_CALL_COUNT_APP_KEY = "secret";
  rs.mocked(getServerSideUser).mockReset();
  rs.mocked(getServerSideUser).mockResolvedValue({
    tag: "authenticated",
    user: AUTH_DISABLED_USER,
  });
  rs.spyOn(Date, "now").mockReturnValue(1_700_000_000_000);
});

afterEach(() => {
  ENV_KEYS.forEach((key, index) => {
    const value = previousEnv[index];
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  });
  rs.unstubAllGlobals();
  rs.restoreAllMocks();
});

test("fetches both counts from the development endpoint using the Java-compatible signature", async () => {
  const fetchSpy = rs.fn(async () =>
    Response.json({
      code: 200,
      data: { todayModelCallCount: 0, totalModelCallCount: 1234 },
    }),
  );
  rs.stubGlobal("fetch", fetchSpy);

  const response = await GET();
  expect(response.status).toBe(200);
  expect(await response.json()).toEqual({
    todayModelCallCount: 0,
    totalModelCallCount: 1234,
  });
  expect(response.headers.get("Cache-Control")).toBe("private, no-store");
  expect(fetchSpy).toHaveBeenCalledWith(
    new URL(DEFAULT_HAINENG_MODEL_CALL_COUNT_URL),
    expect.objectContaining({
      method: "GET",
      headers: {
        "APP-ID": "app",
        "APP-KEY": "fd990318d9db46cd00f7ebb7a396f735.1700000000000",
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      redirect: "error",
      cache: "no-store",
    }),
  );
});

test("allows an explicitly configured HTTPS request path", async () => {
  process.env.HAINENG_MODEL_CALL_COUNT_URL =
    "https://ai-dev.cnooc.com.cn/gaixc/api/listedModelService/modelCallCount";
  const fetchSpy = rs.fn(async () =>
    Response.json({
      code: 200,
      data: { todayModelCallCount: 12, totalModelCallCount: 1234 },
    }),
  );
  rs.stubGlobal("fetch", fetchSpy);
  expect((await GET()).status).toBe(200);
  expect(fetchSpy).toHaveBeenCalledWith(
    new URL(process.env.HAINENG_MODEL_CALL_COUNT_URL),
    expect.anything(),
  );
});

test("does not contact Haineng for unauthenticated browsers", async () => {
  rs.mocked(getServerSideUser).mockResolvedValue({ tag: "unauthenticated" });
  const fetchSpy = rs.fn();
  rs.stubGlobal("fetch", fetchSpy);
  expect((await GET()).status).toBe(401);
  expect(fetchSpy).not.toHaveBeenCalled();
});

test("reports empty credential slots without making an upstream request", async () => {
  process.env.HAINENG_MODEL_CALL_COUNT_APP_KEY = "";
  const fetchSpy = rs.fn();
  rs.stubGlobal("fetch", fetchSpy);
  const response = await GET();
  expect(response.status).toBe(503);
  expect(await response.json()).toEqual({ error: "not_configured" });
  expect(fetchSpy).not.toHaveBeenCalled();
});

test("refuses plaintext endpoints before sending signed credentials", async () => {
  process.env.HAINENG_MODEL_CALL_COUNT_URL = "http://ai-dev.cnooc.com.cn/test";
  const fetchSpy = rs.fn();
  rs.stubGlobal("fetch", fetchSpy);
  const response = await GET();
  expect(response.status).toBe(503);
  expect(await response.json()).toEqual({ error: "invalid_configuration" });
  expect(fetchSpy).not.toHaveBeenCalled();
});

test("identifies HTTP authentication failures without returning upstream bodies or secrets", async () => {
  rs.stubGlobal(
    "fetch",
    rs.fn(
      async () =>
        new Response("secret authentication details", { status: 401 }),
    ),
  );
  const response = await GET();
  expect(response.status).toBe(502);
  expect(await response.json()).toEqual({
    error: "upstream_auth_failed",
    upstreamStatus: 401,
  });
});

test("identifies business-level authentication failures", async () => {
  rs.stubGlobal(
    "fetch",
    rs.fn(async () => Response.json({ code: 403 })),
  );
  const response = await GET();
  expect(response.status).toBe(502);
  expect(await response.json()).toEqual({
    error: "upstream_auth_failed",
    upstreamCode: 403,
  });
});

test("does not convert invalid usage responses into misleading zero counts", async () => {
  rs.stubGlobal(
    "fetch",
    rs.fn(async () => Response.json({ code: 200, data: {} })),
  );
  const response = await GET();
  expect(response.status).toBe(502);
  expect(await response.json()).toEqual({ error: "invalid_upstream_response" });
});

test("reports request timeouts distinctly", async () => {
  rs.stubGlobal(
    "fetch",
    rs.fn(async () => {
      throw new DOMException("Timed out", "TimeoutError");
    }),
  );
  const response = await GET();
  expect(response.status).toBe(504);
  expect(await response.json()).toEqual({ error: "upstream_timeout" });
});

test("does not expose network error details", async () => {
  rs.stubGlobal(
    "fetch",
    rs.fn(async () => {
      throw new Error("Connection failed with secret");
    }),
  );
  const response = await GET();
  expect(response.status).toBe(502);
  expect(await response.json()).toEqual({ error: "upstream_unavailable" });
});
