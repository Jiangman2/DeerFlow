import {
  afterEach,
  beforeEach,
  describe,
  expect,
  rs,
  test,
} from "@rstest/core";
import { NextRequest } from "next/server";

import {
  authorizeCnoocPlatformToken,
  buildCnoocPlatformTokenRejection,
} from "@/server/cnooc-auth/platform-token";

const INTERNAL_GATEWAY_ENV = "DEER_FLOW_INTERNAL_GATEWAY_BASE_URL";

describe("CNOOC platform-token session authorization", () => {
  let previousGatewayBaseUrl: string | undefined;

  beforeEach(() => {
    previousGatewayBaseUrl = process.env[INTERNAL_GATEWAY_ENV];
    process.env[INTERNAL_GATEWAY_ENV] = "http://gateway.internal:8001";
  });

  afterEach(() => {
    if (previousGatewayBaseUrl === undefined) {
      delete process.env[INTERNAL_GATEWAY_ENV];
    } else {
      process.env[INTERNAL_GATEWAY_ENV] = previousGatewayBaseUrl;
    }
    rs.unstubAllGlobals();
  });

  test("rejects a missing platform-token cookie without contacting Gateway", async () => {
    const fetchSpy = rs.fn();
    rs.stubGlobal("fetch", fetchSpy);
    const request = new NextRequest("http://deerflow.local/api/test", {
      headers: { cookie: "access_token=session-token" },
    });

    await expect(authorizeCnoocPlatformToken(request)).resolves.toEqual({
      authorized: false,
      reason: "invalid",
    });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  test("returns the server-only token only after Gateway confirms its user binding", async () => {
    const fetchSpy = rs.fn(() =>
      Promise.resolve(Response.json({ authorized: true }, { status: 200 })),
    );
    rs.stubGlobal("fetch", fetchSpy);
    const cookie =
      "access_token=session-token; cnooc.platform_token=current-user-token";
    const request = new NextRequest("http://deerflow.local/api/test", {
      headers: { cookie },
    });

    await expect(authorizeCnoocPlatformToken(request)).resolves.toEqual({
      authorized: true,
      token: "current-user-token",
    });
    expect(fetchSpy).toHaveBeenCalledWith(
      new URL("http://gateway.internal:8001/api/v1/data-lake/auth/session"),
      {
        cache: "no-store",
        headers: { cookie },
        method: "GET",
        redirect: "manual",
      },
    );
  });

  test("treats a Gateway binding rejection as an invalid stale cookie", async () => {
    rs.stubGlobal(
      "fetch",
      rs.fn(() =>
        Promise.resolve(Response.json({ authorized: false }, { status: 401 })),
      ),
    );
    const request = new NextRequest("http://deerflow.local/api/test", {
      headers: {
        cookie:
          "access_token=session-token; cnooc.platform_token=another-user-token",
      },
    });

    await expect(authorizeCnoocPlatformToken(request)).resolves.toEqual({
      authorized: false,
      reason: "invalid",
    });
  });

  test("fails closed without deleting a valid cookie when Gateway is unavailable", async () => {
    rs.stubGlobal(
      "fetch",
      rs.fn(() => Promise.resolve(new Response(null, { status: 503 }))),
    );
    const request = new NextRequest("http://deerflow.local/api/test", {
      headers: {
        cookie:
          "access_token=session-token; cnooc.platform_token=current-user-token",
      },
    });

    await expect(authorizeCnoocPlatformToken(request)).resolves.toEqual({
      authorized: false,
      reason: "unavailable",
    });
  });

  test("clears only an invalid stale platform-token cookie", () => {
    const invalid = buildCnoocPlatformTokenRejection("invalid");
    const unavailable = buildCnoocPlatformTokenRejection("unavailable");

    expect(invalid.status).toBe(401);
    expect(invalid.headers.get("set-cookie")?.toLowerCase()).toContain(
      "cnooc.platform_token=",
    );
    expect(invalid.headers.get("set-cookie")?.toLowerCase()).toContain(
      "max-age=0",
    );
    expect(unavailable.status).toBe(503);
    expect(unavailable.headers.has("set-cookie")).toBe(false);
  });
});
