import {
  afterEach,
  beforeEach,
  describe,
  expect,
  rs,
  test,
} from "@rstest/core";
import { NextRequest } from "next/server";

const TOKEN = "another-users-token";
const COOKIE = `access_token=current-session; cnooc.platform_token=${TOKEN}`;

const DOWNLOADABLE_FILE = {
  id: "file-row-1",
  fileId: "file-1",
  userId: "user-1",
  isComplete: 1,
  createDate: "2026-08-27 10:00:00",
  fileName: "report.pdf",
  type: 0,
  ptCode: "pt-1",
  isStore: 0,
  dacCode: null,
  num: 1,
  remark: null,
  tipsInfo: null,
  extra: null,
  errorInfo: null,
  shopType: 0,
};

describe("routes that consume cnooc.platform_token", () => {
  const originalGateway = process.env.DEER_FLOW_INTERNAL_GATEWAY_BASE_URL;
  const originalShopCart = process.env.SHOP_CART_API_BASE_URL;

  beforeEach(() => {
    process.env.DEER_FLOW_INTERNAL_GATEWAY_BASE_URL =
      "http://gateway.internal:8001";
    process.env.SHOP_CART_API_BASE_URL = "http://127.0.0.1:9";
    rs.stubGlobal(
      "fetch",
      rs.fn(() =>
        Promise.resolve(Response.json({ authorized: false }, { status: 401 })),
      ),
    );
  });

  afterEach(() => {
    if (originalGateway === undefined) {
      delete process.env.DEER_FLOW_INTERNAL_GATEWAY_BASE_URL;
    } else {
      process.env.DEER_FLOW_INTERNAL_GATEWAY_BASE_URL = originalGateway;
    }
    if (originalShopCart === undefined) {
      delete process.env.SHOP_CART_API_BASE_URL;
    } else {
      process.env.SHOP_CART_API_BASE_URL = originalShopCart;
    }
    rs.unstubAllGlobals();
    rs.resetModules();
  });

  test("external-agent launch refuses a token bound to another user", async () => {
    const { GET } =
      await import("@/app/api/cnooc/external-agents/[agent]/launch/route");
    const response = await GET(
      new NextRequest(
        "http://deerflow.local/api/cnooc/external-agents/zywdjxzj/launch",
        {
          headers: { cookie: COOKIE },
        },
      ),
      { params: Promise.resolve({ agent: "zywdjxzj" }) },
    );

    expect(response.status).toBe(401);
    expect(response.headers.has("location")).toBe(false);
    expect(response.headers.get("set-cookie")?.toLowerCase()).toContain(
      "max-age=0",
    );
    expect(await response.text()).not.toContain(TOKEN);
  });

  test("new shop-cart list refuses a token bound to another user", async () => {
    const { GET } = await import("@/app/api/shop-cart/new/list/route");
    const response = await GET(
      new NextRequest("http://deerflow.local/api/shop-cart/new/list", {
        headers: { cookie: COOKIE },
      }),
    );

    expect(response.status).toBe(401);
    expect(response.headers.get("set-cookie")?.toLowerCase()).toContain(
      "max-age=0",
    );
  });

  test("new shop-cart download refuses a token bound to another user", async () => {
    const { POST } = await import("@/app/api/shop-cart/new/download/route");
    const response = await POST(
      new NextRequest("http://deerflow.local/api/shop-cart/new/download", {
        body: JSON.stringify(DOWNLOADABLE_FILE),
        headers: { "content-type": "application/json", cookie: COOKIE },
        method: "POST",
      }),
    );

    expect(response.status).toBe(401);
    expect(response.headers.get("set-cookie")?.toLowerCase()).toContain(
      "max-age=0",
    );
  });

  test("legacy shop-cart download refuses a token bound to another user", async () => {
    const { GET } = await import("@/app/api/shop-cart/download/route");
    const response = await GET(
      new NextRequest(
        "http://deerflow.local/api/shop-cart/download?fileId=file-1",
        { headers: { cookie: COOKIE } },
      ),
    );

    expect(response.status).toBe(401);
    expect(response.headers.get("set-cookie")?.toLowerCase()).toContain(
      "max-age=0",
    );
  });
});
